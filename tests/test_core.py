import io
from pathlib import Path

import pandas as pd

from tiltcheck.facts import applicable, facts
from tiltcheck.features import add_features, planned_row
from tiltcheck.ninjatrader import load_trades
from tiltcheck.sessions import label, session_of

KO = """거래 번호,종목,계좌,전략,매매구분,수량,진입가,청산가격,진입시간,청산시간,진입구분,청산이름,익절,누적. 순이익,수수료,MAE,MFE,ETD,캔들
1,MNQ SEP26,APEX-0014,,매수,1,100,110,2026-09-01 오후 10:00:00,2026-09-01 오후 10:05:00,,,$20.00,$20.00,$1.04,$0,$20,$0,0
2,MNQ SEP26,APEX-0014,,매도,1,110,115,2026-09-01 오후 10:03:00,2026-09-01 오후 10:20:00,,,-$10.00,$10.00,$1.04,$10,$0,$0,0
3,MNQ SEP26,APEX-0014,,매수,1,100,105,2026-09-01 오후 10:30:00,2026-09-01 오후 10:31:00,,,$10.00,$20.00,$1.04,$0,$10,$0,0
4,MNQ SEP26,APEX-0014,,매수,2,100,108,2026-09-01 오후 10:30:00,2026-09-01 오후 10:35:00,,,$32.00,$52.00,$2.08,$0,$16,$0,0
5,MES DEC26,APEX-0099,,매수,1,10,11,2026-09-02 오전 1:00:00,2026-09-02 오전 1:01:00,,,$5.00,$57.00,$1.04,$0,$5,$0,0
"""


def _load(tmp_path: Path, text: str, account=None) -> pd.DataFrame:
    p = tmp_path / "t.csv"
    p.write_text(text, encoding="utf-8-sig")
    return load_trades(p, account)


def test_korean_headers_and_account_filter(tmp_path):
    t = _load(tmp_path, KO, account="0014")
    assert set(t["account"]) == {"APEX-0014"}
    assert t.loc[0, "side"] == "long" and t.loc[1, "side"] == "short"
    assert t["entry_time"].iloc[0] == pd.Timestamp("2026-09-01 22:00:00")


def test_split_rows_become_one_decision(tmp_path):
    t = _load(tmp_path, KO, account="0014")
    # rows 3 and 4 share the same entry time -> one trade, qty 3, profit 42
    merged = t[t["entry_time"] == pd.Timestamp("2026-09-01 22:30:00")]
    assert len(merged) == 1
    assert merged["qty"].iloc[0] == 3
    assert abs(merged["profit"].iloc[0] - 42.0) < 1e-9


def test_no_look_ahead_on_overlapping_trades(tmp_path):
    t = add_features(_load(tmp_path, KO, account="0014"))
    # trade 2 was entered at 22:03 while trade 1 was still... no, trade 1 closed 22:05.
    # So at 22:03 nothing had closed yet: trade 2 must not "know" trade 1 won.
    second = t[t["entry_time"] == pd.Timestamp("2026-09-01 22:03:00")].iloc[0]
    assert second["minutes_since_exit"] == 24 * 60
    assert second["losses_in_a_row"] == 0
    # The 22:30 entry comes after trade 2 (a loser) closed at 22:20.
    third = t[t["entry_time"] == pd.Timestamp("2026-09-01 22:30:00")].iloc[0]
    assert third["prev_win"] == 0 and third["losses_in_a_row"] == 1
    assert third["pnl_today"] == 10.0          # +20 then -10, both closed before 22:30
    assert third["trades_today"] == 2


def test_facts_and_planned_row(tmp_path):
    t = _load(tmp_path, KO, account="0014")
    f = facts(t)
    assert f["overall"]["trades"] == 3
    row = planned_row(t, "MNQ", "short", 1, pd.Timestamp("2026-09-01 23:00:00"))
    assert row["trades_today"].iloc[0] == 3
    assert row["prev_win"].iloc[0] == 1          # the last closed trade (22:35) was a winner
    assert isinstance(applicable(t, row.iloc[0]), list)


