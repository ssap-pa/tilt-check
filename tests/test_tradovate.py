from pathlib import Path

import pandas as pd
import pytest

from tiltcheck.ninjatrader import load_trades
from tiltcheck.tradovate import root_symbol

HERE = Path(__file__).resolve().parent / "fixtures"


def test_root_symbols():
    assert root_symbol("MNQZ6") == "MNQ"
    assert root_symbol("ESH27") == "ES"
    assert root_symbol("MNQ DEC26") == "MNQ"
    assert root_symbol("M2KZ6") == "M2K"


def test_performance_export_round_trips():
    t = load_trades(HERE / "tradovate_performance.csv")
    assert len(t) == 3
    first = t.iloc[0]
    assert first["side"] == "long" and first["instrument"] == "MNQ" and first["contract"] == "MNQZ6"
    assert first["entry_price"] == 20100.0 and first["exit_price"] == 20110.0
    assert first["profit"] == 20.0
    short = t[t["side"] == "short"].iloc[0]       # sold first, bought back later
    assert short["entry_time"] == pd.Timestamp("2026-10-01 10:00:00")
    assert short["entry_price"] == 6010.0 and short["profit"] == -12.5
    # two pieces of one 3-lot entry scaled out at different prices -> one decision
    scaled = t[t["entry_time"] == pd.Timestamp("2026-10-01 11:00:00")].iloc[0]
    assert scaled["qty"] == 3 and scaled["rows"] == 2 and scaled["profit"] == 30.0


def test_orders_export_pairs_fills_fifo():
    t = load_trades(HERE / "tradovate_orders.csv", account="0014")
    assert set(t["account"]) == {"APEX-0014"}
    # buy 2 @100, sell 1 @105, sell 1 @110 -> one long, qty 2, +$30 on MNQ ($2/pt)
    a = t.iloc[0]
    assert a["side"] == "long" and a["qty"] == 2 and a["profit"] == 30.0
    assert a["exit_price"] == 107.5 and a["exit_time"] == pd.Timestamp("2026-10-01 09:40:00")
    # sell 1 @120, then a buy of 2 @118 closes the short (+$4) and opens a long that closes at 117 (-$2)
    b, c = t.iloc[1], t.iloc[2]
    assert b["side"] == "short" and b["profit"] == 4.0
    assert c["side"] == "long" and c["entry_price"] == 118.0 and c["profit"] == -2.0
    assert len(t) == 3                             # the cancelled order is ignored, the open lot isn't a trade


def test_unknown_file_is_rejected(tmp_path):
    p = tmp_path / "x.csv"
    p.write_text("a,b\n1,2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="NinjaTrader Trades or Tradovate"):
        load_trades(p)
