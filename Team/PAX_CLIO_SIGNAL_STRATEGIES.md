# Signal Strategies Catalogue
**Authors:** Clio (Knowledge Manager) & Pax (Senior Researcher)
**Date:** 2026-04-01
**Sources:** 7 indexed trading books + 6 Pax research briefs
**Status:** Ready for Vera's review → Finn's implementation

---

## Overview

We've synthesized the full library (7 candlestick/technical books) and all 6 research briefs into **6 distinct signal strategies**. Each strategy is designed for a different market condition, timeframe, and edge type. They can run concurrently — Mira's portfolio-level risk caps ensure total exposure stays within bounds.

Strategy 1 (MTF Price Action) already exists. Strategies 2–6 are new.

---

## Strategy 1 — MTF Price Action Scalp (EXISTING)
**Edge:** Candlestick patterns at multi-factor confluence zones
**Timeframes:** 30m trend / 15m setup / 1m entry
**Hold time:** 1–10 minutes
**Sources:** All 7 books, Pax Briefs 01–05

Already implemented in `signal_engine.py`. See `VERA_STRATEGY_MTF_SCALP.md` for full spec.

---

## Strategy 2 — Mean Reversion Bands
**Edge:** Price reverts to mean after extreme deviations from Bollinger Bands + EMA
**Timeframes:** 15m trend bias / 5m signal / 1m entry
**Hold time:** 5–30 minutes
**Asset classes:** Stocks, Forex (liquid pairs only)

### Signal Logic
1. **15m:** Compute 20-period Bollinger Bands (2σ) and EMA 21
2. **5m:** Price touches or pierces the outer Bollinger Band
3. **5m:** Stochastic (14,3,3) is in extreme zone (< 15 for longs, > 85 for shorts)
4. **5m:** A reversal candlestick pattern forms at the band (Hammer, Engulfing, Pin Bar, Morning/Evening Star)
5. **1m:** Confirmation candle in the reversal direction + 1m EMA9 turns

### Entry Rules
- LONG: Price at lower Bollinger Band + oversold stochastic + bullish reversal pattern
- SHORT: Price at upper Bollinger Band + overbought stochastic + bearish reversal pattern
- Entry via limit order at pattern close

### Exit Rules
- Target 1 (60%): Middle Bollinger Band (20 EMA) — the "mean"
- Target 2 (40%): Opposite Bollinger Band or 1.5R, whichever is closer
- Stop: Beyond the pattern extreme + 0.3 × ATR buffer (tighter than MTF Scalp — mean reversion has tighter stops)

### Filters
- Do NOT trade if 15m trend is strongly trending (EMA 9/21/50 fully stacked) — mean reversion fails in trends
- Minimum Bollinger Band width > 1.5 × ATR (bands must be expanded, not squeezed)
- Volume on signal candle must be above 5m average

### Risk Parameters
- Risk per trade: 0.75% (slightly lower — mean reversion has lower win rate but higher R:R)
- Min signal strength: 50/100
- Min confluence: 2/5 (band + stochastic is the core; pattern is the trigger)

### Sources
- Wagner & Matheny: Candlesticks + Bollinger Bands combination (Ch. on computer filtering)
- Pax Brief 01: Mean reversion archetype, z-score entry logic
- Morris Workbook: Stochastic presignal zone filtering
- Pax Brief 05: ATR-based stop sizing


---

## Strategy 3 — Pivot Point Bounce
**Edge:** Price reacts predictably at daily pivot levels — the "magnet effect"
**Timeframes:** 30m context / 15m setup / 5m entry
**Hold time:** 15–60 minutes
**Asset classes:** Stocks, Futures (ES, NQ)

### Signal Logic
1. **Pre-session:** Calculate daily pivot (P), S1, S2, R1, R2 from previous day H/L/C (Person formula)
2. **30m:** Determine session trend bias (EMA stack)
3. **15m:** Price approaches a pivot level (within 0.15% tolerance)
4. **15m:** Candlestick reversal pattern forms AT the pivot level
5. **15m:** Stochastic confirms (< 30 for bounce off support, > 70 for rejection at resistance)
6. **5m:** Entry trigger — confirmation candle + EMA9 alignment