def test_sample_file_loads():
    p = Path(__file__).resolve().parent.parent / "sample" / "trades_sample.csv"
    t = load_trades(p)
    assert len(t) > 150
    assert (t["rows"] > 1).any()                 # the sample includes scaled-out entries


def test_new_york_sessions_from_korean_pc_clock(tmp_path, monkeypatch):
    monkeypatch.setenv("TILTCHECK_TZ", "Asia/Seoul")
    t = _load(tmp_path, KO, account="0014")
    s = dict(zip(t["entry_time"].dt.strftime("%H:%M"), session_of(t["entry_time"])))
    assert s["22:00"] == "before the NY open"        # 9:00 a.m. in New York (EDT)
    assert s["22:30"] == "NY open"                   # 9:30 a.m.
    # Korean lunchtime is the middle of the night in New York, not the main session
    assert session_of(pd.Series([pd.Timestamp("2026-10-02 13:05")])).iloc[0] == "NY overnight"
    assert label("NY overnight", 23 * 60, 2 * 60, "2026-10-02") == \
        "NY overnight 23:00-02:00 New York (12:00-15:00 on this PC)"
    # after daylight saving ends, the same session sits an hour later on a Korean clock
    assert label("NY open", 9 * 60 + 30, 12 * 60, "2026-11-10") == \
        "NY open 09:30-12:00 New York (23:30-02:00 on this PC)"


def _bars_file(tmp_path):
    """Synthetic MNQ 12-26 minute bars in UTC: price climbs 1 point a minute for an hour."""
    lines = []
    for k in range(120):
        p = 100.0 + k if k <= 60 else 160.0 - (k - 60)
        close_utc = pd.Timestamp("2026-09-15 00:00") + pd.Timedelta(minutes=k + 1)
        lines.append(f"{close_utc:%Y%m%d %H%M%S};{p};{p + 1.25};{p - 0.25};{p + 1};100")
    f = tmp_path / "MNQ 12-26.Last.txt"
    f.write_text("\n".join(lines), encoding="utf-8")
    return f


def _two_trades():
    base = dict(account="APEX-0014", instrument="MNQ", contract="MNQ DEC26", qty=1, commission=1.04, rows=1)
    return pd.DataFrame([
        # long at 09:10:30 Korea time = 00:10:30 UTC, filled inside that minute's bar
        dict(base, side="long", entry_time=pd.Timestamp("2026-09-15 09:10:30"),
             exit_time=pd.Timestamp("2026-09-15 09:13:00"), entry_price=110.5, exit_price=112.5, profit=2.96),
        # short into the climb at 09:20:30 Korea time
        dict(base, side="short", entry_time=pd.Timestamp("2026-09-15 09:20:30"),
             exit_time=pd.Timestamp("2026-09-15 09:30:00"), entry_price=120.5, exit_price=130.5, profit=-21.04),
    ])


def test_bars_line_up_and_brackets_replay(tmp_path):
    from tiltcheck.bars import align, contract_key, load_bars
    from tiltcheck.exits import bracket, paths, point_values
    assert contract_key("MNQ DEC26") == contract_key("MNQ 12-26") == "MNQ 12-26"
    bars = load_bars([_bars_file(tmp_path)])
    t = _two_trades()
    offset, share = align(t, bars)
    assert offset == pd.Timedelta(hours=9) and share == 1.0      # bars were in UTC, trades on a Korean clock
    assert point_values(t)["MNQ"] == 2.0
    ps = paths(t, bars, offset)
    assert bracket(ps[0], target=5, stop=5) == 5                  # the climb reaches +5 before -5
    assert bracket(ps[1], target=5, stop=5) == -5                 # the short gets stopped by the same climb


