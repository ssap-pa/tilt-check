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
from .exits import POINT_VALUE, point_values, points
from .features import planned_row
from .sessions import pc_zone, session_of

DEFAULT_RULES = {
    "max_trades_per_day": None,
    "daily_loss_limit_usd": None,
    "pause_after_losses_in_a_row": None,
    "pause_minutes": 15,
    "max_contracts": None,
    "max_contracts_by_class": {"micro": None, "mini": None},   # prop rules, e.g. 20 micros or 2 minis
    "pause_after_loss_minutes": None,  # no new entry this soon after a losing exit
    "daily_loss_limit_pct": None,    # % of the account's cash value at the start of the day
    "risk_per_trade_pct": None,      # stop distance x size, as % of the account (needs the add-on's orders.csv)
    # Prop accounts: limits as % of the drawdown allowance instead of the balance (e.g. $2,500 on a 50K)
    "drawdown_usd": None,
    "daily_loss_limit_pct_of_drawdown": None,
    "risk_per_trade_pct_of_drawdown": None,
    "a_plus_checklist": [],          # shown on entries after the daily limit: only these setups
    "stop_grace_seconds": 20,        # how long after an entry the stop has to show up
    "stop_check_from_trade": 1,      # e.g. 6: her first 5 entries are test trades without stops
    "warn_adding_to_loser": True,    # adding to a position that's under water...
    "adding_to_loser_min_pts": None,  # ...by more than this many points (default: her usual loss for that setup)
    "size_alert_multiple": 3,        # an entry this many times her usual size
    "weak_sessions": [],             # heads-up with her own numbers for that session, e.g. ["NY overnight"]
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


MICRO = {"MNQ", "MES", "MYM", "M2K", "MGC", "MCL", "SIL", "MHG", "MBT", "MET", "M6E", "M6A", "M6B", "MJY", "MNG"}


def contract_class(instrument: str) -> str:
    return "micro" if instrument in MICRO else "mini"


ACTIVE_ORDER = {"Accepted", "Working", "Submitted", "ChangePending", "ChangeSubmitted", "TriggerPending"}


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
    cash: dict = field(default_factory=dict)          # account -> latest cash value
    day_cash: dict = field(default_factory=dict)      # (account, date) -> first cash value seen that day
    orders: dict = field(default_factory=dict)        # order id -> latest state
    pending: list = field(default_factory=list)       # (due time, position key) stop checks
    orders_seen: bool = False

    def __post_init__(self):
        h = self.history
        self.pv = {**POINT_VALUE, **point_values(h)}
        # what happened when she re-entered soon after a losing exit, for the pause rule's message
        self.pause_stats = None
        n = self.rules.get("pause_after_loss_minutes")
        if n and len(h):
            from .features import add_features
            f = add_features(h)
            after = f[f["prev_win"] == 0]
            soon, later = after[after["minutes_since_exit"] < n], after[after["minutes_since_exit"] >= n]
            self.pause_stats = (len(soon), float(soon["profit"].sum()), len(later), float(later["profit"].sum()))
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

    def _last_closed(self, when: pd.Timestamp):
        done = [t for t in self.today if t["exit_time"] <= when]
        return max(done, key=lambda t: t["exit_time"]) if done else None

    def _day_limit(self, account: str, when: pd.Timestamp):
        """Daily loss limit in $: % of the drawdown allowance if set, else % of the day's first cash value."""
        r = self.rules
        if r.get("daily_loss_limit_pct_of_drawdown") and r.get("drawdown_usd"):
            return r["daily_loss_limit_pct_of_drawdown"] / 100 * r["drawdown_usd"]
        pct = r.get("daily_loss_limit_pct")
        start = self.day_cash.get((account, when.date()))
        return pct / 100 * start if pct and start else None

    def _risk_limit(self, account: str):
        """Max $ at risk per entry: % of the drawdown allowance if set, else % of the cash value."""
        r = self.rules
        if r.get("risk_per_trade_pct_of_drawdown") and r.get("drawdown_usd"):
            return r["risk_per_trade_pct_of_drawdown"] / 100 * r["drawdown_usd"]
        cash = self.cash.get(account)
        return r["risk_per_trade_pct"] / 100 * cash if r.get("risk_per_trade_pct") and cash else None

    def _limit_label(self, kind: str) -> str:
        r = self.rules
        if r.get(f"{kind}_pct_of_drawdown") and r.get("drawdown_usd"):
            return f"{r[f'{kind}_pct_of_drawdown']:g}% of your ${r['drawdown_usd']:,.0f} drawdown"
        return f"{r.get(f'{kind}_pct') or 0:g}% of the account"

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
                r = self.rules
                nth = self._today_stats(when)[0] + 1 + sum(1 for p in self.open.values() if p is not pos)
                if ((r.get("risk_per_trade_pct") or r.get("risk_per_trade_pct_of_drawdown")) and self.orders_seen
                        and nth >= r.get("stop_check_from_trade", 1)):
                    self.pending.append((when + pd.Timedelta(seconds=r["stop_grace_seconds"]), key))
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

    def on_order(self, when: pd.Timestamp, account: str, contract: str, order_id: str, action: str, otype: str,
                 state: str, qty: int, stop: float):
        self.orders_seen = True
        self.orders[order_id] = {"account": account, "contract": contract, "action": action, "type": otype,
                                 "state": state, "qty": qty, "stop": stop}

    def tick(self, now: pd.Timestamp):
        """Run the stop checks that are due."""
        due = [p for p in self.pending if p[0] <= now]
        self.pending = [p for p in self.pending if p[0] > now]
        for _, key in due:
            self._risk_check(key)

    def _risk_check(self, key):
        pos = self.open.get(key)
        if pos is None:
            return
        protective = "Sell" if pos.qty > 0 else "Buy"        # Sell stops protect a long; Buy/BuyToCover a short
        stops = [o for o in self.orders.values()
                 if (o["account"], o["contract"]) == key and o["state"] in ACTIVE_ORDER
                 and o["type"] in ("StopMarket", "StopLimit") and o["action"].startswith(protective)]
        cap = self._risk_limit(pos.account)
        rule = f"risk {self._limit_label('risk_per_trade')} per entry" + (f" (${cap:,.2f})" if cap else "")
        if not stops:
            self.alert("tilt-check: no stop", f"{pos.side.title()} {abs(pos.qty)} {pos.contract} at {_px(pos.price)} has no "
                       f"working stop after {self.rules['stop_grace_seconds']} s.\nYour rule: {rule}.", urgent=True)
            return
        risk = sum(abs(pos.price - o["stop"]) * o["qty"] * self.pv.get(pos.instrument, 1.0) for o in stops)
        if cap and risk > cap:
            self.alert("tilt-check: check this one",
                       f"{pos.side.title()} {abs(pos.qty)} {pos.contract}: the stop risks ${risk:,.2f}.\n"
                       f"Your rule: {rule}.", urgent=True)

    def on_snapshot(self, when: pd.Timestamp, account: str, unrealized: float, cash: float | None = None):
        if cash:
            self.cash[account] = cash
            self.day_cash.setdefault((account, when.date()), cash)
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
        cls = contract_class(pos.instrument)
        cap = (r.get("max_contracts_by_class") or {}).get(cls)
        if cap and abs(pos.qty) > cap:
            lines.append(f"RULE: {abs(pos.qty)} {cls} contracts, your max is {cap}.")
        last = self._last_closed(when)
        wait = r.get("pause_after_loss_minutes")
        if wait and last is not None and last["profit"] <= 0:
            mins = (when - last["exit_time"]).total_seconds() / 60
            if mins < wait:
                why = ""
                if self.pause_stats:
                    a, an, b, bn = self.pause_stats
                    why = (f" Your re-entries within {wait:g} min of a loss: {a} trades, ${an:,.2f}. "
                           f"After waiting: {b} trades, ${bn:,.2f}.")
                lines.append(f"RULE: {mins:.0f} min after a losing exit, your pause is {wait:g} min.{why}")
        limit = self._day_limit(pos.account, when)
        if limit and pnl_today <= -limit:
            lines.append(f"RULE: ${pnl_today:,.2f} today, past your daily limit "
                         f"({self._limit_label('daily_loss_limit')}, ${limit:,.2f}). Done for today, unless this is:")
            lines += [f"  - {c}" for c in r.get("a_plus_checklist") or []]
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
        if session in (r.get("weak_sessions") or []):
            g = h[session_of(h["entry_time"]) == session] if len(h) else h
            lines.append(f"WEAK HOURS: {session}: {len(g)} trades, win rate {(g['profit'] > 0).mean():.1%} "
                         f"(usual {self.base:.0%}), net ${g['profit'].sum():,.2f}." if len(g) else
                         f"WEAK HOURS: {session} is on your list.")
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
        flagged = any(l.startswith(("RULE", "RED ZONE", "SIZE", "WEAK HOURS")) for l in lines)
        title = "tilt-check: check this one" if flagged else "tilt-check"
        self.alert(title, "\n".join([f"{side.title()} {abs(pos.qty)} {pos.contract} at {_px(pos.price)} ({session})."] + lines),
                   urgent=flagged)

    def _added(self, pos: Position, price: float, before: float, under: float, when: pd.Timestamp):
        r = self.rules
        lines = []
        # Scaling in inside a planned zone is fine; adding after price has gone further against her
        # than her usual losing trade is averaging down.
        usual = self.usual.get((pos.instrument, pos.side))
        floor = r.get("adding_to_loser_min_pts")
        if floor is None:
            floor = abs(usual[1]) if usual else 0.0
        if r["warn_adding_to_loser"] and under < -floor:
            held = (when - pos.opened).total_seconds() / 60
            past = f", past your usual {floor:.2f}-pt loss" if floor else ""
            lines.append(f"Adding to a losing {pos.side}: average {_px(before)}, now {_px(price)} "
                         f"({under:+.2f} pts{past}, {held:.0f} min in).")
        if r["max_contracts"] and abs(pos.qty) > r["max_contracts"]:
            lines.append(f"RULE: now {abs(pos.qty)} contracts, your max is {r['max_contracts']}.")
        cls = contract_class(pos.instrument)
        cap = (r.get("max_contracts_by_class") or {}).get(cls)
        if cap and abs(pos.qty) > cap:
            lines.append(f"RULE: now {abs(pos.qty)} {cls} contracts, your max is {cap}.")
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
        limit = self._day_limit(pos.account, when)
        if limit and pnl_today <= -limit:
            lines.append(f"RULE: ${pnl_today:,.2f} today. That's your daily limit "
                         f"({self._limit_label('daily_loss_limit')}, ${limit:,.2f}). Done for today.")
        if r["pause_after_losses_in_a_row"] and streak >= r["pause_after_losses_in_a_row"]:
            lines.append(f"RULE: {streak} losses in a row. Your rule says pause {r['pause_minutes']} min.")
        if r["max_trades_per_day"] and n_today >= r["max_trades_per_day"]:
            lines.append(f"RULE: that was trade #{n_today}, your max for the day.")
        wait = r.get("pause_after_loss_minutes")
        if wait and profit <= 0:
            lines.append(f"Loss closed (${profit:,.2f}). Your pause: next entry after "
                         f"{(when + pd.Timedelta(minutes=wait)):%H:%M}.")
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


def _order(w: Watcher, line: str) -> None:
    t, acct, contract, oid, action, otype, state, qty, stop = line.split(",")[:9]
    w.on_order(_pc(t), acct, contract, oid, action, otype, state, int(float(qty)), float(stop))


def _snapshot(w: Watcher, line: str) -> None:
    parts = line.split(",")
    cash = float(parts[4]) if len(parts) > 4 and parts[4] else None
    w.on_snapshot(_pc(parts[0]), parts[1], float(parts[2]), cash)


def follow(w: Watcher, feed: Path, poll: float = 0.5, stop=None) -> None:
    """Catch up on today's fills, orders and snapshots without alerts, then alert on every new one."""
    fills, orders, snaps = _Tail(feed / "fills.csv"), _Tail(feed / "orders.csv"), _Tail(feed / "snapshots.csv")
    today = pd.Timestamp.now().normalize()
    for l in fills.lines():
        t, acct, contract, q, px = l.split(",")[:5]
        if _pc(t) >= today:
            w.on_fill(_pc(t), acct, contract, int(q), float(px), quiet=True)
    for l in orders.lines():
        _order(w, l)
    for l in snaps.lines():
        if _pc(l.split(",")[0]) >= today:
            _snapshot(w, l)
    print(f"Watching {feed} (open positions: {len(w.open)}, closed today: {len(w.today)}). Ctrl+C to stop.",
          flush=True)
    while not (stop and stop()):
        for l in orders.lines():
            _order(w, l)
        for l in fills.lines():
            t, acct, contract, q, px = l.split(",")[:5]
            w.on_fill(_pc(t), acct, contract, int(q), float(px))
        for l in snaps.lines():
            _snapshot(w, l)
        w.tick(pd.Timestamp.now())
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
