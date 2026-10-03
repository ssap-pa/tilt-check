"""Load a Tradovate export into the same trade table ninjatrader.load_trades returns.

Two exports are accepted:

- Performance (Reports > Performance > Export): one row per closed round trip,
  with buyPrice/sellPrice, boughtTimestamp/soldTimestamp and pnl.
- Orders or Fills (one row per fill): Contract, B/S, a quantity column, a fill
  price column and a time column. Fills are paired first-in first-out into round
  trips, and P&L is worked out from the contract's dollar value per point.

Commission isn't in either export, so it is 0 unless a Commission column exists.
"""
from __future__ import annotations

import re
from collections import deque
from pathlib import Path

import pandas as pd

from .exits import POINT_VALUE
from .ninjatrader import _money, merge_split_rows

_ROOT = re.compile(r"^([A-Z0-9]+?)([FGHJKMNQUVXZ]\d{1,2})?$")


def root_symbol(contract: str) -> str:
    """'MNQZ6' -> 'MNQ', 'ESH27' -> 'ES', 'MNQ DEC26' -> 'MNQ'."""
    c = str(contract).strip().upper().split()[0]
    m = _ROOT.match(c)
    return m.group(1) if m else c


def _pick(df: pd.DataFrame, *names: str) -> str | None:
    low = {c.lower().strip(): c for c in df.columns}
    for n in names:
        if n.lower() in low:
            return low[n.lower()]
    return None


def _empty() -> pd.DataFrame:
    return pd.DataFrame(columns=["account", "instrument", "contract", "side", "qty", "entry_time", "exit_time",
                                 "entry_price", "exit_price", "profit", "commission", "mae", "mfe"])


def _from_performance(df: pd.DataFrame) -> pd.DataFrame:
    sym = _pick(df, "symbol", "Contract")
    bt, st = pd.to_datetime(df["boughtTimestamp"]), pd.to_datetime(df["soldTimestamp"])
    long = bt <= st
    buy = pd.to_numeric(df["buyPrice"], errors="coerce")
    sell = pd.to_numeric(df["sellPrice"], errors="coerce")
    acc = _pick(df, "account", "Account")
    com = _pick(df, "commission", "Commission")
    return pd.DataFrame({
        "account": df[acc].astype(str) if acc else "?",
        "instrument": df[sym].map(root_symbol),
        "contract": df[sym].astype(str).str.strip(),
        "side": long.map({True: "long", False: "short"}),
        "qty": pd.to_numeric(df["qty"], errors="coerce").fillna(1).astype(int),
        "entry_time": bt.where(long, st),
        "exit_time": st.where(long, bt),
        "entry_price": buy.where(long, sell),
        "exit_price": sell.where(long, buy),
        "profit": _money(df["pnl"]),
        "commission": _money(df[com]).abs() if com else 0.0,
        "mae": 0.0,
        "mfe": 0.0,
    })


