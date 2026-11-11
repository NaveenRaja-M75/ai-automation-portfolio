# Jevbot — paper-first, evidence-gated crypto trader

A real trading bot that starts in paper mode and is only allowed to trade real money once its own data proves an edge. It runs TypeSafe Jev side by side with three challengers on live Binance data, scores every prediction against what actually happened, and trades through your existing `core/risk_gate.py`.

Research notes behind the design choices are kept outside this repository.

## What it does better than the viral jev-trader

| jev-trader (original) | Jevbot |
|---|---|
| No PnL published, no way to tell whether it works | Every signal is scored live: Brier skill, hit rate, calibration, and a non-overlapping net-of-cost trade test through `core.metrics.scorecard` |
| Feeds Jev raw price numbers | Converts numbers into plain words in code first. Jev's own docs say it "struggles with tasks that require numeric precision" |
| `choice` question with "buy" listed first | One yes/no (`noul`) question per horizon. Jev's docs note `choice` leans toward the first option |
| Prompt says "IOC market order", code posts limit orders | The prompt states exactly what happens |
| Requotes every 300 ms block (~$190–240/day gas live at today's MON price) | Trades only when the expected move beats the full round-trip cost |
| Position cap only | Your full risk gate: kill switch, 3% daily-loss breaker, stale-signal and stale-equity rejection, mandatory stop, 1% risk cap, fail-secure |
| Flips buy/sell freely (a reported copycat loss of ~$31.7k came from this; single source) | Hysteresis (enter ≥0.60, exit ≤0.45), cooldown, one position per symbol, time exit |
| Jev or nothing | Jev vs a locally trained model vs the original's momentum formula vs a coin flip. A signal that can't beat the coin flip has no edge |
| Live = paste a private key | Live needs 3 deliberate switches **plus** a proven-edge scorecard (`REQUIRE_EDGE_FOR_LIVE`) |
| Bot holds custody of a wallet key | Exchange key with trade-only scope. The bot only sells what it bought itself, and places an exchange-side stop after every entry |

## Signals (6) and guards (added 2026-10-07)

- **copy:** what Hyperliquid's 30 best consistent traders hold right now. The data is public and read-only. Market makers (over 100× monthly turnover), small accounts (under $100k) and one-month wonders are filtered out. It's scored forward like any other signal, so a leaderboard's hindsight can't flatter it.
- **ensemble:** combines the other signals, each weighted by its *measured* skill. Signals with zero or negative skill get zero weight. When none has skill, it stays silent.
- **news:** Jev judges every new headline from 5 free feeds (CoinDesk, Cointelegraph, Decrypt, Bitcoin Magazine, The Block): which coin it concerns, whether it moves the whole market, good news or bad news. The tone is relevance-weighted and fades with a 30-minute half-life. This is the job Jev's own docs say it's built for.
- **Edge decay:** a signal is benched as soon as its last 200 predictions lose skill, even if its lifetime record still looks good.
- **jev:** now also receives the top-trader positioning as words, because its docs say it improves when given context.
- **Calibration:** every entry uses what a signal's track record says its confidence is worth, not the number it prints. Entries need a positive expected value after costs.
- **Account guards:** 3 losses in a row → 1 h pause; 5% drawdown from the peak → kill switch; 20 trades a day; 50% total exposure; caps on spread, volatility shock and slippage. A fade exit needs 60 s held and 2 fades in a row. See [LESSONS.md](LESSONS.md).

## Historical backtest and 24/7 watchdog (added 2026-10-28)

- `python -m jevbot.backtest` downloads free Binance 1h/4h candles (3 years by default, cached in `data/history/`) and walk-forward tests 72 long-only candle rules per series: time-series momentum, breakouts, mean reversion and fractional-differencing trend, with and without a trend-efficiency filter, each under 4 stop/target/time exit settings. Results go to `data/backtest/report.md`.
- Honesty rules, each covered by `tests/test_backtest_jev.py`: features use past bars only; entry is at the next bar's open; if a candle touches both stop and target, the stop wins; a gap through the stop fills at the worse open; costs and TDS are applied; rules are chosen on the past year only and traded on the next 90 days; and every result is compared against random entry times and buy-and-hold.
- `run_forever.ps1` restarts the bot if it exits, backing off after repeated crashes, and keeps the PC awake only while its window is open. To stop it, create a file named `STOP_WATCHDOG`. Logs go to `logs/`.

## Allocation test (added 2026-10-28)

`python -m jevbot.allocate` tests rules for *how much* crypto to hold on 8 years of real daily data, taxed per sale on average cost basis (30% on realized gains, no loss set-off, everything liquidated at the end). Each rule is compared with passively holding the same average crypto share and never trading. Output goes to `data/backtest/allocation.md`. The only rule that held up across start dates and assets was the 200-day trend filter that steps down to 50%; see LESSONS 16–19.

## Daily paper allocation and tax ledger (added 2026-10-28)

The live bot now runs the backtest's most consistent allocation rule as a separate 1,000 USDT paper book: the 200-day trend filter on an equal-weight BTC+ETH index, 100% invested, stepping down to 50% after a close more than 6% below the average, back to 100% after a close more than 3% above. It acts once a day after the daily candle closes, replays any missed days, and is shown at the top of the dashboard.
- `data/jevbot/schedule_vda.csv`: every sale, FIFO lot by lot (asset, acquisition date, transfer date, quantity, cost, consideration, fees, gain, 31.2% tax, 1% TDS), for Schedule VDA in the ITR. Values are in USDT; convert to INR at the rate your CA specifies.
- `data/jevbot/alloc_events.jsonl`: each regime flip, with what the copy-traders held and what the news signal said that day, so their value as a filter can be measured.
- Settings: `ALLOC_ENABLED`, `ALLOC_SYMBOLS`, `ALLOC_CAPITAL` in `.env`.

## Why these choices

- **Binance spot.** It's FIU-registered and legal for Indian residents (futures and margin are geo-blocked for India). It has the deepest liquidity and free public data. Polymarket is blocked in India. Hyperliquid's status for Indian residents is unverified.
- **Jev, not Laya.** Laya is free and open source, but without fine-tuning it scores *below* simply always guessing the most common answer (0.362 vs 0.461), and it's GPU-oriented (see RESEARCH.md). Jev costs ~$0.00002 per call, and Vercel's free $5/month covers it.
- **Python.** It reuses your tested risk gate and scorecard. The decision horizon is minutes, so Python's speed is irrelevant: the bottleneck is costs, not latency.

## Step by step

### Step 1 — run it in paper mode (free, no accounts needed)
```bash
cd "C:\Users\naveen\Desktop\AI Trading\tradebot"
```
```bash
copy .env.example .env
```
```bash
python -m jevbot
```
Open http://127.0.0.1:8766. It starts immediately: 30 minutes of history load from Binance on startup. Stop it with Ctrl+C. Positions and scores persist across restarts in `data/jevbot/`.

### Step 2 — add Jev (you do this; Claude never handles keys)
1. Go to vercel.com, sign up, open **AI Gateway → API Keys**, create a key.
2. In `.env` set `JEV_PROVIDER=vercel` and `JEV_API_KEY=<your key>`.
3. Restart. The header shows Jev calls, latency and today's spend against the $0.16/day budget. The budget guard stops calls at the cap so the free allowance can't be overrun.

Alternative: OpenRouter (prepaid), `JEV_PROVIDER=openrouter`.

### Step 3 — collect evidence (the step everyone on X skips)
Leave it running. Watch the **Signal scoreboard**:
- **skill > 0** means the probabilities are better than guessing the base rate.
- **net % (no TDS)** shows whether the signal beats spread plus fees.
- **net % (TDS)** shows whether it survives India's 1% TDS.
- **verdict** reads `EDGE_PLAUSIBLE` only when the t-stat clears the bar corrected for all 36 hypotheses (4 signals × 3 horizons × 3 symbols).

Your contract asks for at least 4 weeks and 100+ trades in paper. Expect most cells to say `NO_EDGE_DEMONSTRATED`. That's the honest default, not a bug.

### Step 4 — testnet (real order plumbing, fake money)
1. Create a key at testnet.binance.vision (free).
2. Set `MODE=testnet`, `BINANCE_API_KEY`, `BINANCE_API_SECRET`.
3. This tests real order placement, stop orders and reconciliation. Prices still come from mainnet while fills happen on the thin testnet book, so testnet P&L means nothing. It's a plumbing test.

### Step 5 — live, minimum size (only after Step 3 shows an edge)
Live refuses to start with `APPLY_TDS=false` or `REQUIRE_EDGE_FOR_LIVE=false`, and scores recorded under different cost or entry settings don't count toward the edge test.
1. Create a binance.com API key with **spot trading only. Withdrawals OFF. Restrict to your IP.**
2. Set `MODE=live`, the keys, `LIVE_CONFIRM=I_ACCEPT_REAL_MONEY_RISK`.
3. Lower `MAX_NOTIONAL_PCT` and fund the account with only what you can lose.
4. With `REQUIRE_EDGE_FOR_LIVE=true` (default), the bot refuses every entry until the primary signal's scorecard says `EDGE_PLAUSIBLE`.

Your contract says production should run on a machine that stays on (a VPS), not a laptop that sleeps.

## Controls

| Control | How |
|---|---|
| Stop everything new | Create an empty file named `KILL_SWITCH` in `tradebot/`. Entries are rejected; exits and stops still work |
| Daily loss breaker | 3% of start-of-day equity trips the kill switch automatically |
| Paper reset | Delete `data/jevbot/paper_state.json` (scores live in `outcomes.jsonl`) |
| Audit trail | `data/jevbot/audit.jsonl`: every gate decision and fill |

## The honest numbers

With TDS on, a round trip costs about **1.2%** (0.1% fee × 2 + 1% TDS + spread). A typical 5-minute BTC move during the first live test was about **0.05%**. So with the default settings the bot will mostly print "costs exceed typical move" and not trade. That's correct behaviour: those trades would lose money. A strategy that pays in India must hold for days and target moves well above 1.2%, which is what your contract's "swing-only crypto" rule says. For that, set `HORIZONS_S=86400,259200` (1 and 3 days) and `TRADE_HORIZON_S=86400`. Entry times persist across restarts, so multi-day holds survive a reboot. Collecting 100 non-overlapping 1-day samples per symbol takes months, which is the real cost of honest evidence. Every horizon you add raises the significance bar.

Not verified: whether the TDS threshold exempts your volume, how Binance India reports TDS for API trades, and real Binance responses. Live order flow is tested only against a simulated exchange in the test suite (ownership, stop reconciliation, flatten-on-failed-stop, no double sell); the first real run must be testnet. A stop-limit can be skipped in a violent gap (limit is 1% under trigger), and paper stops always fill, so paper slightly flatters stops. Confirm tax treatment with a CA. This is software, not financial advice.

## Files
`config.py` settings and interlocks · `feed.py` Binance WebSocket · `features.py` numbers to words · `signals.py` Jev / ml / momentum / random · `evaluator.py` scoring · `policy.py` buy/sell/hold · `broker.py` paper + Binance · `engine.py` main loop · `dashboard.py` UI · tests in `tests/test_jevbot.py`.
