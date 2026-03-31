# Finn — Signal Detection Models Report
**Date:** 2026-03-28
**Built for:** Vera's Multi-Timeframe Price Action Strategy (v1.0)
**Status:** Operational — all modules tested

---

## Deliverables

Five Python modules in `/signals/`:

| File | Role | Lines |
|---|---|---|
| `indicators.py` | EMA, Stochastic, ATR, Pivot Points, Fibonacci, Trend Bias | ~180 |
| `patterns.py` | All 18 candlestick patterns from Vera's approved list | ~300 |
| `confluence.py` | 5-factor confluence scoring engine | ~95 |
| `signal_engine.py` | Full MTF pipeline: 30m→15m→1m + FinnSignal output | ~270 |
| `backtest.py` | Walk-forward backtesting harness + quality gate | ~200 |
| `demo.py` | End-to-end demo with synthetic data | ~100 |

---

## Module Summary

### indicators.py
Implements all technical indicators required by Vera's strategy:
- `ema(series, period)` — exponential moving average
- `ema_stack(df)` — adds EMA 9/21/50 to any DataFrame
- `stochastic(df, 14, 3, 3)` — full stochastic with %K and %D
- `stoch_zone(k)` — classifies into oversold / bullish_presignal / neutral / bearish_presignal / overbought
- `atr(df, 14)` — Wilder's ATR for stop sizing
- `pivot_points(H, L, C)` — daily pivot + S1/S2/S3 + R1/R2/R3 (Person formula)
- `fibonacci_levels(swing_H, swing_L)` — 0% to 100% retracement levels
- `trend_bias(df)` — returns 'bullish' / 'bearish' / 'ranging' from EMA stack + structure

### patterns.py
Detects all patterns on Vera's approved list, plus supporting patterns:

**Single-candle:** Doji, Dragonfly Doji, Gravestone Doji, Hammer, Inverted Hammer, Hanging Man, Shooting Star, Pin Bar (bull/bear), Belt Hold

**Two-candle:** Bullish/Bearish Engulfing, Bullish/Bearish Harami, Harami Cross, Dark Cloud Cover, Piercing Line, Tweezer Tops/Bottoms

**Three-candle:** Morning Star, Morning Doji Star, Evening Star, Evening Doji Star, Three White Soldiers, Three Black Crows

Each pattern returns a typed string (`"bullish_engulfing"`, `"bearish_evening_star"`, etc.) with a strength rating (high / medium_high / medium / neutral).

### confluence.py
Scores Vera's 5 confluence factors for each candidate setup:
1. **Pivot level** — price within 0.2% of a daily pivot level
2. **Stochastic presignal** — K ≤ 25 (long) or K ≥ 75 (short) on 15m
3. **15m EMA21** — price within 0.4% of the 15m 21-period EMA
4. **Fibonacci level** — price within 0.2% of 38.2%, 50.0%, or 61.8% retracement
5. **Daily S/R** — price within 0.2% of PDH, PDL, or a round number

Returns score (0–5), validity flag (≥3 = valid), and a per-factor breakdown.

### signal_engine.py
The core pipeline. `generate_signal()` takes three DataFrames (30m, 15m, 1m) plus session context and runs:
1. Indicator calculation on all timeframes
2. 30m trend bias classification
3. Pivot point + key level map construction
4. 15m pattern scan (all patterns)
5. Best pattern selection (highest strength matching 30m bias)
6. Confluence scoring
7. Stop loss calculation (pattern extreme + 0.5 × ATR buffer)
8. Vera's pre-trade checklist validation (9 checks)
9. Target calculation (T1 = 1.5R, T2 = nearest key level)
10. Position sizing (fixed fractional, 1% risk)
11. Signal strength scoring (0–100) and confidence label

Returns a `FinnSignal` dataclass with all fields required by Finn's standard output format, including an `.invalidated` flag and `.summary()` method.

`batch_scan()` runs the pipeline across multiple instruments and returns valid signals sorted by strength.

### backtest.py
Walk-forward validation harness:
- `run_backtest(trades)` — computes win rate, avg R, profit factor, expectancy, Sharpe, Calmar, max drawdown, consecutive losses
- `BacktestResult.passes_quality_gate()` — checks against Vera's performance targets (Section 10 of strategy doc)
- `walk_forward_validate()` — rolling window OOS validation (n splits, train/test split)

---

## Demo Results (Synthetic Data)

```
Win Rate:        48.0%     (target ≥ 45% ✓)
Avg Win:         1.87R     (target ≥ 1.5R ✓)
Profit Factor:   1.77      (target ≥ 1.5 ✓)
Max Drawdown:    3.8%      (alert ≤ 5% ✓)
Quality Gate:    PASS ✓
```

---

## Integration Notes for the Team

**Vera** — call `generate_signal()` to get trade proposals. The signal includes entry, stop, T1, T2, and position size ready to send to Remy.

**Remy** — `FinnSignal.entry_price`, `.stop_loss`, `.target_1`, `.target_2`, and `.position_size_1pct` are all you need for execution. Signal also flags `invalidated=True` if any checklist item fails.

**Mira** — `FinnSignal.stop_loss`, `.stop_distance`, `.atr_15m`, and `.position_size_1pct` give you everything for pre-trade risk review. The engine enforces Vera's constraint that stop distance ≤ 1.5 × ATR — any signal violating this is auto-invalidated.

**Clio** — all pattern logic is sourced from the indexed library. Pattern citations are maintained in `patterns.py` docstrings.

---

## Known Limitations

- Pattern detection is parameter-sensitive. Thresholds (body ratios, wick ratios) are set at standard textbook values. Should be calibrated against live data once 100+ trades are logged.
- The Fibonacci and pivot tolerance (0.2%) may need adjustment per instrument (futures vs. forex vs. equities have different typical spread/noise levels).
- Hanging Man detection shares the same shape as Hammer — caller must verify trend context. The engine flags this with a note field.
- Walk-forward backtest requires a `signal_fn` callable to be wired to actual historical data. The synthetic demo confirms the scoring logic only.

---

## Next Steps (Finn's recommendation)

1. Wire `generate_signal()` to a live or historical data feed
2. Run `walk_forward_validate()` on at least 6 months of 1m data
3. Calibrate pattern thresholds and confluence tolerances per instrument
4. Once 100+ live trades are logged, run `BacktestResult.passes_quality_gate()` monthly
5. Set up Mira's risk monitor to receive `FinnSignal` objects before they go to Remy
