"""Minute bars exported from NinjaTrader (Control Center > New > Historical Data > Export, Minute).

One file per contract, named like "MNQ 12-26.Last.txt", one bar per line:

    20260915 132100;24350.25;24361.5;24348;24359.75;812

The timestamp is the bar's close. The clock it's written in depends on the
NinjaTrader setup, so `align` finds it from the trades themselves: the right
offset is the one that puts her fill prices inside the bar they were filled in.
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

MONTHS = {m: f"{i:02d}" for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], 1)}


def contract_key(name: str) -> str:
    """'MNQ DEC26' (Trades grid) and 'MNQ 12-26' (bar export) -> 'MNQ 12-26'."""
    name = name.strip().upper()
    m = re.match(r"^(\S+)\s+([A-Z]{3})(\d{2})$", name)
    if m and m.group(2) in MONTHS:
        return f"{m.group(1)} {MONTHS[m.group(2)]}-{m.group(3)}"
    m = re.match(r"^(\S+)\s+(\d{2})-(\d{2})$", name)
    return f"{m.group(1)} {m.group(2)}-{m.group(3)}" if m else name


def load_bars(paths) -> dict[str, pd.DataFrame]:
    """{contract: bars indexed by close time}. `paths` can mix files and folders."""
    files = []
    for p in map(Path, paths):
        files += sorted(p.glob("*.txt")) if p.is_dir() else [p]
    out = {}
    for f in files:
        df = pd.read_csv(f, sep=";", header=None, names=["time", "open", "high", "low", "close", "volume"],
                         dtype={"time": str})
        df["time"] = pd.to_datetime(df["time"], format="%Y%m%d %H%M%S")
        key = contract_key(f.name.split(".")[0])
        out[key] = pd.concat([out[key], df.set_index("time")]) if key in out else df.set_index("time")
    return {k: v[~v.index.duplicated()].sort_index() for k, v in out.items()}


def _fill_bars(trades: pd.DataFrame, bars: dict, offset: pd.Timedelta) -> pd.DataFrame:
    """For each trade, the bar its entry was filled in (bar close strictly after the entry)."""
    parts = []
    for key, g in trades.assign(key=trades["contract"].map(contract_key)).groupby("key"):
        if key not in bars:
            continue
        b = bars[key][["low", "high"]].reset_index().rename(columns={"time": "bar_close"})
        b["bar_close"] = b["bar_close"] + offset
        g = g.reset_index().sort_values("entry_time")
        m = pd.merge_asof(g, b, left_on="entry_time", right_on="bar_close", direction="forward",
                          allow_exact_matches=False, tolerance=pd.Timedelta(minutes=1))
        parts.append(m)
    return pd.concat(parts) if parts else pd.DataFrame(columns=["index", "low", "high", "entry_price"])


def align(trades: pd.DataFrame, bars: dict) -> tuple[pd.Timedelta, float]:
    """Clock offset to add to bar times so they line up with the trade times, and the
    share of entries whose fill price sits inside their bar at that offset."""
    best = (pd.Timedelta(0), -1.0)
    for half_hours in range(-28, 29):
        off = pd.Timedelta(minutes=30 * half_hours)
        m = _fill_bars(trades, bars, off)
        if not len(m):
            continue
        inside = ((m["entry_price"] >= m["low"]) & (m["entry_price"] <= m["high"])).sum() / len(m)
        if inside > best[1]:
            best = (off, float(inside))
    return best