def test_replay_picks_on_older_trades_and_scores_newer(tmp_path):
    from tiltcheck.bars import align, load_bars
    from tiltcheck.exits import replay
    bars = load_bars([_bars_file(tmp_path)])
    t = _two_trades()
    offset, _ = align(t, bars)
    r = replay(t, bars, offset)
    assert (r.covered, r.train, r.test) == (2, 1, 1)
    assert r.actual_test == -21.04                # the short she actually closed for a loss
    assert r.in_sample.shape == (6, 6)


def _watcher(rules=None):
    from tiltcheck.watch import DEFAULT_RULES, Watcher
    hist = load_trades(Path(__file__).resolve().parent.parent / "sample" / "trades_sample.csv")
    said = []
    w = Watcher(hist, dict(DEFAULT_RULES, **(rules or {})),
                alert=lambda title, text, urgent=False: said.append((title, text, urgent)))
    return w, said


def test_watch_flags_adding_to_a_loser_and_big_size():
    w, said = _watcher()
    t0 = pd.Timestamp("2026-10-05 23:00:00")
    w.on_fill(t0, "APEX-0014", "MNQ 12-26", -1, 20000.0)                       # short 1
    w.on_fill(t0 + pd.Timedelta(minutes=9), "APEX-0014", "MNQ 12-26", -1, 20033.25)   # adds 33 points under water
    assert said[-1][2] and "Adding to a losing short" in said[-1][1] and "-33.25 pts" in said[-1][1]
    w.on_fill(t0 + pd.Timedelta(minutes=20), "APEX-0014", "MNQ 12-26", 2, 20040.0)    # flat
    assert w.open == {} and len(w.today) == 1 and w.today[0]["profit"] < 0
    w.on_fill(t0 + pd.Timedelta(minutes=30), "APEX-0014", "MNQ 12-26", 20, 20040.0)   # 20 contracts
    assert "SIZE: 20 contracts" in said[-1][1] and said[-1][2]


def test_watch_rules_from_rules_json(monkeypatch):
    monkeypatch.setenv("TILTCHECK_TZ", "Asia/Seoul")
    w, said = _watcher({"max_contracts": 2, "daily_loss_limit_usd": 50, "avoid_sessions": ["NY overnight"]})
    t0 = pd.Timestamp("2026-10-06 12:30:00")                                    # Korean lunchtime = NY overnight
    w.on_fill(t0, "APEX-0014", "MNQ 12-26", 3, 20000.0)
    text = said[-1][1]
    assert "your max is 2" in text and "NY overnight is on your avoid list" in text
    w.on_fill(t0 + pd.Timedelta(minutes=5), "APEX-0014", "MNQ 12-26", -3, 19985.0)   # -15 pts x 3 x $2 = -$90
    assert said[-1][0] == "tilt-check: rule" and "$50 limit" in said[-1][1]


def test_watch_reads_the_add_on_files(tmp_path, monkeypatch):
    from tiltcheck.watch import follow
    monkeypatch.setenv("TILTCHECK_TZ", "Asia/Seoul")
    w, said = _watcher()
    (tmp_path / "fills.csv").write_text("time_utc,account,instrument,signed_qty,price,execution_id,platform_time\n",
                                        encoding="utf-8")
    ticks = iter(range(3))

    def stop():
        n = next(ticks, None)
        if n == 1:   # a fill arrives while it's watching; the last line is still half-written
            with open(tmp_path / "fills.csv", "a", encoding="utf-8") as f:
                f.write("2026-10-06 03:30:00.000,APEX-0014,MNQ 12-26,-1,20000,x1,2026-10-06 12:30:00.000\n2026-10-06 03:3")
        return n is None
    follow(w, tmp_path, poll=0, stop=stop)
    assert len(said) == 1 and said[0][1].startswith("Short 1 MNQ 12-26 at 20000")
    assert ("APEX-0014", "MNQ 12-26") in w.open


