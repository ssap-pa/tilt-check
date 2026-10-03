"""Where would a fixed take-profit and stop have closed her trades?

Her prop-account export can't answer that by itself: NinjaTrader's MAE and MFE
columns there just repeat the final P&L. So this replays each of her entries on
the minute bars that followed it. A bracket is +target / -stop in points from
her fill price, and every rule leans conservative:

- only bars that start after the entry minute are used (the rest of that minute is unknown);
- a bar that touches both the stop and the target can't say which came first, so every
  result is worked out twice: once counting it as the stop, once as the target;
- a trade that hits neither within `horizon` minutes closes at that bar's close.

Brackets are picked on her older 70% of trades and scored on the newer 30%, so
the result isn't just the best-looking row of a table fitted to the same trades.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .bars import contract_key, inside

TICK = {"MNQ": 0.25, "NQ": 0.25, "MES": 0.25, "ES": 0.25, "M2K": 0.1, "RTY": 0.1, "MYM": 1.0, "YM": 1.0,
        "MGC": 0.1, "GC": 0.1, "MCL": 0.01, "CL": 0.01}
MULTS = [0.5, 0.75, 1.0, 1.5, 2.0, 3.0]          # bracket sizes, in units of her usual move
# $ per point per contract (CME/COMEX/NYMEX), for instruments her history hasn't seen yet
POINT_VALUE = {"MNQ": 2, "NQ": 20, "MES": 5, "ES": 50, "MYM": 0.5, "YM": 5, "M2K": 5, "RTY": 50,
               "MGC": 10, "GC": 100, "MCL": 100, "CL": 1000, "SIL": 1000, "SI": 5000, "MHG": 2500, "HG": 25000}


def points(trades: pd.DataFrame) -> pd.Series:
    """Points per contract from entry to (last) exit, positive = in her favour."""
    d = np.where(trades["side"] == "long", 1.0, -1.0)
    return (trades["exit_price"] - trades["entry_price"]) * d


def point_values(trades: pd.DataFrame) -> dict[str, float]:
    """Dollars per point per contract, read off her own fills (profit + commission = gross)."""
    t = trades[trades.get("rows", 1) == 1].copy()
    t["pts"] = points(t)
    t = t[t["pts"].abs() > 1e-9]
    pv = ((t["profit"] + t["commission"]) / (t["pts"] * t["qty"])).groupby(t["instrument"]).median()
    return pv.round(4).to_dict()


def usual_move(trades: pd.DataFrame) -> dict[str, float]:
    """Median size of her closed moves per instrument, in points: the unit brackets are measured in."""
    t = trades[trades.get("rows", 1) == 1]
    return points(t).abs().groupby(t["instrument"]).median().to_dict()


def how_she_exits(trades: pd.DataFrame) -> pd.DataFrame:
    """Per instrument: winners vs losers in points and minutes held. Needs no bars."""
    t = trades[trades.get("rows", 1) == 1].copy()
    t["pts"] = points(t)
    t["minutes"] = (t["exit_time"] - t["entry_time"]).dt.total_seconds() / 60
    t["result"] = np.where(t["profit"] > 0, "win", "loss")
    g = t.groupby(["instrument", "result"]).agg(trades=("pts", "size"), median_pts=("pts", "median"),
                                                median_minutes=("minutes", "median"))
    return g.round(2)


def _round(x: float, tick: float) -> float:
    return max(tick, round(x / tick) * tick)


@dataclass
class Path_:
    fav: np.ndarray        # best price in her favour on each bar, points from her fill
    adv: np.ndarray        # worst price against her on each bar, points from her fill
    close: np.ndarray      # bar closes, points from her fill


def paths(trades: pd.DataFrame, bars: dict, offset: pd.Timedelta, horizon: int = 120) -> dict[int, Path_]:
    """Price path after each entry, from the first full bar after the entry minute.
    Trades whose fill doesn't sit inside its bar (wrong contract, missing data) are left out."""
    out = {}
    ok = inside(trades, bars, offset)
    for i, r in trades.iterrows():
        b = bars.get(contract_key(r["contract"]))
        if b is None or not ok[i]:
            continue
        start = r["entry_time"].floor("min") + pd.Timedelta(minutes=2)    # close of the first full bar
        w = b.loc[start - offset: start - offset + pd.Timedelta(minutes=horizon - 1)]
        if len(w) < 3:
            continue
        e = r["entry_price"]
        if r["side"] == "long":
            out[i] = Path_(w["high"].to_numpy() - e, e - w["low"].to_numpy(), w["close"].to_numpy() - e)
        else:
            out[i] = Path_(e - w["low"].to_numpy(), w["high"].to_numpy() - e, e - w["close"].to_numpy())
    return out


