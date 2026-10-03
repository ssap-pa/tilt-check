"""The learning loop: every check is logged with what you decided and why.
When you import the next export, each decision is matched to the trade that
followed (or to no trade, if you skipped), so you can see whether listening
to your own numbers paid off. New trades also go straight into the history
TabPFN reads on the next check."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

HOME = Path.home() / ".tilt-check"
DB = HOME / "journal.db"
HISTORY = HOME / "history.csv"


def _db() -> sqlite3.Connection:
    HOME.mkdir(exist_ok=True)
    con = sqlite3.connect(DB)
    con.execute("""create table if not exists decisions(
        id integer primary key, at text, plan text, instrument text, side text, qty integer,
        p_win real, base_rate real, decision text, reason text,
        matched_entry text, matched_profit real)""")
    return con


def log_decision(plan: str, instrument: str, side: str, qty: int, p_win: float, base_rate: float,
                 decision: str, reason: str = "") -> int:
    con = _db()
    cur = con.execute("insert into decisions(at, plan, instrument, side, qty, p_win, base_rate, decision, reason) "
                      "values (?,?,?,?,?,?,?,?,?)",
                      (datetime.now().isoformat(timespec="seconds"), plan, instrument, side, qty, p_win, base_rate,
                       decision, reason))
    con.commit()
    return int(cur.lastrowid)


def merge_history(new: pd.DataFrame) -> pd.DataFrame:
    """Add newly exported trades to the stored history (no duplicates)."""
    HOME.mkdir(exist_ok=True)
    key = ["account", "instrument", "side", "qty", "entry_time", "exit_time"]
    if HISTORY.exists():
        old = pd.read_csv(HISTORY, parse_dates=["entry_time", "exit_time"])
        allt = pd.concat([old, new], ignore_index=True).drop_duplicates(subset=key, keep="last")
    else:
        allt = new
    allt = allt.sort_values("entry_time").reset_index(drop=True)
    allt.to_csv(HISTORY, index=False)
    return allt


def load_history() -> pd.DataFrame | None:
    if not HISTORY.exists():
        return None
    return pd.read_csv(HISTORY, parse_dates=["entry_time", "exit_time"])


def match_outcomes(history: pd.DataFrame, window_min: int = 15) -> pd.DataFrame:
    """Link each logged decision to the first same-instrument, same-side trade
    entered within `window_min` minutes after it."""
    con = _db()
    d = pd.read_sql("select * from decisions", con, parse_dates=["at"])
    for i, row in d.iterrows():
        lo, hi = row["at"], row["at"] + timedelta(minutes=window_min)
        m = history[(history["instrument"] == row["instrument"]) & (history["side"] == row["side"]) &
                    (history["entry_time"] >= lo) & (history["entry_time"] <= hi)]
        if len(m):
            first = m.iloc[0]
            con.execute("update decisions set matched_entry=?, matched_profit=? where id=?",
                        (str(first["entry_time"]), float(first["profit"]), int(row["id"])))
    con.commit()
    return pd.read_sql("select * from decisions order by at", con)


def scoreboard(decisions: pd.DataFrame) -> str:
    if decisions.empty:
        return "No checks logged yet."
    lines = []
    for dec, g in decisions.groupby("decision"):
        taken = g[g["matched_profit"].notna()]
        lines.append(f"{dec}: {len(g)} checks, {len(taken)} followed by a trade, "
                     f"net ${taken['matched_profit'].sum():,.2f}")
    return "\n".join(lines)
