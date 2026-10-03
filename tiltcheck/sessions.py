"""New York session labels.

NinjaTrader writes entry times on the PC's clock, but futures traders think in
New York hours. On a Korean PC, "12:00-14:59" is easy to read as the New York
session; it's 11 p.m. to 2 a.m. in New York. So every time window the tool
prints is a New York session, with the PC-clock hours next to it.
"""
from __future__ import annotations

import os
from datetime import datetime

import pandas as pd

NY = "America/New_York"

# name, start, end in minutes after midnight New York time (end exclusive).
# Together they cover the whole day.
SESSIONS = [
    ("NY overnight", 23 * 60, 2 * 60),
    ("Europe open", 2 * 60, 5 * 60),
    ("before the NY open", 5 * 60, 9 * 60 + 30),
    ("NY open", 9 * 60 + 30, 12 * 60),
    ("NY afternoon", 12 * 60, 16 * 60),
    ("after the NY close", 16 * 60, 23 * 60),
]
REGULAR = ("NY regular session", 9 * 60 + 30, 16 * 60)


def pc_zone():
    """Zone of the times in the export: TILTCHECK_TZ (e.g. Asia/Seoul), else this PC's current offset."""
    return os.environ.get("TILTCHECK_TZ") or datetime.now().astimezone().tzinfo


def ny_minutes(times: pd.Series) -> pd.Series:
    """Minutes after midnight in New York for PC-clock timestamps."""
    ny = times.dt.tz_localize(pc_zone(), ambiguous="NaT", nonexistent="shift_forward").dt.tz_convert(NY)
    return ny.dt.hour * 60 + ny.dt.minute


def _inside(m, start: int, end: int):
    return (m >= start) & (m < end) if start < end else (m >= start) | (m < end)


def session_of(times: pd.Series) -> pd.Series:
    m = ny_minutes(times)
    out = pd.Series("", index=times.index)
    for name, start, end in SESSIONS:
        out[_inside(m, start, end)] = name
    return out


def in_window(times: pd.Series, start: int, end: int) -> pd.Series:
    return _inside(ny_minutes(times), start, end)


def label(name: str, start: int, end: int, day=None) -> str:
    """'NY overnight 23:00-02:00 New York (12:00-15:00 on this PC)'. PC hours are for `day`,
    since daylight saving moves them."""
    day = pd.Timestamp(day or datetime.now()).normalize()
    def hhmm(m):
        return f"{m // 60:02d}:{m % 60:02d}"
    def pc(m):
        t = (day.tz_localize(NY) + pd.Timedelta(minutes=m)).tz_convert(pc_zone())
        return t.strftime("%H:%M")
    return f"{name} {hhmm(start)}-{hhmm(end)} New York ({pc(start)}-{pc(end)} on this PC)"
