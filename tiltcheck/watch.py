"""Watch mode: nothing to type.

The NinjaTrader add-on (ninjatrader/TiltCheckFeed.cs) writes every fill to
Documents\\tilt-check\\fills.csv. This follows that file, rebuilds her positions
from the fills, and speaks up the moment a trade:

- breaks one of her own rules (rules.json: trades per day, daily loss limit,
  losses in a row, contract size, sessions she avoids), or
- lands in a part of her history that has been losing (a "red zone": a group
  well below her usual win rate, with a net loss, over enough trades), or
- is losing more than her usual losing trade while it's still open.

At every entry it also shows her usual winner and loser for that setup, as
prices. Those are her own medians, not a prediction: fitted brackets lost to
her own exits on trades they hadn't seen (see `exits`).

It never places, changes or cancels an order.
"""
from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from . import facts as F
from .exits import point_values, points
from .features import planned_row
from .sessions import pc_zone, session_of

DEFAULT_RULES = {
    "max_trades_per_day": None,
    "daily_loss_limit_usd": None,
    "pause_after_losses_in_a_row": None,
    "pause_minutes": 15,
    "max_contracts": None,
    "warn_adding_to_loser": True,    # adding to a position that's under water
    "size_alert_multiple": 3,        # an entry this many times her usual size
    "avoid_sessions": [],            # e.g. ["NY overnight"]
    "mute": [],                      # history groups she already knows about, e.g. ["first 5 trades"]
    "red_zone_gap": 0.08,            # a group this far below her usual win rate...
    "red_zone_min_trades": 15,       # ...over at least this many trades, with a net loss
}


def documents_dir() -> Path:
    """The real Documents folder (it can live inside OneDrive), the same one the add-on writes to."""
    if sys.platform == "win32":
        try:
            import ctypes
            buf = ctypes.create_unicode_buffer(260)
            ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf)     # CSIDL_PERSONAL
            if buf.value:
                return Path(buf.value)
        except Exception:
            pass
    return Path.home() / "Documents"


def _px(x: float) -> str:
    """31061.75 -> '31061.75', 30995.0 -> '30995' (ticks matter, trailing zeros don't)."""
    return f"{x:.2f}".rstrip("0").rstrip(".")


def load_rules(path: str | Path | None = None) -> dict:
    p = Path(path) if path else Path.home() / ".tilt-check" / "rules.json"
    rules = dict(DEFAULT_RULES)
    if p.exists():
        rules.update(json.loads(p.read_text(encoding="utf-8")))
    return rules


_TOAST = (
    "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null;"
    "$x = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent("
    "[Windows.UI.Notifications.ToastTemplateType]::ToastText02);"
    "$t = $x.GetElementsByTagName('text');"
    "$t.Item(0).AppendChild($x.CreateTextNode($env:TC_TITLE)) > $null;"
    "$t.Item(1).AppendChild($x.CreateTextNode($env:TC_TEXT)) > $null;"
    "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("
    "'{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe')"
    ".Show([Windows.UI.Notifications.ToastNotification]::new($x))"
)


def desktop_alert(title: str, text: str, urgent: bool = False) -> None:
    """Console line plus a Windows notification. Notifications don't take the keyboard focus,
    which matters while she's managing a trade. Urgent ones also beep."""
    print(f"\n[{title}]\n{text}\n", flush=True)
    if sys.platform != "win32":
        return
    import os
    import subprocess
    if urgent:
        import winsound
        winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
    env = dict(os.environ, TC_TITLE=title, TC_TEXT=text[:600])
    subprocess.Popen(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _TOAST], env=env,
                     creationflags=0x08000000)            # no console window


@dataclass
class Position:
    account: str
    contract: str
    instrument: str
    side: str                  # "long" or "short", fixed at entry
    qty: int                   # signed: + long, - short
    price: float               # average fill
    opened: pd.Timestamp       # PC clock
    peak_qty: int = 0
    realized: float = 0.0      # $ before commission, from partial exits
    warned_loss: bool = False


