# tilt-check

A pre-trade check for futures traders that learns from your own NinjaTrader history.

[TabPFN](https://github.com/PriorLabs/TabPFN) (open weights) reads your past trades. [Gemma](https://ai.google.dev/gemma) (open weights, through [Ollama](https://ollama.com)) tells you in plain language what your own numbers say about the trade you're about to take. Everything runs on your machine. It never places an order.

I built it for my partner, who trades micro futures (MNQ, MES, MGC) on a prop-firm account and kept asking the same question after a bad session: *is this one of my good trades or one of my bad ones?*

## What it said about her account

Her numbers, shared with her permission. One account, Sep 1 to Oct 2, 2026, Korea time:

- 293 entries over 23 days, win rate 64.2%, net **+$1,832.87** after commissions. Average win $29.01, average loss $34.49.
- Her **first 5 entries each day**: 92 trades, win rate 55.4%, net **-$327.38**. From the 6th entry on: 201 trades, 68.2%, **+$2,160.25**.
- Entries between **12:00 and 14:59**: 17 trades, win rate 47.1%, net **-$306.01**.
- And the part I didn't expect: TabPFN **can't tell her winners from her losers**. Trained on older trades and scored on newer ones it had never seen, it got AUC 0.42, 0.52, 0.48 and 0.55 on four walk-forward blocks (0.50 is a coin flip). So the check says that out loud instead of showing a confident percentage.

## The bug that almost became the headline

My first version said she falls apart after a losing trade: 74% win rate after a win, 50% after a loss. It was a great story and it was wrong.

NinjaTrader writes one row per exit, so a scaled-out entry becomes several rows, and her trades overlap. Computing "was the previous trade a loss?" from the previous *row* let a trade peek at a result that hadn't happened yet. Once every feature is computed only from trades that had **already closed** when she clicked, the gap disappears: right after a loss she won 65.2% (92 trades, +$1,217.08). There's a test for this now (`test_no_look_ahead_on_overlapping_trades`).

## What a check looks like

```text
$ python -m tiltcheck check "short 2 MNQ" --lang en

Plan: short 2 MNQ at 13h Fri, 0 loss(es) in a row before, 1 trade(s) earlier today, today P&L $26

What your own history says about trades like this:
- trade #2 of the day (your first 5 trades each day): 92 trades, win rate 55.4%, net $-327.38
- bigger size than usual: 26 trades, win rate 61.5%, net $816.31
- entries 12:00-14:59 (PC time): 17 trades, win rate 47.1%, net $-306.01
- MNQ short: 128 trades, win rate 61.7%, net $182.20

Model check: on trades it had not seen, TabPFN scored AUC 0.50 (0.50 = coin flip).
Your history can't tell winners from losers yet, so the 59% below is shown for the log only.

Your group of trades in entries 12:00-14:59 has a win rate of 47.1%, compared to your
overall 64%. ... Before clicking, how does the sizing of this trade compare ...   <- Gemma, local

take / skip / wait?  skip
why (one line)?  second trade of the day, lunch hour
```

Every check is logged with her decision and reason. `learn` imports the next export, adds the new trades to the history TabPFN reads (there's no training run, it's in-context, so new trades count on the very next check), and matches each logged decision to the trade that followed, so she can see whether skipping paid off.

## Install

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt   # Windows: .venv\Scripts\pip
# GPU is optional; TabPFN runs on CPU for a few hundred trades.
ollama pull gemma4:e4b       # or gemma4:e2b on smaller GPUs
```

Export from NinjaTrader: Control Center > Trade Performance > Trades tab > right-click > Export. English and Korean column names both work.

## Use

```bash
python -m tiltcheck report --csv trades.csv [--account 0014] [--lang ko]
python -m tiltcheck check "short 2 MNQ right after a stop" --csv trades.csv
python -m tiltcheck learn --csv next_export.csv
```

Try it without your own data: `python -m tiltcheck report --csv sample/trades_sample.csv` (made-up trades with a slow-start pattern baked in).

## What stays where

- Your CSV, the merged history and the decision log live in `~/.tilt-check/`.
- Gemma runs through your local Ollama. TabPFN runs in-process. No API keys, nothing uploaded.

## What it is not

It doesn't predict markets, tell you what to buy or sell, or place orders. It describes your own history. Not financial advice.

## Licenses

Code: MIT. Built with TabPFN: the v2 classifier weights are under the Prior Labs License (Apache 2.0 with an attribution clause). Gemma is under the Gemma Terms of Use.
