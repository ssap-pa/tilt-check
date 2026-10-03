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