def pair_fills(fills: pd.DataFrame) -> pd.DataFrame:
    """fills: account, contract, time, side (+1 buy / -1 sell), qty, price, commission.

    Walks fills in time order per account and contract. A fill against the open
    position closes lots first in, first out; whatever is left opens a new lot.
    Each opening fill becomes one trade (closed pieces are summed into it), so a
    scale-out is still one decision, same as merge_split_rows does for NinjaTrader.
    """
    rows = []
    for (acc, contract), g in fills.sort_values("time", kind="stable").groupby(["account", "contract"], sort=False):
        root = root_symbol(contract)
        if root not in POINT_VALUE:
            raise ValueError(f"unknown dollar value per point for {contract!r}; add {root!r} to exits.POINT_VALUE")
        pv = POINT_VALUE[root]
        lots: deque = deque()      # [trade dict, open qty left]
        for f in g.itertuples(index=False):
            left = int(f.qty)
            per_fill_com = float(f.commission) / max(int(f.qty), 1)
            while left and lots and lots[0][0]["dir"] != f.side:
                trade, open_left = lots[0]
                n = min(left, open_left)
                trade["profit"] += (f.price - trade["entry_price"]) * trade["dir"] * n * pv
                trade["exit_value"] += f.price * n
                trade["closed"] += n
                trade["exit_time"] = f.time
                trade["commission"] += per_fill_com * n
                left -= n
                lots[0][1] -= n
                if lots[0][1] == 0:
                    lots.popleft()
            if left:
                trade = {"account": acc, "contract": contract, "instrument": root, "dir": f.side,
                         "qty": left, "entry_time": f.time, "entry_price": f.price, "exit_time": pd.NaT,
                         "exit_value": 0.0, "closed": 0, "profit": 0.0, "commission": per_fill_com * left}
                rows.append(trade)
                lots.append([trade, left])
    done = [r for r in rows if r["closed"] == r["qty"]]       # still-open positions are not trades yet
    if not done:
        return _empty()
    t = pd.DataFrame(done)
    return pd.DataFrame({
        "account": t["account"].astype(str), "instrument": t["instrument"], "contract": t["contract"],
        "side": t["dir"].map({1: "long", -1: "short"}), "qty": t["qty"].astype(int),
        "entry_time": t["entry_time"], "exit_time": t["exit_time"], "entry_price": t["entry_price"],
        "exit_price": t["exit_value"] / t["closed"], "profit": t["profit"].round(2),
        "commission": t["commission"].round(2), "mae": 0.0, "mfe": 0.0,
    })


def _from_fills(df: pd.DataFrame) -> pd.DataFrame:
    contract = _pick(df, "Contract", "symbol")
    bs = _pick(df, "B/S", "Action", "Side", "action")
    qty = _pick(df, "Filled Qty", "filledQty", "Qty", "Quantity", "qty")
    price = _pick(df, "Avg Fill Price", "avgPrice", "decimalFillAvg", "Fill Price", "Price", "price")
    when = _pick(df, "Fill Time", "Timestamp", "Date/Time", "timestamp")
    missing = [n for n, c in [("contract", contract), ("B/S", bs), ("quantity", qty), ("price", price),
                              ("time", when)] if c is None]
    if missing:
        raise ValueError(f"not a Tradovate Performance, Orders or Fills export, missing: {missing}")
    status = _pick(df, "Status")
    if status:
        df = df[df[status].astype(str).str.strip().str.lower() == "filled"]
    acc = _pick(df, "Account", "account")
    com = _pick(df, "Commission", "commission")
    fills = pd.DataFrame({
        "account": df[acc].astype(str) if acc else "?",
        "contract": df[contract].astype(str).str.strip(),
        "time": pd.to_datetime(df[when]),
        "side": df[bs].astype(str).str.strip().str.lower().str[0].map({"b": 1, "s": -1}),
        "qty": pd.to_numeric(df[qty], errors="coerce"),
        "price": pd.to_numeric(df[price], errors="coerce"),
        "commission": _money(df[com]).abs() if com else 0.0,
    }).dropna(subset=["side", "qty", "price", "time"])
    fills = fills[fills["qty"] > 0]
    return pair_fills(fills)


def is_tradovate(path: str | Path) -> bool:
    head = pd.read_csv(path, encoding="utf-8-sig", nrows=0).columns
    cols = {c.strip().lower() for c in head}
    return bool({"boughttimestamp", "b/s", "filled qty", "avg fill price", "fill price"} & cols) or \
        ("action" in cols and "contract" in cols)


def load_tradovate(path: str | Path, account: str | None = None) -> pd.DataFrame:
    """One row per trade, sorted by entry time. account: keep names ending with this."""
    df = pd.read_csv(path, encoding="utf-8-sig")
    df.columns = [c.strip() for c in df.columns]
    if account:
        acc = _pick(df, "Account", "account")
        if acc:
            df = df[df[acc].astype(str).str.endswith(account)]
    out = _from_performance(df) if "boughtTimestamp" in df.columns else _from_fills(df)
    if out.empty:
        return out
    out = out.sort_values(["entry_time", "exit_time"]).reset_index(drop=True)
    return merge_split_rows(out)