def test_indicators_use_only_closed_bars_and_fvg_first_touch(tmp_path, monkeypatch):
    from tiltcheck.indicators import at_entries, fvgs, resample
    monkeypatch.setenv("TILTCHECK_TZ", "Asia/Seoul")
    # 15-minute structure in 1-minute bars (UTC): flat at 100, a jump to 110 (leaves a gap), then drift back down
    idx = pd.date_range("2026-09-15 00:01", periods=120, freq="1min")
    price = [100.0] * 15 + [105.0] * 15 + [110.0] * 15 + [108.0] * 30 + [104.0] * 45
    b = pd.DataFrame({"open": price, "high": [p + 0.5 for p in price], "low": [p - 0.5 for p in price],
                      "close": price, "volume": 100}, index=idx)
    g = fvgs(resample(b, 15))
    assert (g["kind"] == "bull").any()                       # 100.5 high, then a 109.5 low two bars later
    zone = g[g["kind"] == "bull"].iloc[0]
    bars = {"MNQ 12-26": b}
    # a long at 104 (inside the gap) in the first minute price is there, on the Korean clock
    t = pd.DataFrame([dict(account="A", instrument="MNQ", contract="MNQ DEC26", side="long", qty=1,
                           entry_time=pd.Timestamp("2026-09-15 10:16:30"), exit_time=pd.Timestamp("2026-09-15 10:20"),
                           entry_price=104.0, exit_price=105.0, profit=1.0, commission=0.0, rows=1)])
    ind = at_entries(t, bars, pd.Timedelta(hours=9))
    assert ind.loc[0, "fvg"] == "bull" and zone["lo"] <= 104.0 <= zone["hi"]
    assert bool(ind.loc[0, "fvg_first_touch"])
    # the value used at 10:16:30 is the bar that closed at 01:16 UTC, not anything later
    assert ind.loc[0, "price_before"] == 104.0


def test_watch_prop_limits_pause_daily_pct_and_stop_risk(monkeypatch):
    monkeypatch.setenv("TILTCHECK_TZ", "Asia/Seoul")
    w, said = _watcher({"max_contracts_by_class": {"micro": 20, "mini": 2}, "pause_after_loss_minutes": 15,
                        "daily_loss_limit_pct": 1, "risk_per_trade_pct": 1})
    t0 = pd.Timestamp("2026-10-06 22:40:00")
    w.on_snapshot(t0, "APEX-0014", 0.0, 50000.0)                          # $50k account -> 1% = $500
    w.on_fill(t0, "APEX-0014", "NQ 12-26", 3, 20000.0)                    # 3 minis, max 2
    assert "3 mini contracts, your max is 2" in said[-1][1]
    w.on_fill(t0 + pd.Timedelta(minutes=1), "APEX-0014", "NQ 12-26", -3, 19990.0)   # -10 pts x 3 x $20 = -$600 loss
    assert "daily limit (1% of the account, $500.00)" in said[-1][1] and "next entry after 22:56" in said[-1][1]
    w.on_fill(t0 + pd.Timedelta(minutes=5), "APEX-0014", "MNQ 12-26", 1, 20000.0)  # 4 min after a loss
    assert "4 min after a losing exit, your pause is 15 min" in said[-1][1]
    # stop checks: an order feed exists, the entry gets a stop that risks more than 1%
    w.on_order(t0, "APEX-0014", "MNQ 12-26", "s1", "Sell", "StopMarket", "Working", 1, 19700.0)  # 300 pts x $2 = $600
    w.on_fill(t0 + pd.Timedelta(minutes=6), "APEX-0014", "MNQ 12-26", -1, 20001.0)  # flat
    w.on_order(t0, "APEX-0014", "MNQ 12-26", "s1", "Sell", "StopMarket", "Cancelled", 1, 19700.0)
    w.on_fill(t0 + pd.Timedelta(minutes=30), "APEX-0014", "MNQ 12-26", 1, 20000.0)  # new long, stop check scheduled
    w.on_order(t0 + pd.Timedelta(minutes=30), "APEX-0014", "MNQ 12-26", "s2", "Sell", "StopMarket", "Working", 1, 19700.0)
    w.tick(t0 + pd.Timedelta(minutes=31))
    assert said[-1][0] == "tilt-check: check this one" and "the stop risks $600.00" in said[-1][1]
    w.on_order(t0, "APEX-0014", "MNQ 12-26", "s2", "Sell", "StopMarket", "Cancelled", 1, 19700.0)
    w.pending.append((t0, ("APEX-0014", "MNQ 12-26")))
    w.tick(t0 + pd.Timedelta(minutes=32))
    assert said[-1][0] == "tilt-check: no stop"


