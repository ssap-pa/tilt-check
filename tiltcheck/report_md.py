"""A written report from one trade history, as Markdown.

    python -m tiltcheck audit-report --csv trades.csv [--bars ...] [--rules rules.json] [--out report.md]

Every number is computed here from the fills; the prose around them is fixed. It describes
what the trader did. It does not predict, and it says so.
"""
from __future__ import annotations

import pandas as pd

from .exits import TICK, how_she_exits, replay, excursions, usual_move
from .features import add_features
from .model import walk_forward
from .sessions import REGULAR, SESSIONS, in_window, label


def _money(x: float) -> str:
    return f"{'-' if x < 0 else '+'}${abs(x):,.2f}"


def _grp(g: pd.DataFrame) -> tuple[int, float, float]:
    n = len(g)
    return n, (float((g["profit"] > 0).mean()) if n else float("nan")), float(g["profit"].sum())


def _row(name: str, g: pd.DataFrame) -> str:
    n, wr, net = _grp(g)
    return f"| {name} | {n} | {wr:.1%} | **{_money(net)}** |" if n else f"| {name} | 0 | | |"


def _sessions(t: pd.DataFrame) -> list[str]:
    last = t["entry_time"].max()
    out = ["| New York session (your local hours) | Trades | Win rate | Net |", "|---|---|---|---|"]
    for name, a, b in [REGULAR] + SESSIONS:
        g = t[in_window(t["entry_time"], a, b)]
        if len(g) >= 5:
            out.append(_row(label(name, a, b, last), g))
    return out


def _first_trades(t: pd.DataFrame, n: int = 5) -> list[str]:
    return ["| | Trades | Win rate | Net |", "|---|---|---|---|",
            _row(f"Entries 1-{n} each day", t[t["trades_today"] < n]),
            _row(f"Entry {n + 1} onward", t[t["trades_today"] >= n])]


def _after_loss(t: pd.DataFrame, pause: int) -> tuple[list[str], float, float]:
    after = t[t["prev_win"] == 0]
    n, wr, net = _grp(after)
    quick, slow = after[after["minutes_since_exit"] < pause], after[after["minutes_since_exit"] >= pause]
    qn, _, qnet = _grp(quick)
    sn, _, snet = _grp(slow)
    overall = float((t["profit"] > 0).mean())
    lines = [f"**Does a loss make the next trade worse?** Right after a losing exit you won {wr:.1%} "
             f"({n} trades, {_money(net)}); overall you win {overall:.1%}. "
             + ("No real difference." if abs(wr - overall) < 0.05 else "That gap is worth a look."),
             "",
             f"**Does re-entering quickly after a loss cost money?**", "",
             "| Next entry after a losing exit | Trades | Net |", "|---|---|---|",
             f"| Within {pause} minutes | {qn} | **{_money(qnet)}** |",
             f"| After {pause} minutes or more | {sn} | **{_money(snet)}** |"]
    return lines, qnet, snet


def _size(t: pd.DataFrame) -> list[str]:
    big = t[t["size_vs_usual"] >= 3]
    usual = {k: int(v) for k, v in t.groupby("instrument")["qty"].median().items()}
    lines = ["Your usual size: " + ", ".join(f"{k} {v}" for k, v in usual.items()) + "."]
    if len(big):
        n, wr, net = _grp(big)
        lines.append(f"Entries at 3x your usual size or more: {n} trades, win rate {wr:.1%}, net {_money(net)}. "
                     "On a prop account the size rule is the one that ends the account, so each one is listed:")
        lines.append("")
        lines.append("| When | Instrument | Side | Size | Result |")
        lines.append("|---|---|---|---|---|")
        for r in big.sort_values("entry_time").itertuples():
            lines.append(f"| {r.entry_time:%Y-%m-%d %H:%M} | {r.instrument} | {r.side} | {int(r.qty)} | {_money(r.profit)} |")
    else:
        lines.append("No entry was 3x your usual size or more.")
    return lines


