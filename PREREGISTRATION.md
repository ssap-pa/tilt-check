# Pre-registered forward test: 3x/3x bracket vs her hand exits

Written 2026-10-08 (KST), before the window starts. Nothing below changes after this commit.

## Why

The September replay said a 3x/3x bracket beat her exits on the newest 30% of trades (+$444.86 vs +$271.36 on 88 trades).
A reader (arhancanli, on the DEV post) showed the sign of that comparison flips with a one-day move of the holdout start
(from Sept 22: +$430; Sept 23 00:00: -$64; Sept 24: -$125; Sept 25: +$85), and that 15 of the 18 decisive trades sit on one day.
The unit of evidence is days, not trades, and September has seven holdout days. So the comparison is not settled on the data that exists.
A forward window fixed before it starts can contradict the in-sample result, which no re-cut of September can.

## Frozen

- Bracket: target 3x and stop 3x her usual move, per instrument, with the point sizes the September report printed
  (GC +3.3/-3.3, MCL +0.3/-0.3, MES +6.75/-6.75, MGC +7.1/-7.1, MNQ +26.25/-26.25, NQ +3/-3). Not re-fitted.
- Replay: her own NinjaTrader 1-minute bars, 120-minute horizon, a bar that touches both levels counts as the stop
  (the September report showed the target-first variant gives the same number).
- Her side: her actual net profit per trade from the NinjaTrader export, as before.
- Window: entries from 2026-10-08 00:00 KST through 2026-10-31 23:59 KST, or until 15 distinct trading days with at least one entry, whichever is later.
- Metric: per trading day, (bracket net on that day's entries) minus (her net on the same entries). Days, not trades, are the observations.
- Decision rule: two-sided sign test on the daily differences.
  - Bracket ahead on at least 13 of 15 days (p < 0.01) or 12 of 15 (p = 0.035): "the bracket beat her exits in October".
  - Her exits ahead by the same margins: "her exits beat the bracket in October".
  - Anything else: "not settled", and the note says so. No new cut, no new bracket, no second look.
- Reported either way, in the same post and README, with the per-day table.

## What would invalidate it

- Fewer than 15 trading days with entries by 2026-10-31: the window extends until 15, and the note says the window extended.
- Bars missing for any entry: that day is dropped and listed.
- Any change to the bracket, horizon, tie rule, window or metric after this commit: the test is void and starts over.