### Entry Rules
- LONG at S1/S2/Pivot (support bounce): Bullish pattern + stochastic oversold
- SHORT at R1/R2/Pivot (resistance rejection): Bearish pattern + stochastic overbought
- With-trend trades only: long bounces in bullish bias, short rejections in bearish bias
- Exception: Pivot point itself can be traded both ways (it's the fulcrum)

### Exit Rules
- Target 1 (50%): Next pivot level in trade direction (e.g., long at S1 → target Pivot)
- Target 2 (30%): Second pivot level (e.g., long at S1 → target R1)
- Runner (20%): Trail behind 5m EMA9
- Stop: Below/above the pivot level + 0.5 × ATR buffer

### Filters
- Only trade during core session (9:35–15:30 ET for stocks)
- Skip if price has already bounced off this level today (first touch only)
- Skip if ATR is > 2× its 20-day average (too volatile for pivot trading)
- Person's Friday 10:30 rule: avoid entries 10:25–10:35 on Fridays

### Risk Parameters
- Risk per trade: 1.0%
- Min signal strength: 55/100
- Min confluence: 3/5 (pivot level + pattern + stochastic = core three)

### Sources
- Person, Complete Guide: P3T technique, pivot point formula, weekly magnets, Friday rule (Ch. 6, 7, 12)
- Sadekar: Stop loss per pattern type
- Pax Brief 04: Execution timing, avoid first 5 minutes

---

## Strategy 4 — Momentum Breakout
**Edge:** Strong directional moves after consolidation, confirmed by volume and pattern
**Timeframes:** 1h trend / 15m setup / 5m entry
**Hold time:** 30 minutes – 2 hours
**Asset classes:** Stocks (high-beta: TSLA, NVDA, META), Commodity ETFs

### Signal Logic
1. **1h:** Strong trend established (EMA 9 > 21 > 50 for longs, inverse for shorts)
2. **15m:** Price consolidates in a tight range (ATR contracts to < 0.7× its 20-period average)
3. **15m:** Three White Soldiers or Three Black Crows pattern forms, OR Bullish/Bearish Engulfing with volume > 1.5× average
4. **15m:** Breakout candle closes beyond the consolidation range
5. **5m:** Pullback to the breakout level (retest) + hold → entry

### Entry Rules
- LONG: Breakout above consolidation high + retest holds + volume confirms
- SHORT: Breakdown below consolidation low + retest holds + volume confirms
- Entry via stop-limit at the breakout level on the retest
- If no retest within 3 bars (15m), skip — the move is too fast

### Exit Rules
- Target 1 (40%): 1.5 × consolidation range projected from breakout
- Target 2 (40%): 2.5 × consolidation range (measured move)
- Runner (20%): Trail behind 15m EMA9
- Stop: Inside the consolidation range, at the midpoint + 0.3 × ATR buffer

### Filters
- Only trade with the 1h trend (no counter-trend breakouts)
- Minimum consolidation duration: 4 bars on 15m (1 hour of compression)
- Volume on breakout candle must be > 1.5× the 20-bar average
- Skip if VIX > 30 (breakouts in crisis are unreliable — Pax Brief 05)

### Risk Parameters
- Risk per trade: 1.0% (standard), 1.5% if 5/5 confluence
- Min signal strength: 60/100
- Min confluence: 3/5

### Sources
- Candlestick Trading Bible (Homm): Inside Bar breakout strategy
- Arul Pandi: Three White Soldiers / Three Black Crows as momentum signals
- Pax Brief 01: Momentum/trend following archetype
- Pax Brief 05: VIX regime filter, ATR contraction as setup indicator

---

## Strategy 5 — Volatility Squeeze Scalp
**Edge:** Explosive moves after Bollinger Band squeeze (low vol → high vol transition)
**Timeframes:** 15m setup / 5m trigger / 1m entry
**Hold time:** 2–15 minutes
**Asset classes:** Stocks (liquid large-caps), Forex majors

### Signal Logic
1. **15m:** Bollinger Band width (BBW) drops below its 20-period low — "squeeze" detected
2. **15m:** Keltner Channels (2 × ATR) are OUTSIDE the Bollinger Bands — confirms extreme compression
3. **5m:** First candle that closes outside the Bollinger Band after the squeeze = direction signal
4. **5m:** Volume on breakout candle > 2× average
5. **1m:** Entry on first pullback to the 1m EMA9 after the breakout candle

### Entry Rules
- Direction determined by which side of the band breaks first
- LONG: Breakout above upper Bollinger Band from squeeze
- SHORT: Breakdown below lower Bollinger Band from squeeze
- Entry via limit order at 1m EMA9 on the pullback

### Exit Rules
- Target 1 (50%): 1.0 × ATR from entry (quick scalp target)
- Target 2 (50%): 2.0 × ATR from entry
- Stop: Opposite side of the squeeze range + 0.25 × ATR
- Time stop: If neither target hit within 15 minutes, close at market

### Filters
- Squeeze must have lasted at least 6 bars on 15m (1.5 hours of compression)
- Skip if the squeeze breaks during first 10 minutes of session (noise)
- Skip if there's a major news event within 15 minutes (Vera's rule)
- Only trade the FIRST breakout from each squeeze (subsequent moves are less reliable)

