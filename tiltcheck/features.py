"""Pre-trade features: only things you know at the moment you click Buy or Sell.

No MAE, MFE, exit time or exit price here. Those describe how the trade went, and
using them would make the model look smarter than it can be in real time.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .sessions import session_of

# Column order matters: TabPFN gets a plain matrix plus the indices of categorical columns.
FEATURES = [
    "instrument",            # categorical
    "side",                  # categorical
    "qty",
    "size_vs_usual",         # qty / median qty of earlier trades
    "hour",
    "weekday",               # categorical
    "minutes_since_exit",    # since the previous trade closed (capped at 24h)
    "prev_win",              # 1 if the previous trade made money
    "losses_in_a_row",       # losing trades right before this one
    "trades_today",          # trades already taken this calendar day
    "pnl_today",             # money already made or lost this calendar day
]
CATEGORICAL = ["instrument", "side", "weekday"]


def add_features(trades: pd.DataFrame) -> pd.DataFrame:
    """Return trades with feature columns and the label `win` (profit > 0)."""
    t = trades.sort_values("entry_time").reset_index(drop=True).copy()
    t["win"] = (t["profit"] > 0).astype(int)
    t["hour"] = t["entry_time"].dt.hour
    t["weekday"] = t["entry_time"].dt.day_name().str[:3]
    t["day"] = t["entry_time"].dt.date

    # What was already closed when this trade was entered? Trades can overlap, so
    # "the previous row" is not good enough: walk the exits in time order and look
    # up the latest exit strictly before each entry.
    closed = t[["exit_time", "profit"]].sort_values("exit_time").reset_index(drop=True)
    closed["c_win"] = (closed["profit"] > 0).astype(int)
    streak, s = [], 0
    for w in closed["c_win"]:
        s = 0 if w else s + 1
        streak.append(s)
    closed["c_streak"] = streak
    closed["c_day"] = closed["exit_time"].dt.date
    closed["c_day_pnl"] = closed.groupby("c_day")["profit"].cumsum()
    m = pd.merge_asof(t[["entry_time"]].reset_index(), closed.rename(columns={"exit_time": "c_exit"}),
                      left_on="entry_time", right_on="c_exit", direction="backward",
                      allow_exact_matches=False).set_index("index").sort_index()

    gap = (t["entry_time"] - m["c_exit"]).dt.total_seconds() / 60
    t["minutes_since_exit"] = gap.clip(lower=0, upper=24 * 60).fillna(24 * 60)
    t["prev_win"] = m["c_win"].fillna(1).astype(int)
    t["losses_in_a_row"] = m["c_streak"].fillna(0).astype(int)
    same_day = m["c_day"] == t["day"]
    t["pnl_today"] = m["c_day_pnl"].where(same_day, 0.0).fillna(0.0)
    # entries earlier the same day (simultaneous entries count once)
    t["trades_today"] = t.groupby("day")["entry_time"].rank(method="dense").astype(int) - 1

    usual = t["qty"].expanding().median().shift(1).fillna(t["qty"].iloc[0])
    t["size_vs_usual"] = (t["qty"] / usual).round(2)
    return t


def encode(df: pd.DataFrame, categories: dict[str, list[str]] | None = None):
    """Matrix for the model. Categories are fixed from the training data so a
    planned trade is encoded the same way as history."""
    if categories is None:
        categories = {c: sorted(df[c].astype(str).unique()) for c in CATEGORICAL}
    X = df[FEATURES].copy()
    for c in CATEGORICAL:
        lookup = {v: i for i, v in enumerate(categories[c])}
        X[c] = X[c].astype(str).map(lambda v: lookup.get(v, -1))
    cat_idx = [FEATURES.index(c) for c in CATEGORICAL]
    return X.to_numpy(dtype=float), cat_idx, categories


def planned_row(history: pd.DataFrame, instrument: str, side: str, qty: int, when: pd.Timestamp) -> pd.DataFrame:
    """Feature row for a trade you are about to take, using the history up to now."""
    h = add_features(history)
    last = h.iloc[-1] if len(h) else None
    today = h[h["day"] == when.date()] if len(h) else h
    if last is not None:
        last_win = int(last["profit"] > 0)
        streak = 0 if last_win else int(last["losses_in_a_row"]) + 1
        gap = max(0.0, (when - last["exit_time"]).total_seconds() / 60)
    else:
        last_win, streak, gap = 1, 0, 24 * 60
    usual = float(h["qty"].median()) if len(h) else float(qty)
    return pd.DataFrame([{
        "instrument": instrument,
        "side": side,
        "qty": qty,
        "size_vs_usual": round(qty / usual, 2) if usual else 1.0,
        "hour": when.hour,
        "weekday": when.day_name()[:3],
        "minutes_since_exit": min(gap, 24 * 60),
        "prev_win": last_win,
        "losses_in_a_row": streak,
        "trades_today": len(today),
        "pnl_today": float(today["profit"].sum()) if len(today) else 0.0,
        "entry_time": when,                  # not a model feature; used for New York session labels
    }])


def describe_row(row: pd.Series) -> str:
    ny = session_of(pd.Series([row["entry_time"]])).iloc[0] if "entry_time" in row else ""
    return (f"{row['side']} {int(row['qty'])} {row['instrument']} at {int(row['hour']):02d}h {row['weekday']}"
            f"{f' ({ny})' if ny else ''}, "
            f"{int(row['losses_in_a_row'])} loss(es) in a row before, {int(row['trades_today'])} trade(s) earlier today, "
            f"today P&L ${row['pnl_today']:.0f}, {row['minutes_since_exit']:.0f} min since last exit")


def similar_trades(history: pd.DataFrame, row: pd.DataFrame, k: int = 5) -> pd.DataFrame:
    """Past trades closest to the planned one (same instrument and side first)."""
    h = add_features(history)
    cand = h[(h["instrument"] == row["instrument"].iloc[0]) & (h["side"] == row["side"].iloc[0])]
    if len(cand) < k:
        cand = h
    num = ["hour", "losses_in_a_row", "trades_today", "size_vs_usual", "minutes_since_exit"]
    scale = cand[num].std().replace(0, 1)
    d = (((cand[num] - row[num].iloc[0]) / scale) ** 2).sum(axis=1) ** 0.5
    return cand.assign(distance=d).nsmallest(k, "distance")