def _exits(t: pd.DataFrame, bars, offset) -> list[str]:
    lines = ["Winners and losers per instrument, in points per contract and minutes held (trades closed in one piece):", "",
             "| Instrument | Result | Trades | Median points | Median minutes |", "|---|---|---|---|---|"]
    for (inst, res), r in how_she_exits(t).iterrows():
        lines.append(f"| {inst} | {res} | {int(r['trades'])} | {r['median_pts']:+.2f} | {r['median_minutes']:.1f} |")
    if bars is None:
        lines += ["", "The bracket replay needs 1-minute bars. Send them and this section gets the replay."]
        return lines
    rs = {ties: replay(t, bars, offset, ties=ties) for ties in ("stop", "target")}
    r = rs["stop"]
    if r.covered < 20:
        lines += ["", f"Only {r.covered} entries had matching bars, too few to replay."]
        return lines
    mt, ms = r.best
    sizes = ", ".join(f"{k} +{a:g}/-{b:g}" for k, (a, b) in r.sizes.items())
    lines += ["", f"I replayed {r.covered} of your entries on 1-minute bars with fixed take-profit/stop brackets, "
              f"picked the best bracket on your older {r.train} trades, and scored it on your newer {r.test} trades it had never seen.", "",
              f"- Best bracket in-sample: target {mt:g}x and stop {ms:g}x your usual move ({sizes} points). "
              f"On those older trades it made {_money(float(r.in_sample.loc[mt, ms]))} to your {_money(r.actual_train)}.",
              f"- Out of sample: that bracket **{_money(r.bracket_test)}**. **Your own exits {_money(r.actual_test)}** on the same trades. "
              f"Counting bars that touched both levels as the target instead gives {_money(rs['target'].bracket_test)}."]
    ex = excursions(t, bars, offset)
    if len(ex):
        for inst, g in t.loc[ex.index].groupby("instrument"):
            if len(g) >= 20:
                e = ex.loc[g.index]
                lines.append(f"- {inst}: in the 30 minutes after an entry, price typically went {e['mfe'].median():g} points "
                             f"your way and {e['mae'].median():g} against (medians, {len(g)} entries).")
    from .exits import bracket_vs_exits_share
    share = bracket_vs_exits_share(r.test_diffs, r.test_days)
    if share == share:  # not NaN
        lines.append(f"- Resampling the newer trades by day 1,000 times, the bracket came out ahead of your exits in "
                     f"{share:.0%} of resamples (50% would be a coin flip).")
    verdict = ("Your exits beat the fitted bracket out of sample, so this report does not hand you a take-profit number."
               if r.actual_test >= r.bracket_test else
               "The fitted bracket beat your exits out of sample. One month is a small test; worth re-running next month before changing anything.")
    if share == share and 0.35 < share < 0.65:
        verdict += " The resample says the difference is inside the noise for now."
    lines += ["", verdict]
    return lines


def _rules(t: pd.DataFrame, bars, offset, vwap_sd: float) -> list[str]:
    from .bars import inside
    from .indicators import at_entries
    from .principles import audit, flags, vwap_bands, winners_in_ticks, trades_per_day
    lines = []
    if bars is not None:
        ok = inside(t, bars, offset)
        tv = t[ok]
        ind = at_entries(tv, bars, offset)
        a = audit(tv, flags(tv, ind, vwap_sd=vwap_sd))
        lines += [f"Checked on {len(tv)} entries with matching bars, using the chart as it stood at each click (no look-ahead).", "",
                  "| Rule | Followed | Trades | Win rate | Net |", "|---|---|---|---|---|"]
        for r in a.itertuples():
            note = " (small sample)" if r.note else ""
            lines.append(f"| {r.principle} | {r.followed} | {r.trades} | {r.win_rate:.1%} | {_money(r.net)}{note} |")
        vb = vwap_bands(tv, ind)
        lines += ["", "By distance from VWAP (chase = with the stretch, fade = against it):", "",
                  "| Position vs. VWAP | Trades | Win rate | Net |", "|---|---|---|---|"]
        for name, r in vb.iterrows():
            lines.append(f"| {name} | {int(r['trades'])} | {r['win_rate']:.0%} | {_money(r['net'])} |")
    else:
        lines.append("Your rules against the chart (trend filters, VWAP) need 1-minute bars. Send them and this section gets filled in.")
    w = winners_in_ticks(t, TICK, 200)
    lines += ["", "Winners in ticks:", "", "| Instrument | Winners | Median ticks | Reached 200+ |", "|---|---|---|---|"]
    for inst, r in w.iterrows():
        lines.append(f"| {inst} | {int(r['winners'])} | {r['median_ticks']:g} | {r['share_200+']:.0%} |")
    d = trades_per_day(t)
    lines += ["", "Busy days vs. quiet days:", "", "| | Days | Trades | Net | Per day |", "|---|---|---|---|---|"]
    for name, r in d.iterrows():
        lines.append(f"| {name} | {int(r['days'])} | {int(r['trades'])} | {_money(r['net'])} | ${r['net_per_day']:,.0f} |")
    return lines


