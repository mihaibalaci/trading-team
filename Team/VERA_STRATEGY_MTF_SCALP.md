# Multi-Timeframe Price Action Strategy
**Author:** Vera — Strategy & Portfolio Manager
**Date:** 2026-03-28
**Timeframes:** 30m (Trend) / 15m (Setup) / 1m (Entry)
**Knowledge sources:** Clio Book Index (7 books) + Pax Research Briefs 01–06
**Status:** Active — Version 1.0

---

## 1. Strategy Philosophy

This strategy applies top-down multi-timeframe analysis (as documented in *Candlestick Bible* and *Candlestick Trading Bible*) combined with pivot point target trading (Person, *Complete Guide*), candlestick pattern confirmation (all 7 library sources), and volatility-adjusted position sizing (Pax Brief 05).

The core logic:
- **30m defines the battlefield** — trend, structure, key levels
- **15m identifies the battle** — setup formation, pattern, confluence zone
- **1m times the strike** — precise entry on confirmation candle

We only trade with the 30m trend. We only enter on 15m setups at confluence zones. We only pull the trigger on a 1m signal candle. All three must align. No exceptions.

---

## 2. Required Indicators

| Indicator | Settings | Timeframe Applied | Source |
|---|---|---|---|
| EMA Fast | 9-period | All TFs | Trend filter |
| EMA Slow | 21-period | All TFs | Trend filter |
| EMA Trend | 50-period | 30m only | Primary trend bias |
| Stochastic | (14, 3, 3) | 15m + 1m | Overbought/oversold filter |
| ATR | 14-period | 15m | Stop loss sizing |
| Pivot Points | Daily | All TFs | Support/resistance targets |
| Volume | Raw | 15m + 1m | Confirmation |

**Setup requirement:** All indicators must be visible on your chart before taking any trade. No indicator = no trade.

---

## 3. Market Bias — The 30-Minute Timeframe

The 30m chart defines your directional bias for the session. Check this first, before looking at any lower timeframe.

### 3.1 Trend Identification

**Bullish bias (long-only mode):**
- Price is above the 50 EMA
- 9 EMA is above the 21 EMA
- Market structure shows higher highs and higher lows
- Recent candlestick patterns are bullish (Morning Star, Bullish Engulfing, Hammer at support)

**Bearish bias (short-only mode):**
- Price is below the 50 EMA
- 9 EMA is below the 21 EMA
- Market structure shows lower highs and lower lows
- Recent candlestick patterns are bearish (Evening Star, Bearish Engulfing, Shooting Star at resistance)

**No-trade zone (range / chop):**
- 9 EMA and 21 EMA are intertwined or flat
- Price is oscillating around the 50 EMA
- No clear higher highs/higher lows or lower highs/lower lows
- **Action: stand aside. Do not force a bias.**

### 3.2 Key Level Identification on 30m

Mark the following before the session opens:

1. **Daily Pivot Point** and S1, S2, R1, R2 levels (Person formula: `Pivot = (H + L + C) / 3`)
2. **Previous day's high and low** (strong psychological magnets)
3. **Round numbers** (00 and 50 levels on the instrument)
4. **30m swing highs/lows** from the last 3–5 sessions
5. **Weekly pivot** (if trading near open of week — strong magnet, see Person Ch.6)

These levels are your **target zones** — where price is likely to pause, reverse, or accelerate. The 30m analysis takes 5 minutes maximum. Do it before the session, not during.

---

## 4. Setup Identification — The 15-Minute Timeframe

Once the 30m bias is established and key levels are marked, drop to the 15m chart and wait for a setup to form **at a marked level**.

### 4.1 Confluence Requirement

A valid setup requires **at least 3 of the following 5 confluence factors:**

1. Price is at a marked 30m key level (pivot, swing high/low, round number, PDH/PDL)
2. Stochastic (14,3,3) is in the presignal zone: below 20 for longs, above 80 for shorts (Morris *Workbook*: presignal zone precedes technical signal)
3. Price is at or near the 21 EMA on the 15m chart
4. A Fibonacci retracement level (38.2%, 50%, or 61.8%) coincides with the price level
5. The level aligns with a daily S/R level from the pivot point calculation

**Minimum 3 confluences = proceed to pattern check. Fewer than 3 = skip the setup.**

### 4.2 Candlestick Pattern Requirement at the Confluence Zone

Only these patterns are valid setups on the 15m chart. They must form **at** the confluence zone, not in open space.

**Bullish Reversal Setups (in bullish 30m bias, at support zone):**