@dataclass
class Watcher:
    history: pd.DataFrame
    rules: dict
    alert: callable = desktop_alert
    open: dict = field(default_factory=dict)
    today: list = field(default_factory=list)

    def __post_init__(self):
        h = self.history
        self.pv = point_values(h)
        one = h[h.get("rows", 1) == 1].copy()
        one["pts"] = points(one)
        self.usual = {}
        for (inst, side), g in one.groupby(["instrument", "side"]):
            w, l = g[g["profit"] > 0]["pts"], g[g["profit"] <= 0]["pts"]
            if len(w) >= 5 and len(l) >= 5:
                self.usual[(inst, side)] = (float(w.median()), float(l.median()), len(g))
        self.fee = (h["commission"] / h["qty"]).groupby(h["instrument"]).median().to_dict()
        self.base = float((h["profit"] > 0).mean())

    # ---- state -------------------------------------------------------------------------------
    def _now_history(self, when: pd.Timestamp) -> pd.DataFrame:
        today = pd.DataFrame(self.today)
        h = pd.concat([self.history, today], ignore_index=True) if len(today) else self.history
        return h[h["exit_time"] < when].reset_index(drop=True)

    def _today_stats(self, when: pd.Timestamp):
        done = [t for t in self.today if t["exit_time"].date() == when.date()]
        streak = 0
        for t in sorted(done, key=lambda t: t["exit_time"], reverse=True):
            if t["profit"] > 0:
                break
            streak += 1
        return len(done), sum(t["profit"] for t in done), streak, (max(t["exit_time"] for t in done) if done else None)

    # ---- events ------------------------------------------------------------------------------
    def on_fill(self, when: pd.Timestamp, account: str, contract: str, signed_qty: int, price: float, quiet=False):
        key = (account, contract)
        pos = self.open.get(key)
        if pos is None:
            pos = Position(account, contract, contract.split()[0], "long" if signed_qty > 0 else "short",
                           signed_qty, price, when, abs(signed_qty))
            self.open[key] = pos
            if not quiet:
                self._entry(pos)
            return
        if np.sign(signed_qty) == np.sign(pos.qty):           # adding to it
            under = (price - pos.price) * np.sign(pos.qty)       # points the position is up (+) or down (-)
            before = pos.price
            n = abs(pos.qty) + abs(signed_qty)
            pos.price = (pos.price * abs(pos.qty) + price * abs(signed_qty)) / n
            pos.qty += signed_qty
            pos.peak_qty = max(pos.peak_qty, abs(pos.qty))
            if not quiet:
                self._added(pos, price, before, under, when)
            return
        closing = min(abs(signed_qty), abs(pos.qty))             # reducing, closing or reversing
        direction = 1 if pos.qty > 0 else -1
        pos.realized += (price - pos.price) * direction * closing * self.pv.get(pos.instrument, 1.0)
        pos.qty -= direction * closing
        if pos.qty == 0:
            del self.open[key]
            self._closed(pos, when, price, quiet)
            left = abs(signed_qty) - closing
            if left:
                self.on_fill(when, account, contract, int(np.sign(signed_qty)) * left, price, quiet)

    def on_snapshot(self, when: pd.Timestamp, account: str, unrealized: float):
        mine = [p for p in self.open.values() if p.account == account]
        if len(mine) != 1:
            return
        p = mine[0]
        u = self.usual.get((p.instrument, p.side))
        if not u or p.warned_loss:
            return
        usual_loss = abs(u[1]) * self.pv.get(p.instrument, 1.0) * abs(p.qty)
        if unrealized <= -usual_loss:
            p.warned_loss = True
            held = (when - p.opened).total_seconds() / 60
            self.alert("tilt-check: past your usual loss",
                       f"{'Long' if p.qty > 0 else 'Short'} {abs(p.qty)} {p.contract} is down ${-unrealized:,.2f} "
                       f"after {held:.0f} min.\nYour usual losing {p.instrument} trade: {u[1]:+.2f} points "
                       f"(${usual_loss:,.2f} at this size).\nNo order was touched.", urgent=True)

    # ---- what she sees -----------------------------------------------------------------------
    def _entry(self, pos: Position):
        side = "long" if pos.qty > 0 else "short"
        when = pos.opened
        h = self._now_history(when)
        row = planned_row(h, pos.instrument, side, abs(pos.qty), when).iloc[0]
        lines = []
        n_today, pnl_today, streak, last_exit = self._today_stats(when)
        r = self.rules
        if r["max_contracts"] and abs(pos.qty) > r["max_contracts"]:
            lines.append(f"RULE: {abs(pos.qty)} contracts, your max is {r['max_contracts']}.")
        if r["max_trades_per_day"] and n_today + 1 > r["max_trades_per_day"]:
            lines.append(f"RULE: trade #{n_today + 1} today, your max is {r['max_trades_per_day']}.")
        if r["daily_loss_limit_usd"] and pnl_today <= -r["daily_loss_limit_usd"]:
            lines.append(f"RULE: you're at ${pnl_today:,.2f} today, past your ${r['daily_loss_limit_usd']:,.0f} limit.")
        if r["pause_after_losses_in_a_row"] and streak >= r["pause_after_losses_in_a_row"] and last_exit is not None:
            mins = (when - last_exit).total_seconds() / 60
            if mins < r["pause_minutes"]:
                lines.append(f"RULE: {streak} losses in a row and {mins:.0f} min since the last one; "
                             f"your pause is {r['pause_minutes']} min.")
        usual_qty = float(h[h["instrument"] == pos.instrument]["qty"].median()) if len(h) else 0.0
        if usual_qty and abs(pos.qty) >= r["size_alert_multiple"] * usual_qty:
            lines.append(f"SIZE: {abs(pos.qty)} contracts, your usual {pos.instrument} size is {usual_qty:g}.")
        session = session_of(pd.Series([when])).iloc[0]
        if session in r["avoid_sessions"]:
            lines.append(f"RULE: {session} is on your avoid list.")
        for g in F.applicable(h, row):
            if any(m.lower() in g["group"].lower() for m in r["mute"]):
                continue
            if (g["trades"] >= r["red_zone_min_trades"] and g["pnl"] < 0
                    and g["win_rate"] <= self.base - r["red_zone_gap"]):
                lines.append(f"RED ZONE: {g['group']}: {g['trades']} trades, win rate {g['win_rate']:.1%} "
                             f"(usual {self.base:.0%}), net ${g['pnl']:,.2f}.")
        u = self.usual.get((pos.instrument, side))
        if u:
            d = 1 if side == "long" else -1
            lines.append(f"Your usual {pos.instrument} {side}: winners {u[0]:+.2f} pts (~{pos.price + d * u[0]:.2f}), "
                         f"losers {u[1]:+.2f} pts (~{pos.price + d * u[1]:.2f}). Medians of {u[2]} trades, "
                         f"not a prediction.")
        flagged = any(l.startswith(("RULE", "RED ZONE", "SIZE")) for l in lines)
        title = "tilt-check: check this one" if flagged else "tilt-check"
        self.alert(title, "\n".join([f"{side.title()} {abs(pos.qty)} {pos.contract} at {_px(pos.price)} ({session})."] + lines),
                   urgent=flagged)

    def _added(self, pos: Position, price: float, before: float, under: float, when: pd.Timestamp):
        r = self.rules
        lines = []
        if r["warn_adding_to_loser"] and under < 0:
            held = (when - pos.opened).total_seconds() / 60
            lines.append(f"Adding to a losing {pos.side}: average {_px(before)}, now {_px(price)} "
                         f"({under:+.2f} pts, {held:.0f} min in).")
        if r["max_contracts"] and abs(pos.qty) > r["max_contracts"]:
            lines.append(f"RULE: now {abs(pos.qty)} contracts, your max is {r['max_contracts']}.")
        if lines:
            self.alert("tilt-check: check this one", f"{pos.contract}, {abs(pos.qty)} contracts now.\n" + "\n".join(lines),
                       urgent=True)

    def _closed(self, pos: Position, when: pd.Timestamp, price: float, quiet: bool):
        fee = self.fee.get(pos.instrument, 0.0) * pos.peak_qty
        profit = pos.realized - fee
        self.today.append({"account": pos.account, "instrument": pos.instrument, "contract": pos.contract,
                           "side": pos.side, "qty": pos.peak_qty,
                           "entry_time": pos.opened, "exit_time": when, "entry_price": pos.price,
                           "exit_price": price, "profit": profit, "commission": fee, "rows": 1})
        if quiet:
            return
        n_today, pnl_today, streak, _ = self._today_stats(when)
        r = self.rules
        lines = []
        if r["daily_loss_limit_usd"] and pnl_today <= -r["daily_loss_limit_usd"]:
            lines.append(f"RULE: ${pnl_today:,.2f} today. That's your ${r['daily_loss_limit_usd']:,.0f} limit.")
        if r["pause_after_losses_in_a_row"] and streak >= r["pause_after_losses_in_a_row"]:
            lines.append(f"RULE: {streak} losses in a row. Your rule says pause {r['pause_minutes']} min.")
        if r["max_trades_per_day"] and n_today >= r["max_trades_per_day"]:
            lines.append(f"RULE: that was trade #{n_today}, your max for the day.")
        if lines:
            self.alert("tilt-check: rule", "\n".join(lines), urgent=True)