def _model(t: pd.DataFrame) -> list[str]:
    try:
        ev, _ = walk_forward(t)
    except Exception as e:  # too few trades, missing optional deps
        return [f"Not enough history to test a model honestly ({e.__class__.__name__})."]
    verdict = ("That's a coin flip. Your history can't predict your next trade." if ev.auc_tabpfn < 0.6 else
               f"Better than chance, but on {ev.n_test} trades that can still be luck. Treat the number as a hint, not a rule.")
    return [f"I trained a model (TabPFN) on your older {ev.n_train} trades and scored the next {ev.n_test} it never saw: "
            f"AUC {ev.auc_tabpfn:.2f} (0.50 = coin flip). {verdict}"]


def _highlights(t: pd.DataFrame, pause: int) -> list[str]:
    """The three largest gaps in the record, as observations. No advice; the reader decides."""
    cands = []
    a, b = t[t["trades_today"] < 5], t[t["trades_today"] >= 5]
    if len(a) >= 20 and len(b) >= 20:
        cands.append((abs(_grp(a)[2] - _grp(b)[2]),
                      f"Your first five entries each day made {_money(_grp(a)[2])} over {len(a)} trades; everything after them "
                      f"made {_money(_grp(b)[2])} over {len(b)}."))
    after = t[t["prev_win"] == 0]
    q, s = after[after["minutes_since_exit"] < pause], after[after["minutes_since_exit"] >= pause]
    if len(q) >= 15 and len(s) >= 15:
        cands.append((abs(_grp(q)[2] - _grp(s)[2]),
                      f"Re-entering within {pause} minutes of a losing exit made {_money(_grp(q)[2])} ({len(q)} trades); "
                      f"waiting longer made {_money(_grp(s)[2])} ({len(s)} trades)."))
    last = t["entry_time"].max()
    rows = [(label(n, x, y, last), t[in_window(t["entry_time"], x, y)]) for n, x, y in SESSIONS]
    rows = [(n, g) for n, g in rows if len(g) >= 15]
    if len(rows) >= 2:
        worst = min(rows, key=lambda r: _grp(r[1])[2]); best = max(rows, key=lambda r: _grp(r[1])[2])
        cands.append((abs(_grp(best[1])[2] - _grp(worst[1])[2]),
                      f"By session, {best[0]} made {_money(_grp(best[1])[2])} ({len(best[1])} trades) and {worst[0]} made "
                      f"{_money(_grp(worst[1])[2])} ({len(worst[1])} trades)."))
    big = t[t["size_vs_usual"] >= 3]
    if len(big) >= 3:
        cands.append((abs(_grp(big)[2]), f"{len(big)} entries were 3x your usual size or more; together they made {_money(_grp(big)[2])}."))
    cands.sort(key=lambda c: -c[0])
    return [f"{i + 1}. {txt}" for i, (_, txt) in enumerate(cands[:3])]


PDF_CSS = """@page{size:Letter;margin:0.8in 0.85in}
body{font-family:Georgia,'Times New Roman',serif;font-size:11pt;line-height:1.55;color:#111}
h1{font-family:Arial,sans-serif;font-size:24pt;line-height:1.15;margin:0 0 4pt}
h2{font-family:Arial,sans-serif;font-size:14pt;margin:20pt 0 6pt;border-bottom:3px solid #FFE600;padding-bottom:2pt}
table{border-collapse:collapse;width:100%;font-size:9.5pt;margin:6pt 0 10pt;page-break-inside:avoid}
th,td{border:1px solid #999;padding:3pt 6pt;vertical-align:top;text-align:left}th{background:#f4f4f4}
hr{border:0;border-top:1px solid #ccc;margin:16pt 0}"""

