"""Write sample/trades_sample.csv: made-up trades in NinjaTrader's Trades-grid format.

Nothing here is real. It has one pattern baked in (the first few entries of each
day go worse, and so does one afternoon window) so you can see what the report
looks like, plus a few scaled-out entries that NinjaTrader splits into rows.
"""
import csv
import random
from datetime import datetime, timedelta
from pathlib import Path

random.seed(7)
POINT = {"MNQ": 2.0, "MES": 5.0, "MGC": 10.0}
rows, n = [], 0
day = datetime(2026, 9, 1)
for d in range(22):
    day += timedelta(days=1 if day.weekday() < 4 else 3)
    t = day.replace(hour=random.choice([0, 1, 2, 21, 22]), minute=random.randint(0, 59))
    for k in range(random.randint(6, 16)):
        inst = random.choices(["MNQ", "MES", "MGC"], [0.75, 0.15, 0.10])[0]
        side = random.choice(["Long", "Short"])
        qty = random.choice([1, 1, 2, 2, 3])
        p_win = 0.66
        if k < 5:
            p_win -= 0.12          # slow start
        if 12 <= t.hour < 15:
            p_win -= 0.15          # a bad window
        win = random.random() < p_win
        pts = random.uniform(3, 12) if win else -random.uniform(4, 16)
        hold = timedelta(seconds=random.randint(20, 900))
        entry_px = round(random.uniform(24000, 25000) if inst == "MNQ" else
                         random.uniform(6400, 6600) if inst == "MES" else random.uniform(3500, 3600), 2)
        legs = [qty] if qty < 3 or random.random() < 0.5 else [1, qty - 1]   # sometimes scaled out
        for i, q in enumerate(legs):
            n += 1
            sign = 1 if side == "Long" else -1
            exit_px = round(entry_px + sign * pts * (1 + 0.3 * i), 2)
            gross = sign * (exit_px - entry_px) * POINT[inst] * q
            fee = round(1.04 * q, 2)
            rows.append({
                "Trade number": n, "Instrument": f"{inst} DEC26", "Account": "Sim-sample", "Strategy": "",
                "Market pos.": side, "Qty": q, "Entry price": entry_px, "Exit price": exit_px,
                "Entry time": t.strftime("%m/%d/%Y %I:%M:%S %p"),
                "Exit time": (t + hold + timedelta(seconds=30 * i)).strftime("%m/%d/%Y %I:%M:%S %p"),
                "Entry name": "", "Exit name": "",
                "Profit": f"${gross - fee:,.2f}" if gross - fee >= 0 else f"-${abs(gross - fee):,.2f}",
                "Commission": f"${fee:.2f}", "MAE": f"${abs(min(0, gross)) + 5:.2f}", "MFE": f"${max(0, gross) + 5:.2f}",
            })
        t += hold + timedelta(minutes=random.randint(1, 60))

out = Path(__file__).with_name("trades_sample.csv")
with out.open("w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0]))
    w.writeheader()
    w.writerows(rows)
print(f"wrote {len(rows)} rows to {out}")
