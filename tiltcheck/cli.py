"""tilt-check: a pre-trade check that learns from your own NinjaTrader history.

  python -m tiltcheck report --csv trades.csv [--account 0014] [--lang ko]
  python -m tiltcheck check "short 2 MNQ, just got stopped out" [--csv trades.csv] [--lang ko]
  python -m tiltcheck learn --csv new_export.csv

It never places orders. It reads your history, tells you what your own numbers
say about the trade you are about to take, and writes down what you decided.
"""
from __future__ import annotations

import argparse
import sys

import pandas as pd

from . import facts as F
from . import journal
from .features import describe_row, planned_row, similar_trades
from .model import reliability, walk_forward, win_probability
from .ninjatrader import load_trades


def _history(args) -> pd.DataFrame:
    if args.csv:
        h = load_trades(args.csv, args.account)
        return journal.merge_history(h) if getattr(args, "save", False) else h
    h = journal.load_history()
    if h is None:
        sys.exit("No history yet. Pass --csv with a NinjaTrader Trades export, or run `learn` first.")
    return h


def cmd_report(args):
    t = _history(args)
    f = F.facts(t)
    text = F.as_text(f)
    print(text, "\n")
    ev, _ = walk_forward(t)
    print(f"Walk-forward test: trained on the first {ev.n_train} trades, scored the next {ev.n_test} it never saw.")
    print(f"  AUC TabPFN {ev.auc_tabpfn:.2f} vs logistic regression {ev.auc_logreg:.2f} (0.50 = coin flip)")
    print(f"  Brier {ev.brier_tabpfn:.3f} vs {ev.brier_base:.3f} for always guessing the base rate {ev.base_rate:.0%}")
    print(f"  Lowest-scored fifth: {ev.flagged} trades, win rate {ev.flagged_win_rate:.0%}, net ${ev.flagged_pnl:,.2f}")
    print(f"  Everything else:     {ev.n_test - ev.flagged} trades, win rate {ev.rest_win_rate:.0%}, "
          f"net ${ev.rest_pnl:,.2f}\n")
    if not args.no_llm:
        from .gemma import coach_note
        print(coach_note(text, args.lang))


def cmd_check(args):
    from .gemma import explain_check, parse_plan
    t = _history(args)
    plan = parse_plan(args.plan, sorted(t["instrument"].unique())) if not args.instrument else {
        "instrument": args.instrument, "side": args.side, "qty": args.qty}
    now = pd.Timestamp(args.at) if args.at else pd.Timestamp.now().floor("s")
    # Only trades that had closed by `now` exist at that moment (matters when replaying with --at).
    t = t[t["exit_time"] < now].reset_index(drop=True)
    row = planned_row(t, plan["instrument"], plan["side"], plan["qty"], now)
    p = win_probability(t, row)
    auc = reliability(t)
    base = float((t["profit"] > 0).mean())
    sim = similar_trades(t, row)
    sim_text = "\n".join(f"- {r.entry_time:%m-%d %H:%M} {r.side} {r.qty} {r.instrument}: "
                         f"{'won' if r.profit > 0 else 'lost'} ${r.profit:,.2f} "
                         f"(trade #{int(r.trades_today) + 1} that day)" for r in sim.itertuples())
    hits = F.applicable(t, row.iloc[0])
    hits_text = "\n".join(f"- {h['group']}: {h['trades']} trades, win rate {h['win_rate']:.1%}, net ${h['pnl']:,.2f}"
                          for h in hits)
    desc = describe_row(row.iloc[0])
    print(f"Plan: {desc}\n")
    print("What your own history says about trades like this:\n" + hits_text + "\n")
    if auc < 0.55:
        print(f"Model check: on trades it had not seen, TabPFN scored AUC {auc:.2f} (0.50 = coin flip). "
              f"Your history can't tell winners from losers yet, so the {p:.0%} below is shown for the log only.")
    else:
        print(f"Model check: AUC {auc:.2f} on unseen trades.")
    print(f"TabPFN chance this ends green: {p:.0%} (your usual: {base:.0%})")
    print("Closest past trades:\n" + sim_text + "\n")
    note = explain_check(desc, p, base, sim_text, hits_text +
                         (f"\n(Model reliability on unseen trades: AUC {auc:.2f}; below 0.55 means the % is noise.)"),
                         args.lang)
    print(note, "\n")
    if args.decision:
        decision, reason = args.decision, args.reason or ""
    else:
        decision = input("take / skip / wait? ").strip() or "skip"
        reason = input("why (one line)? ").strip()
    journal.log_decision(args.plan, plan["instrument"], plan["side"], plan["qty"], p, base, decision, reason)
    print("Logged. It will be matched to what happened next when you import your next export.")


def cmd_learn(args):
    h = journal.merge_history(load_trades(args.csv, args.account))
    d = journal.match_outcomes(h)
    print(f"History now has {len(h)} trades. The next check reads all of them.")
    print(journal.scoreboard(d))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="tiltcheck")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("report", "check", "learn"):
        p = sub.add_parser(name)
        p.add_argument("--csv")
        p.add_argument("--account", help="keep accounts ending with this (e.g. 0014)")
        p.add_argument("--lang", default="en", choices=["en", "ko"])
        if name == "report":
            p.add_argument("--no-llm", action="store_true")
        if name == "check":
            p.add_argument("plan", help='e.g. "short 2 MNQ right after a stop"')
            p.add_argument("--instrument"); p.add_argument("--side", default="long"); p.add_argument("--qty", type=int, default=1)
            p.add_argument("--at", help="pretend it is this time (for demos), e.g. 2026-10-02 14:05")
            p.add_argument("--decision", choices=["take", "skip", "wait"]); p.add_argument("--reason")
    a = ap.parse_args(argv)
    {"report": cmd_report, "check": cmd_check, "learn": cmd_learn}[a.cmd](a)


if __name__ == "__main__":
    main()