def test_watch_drawdown_limits_scale_ins_and_weak_hours(monkeypatch):
    monkeypatch.setenv("TILTCHECK_TZ", "Asia/Seoul")
    w, said = _watcher({"drawdown_usd": 2500, "risk_per_trade_pct_of_drawdown": 1, "daily_loss_limit_pct_of_drawdown": 15,
                        "a_plus_checklist": ["first touch of the wick or FVG"], "stop_check_from_trade": 2,
                        "weak_sessions": ["NY overnight"]})
    usual_loss = abs(w.usual[("MNQ", "short")][1])
    t0 = pd.Timestamp("2026-10-07 22:40:00")
    w.on_order(t0, "A", "MNQ 12-26", "s0", "Buy", "StopMarket", "Cancelled", 1, 0.0)        # the add-on is running
    w.on_fill(t0, "A", "MNQ 12-26", -1, 20000.0)                                          # trade 1: test trade
    assert w.pending == []                                                                 # no stop check yet
    w.on_fill(t0 + pd.Timedelta(seconds=30), "A", "MNQ 12-26", -1, 20000.0 + usual_loss / 2)   # scale-in inside her usual stop
    assert not said[-1][1].startswith("MNQ 12-26, 2 contracts")
    w.on_fill(t0 + pd.Timedelta(minutes=1), "A", "MNQ 12-26", -1, 20000.0 + usual_loss + 5)    # past her usual loss
    assert "Adding to a losing short" in said[-1][1] and "past your usual" in said[-1][1]
    w.on_fill(t0 + pd.Timedelta(minutes=2), "A", "MNQ 12-26", 3, 20200.0)                 # big loss: about -$1,200
    assert "daily limit (15% of your $2,500 drawdown, $375.00). Done for today." in said[-1][1]
    w.on_fill(t0 + pd.Timedelta(minutes=30), "A", "MNQ 12-26", 1, 20200.0)                # trade 2, after the limit
    assert "Done for today, unless this is:" in said[-1][1] and "first touch of the wick or FVG" in said[-1][1]
    w.on_order(t0, "A", "MNQ 12-26", "s1", "Sell", "StopMarket", "Working", 1, 20180.0)  # 20 pts x $2 = $40 > $25
    w.tick(t0 + pd.Timedelta(minutes=31))
    assert "the stop risks $40.00" in said[-1][1] and "1% of your $2,500 drawdown per entry ($25.00)" in said[-1][1]
    w.on_fill(t0 + pd.Timedelta(minutes=32), "A", "MNQ 12-26", -1, 20201.0)
    w.on_fill(pd.Timestamp("2026-10-08 13:00:00"), "A", "MNQ 12-26", 1, 20000.0)            # Korean lunchtime
    assert "WEAK HOURS: NY overnight" in said[-1][1]


def test_audit_report_runs_without_bars(tmp_path):
    from tiltcheck.report_md import write_report
    t = _load(tmp_path, KO, account="0014")
    md = write_report(t)
    assert md.startswith("# Your Trading History, Audited")
    for h in ("## 1. The period on one page", "## 4. After a loss", "## 6. How you exit", "## 8. What the data"):
        assert h in md
    assert "3 over 1 trading days" in md
    assert "needs 1-minute bars" in md          # no bars -> the replay and the rules say so
    assert " her " not in md and "she " not in md  # written to the trader, not about her


