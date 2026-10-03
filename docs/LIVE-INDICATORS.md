# Live indicators for watch mode (design note, Oct 4)

Goal: at the moment a fill arrives, say where price sits against the things she actually watches (15-min 200 EMA, 5-min 100 EMA, 1-hour 9 EMA, session VWAP and its 1-4 sd bands), using only bars that have closed. Never a prediction; never an order.

## Where the bars come from

Two options, in order of preference:

1. **The add-on writes bars.** `TiltCheckFeed.cs` already subscribes to account events. Add a `BarsRequest` per instrument she has a position or working order in, 1-minute, and append each *closed* bar to `Documents\tilt-check\bars\<contract>.csv` in the same `instrument,bar_end_utc,open,high,low,close,volume` shape as her export (so `bars.load_bars` reads it unchanged). One file per contract, append-only, rotated monthly.
2. **Fallback: public 1-minute bars** pulled every minute. Rejected for the audit (missing contracts, late start) and rejected here for the same reason; only as a last resort when the add-on isn't installed.

## What the watcher does with them

- `indicators.at_entries` already computes everything from a bars dict with no look-ahead (it resamples with `closed="right"` and only uses bars whose close time is ≤ the fill time). For live use, call it on the trailing window (last 3 trading days is enough for a 200-period 15-minute EMA to settle) at each fill.
- The quiet line at entry grows one clause: `Long 1 MNQ at 24351.25 (NY afternoon). 15m 200 EMA: above. VWAP: +1.6 sd.` Only the facts; the rule engine decides whether to beep.
- New rule keys for `rules.json`: `with_15m_200` (warn when the entry is against it), `vwap_sd_max` (warn when |sd| exceeds it in the chasing direction), `fade_inside_1sd` (warn on a fade inside the first band; her own record says that's her weak spot).

## What it must not do

- Reach into the chart for indicator values. Everything is recomputed from closed bars so the audit and the live alert agree to the tick.
- Use the current forming bar. If the add-on hands over a bar before it closes, the watcher ignores it.
- Place, modify or cancel orders. Same as everything else in the tool.

## Status (Oct 4)

- Step 1 drafted: `ninjatrader/TiltCheckBars.cs` (BarsRequest per instrument listed in `Documents\tilt-check\bars-watch.txt`, closed bars appended to `Documents\tilt-check\bars\<instrument>.csv`). Not yet compiled in a real NinjaTrader.
- Step 2 done: `watch` (without `--bars`) re-reads `Documents\tilt-check\bars\` every 60 s and recomputes the clock offset once from history, so CHART lines follow the add-on's file as it grows.
- Step 3 done: `Watcher._chart` + the three rule keys; `--bars` still works for a one-off export.

## Order of work

1. Add-on: `BarsRequest` + append-only CSV writer (C#, ~60 lines). Test on her machine first; the add-on itself hasn't run in a real NinjaTrader 8 yet.
2. `watch.follow`: tail `bars\*.csv` alongside `fills.csv`, keep an in-memory bars dict per contract, prune to the trailing window.
3. `Watcher._entry`: call `at_entries` for the one new fill and format the clause.
4. Rules: the three keys above, each with a test that replays one of her days.
5. Only then: the indicator checks in the "check this one" alerts.
