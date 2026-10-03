"""Plain, checkable facts about a trade history. Gemma only rephrases these;
every number it is allowed to say comes from here."""
from __future__ import annotations

import pandas as pd

from .features import add_features
from .sessions import REGULAR, SESSIONS, in_window, label


def _row(name: str, g: pd.DataFrame, overall_wr: float) -> dict:
    n = len(g)
    wr = float((g["profit"] > 0).mean()) if n else float("nan")
    return {"group": name, "trades": n, "win_rate": round(wr, 3), "pnl": round(float(g["profit"].sum()), 2),
            "avg_pnl": round(float(g["profit"].mean()), 2) if n else 0.0,
            "lift": round((wr - overall_wr) * (n ** 0.5), 3) if n else 0.0}


def facts(trades: pd.DataFrame) -> dict:
    t = add_features(trades)
    wins, losses = t[t["profit"] > 0], t[t["profit"] <= 0]
    wr = float(t["win"].mean())
    overall = {
        "trades": len(t), "days": int(t["day"].nunique()), "win_rate": round(wr, 3),
        "net_pnl": round(float(t["profit"].sum()), 2),
        "avg_win": round(float(wins["profit"].mean()), 2) if len(wins) else 0.0,
        "avg_loss": round(float(losses["profit"].mean()), 2) if len(losses) else 0.0,
        "largest_loss": round(float(t["profit"].min()), 2),
        "commission": round(float(t["commission"].sum()), 2) if "commission" in t else 0.0,
    }
    groups = [
        ("right after a winning trade", t[t["prev_win"] == 1]),
        ("right after a losing trade", t[t["prev_win"] == 0]),
        ("after 2+ losses in a row", t[t["losses_in_a_row"] >= 2]),
        ("first 5 trades of the day", t[t["trades_today"] < 5]),
        ("6th trade of the day or later", t[t["trades_today"] >= 5]),
        ("while already down for the day", t[t["pnl_today"] < 0]),
        ("while already up for the day", t[t["pnl_today"] > 0]),
        ("bigger size than usual", t[t["size_vs_usual"] > 1.0]),
        ("within 2 minutes of the last exit", t[t["minutes_since_exit"] <= 2]),
        ("long", t[t["side"] == "long"]),
        ("short", t[t["side"] == "short"]),
    ]
    for inst, g in t.groupby("instrument"):
        if len(g) >= 10:
            groups.append((f"{inst}", g))
    last_day = t["entry_time"].max()
    for name, start, end in [REGULAR] + SESSIONS:
        g = t[in_window(t["entry_time"], start, end)]
        if len(g) >= 10:
            groups.append((label(name, start, end, last_day), g))
    rows = [_row(name, g, wr) for name, g in groups if len(g)]
    rows.sort(key=lambda r: abs(r["lift"]), reverse=True)
    return {"overall": overall, "groups": rows}


def applicable(trades: pd.DataFrame, row: pd.Series) -> list[dict]:
    """The groups from history that the planned trade falls into, with their numbers."""
    t = add_features(trades)
    wr = float(t["win"].mean())
    when = pd.Series([row["entry_time"]])
    sessions = [(n, a, b) for n, a, b in SESSIONS if in_window(when, a, b).iloc[0]]
    if in_window(when, REGULAR[1], REGULAR[2]).iloc[0]:
        sessions.insert(0, REGULAR)
    checks = [
        (f"trade #{int(row['trades_today']) + 1} of the day (your first 5 trades each day)",
         row["trades_today"] < 5, t[t["trades_today"] < 5]),
        ("6th trade of the day or later", row["trades_today"] >= 5, t[t["trades_today"] >= 5]),
        ("right after a losing trade", row["prev_win"] == 0, t[t["prev_win"] == 0]),
        ("after 2+ losses in a row", row["losses_in_a_row"] >= 2, t[t["losses_in_a_row"] >= 2]),
        ("while already down for the day", row["pnl_today"] < 0, t[t["pnl_today"] < 0]),
        ("bigger size than usual", row["size_vs_usual"] > 1.0, t[t["size_vs_usual"] > 1.0]),
        *[(label(n, a, b, row["entry_time"]), True, t[in_window(t["entry_time"], a, b)]) for n, a, b in sessions],
        (f"{row['instrument']} {row['side']}", True,
         t[(t["instrument"] == row["instrument"]) & (t["side"] == row["side"])]),
    ]
    out = []
    for name, applies, g in checks:
        if applies and len(g) >= 5:
            out.append(_row(name, g, wr))
    return out


def as_text(f: dict, top: int = 8) -> str:
    o = f["overall"]
    lines = [f"Overall: {o['trades']} trades over {o['days']} days, win rate {o['win_rate']:.1%}, "
             f"net ${o['net_pnl']:,.2f}, average win ${o['avg_win']:.2f}, average loss ${o['avg_loss']:.2f}, "
             f"largest loss ${o['largest_loss']:.2f}."]
    for r in f["groups"][:top]:
        lines.append(f"- {r['group']}: {r['trades']} trades, win rate {r['win_rate']:.1%}, net ${r['pnl']:,.2f}")
    return "\n".join(lines)