def test_load_bars_reads_multi_contract_csv(tmp_path):
    from tiltcheck.bars import load_bars
    p = tmp_path / "bars.csv"
    p.write_text("﻿instrument,bar_end_utc,bar_end_kst,open,high,low,close,volume\n"
                 "MNQ DEC26,2026-09-10T00:01:00.000Z,2026-09-10T09:01:00.000+09:00,1,2,0.5,1.5,10\n"
                 "MNQ DEC26,2026-09-10T00:02:00.000Z,2026-09-10T09:02:00.000+09:00,1.5,2.5,1,2,11\n"
                 "MES SEP26,2026-09-10T00:01:00.000Z,2026-09-10T09:01:00.000+09:00,5,6,4,5.5,3\n", encoding="utf-8")
    b = load_bars([p])
    assert set(b) == {"MNQ 12-26", "MES 09-26"}          # same keys as the .txt exports
    assert b["MNQ 12-26"].index[0] == pd.Timestamp("2026-09-10 00:01:00")   # UTC, naive; align() finds the offset
    assert list(b["MNQ 12-26"].columns) == ["open", "high", "low", "close", "volume"]


def test_watch_chart_line_from_closed_bars(tmp_path):
    import numpy as np
    from tiltcheck.watch import Watcher, load_rules
    t = _load(tmp_path, KO, account="0014")
    # three days of rising 1-minute bars, so the fill is above the 15m 200 EMA and VWAP
    idx = pd.date_range("2026-09-08 09:00", "2026-09-10 23:59", freq="min")
    px = 100 + np.arange(len(idx)) * 0.01
    bars = {"MNQ 12-26": pd.DataFrame({"open": px, "high": px + 0.5, "low": px - 0.5, "close": px, "volume": 10}, index=idx)}
    out = []
    w = Watcher(t, {**load_rules(None), "with_15m_200": True, "fade_inside_1sd": True}, bars=bars,
                alert=lambda title, text, urgent=False: out.append(text))
    w.on_fill(pd.Timestamp("2026-09-10 22:00:00"), "APEX-0014", "MNQ DEC26", -1, float(px[-120]))
    text = out[-1]
    assert "CHART: 15m 200 EMA" in text and "against the trade" in text   # short, price above the EMA
    assert "RULE: against the 15m 200 EMA" in text


def test_follow_reloads_bars_from_the_add_on_folder(tmp_path, monkeypatch):
    import numpy as np
    from tiltcheck.watch import Watcher, follow, load_rules
    t = _load(tmp_path, KO, account="0014")
    feed = tmp_path / "feed"; (feed / "bars").mkdir(parents=True)
    # the add-on's bars file: UTC bar ends; the fills below are PC time (KST), align() finds +9h
    idx = pd.date_range("2026-09-08 00:00", "2026-09-10 14:59", freq="min", tz="UTC")
    px = 100 + np.arange(len(idx)) * 0.01
    pd.DataFrame({"instrument": "MNQ DEC26", "bar_end_utc": idx.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                  "open": px, "high": px + 0.5, "low": px - 0.5, "close": px, "volume": 10}).to_csv(feed / "bars" / "MNQ 12-26.csv", index=False)
    (feed / "fills.csv").write_text("time_utc,account,instrument,signed_qty,price,execution_id,platform_time\n"
                                    f"{pd.Timestamp.utcnow().strftime('%Y-%m-%d %H:%M:%S.000')},APEX-0014,MNQ DEC26,-1,{px[-60]:.2f},x1,\n")
    out = []
    w = Watcher(t, {**load_rules(None), "with_15m_200": True}, alert=lambda title, text, urgent=False: out.append(text))
    n = {"i": 0}
    follow(w, feed, poll=0.01, stop=lambda: n.__setitem__("i", n["i"] + 1) or n["i"] > 3, bars_dir=feed / "bars")
    assert w.bars and "MNQ 12-26" in w.bars            # loaded from the folder, not from --bars