BROWSERS = [r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser"]


def to_pdf(markdown_text: str, out_pdf) -> None:
    """Render the report with a headless Chrome/Edge (no extra Python deps beyond `markdown`)."""
    import subprocess
    import tempfile
    from pathlib import Path

    import markdown
    browser = next((b for b in BROWSERS if Path(b).exists()), None)
    if not browser:
        raise RuntimeError("no Chrome or Edge found for PDF rendering")
    html = (f'<!doctype html><html><head><meta charset="utf-8"><title>Your Trading History, Audited</title>'
            f'<style>{PDF_CSS}</style></head><body>{markdown.markdown(markdown_text, extensions=["tables"])}</body></html>')
    with tempfile.TemporaryDirectory() as d:
        src = Path(d) / "report.html"
        src.write_text(html, encoding="utf-8")
        subprocess.run([browser, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                        f"--print-to-pdf={Path(out_pdf).resolve()}", src.as_uri()], check=True, timeout=180)


def three_numbers(trades: pd.DataFrame, pause: int = 15) -> str:
    """The free teaser: sessions, first five trades, and the after-loss pause, nothing else."""
    t = add_features(trades)
    n, wr, net = _grp(t)
    after, qnet, snet = _after_loss(t, pause)
    L = [f"{n} trades, {t['day'].nunique()} days, win rate {wr:.1%}, net {_money(net)}.", "",
         "**1. By New York session**", ""] + _sessions(t)
    L += ["", "**2. Your first trades of the day**", ""] + _first_trades(t)
    L += ["", "**3. After a loss**", ""] + after[4:]
    return "\n".join(L) + "\n"


def write_report(trades: pd.DataFrame, bars: dict | None = None, offset=None, pause: int = 15,
                 vwap_sd: float = 1.0, who: str = "you") -> str:
    t = add_features(trades)
    n, wr, net = _grp(t)
    days = t["day"].nunique()
    first, last = t["entry_time"].min(), t["entry_time"].max()
    insts = ", ".join(f"{k} ({v})" for k, v in t["instrument"].value_counts().items())
    ex = how_she_exits(t)
    top = t["instrument"].value_counts().index[0]
    med = {res: ex.loc[(top, res)] for res in ("win", "loss") if (top, res) in ex.index}
    after, qnet, snet = _after_loss(t, pause)
    L = [f"# Your Trading History, Audited", "",
         f"## {first:%B %d} to {last:%B %d, %Y}", "",
         "What this is: a description of what you did, in your own numbers, from your fills. "
         "What it is not: trading advice, a signal, or a prediction. Where the data can't tell, it says so.", "",
         "## 1. The period on one page", "",
         "| | |", "|---|---|",
         f"| Entries | {n} over {days} trading days |",
         f"| Instruments | {insts} |",
         f"| Win rate | {wr:.1%} |",
         f"| Net after commissions | **{_money(net)}** |"]
    if "win" in med and "loss" in med:
        L += [f"| Median winner, {top} | {med['win']['median_pts']:+.2f} points, held {med['win']['median_minutes']:.1f} minutes |",
              f"| Median loser, {top} | {med['loss']['median_pts']:+.2f} points, held {med['loss']['median_minutes']:.1f} minutes |"]
        if abs(med['loss']['median_pts']) > med['win']['median_pts'] and med['loss']['median_minutes'] > med['win']['median_minutes']:
            L += ["", "Read that last pair twice. The winners are taken faster and smaller than the losers."]
    L += ["", "## 2. When you trade, in market time", "",
          "Every window below is a New York session, with your local hours beside it.", ""] + _sessions(t)
    L += ["", "## 3. Your first trades of the day", ""] + _first_trades(t)
    L += ["", "## 4. After a loss", ""] + after
    if snet - qnet > 0 and qnet < 0:
        L += ["", f"The {pause} minutes after a losing exit have a price tag in your record: about ${snet - qnet:,.0f} between those two rows."]
    L += ["", "## 5. Size", ""] + _size(t)
    L += ["", "## 6. How you exit", ""] + _exits(t, bars, offset)
    L += ["", "## 7. Your rules vs. your trades", ""] + _rules(t, bars, offset, vwap_sd)
    L += ["", "## 8. What the data can't tell you", ""] + _model(t)
    hl = _highlights(t, pause)
    if hl:
        L += ["", "## 9. Three things your own numbers point at", "", "Not advice. The largest gaps in your record, in your own numbers.", ""] + hl
    L += ["", "---", "", "Made with [tilt-check](https://github.com/ssap-pa/tilt-check). Every number above comes from your export; "
          "nothing is a projection. Your files are deleted after delivery."]
    return "\n".join(L) + "\n"
