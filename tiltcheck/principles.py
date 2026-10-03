"""Check a trader's own written principles against her own trades.

Each principle is a yes/no question about one entry, answered from what the
chart showed at the click (indicators.at_entries) or from the trade itself.
For each one: trades where she followed it vs. trades where she didn't, with
win rate and net. Small groups are marked, because a rule that "worked" on 9
trades hasn't shown anything yet.

The definitions below are a starting point; a trader's own wording decides
what "near" or "extended" means, and each threshold is a parameter.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .exits import points, usual_move

FULL_SIZE = {"GC", "CL", "SI", "HG"}          # not micro or mini


def flags(trades: pd.DataFrame, ind: pd.DataFrame, vwap_sd: float = 1.0) -> pd.DataFrame:
    """True = followed the principle, False = broke it, NaN = can't tell (no bars)."""
    t = trades.join(ind, how="left")
    d = np.where(t["side"] == "long", 1.0, -1.0)
    px = t["entry_price"]
    unit = t["instrument"].map(usual_move(trades))
    f = pd.DataFrame(index=t.index)

    def where(cond, known):
        return pd.Series(np.where(known, cond, np.nan), index=t.index)

    f["with the 15m 200 SMA trend"] = where((px - t["sma200_15m"]) * d > 0, t["sma200_15m"].notna())
    f["with the 15m 200 EMA trend"] = where((px - t["ema200_15m"]) * d > 0, t["sma200_15m"].notna())
    f["with the 5m 100 EMA trend"] = where((px - t["ema100_5m"]) * d > 0, t["ema100_5m"].notna())
    stretched = (px - t["vwap"]) * d > vwap_sd * t["vwap_sd"]
    f[f"not chasing VWAP (> {vwap_sd:g} sd)"] = where(~stretched, t["vwap"].notna())
    f["near the 1h 9 EMA (within her usual move)"] = where((px - t["ema9_1h"]).abs() <= unit, t["ema9_1h"].notna())
    in_dir = ((t["fvg"] == "bull") & (d > 0)) | ((t["fvg"] == "bear") & (d < 0))
    f["in a first-touch 15m FVG, trade direction"] = where(in_dir & (t["fvg_first_touch"] == True),
                                                           t["ema9_1h"].notna())
    f["micro or mini contract"] = ~t["instrument"].isin(FULL_SIZE)
    return f


def audit(trades: pd.DataFrame, f: pd.DataFrame, min_n: int = 20) -> pd.DataFrame:
    rows = []
    for name in f.columns:
        for followed in (True, False):
            g = trades[f[name] == followed]
            if not len(g):
                continue
            rows.append({"principle": name, "followed": "yes" if followed else "no", "trades": len(g),
                         "win_rate": round(float((g["profit"] > 0).mean()), 3),
                         "net": round(float(g["profit"].sum()), 2), "avg": round(float(g["profit"].mean()), 2),
                         "note": "" if len(g) >= min_n else "small sample"})
    return pd.DataFrame(rows)


def winners_in_ticks(trades: pd.DataFrame, tick: dict, target_ticks: int = 200) -> pd.DataFrame:
    """How her winners compare with a target written in ticks (e.g. 'take 200+ tick moves')."""
    t = trades[(trades["profit"] > 0) & (trades.get("rows", 1) == 1)].copy()
    t["ticks"] = points(t) / t["instrument"].map(tick)
    g = t.groupby("instrument")["ticks"]
    return pd.DataFrame({"winners": g.size(), "median_ticks": g.median().round(1),
                         f"share_{target_ticks}+": g.apply(lambda s: round(float((s >= target_ticks).mean()), 3))})


def trades_per_day(trades: pd.DataFrame) -> pd.DataFrame:
    """Busy days vs quiet days ('trade less, only the sure spots')."""
    d = trades.groupby(trades["entry_time"].dt.date).agg(trades=("profit", "size"), net=("profit", "sum"))
    cut = d["trades"].median()
    d["day"] = np.where(d["trades"] <= cut, f"<= {cut:g} trades", f"> {cut:g} trades")
    return d.groupby("day").agg(days=("net", "size"), trades=("trades", "sum"), net=("net", "sum"),
                                net_per_day=("net", "mean")).round(2)
