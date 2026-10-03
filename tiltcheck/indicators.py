"""Indicators from 1-minute bars, as they stood when she clicked.

Every value for an entry at time t comes from bars that had closed by t, so a
trade never "knows" the bar it was entered in. Bars are indexed by close time
(NinjaTrader's convention); `offset` (from bars.align) moves them onto the PC
clock the trades are on.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .bars import contract_key
from .sessions import NY, pc_zone


def on_pc_clock(bars: pd.DataFrame, offset: pd.Timedelta) -> pd.DataFrame:
    b = bars.copy()
    b.index = b.index + offset
    return b


def resample(b1: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """1-minute bars -> N-minute bars, still stamped by close time."""
    return b1.resample(f"{minutes}min", label="right", closed="right").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def session_vwap(b1: pd.DataFrame) -> pd.DataFrame:
    """VWAP and its volume-weighted standard deviation, restarting at 18:00 New York (the CME session open)."""
    ny = b1.index.tz_localize(pc_zone(), ambiguous="NaT", nonexistent="shift_forward").tz_convert(NY)
    session = (ny - pd.Timedelta(hours=18)).date
    tp = (b1["high"] + b1["low"] + b1["close"]) / 3
    v = b1["volume"].clip(lower=0).astype(float) + 1e-9
    cv = v.groupby(session).cumsum()
    vwap = (tp * v).groupby(session).cumsum() / cv
    var = (tp * tp * v).groupby(session).cumsum() / cv - vwap * vwap
    return pd.DataFrame({"vwap": vwap, "vwap_sd": np.sqrt(var.clip(lower=0))}, index=b1.index)


def fvgs(b: pd.DataFrame) -> pd.DataFrame:
    """Fair value gaps: a 3-bar gap between bar i-2 and bar i. Formed when bar i closes."""
    h, l = b["high"].to_numpy(), b["low"].to_numpy()
    rows = []
    for i in range(2, len(b)):
        if l[i] > h[i - 2]:
            rows.append((b.index[i], "bull", h[i - 2], l[i]))
        elif h[i] < l[i - 2]:
            rows.append((b.index[i], "bear", h[i], l[i - 2]))
    return pd.DataFrame(rows, columns=["formed", "kind", "lo", "hi"])


def _asof(s: pd.Series, t: pd.Timestamp) -> float:
    """Last value with index <= t (bars that had closed by t)."""
    i = s.index.searchsorted(t, side="right") - 1
    return float(s.iloc[i]) if i >= 0 else float("nan")


def at_entries(trades: pd.DataFrame, bars: dict, offset: pd.Timedelta, fvg_hours: int = 24) -> pd.DataFrame:
    """One row per trade (same index) with what the chart showed at the click."""
    out = []
    for key, g in trades.assign(_k=trades["contract"].map(contract_key)).groupby("_k"):
        if key not in bars:
            continue
        b1 = on_pc_clock(bars[key], offset)
        b5, b15, b60 = resample(b1, 5), resample(b1, 15), resample(b1, 60)
        s = {
            "sma200_15m": b15["close"].rolling(200).mean(),
            "ema200_15m": ema(b15["close"], 200),
            "ema9_5m": ema(b5["close"], 9),
            "ema100_5m": ema(b5["close"], 100),
            "ema9_1h": ema(b60["close"], 9),
        }
        vw = session_vwap(b1)
        gaps = fvgs(b15)
        for i, r in g.iterrows():
            t, px = r["entry_time"], r["entry_price"]
            row = {"index": i, "price_before": _asof(b1["close"], t)}
            row.update({k: _asof(v, t) for k, v in s.items()})
            row["vwap"], row["vwap_sd"] = _asof(vw["vwap"], t), _asof(vw["vwap_sd"], t)
            # 15-minute FVGs formed in the last `fvg_hours` that her fill sits in, and whether
            # price had already been back into that gap before this entry (first touch or not).
            row["fvg"], row["fvg_first_touch"] = None, None
            recent = gaps[(gaps["formed"] <= t) & (gaps["formed"] > t - pd.Timedelta(hours=fvg_hours))]
            for z in recent.itertuples():
                if z.lo <= px <= z.hi:
                    between = b1.loc[(b1.index > z.formed) & (b1.index <= t.floor("min"))]
                    touch = ((between["low"] <= z.hi) & (between["high"] >= z.lo)).to_numpy()
                    # First touch = she's still in price's first visit to the gap: no touch yet, or
                    # one unbroken run of touching bars that reaches her entry minute.
                    if not touch.any():
                        first = True
                    else:
                        start = int(np.argmax(touch))
                        first = bool(touch[start:].all())
                    row["fvg"], row["fvg_first_touch"] = z.kind, first
                    break
            out.append(row)
    return pd.DataFrame(out).set_index("index") if out else pd.DataFrame()