def bracket(p: Path_, target: float, stop: float, ties: str = "stop") -> float:
    """Points per contract this bracket would have closed at. `ties`: what a bar that
    touched both levels counts as ("stop" or "target")."""
    hit_t = np.flatnonzero(p.fav >= target)
    hit_s = np.flatnonzero(p.adv >= stop)
    t = hit_t[0] if len(hit_t) else np.inf
    s = hit_s[0] if len(hit_s) else np.inf
    if np.isinf(t) and np.isinf(s):
        return float(p.close[-1])
    if s < t or (s == t and ties == "stop"):
        return -stop
    return target


@dataclass
class Replay:
    covered: int                 # trades with bars
    total: int
    offset: pd.Timedelta
    train: int
    test: int
    best: tuple[float, float]    # (target mult, stop mult) picked on the older trades
    actual_train: float          # her real net on the older trades
    actual_test: float           # her real net on the newer trades
    bracket_test: float          # the picked bracket's net on the same trades
    in_sample: pd.DataFrame      # net $ for every bracket on the older trades (rows target, cols stop)
    sizes: dict                  # instrument -> (target pts, stop pts) for the picked bracket


def replay(trades: pd.DataFrame, bars: dict, offset: pd.Timedelta, horizon: int = 120,
           train_share: float = 0.7, ties: str = "stop") -> Replay:
    t = trades.sort_values("entry_time").reset_index(drop=True)
    pv, unit = point_values(t), usual_move(t)
    ps = paths(t, bars, offset, horizon)
    idx = [i for i in t.index if i in ps and t.loc[i, "instrument"] in pv and t.loc[i, "instrument"] in unit]
    cut = int(len(idx) * train_share)
    train, test = idx[:cut], idx[cut:]

    def net(ids, mt, ms):
        total = 0.0
        for i in ids:
            r = t.loc[i]
            tick = TICK.get(r["instrument"], 0.01)
            pts = bracket(ps[i], _round(mt * unit[r["instrument"]], tick), _round(ms * unit[r["instrument"]], tick),
                          ties)
            total += pts * pv[r["instrument"]] * r["qty"] - r["commission"]
        return total

    table = pd.DataFrame({ms: {mt: round(net(train, mt, ms), 2) for mt in MULTS} for ms in MULTS})
    mt, ms = table.stack().idxmax()
    sizes = {k: (_round(mt * unit[k], TICK.get(k, 0.01)), _round(ms * unit[k], TICK.get(k, 0.01)))
             for k in sorted({t.loc[i, "instrument"] for i in idx})}
    return Replay(covered=len(idx), total=len(t), offset=offset, train=len(train), test=len(test),
                  best=(mt, ms), actual_train=round(float(t.loc[train, "profit"].sum()), 2),
                  actual_test=round(float(t.loc[test, "profit"].sum()), 2),
                  bracket_test=round(net(test, mt, ms), 2), in_sample=table, sizes=sizes)


def excursions(trades: pd.DataFrame, bars: dict, offset: pd.Timedelta, minutes: int = 30) -> pd.DataFrame:
    """How far price went for and against her in the first `minutes` after each entry, in points."""
    ps = paths(trades, bars, offset, minutes)
    rows = [{"index": i, "mfe": float(p.fav.max()), "mae": float(p.adv.max())} for i, p in ps.items()]
    return pd.DataFrame(rows).set_index("index") if rows else pd.DataFrame(columns=["mfe", "mae"])