| Pattern | Strength | Source |
|---|---|---|
| Bullish Engulfing | High | All 7 sources |
| Morning Star | High | Sources 1,2,3,4,7 |
| Hammer | Medium-High | All 7 sources — requires confirmation candle |
| Inverted Hammer | Medium | Requires strong confirmation candle |
| Bullish Harami | Medium | Requires confirmation candle |
| Pin Bar (long lower wick, small body, short upper wick) | High | Sources 2,3 |
| Three White Soldiers | High | Sources 4,5 — strong momentum signal |

**Bearish Reversal Setups (in bearish 30m bias, at resistance zone):**

| Pattern | Strength | Source |
|---|---|---|
| Bearish Engulfing | High | All 7 sources |
| Evening Star | High | Sources 1,2,3,4,7 |
| Shooting Star | Medium-High | All 7 sources — requires confirmation |
| Hanging Man | Medium | Requires confirmation candle |
| Bearish Harami | Medium | Requires confirmation candle |
| Pin Bar (long upper wick, small body, short lower wick) | High | Sources 2,3 |
| Three Black Crows | High | Sources 4,5 — strong momentum signal |

**Doji rule:** A Doji at a confluence zone signals indecision. Do NOT trade the Doji itself. Wait for the confirmation candle. If the next candle is bullish and you are in bullish bias → valid long setup. If bearish and you are in bearish bias → valid short setup. (Morris *Workbook*, Sadekar *How to Make Money*)

### 4.3 Stochastic Alignment (15m)

For LONG setups: Stochastic must be below 25 when the pattern forms, OR it must be crossing upward from below 20. Do not enter longs when stochastic is above 50.

For SHORT setups: Stochastic must be above 75 when the pattern forms, OR it must be crossing downward from above 80. Do not enter shorts when stochastic is below 50.

*(Filtering logic from Wagner & Matheny Ch. 15, and Morris Workbook Ch. 5 — presignal zone methodology)*

**When the 15m setup is confirmed (pattern + confluence + stochastic), drop to the 1m chart.**

---

## 5. Entry Execution — The 1-Minute Timeframe

The 1m chart is for timing only. The decision has already been made on the 15m. You are looking for one thing: the entry trigger candle.

### 5.1 Long Entry Trigger

All of the following must be true:
1. The 15m setup is confirmed (bullish pattern at confluence, stochastic aligned)
2. On the 1m chart, price forms a bullish candle after a pullback or consolidation
3. The 1m Stochastic is not above 80 (avoid entering into already-extended 1m momentum)
4. The 1m 9 EMA has turned upward or price is crossing above the 1m 21 EMA

**Entry:** Enter a long position at the close of the trigger candle, or on the open of the next 1m candle.

