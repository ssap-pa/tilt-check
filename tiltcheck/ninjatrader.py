"""Load a NinjaTrader "Trades" grid export (Control Center > Trade Performance > Trades > Export).

NinjaTrader writes the column names in the UI language, so this accepts both the
English and the Korean headers. One row is one round-trip trade.
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

COLUMNS = {
    # canonical      English header        Korean header
    "trade_no":     ("Trade number",      "거래 번호"),
    "instrument":   ("Instrument",        "종목"),
    "account":      ("Account",           "계좌"),
    "strategy":     ("Strategy",          "전략"),
    "market_pos":   ("Market pos.",       "매매구분"),
    "qty":          ("Qty",               "수량"),
    "entry_price":  ("Entry price",       "진입가"),
    "exit_price":   ("Exit price",        "청산가격"),
    "entry_time":   ("Entry time",        "진입시간"),
    "exit_time":    ("Exit time",         "청산시간"),
    "entry_name":   ("Entry name",        "진입구분"),
    "exit_name":    ("Exit name",         "청산이름"),
    "profit":       ("Profit",            "익절"),
    "commission":   ("Commission",        "수수료"),
    "mae":          ("MAE",               "MAE"),
    "mfe":          ("MFE",               "MFE"),
}

LONG = {"Long", "매수"}


def _money(s: pd.Series) -> pd.Series:
    """'$59.46', '-$194.16', '($194.16)', '1,234.50' -> float."""
    t = s.astype(str).str.strip()
    neg = t.str.startswith("-") | t.str.startswith("(")
    v = pd.to_numeric(t.str.replace(r"[^0-9.]", "", regex=True), errors="coerce").fillna(0.0)
    return v.where(~neg, -v)


_KO = re.compile(r"(\d{4}-\d{2}-\d{2}) (오전|오후) (\d{1,2}):(\d{2}):(\d{2})")


def _time(s: str) -> pd.Timestamp:
    s = str(s).strip()
    m = _KO.match(s)
    if m:
        d, ampm, h, mi, se = m.groups()
        hour = int(h) % 12 + (12 if ampm == "오후" else 0)
        return pd.Timestamp(f"{d} {hour:02d}:{mi}:{se}")
    return pd.Timestamp(s)  # English exports: '9/2/2026 1:44:20 AM'


def load_trades(path: str | Path, account: str | None = None) -> pd.DataFrame:
    """Return one row per trade, sorted by entry time, with clean numeric columns.

    account: keep only accounts whose name ends with this string (e.g. "0014").
    """
    raw = pd.read_csv(path, encoding="utf-8-sig")
    rename = {}
    for canon, names in COLUMNS.items():
        for n in names:
            if n in raw.columns:
                rename[n] = canon
                break
    df = raw.rename(columns=rename)
    missing = {"instrument", "market_pos", "qty", "entry_time", "exit_time", "profit"} - set(df.columns)
    if missing:
        raise ValueError(f"not a NinjaTrader Trades export, missing: {sorted(missing)}")
    if account:
        df = df[df["account"].astype(str).str.endswith(account)]
    out = pd.DataFrame({
        "account": df.get("account", pd.Series(["?"] * len(df), index=df.index)).astype(str),
        "instrument": df["instrument"].astype(str).str.split().str[0],
        "contract": df["instrument"].astype(str).str.strip(),      # "MNQ DEC26", for matching minute bars
        "side": df["market_pos"].map(lambda v: "long" if v in LONG else "short"),
        "qty": pd.to_numeric(df["qty"], errors="coerce").fillna(1).astype(int),
        "entry_time": df["entry_time"].map(_time),
        "exit_time": df["exit_time"].map(_time),
        "entry_price": pd.to_numeric(df.get("entry_price"), errors="coerce"),
        "exit_price": pd.to_numeric(df.get("exit_price"), errors="coerce"),
        "profit": _money(df["profit"]),
        "commission": _money(df["commission"]) if "commission" in df else 0.0,
        "mae": _money(df["mae"]) if "mae" in df else 0.0,
        "mfe": _money(df["mfe"]) if "mfe" in df else 0.0,
    })
    out = out.sort_values(["entry_time", "exit_time"]).reset_index(drop=True)
    return merge_split_rows(out)


def merge_split_rows(t: pd.DataFrame) -> pd.DataFrame:
    """NinjaTrader writes one row per exit, so a 3-contract entry scaled out at two
    prices becomes several rows with the same entry time. One click is one
    decision, so those rows are folded back into a single trade."""
    key = ["account", "instrument", "side", "entry_time"]
    g = t.groupby(key, sort=False)
    merged = g.agg(contract=("contract", "first"), qty=("qty", "sum"), exit_time=("exit_time", "max"),
                   entry_price=("entry_price", "first"), exit_price=("exit_price", "last"),
                   profit=("profit", "sum"), commission=("commission", "sum"),
                   mae=("mae", "max"), mfe=("mfe", "max"), rows=("qty", "size")).reset_index()
    return merged.sort_values(["entry_time", "exit_time"]).reset_index(drop=True)