### Risk Parameters
- Risk per trade: 0.75% (scalp — smaller size, tighter stops)
- Min signal strength: 45/100
- Min confluence: 2/5 (squeeze + volume is the core edge)

### Sources
- Wagner & Matheny: Bollinger Bands + candlestick filtering
- Pax Brief 05: Volatility regime detection, ATR as vol measure
- Pax Brief 01: Breakout strategies, vol expansion signals
- Morris Workbook: Pattern filtering methodology

---

## Strategy 6 — Fibonacci Confluence Reversal
**Edge:** High-probability reversals where Fibonacci retracements align with other S/R
**Timeframes:** 1h trend / 15m setup / 5m entry
**Hold time:** 20–90 minutes
**Asset classes:** All (stocks, forex, commodity ETFs)

### Signal Logic
1. **1h:** Identify the most recent significant swing (high to low or low to high, minimum 2% move)
2. **1h:** Calculate Fibonacci retracement levels (38.2%, 50%, 61.8%)
3. **15m:** Price retraces to a Fibonacci level that ALSO aligns with at least one of:
   - A daily pivot level (S1, S2, R1, R2)
   - A previous swing high/low
   - A round number (00 or 50 level)
   - The 15m EMA 21
4. **15m:** Reversal candlestick pattern forms at the Fibonacci confluence zone
5. **15m:** Stochastic confirms (< 30 for longs at support fib, > 70 for shorts at resistance fib)
6. **5m:** Entry trigger candle in the reversal direction

### Entry Rules
- LONG: Price at 38.2/50/61.8% retracement of an upswing + bullish pattern + additional S/R alignment
- SHORT: Price at 38.2/50/61.8% retracement of a downswing + bearish pattern + additional S/R alignment
- The 61.8% level is the strongest (golden ratio) — gets a strength bonus
- Entry via limit order at pattern close

### Exit Rules
- Target 1 (50%): Return to the 23.6% retracement level (partial retracement recovery)
- Target 2 (30%): Return to the swing origin (0% level — full recovery)
- Runner (20%): Trail behind 5m EMA9
- Stop: Beyond the 78.6% retracement + 0.5 × ATR buffer
- If price breaks 78.6%, the retracement thesis is invalidated — the trend has reversed

### Filters
- Only trade retracements in the direction of the larger trend (1h EMA stack)
- Minimum swing size: 2% price move (smaller swings produce unreliable fib levels)
- Skip the 38.2% level if the trend is weak (ranging on 1h) — only 50% and 61.8% in weak trends
- Require at least 2 additional S/R factors aligning with the fib level (not just fib alone)