**Do not use market orders in the entry.** Use a limit order just above the high of the trigger candle, or a stop-limit order. (Remy's brief: arrival price slippage is minimized with limit orders on liquid instruments.)

### 5.2 Short Entry Trigger

All of the following must be true:
1. The 15m setup is confirmed (bearish pattern at confluence, stochastic aligned)
2. On the 1m chart, price forms a bearish candle after a pullback or consolidation
3. The 1m Stochastic is not below 20
4. The 1m 9 EMA has turned downward or price is crossing below the 1m 21 EMA

**Entry:** Enter a short position at the close of the trigger candle, or on the open of the next 1m candle.

### 5.3 Entry Invalidation

Cancel the entry (do not enter) if:
- Price has already moved more than 1.0 × ATR(14) on the 15m chart from the confluence zone before the 1m trigger forms — you have missed the entry
- A new opposing 15m candlestick pattern has formed at the zone before your 1m trigger appears
- The 30m bias has changed (e.g., a large 30m candle has reversed the trend structure)

---

## 6. Stop Loss Placement

Stop loss is non-negotiable. It is placed before entry is executed. No stop = no trade.

### 6.1 Stop Loss Rules

**For LONG trades:**
Place the stop loss below the lowest point of the 15m setup pattern (below the wick of the Hammer, below the lowest candle of the Morning Star, below the Pin Bar wick), **plus 0.5 × ATR(14) buffer.**

```
Stop = Setup Pattern Low - (0.5 × ATR14_15m)
```

The ATR buffer prevents being stopped out by normal noise while still respecting the technical level. *(Sadekar: stop loss must be defined per pattern; ATR buffer from Pax Brief 05)*

**For SHORT trades:**
```
Stop = Setup Pattern High + (0.5 × ATR14_15m)
```

### 6.2 Maximum Stop Size

If the calculated stop distance exceeds **1.5 × ATR(14) on the 15m**, the setup has too much risk relative to the timeframe. Do not take the trade. The zone was too wide or the pattern was not clean enough.

### 6.3 Stop Loss Management

- **Never move the stop against you** (wider). Once placed, it stays or moves in your favour.
- After price moves 1:1 (first target hit), move stop to breakeven.
- After price hits the second target, trail the stop behind the 9 EMA on the 1m chart.

---

## 7. Take Profit Targets

Use a tiered exit structure. Never exit 100% at once on the first target — let part of the position run.

### 7.1 Target Levels

**Target 1 — 1:1.5 Risk/Reward (50% of position)**
- Distance = 1.5 × stop distance from entry
- Exit 50% of the position here
- Move stop to breakeven on remaining position

**Target 2 — Next Key Level (30% of position)**
- The nearest marked 30m key level (pivot S1/R1, swing high/low, round number) in the direction of the trade
- Exit 30% of the position here

**Target 3 — Trail the runner (20% of position)**
- Remaining 20% is the "runner"
- Trail stop behind the 1m 9 EMA
- Exit when 1m 9 EMA is crossed by price, or when a clear opposing 1m candlestick pattern appears
- Maximum hold time for the runner: until the 30m session context changes (30m reversal pattern or 30m EMA crossover)

### 7.2 Minimum Acceptable R:R

**Do not take any trade where Target 1 cannot be reached before hitting a major opposing key level.**

If the nearest resistance (for longs) or support (for shorts) is closer than 1.5× your stop distance, the trade does not have sufficient room. Skip it and wait for a better setup.

---

## 8. Position Sizing

Position sizing uses fixed fractional risk management, with ATR-based stop distance and Kelly-informed maximum exposure. *(Sources: Pax Brief 02 — Kelly & Fixed Fractional; Pax Brief 05 — ATR sizing)*

### 8.1 Core Formula

```
Risk per trade = Account Equity × 1%

Stop Distance (in price) = |Entry Price - Stop Loss Price|

Position Size = Risk per Trade / Stop Distance
```

**Example:**
- Account: $100,000
- Risk: 1% = $1,000
- Entry: $4,520.00, Stop: $4,508.00 → Stop distance = $12.00
- Position size = $1,000 / $12.00 = 83 units

### 8.2 Risk Per Trade Limits

| Conviction Level | Risk % | When |
|---|---|---|
| Standard setup (3 confluences) | 0.75% | Default |
| High-conviction setup (4–5 confluences) | 1.0% | Strong alignment |
| Maximum per trade | 1.5% | Never exceed — requires 5 confluences AND trend is very strong |

**Do not use fractional Kelly above 0.25 Kelly on any single trade.** The Kelly formula requires accurate edge estimation. Until 100+ live trades are logged, assume edge is lower than backtest suggests.

### 8.3 Portfolio-Level Risk Limits

- Maximum open risk at any time: **3% of equity** (across all open positions)
- Maximum positions open simultaneously: **3** (on correlated instruments — treat as 1 risk unit)
- If in drawdown > 5% from equity high: **reduce risk per trade to 0.5%** until flat
- If in drawdown > 10%: **stop trading and review**. Do not average down into drawdown.

*(Drawdown circuit breaker logic from Pax Brief 03)*

---

## 9. Trade Management Rules

### 9.1 Time-Based Rules

- **Do not enter trades in the first 5 minutes of a new session** (spread is wide, volatility is artificial)
- **Avoid entries in the 10 minutes before a major economic release** (volatility is unpredictable, slippage is high)
- **The 1m chart becomes less reliable after 3+ hours of continuous trading** — fatigue increases bad entries; take breaks
- **Person's Friday 10:30 rule:** On Fridays at approximately 10:30 AM (market time), short-term momentum frequently reverses as weekly positions are adjusted. Be cautious about entries in the 15 minutes around this window.

### 9.2 Session Rules

- **Pre-session (15 min before open):** Mark key levels on 30m. Identify daily pivot levels. Do not trade yet.
- **Opening 30 minutes:** Observe only. Let the market establish initial direction. Do not chase the first move.
- **Core session:** Look for setups. This is the primary trading window.
- **Into close (last 30 min):** Close all day-trade positions unless the runner is clearly trending and stop is at breakeven. Do not hold intraday positions overnight without a clear structural reason.

### 9.3 Setup Frequency Expectations

This is a **quality-over-quantity** strategy. Expect 1–4 valid setups per session on a liquid instrument. On some sessions: zero valid setups. That is normal and correct.

**Signs you are forcing trades (warning):**
- You are on your 5th trade of the session
- You are reducing confluence requirements to justify an entry
- Your stop is wider than 1.5× ATR
- You entered without a confirmed 15m pattern
- You are trading against the 30m trend "because it looks like it's turning"

If you observe any of these signs: stop trading for the session. Come back tomorrow.

---

## 10. Strategy Performance Metrics

Track every trade. Minimum metrics required:

| Metric | Target | Alert Level |
|---|---|---|
| Win rate | ≥ 45% | < 35% |
| Average R:R | ≥ 1.8:1 | < 1.5:1 |
| Profit factor | ≥ 1.5 | < 1.2 |
| Max consecutive losses | Monitor | 5+ in a row → pause |
| Monthly max drawdown | ≤ 5% | > 8% → reduce size |
| Sharpe Ratio (rolling 3m) | ≥ 1.0 | < 0.5 → review strategy |

**Minimum sample:** Do not judge this strategy on fewer than 50 live trades. Statistical significance requires sample size.

---

## 11. Instruments & Applicability

This strategy works on any liquid instrument with clean price action and tight spreads. Tested conceptual compatibility (via book examples):

**Best fit:**
- Equity index futures (ES, NQ, DAX, FTSE) — high liquidity, clean technicals, active during core sessions
- Major forex pairs (EUR/USD, GBP/USD, USD/JPY) — 24h liquidity, low spread
- Large-cap individual equities — during core session hours only

**Avoid:**
- Instruments with wide spreads (> 0.05% of price)
- Low-volume instruments where 1m candles have gaps
- Instruments ahead of earnings or binary events (treat as no-trade zones)
- Crypto (high noise on 1m; gaps common; microstructure issues flagged in Pax Brief 04)

---

## 12. Quick Reference — Trade Checklist

Use this before every trade entry:

```
PRE-TRADE CHECKLIST
═══════════════════
30M ANALYSIS
[ ] 30m trend direction confirmed (EMA stack + structure)
[ ] Key levels marked (pivot, PDH/PDL, swing H/L, round numbers)
[ ] No major economic release in next 15 minutes

15M SETUP
[ ] Pattern formed AT a marked key level (not in open space)
[ ] Pattern identified from approved list
[ ] Minimum 3 confluence factors confirmed
[ ] Stochastic below 25 (long) or above 75 (short) on 15m
[ ] Volume at or above average on the signal candle

1M ENTRY
[ ] Trigger candle confirmed (bullish/bearish, per direction)
[ ] 1m stochastic not extended against direction (< 80 for long, > 20 for short)
[ ] 1m EMA alignment supports direction

RISK MANAGEMENT
[ ] Stop loss level calculated (pattern extreme + 0.5 ATR buffer)
[ ] Stop distance ≤ 1.5 × ATR14 on 15m
[ ] Position size calculated at 1% risk (or conviction level)
[ ] Total open risk after this trade ≤ 3% of equity
[ ] Target 1 (1.5R) is clear of any opposing key level
[ ] Entry order type: limit or stop-limit (not market)

READY TO ENTER: All boxes checked ✓
NOT READY: Any box unchecked → DO NOT ENTER
```

---

## 13. Knowledge Sources Used

| Section | Source |
|---|---|
| Top-down multi-TF structure | *Candlestick Bible* (Homa) — Time Frames & Top Down Analysis |
| Candlestick patterns | All 7 library books (see Clio index) |
| Pattern confluence | *Candlestick Trading Bible* (Homm) — Trading with Confluence |
| Stochastic presignal filtering | Morris *Workbook* Ch.5; Wagner & Matheny Ch.15; Sadekar throughout |
| Pivot point targets | Person *Complete Guide* Ch.6 — P3T Technique |
| ATR-based stop sizing | Pax Brief 05 — Volatility Strategies |
| Fixed fractional sizing | Pax Brief 02 — Portfolio Construction & Position Sizing |
| Kelly cap (¼ Kelly) | Pax Brief 02 — Kelly Criterion |
| Drawdown circuit breakers | Pax Brief 03 — Risk Models |
| Execution / order type rules | Pax Brief 04 — Market Microstructure |
| Friday 10:30 rule | Person *Complete Guide* Ch.12 |
| Verify-Verify-Verify | Person *Complete Guide* Ch.7 |
| Single-day candle caution | Morris *Workbook* Ch.1 |