# ---- following the add-on's files -----------------------------------------------------------
def _pc(utc: str) -> pd.Timestamp:
    """'2026-10-02 04:05:00.123' (UTC) -> naive PC-clock time, like the history."""
    return pd.Timestamp(utc, tz="UTC").tz_convert(pc_zone()).tz_localize(None)


class _Tail:
    def __init__(self, path: Path):
        self.path, self.pos = path, 0

    def lines(self) -> list[str]:
        if not self.path.exists():
            return []
        with open(self.path, "rb") as f:
            f.seek(self.pos)
            chunk = f.read()
        end = chunk.rfind(b"\n") + 1               # only whole lines; a half-written one waits
        self.pos += end
        text = chunk[:end].decode("utf-8", errors="replace")
        return [l.strip() for l in text.splitlines() if l.strip() and not l.startswith("time_utc")]


def follow(w: Watcher, feed: Path, poll: float = 0.5, stop=None) -> None:
    """Catch up on today's fills without alerts, then alert on every new fill and snapshot."""
    fills, snaps = _Tail(feed / "fills.csv"), _Tail(feed / "snapshots.csv")
    today = pd.Timestamp.now().normalize()
    for l in fills.lines():
        t, acct, contract, q, px = l.split(",")[:5]
        if _pc(t) >= today:
            w.on_fill(_pc(t), acct, contract, int(q), float(px), quiet=True)
    snaps.lines()
    print(f"Watching {feed} (open positions: {len(w.open)}, closed today: {len(w.today)}). Ctrl+C to stop.",
          flush=True)
    while not (stop and stop()):
        for l in fills.lines():
            t, acct, contract, q, px = l.split(",")[:5]
            w.on_fill(_pc(t), acct, contract, int(q), float(px))
        for l in snaps.lines():
            t, acct, upl = l.split(",")[:3]
            w.on_snapshot(_pc(t), acct, float(upl))
        time.sleep(poll)


def fills_from_history(trades: pd.DataFrame, day: str) -> list[tuple]:
    """Her real trades on `day` turned back into fills (entry, then exit), for replaying a day."""
    d = trades[trades["entry_time"].dt.date == pd.Timestamp(day).date()]
    out = []
    for r in d.itertuples():
        q = r.qty if r.side == "long" else -r.qty
        out.append((r.entry_time, r.account, r.contract, q, r.entry_price))
        out.append((r.exit_time, r.account, r.contract, -q, r.exit_price))
    return sorted(out, key=lambda x: x[0])