### Risk Parameters
- Risk per trade: 1.0%
- Min signal strength: 55/100
- Min confluence: 4/5 (fib + pattern + stochastic + at least one more S/R factor)

### Sources
- Wagner & Matheny: Candlesticks + Fibonacci Retracement combination
- Candlestick Trading Bible (Homm): Pin Bar + Fibonacci strategy, Engulfing + Fibonacci
- Person, Complete Guide: Pivot points as confluence with Fibonacci
- Pax Brief 02: Position sizing at different conviction levels


---

## Strategy Comparison Matrix

| # | Strategy | Edge Type | Timeframe | Hold Time | Best Market | Win Rate Target | Avg R:R | Risk/Trade |
|---|----------|-----------|-----------|-----------|-------------|-----------------|---------|------------|
| 1 | MTF Price Action Scalp | Pattern + Confluence | 30m/15m/1m | 1–10 min | Trending | ≥ 45% | 1.8:1 | 1.0% |
| 2 | Mean Reversion Bands | Bollinger reversion | 15m/5m/1m | 5–30 min | Ranging | ≥ 40% | 2.0:1 | 0.75% |
| 3 | Pivot Point Bounce | S/R reaction at pivots | 30m/15m/5m | 15–60 min | Any (with trend) | ≥ 50% | 1.5:1 | 1.0% |
| 4 | Momentum Breakout | Vol expansion after squeeze | 1h/15m/5m | 30m–2h | Strong trend | ≥ 35% | 2.5:1 | 1.0% |
| 5 | Volatility Squeeze Scalp | BB squeeze breakout | 15m/5m/1m | 2–15 min | Transitioning | ≥ 42% | 1.8:1 | 0.75% |
| 6 | Fibonacci Confluence | Fib + multi-S/R reversal | 1h/15m/5m | 20–90 min | Trending (pullback) | ≥ 48% | 2.0:1 | 1.0% |

---

## Regime Allocation Guide

Different strategies perform in different market conditions. Pax recommends running this allocation:

| Market Regime | VIX Level | Best Strategies | Avoid |
|---------------|-----------|-----------------|-------|
| Low vol, trending | < 16 | 1 (MTF Scalp), 3 (Pivot), 6 (Fib) | 2 (Mean Rev — trends kill it) |
| Low vol, ranging | < 16 | 2 (Mean Rev), 3 (Pivot), 5 (Squeeze) | 4 (Breakout — no momentum) |
| Moderate vol, trending | 16–25 | 1, 4 (Breakout), 6 (Fib) | — |
| Moderate vol, ranging | 16–25 | 2, 3, 5 | 4 |
| High vol | 25–35 | 5 (Squeeze — vol transitions), 3 (Pivot — levels hold) | 2 (Mean Rev — extremes extend), 4 (false breakouts) |
| Crisis | > 35 | NONE — reduce to 50% size or flat | All — Mira's circuit breaker |

---

## Implementation Priority (Finn's Roadmap)

1. **Strategy 3 — Pivot Point Bounce** (easiest — pivot calculation already exists in `indicators.py`)
2. **Strategy 2 — Mean Reversion Bands** (Bollinger Bands need to be added to `indicators.py`)
3. **Strategy 6 — Fibonacci Confluence** (Fibonacci already exists; needs multi-S/R alignment scoring)
4. **Strategy 5 — Volatility Squeeze** (needs Keltner Channels + BB width indicator)
5. **Strategy 4 — Momentum Breakout** (needs consolidation detection + volume analysis)

---

## Next Steps

1. **Vera** — Review and approve/modify these strategies before Finn implements
2. **Finn** — Implement strategies 2–6 as additional signal models in `signal_engine.py`
3. **Mira** — Define portfolio-level constraints for running multiple strategies simultaneously (max total open risk across all strategies, correlation limits between concurrent positions)
4. **Remy** — No changes needed — all strategies output `FinnSignal` which Remy already handles
5. **Kai** — No changes needed — broker connector is strategy-agnostic

---

*— Clio & Pax*
