# Mira — Risk Review: Finn's Signal Detection Models
**Reviewer:** Mira, Trade Risk Officer
**Date:** 2026-03-28
**Scope:** signals/indicators.py, patterns.py, confluence.py, signal_engine.py, backtest.py
**Status:** AMBER — cleared for continued development, NOT cleared for live capital

---

## Executive Summary

Finn's models are structurally sound and correctly implement the core of Vera's strategy. The pattern logic, confluence scoring, and indicator calculations are all accurate. However, I identified **3 critical issues**, **5 high-priority issues**, and **5 medium-priority issues** that must be addressed before this engine touches live capital. The most serious: there is no portfolio-level exposure guard, no drawdown circuit breaker, and the Sharpe ratio in the backtest module is computed incorrectly for intraday trading.

These are fixable. None require architectural changes. I am flagging them now, as is my job.

---

## Findings

### CRITICAL — Must fix before any live trading

---

**CRIT-01: No portfolio-level exposure guard**
**File:** `signal_engine.py` — `generate_signal()` and `batch_scan()`

`generate_signal()` has no awareness of currently open positions. It will size every new signal at `risk_pct` (default 1%) regardless of how many positions are already open. Vera's strategy is explicit: **maximum total open risk = 3% at any time**.

Three simultaneous 1% positions is the limit. The current code could generate a fourth, fifth, or sixth signal at full size if called repeatedly. `batch_scan()` compounds this — it processes all instruments independently and returns all valid signals without checking aggregate exposure.

**Required fix:** Accept a `current_open_risk_pct` parameter. If `current_open_risk_pct + risk_pct > 0.03`, either reject the signal or reduce position size to fit within the 3% cap.

---

**CRIT-02: No drawdown circuit breaker**
**File:** `signal_engine.py` — `generate_signal()`

Vera's strategy (Section 8.3) mandates:
- Drawdown > 5% from equity high → reduce risk per trade to 0.5%
- Drawdown > 10% → stop trading entirely

Neither check exists anywhere in the code. The engine will generate full-size signals at 1% risk even after a 9% drawdown. This is exactly the scenario that destroys accounts — the engine accelerates into a losing streak rather than pulling back.

**Required fix:** Accept `peak_equity` and `current_equity` as parameters. Compute drawdown. Apply the tiered risk reduction logic before sizing the position. Return `None` (no signal) if drawdown > 10%.

---

**CRIT-03: Sharpe ratio annualisation is wrong for intraday R-series**
**File:** `backtest.py` — `_sharpe()`, line 92

```python
return float(np.mean(excess) / np.std(excess) * np.sqrt(252))
```

`np.sqrt(252)` annualises a Sharpe computed on **daily returns**. But `run_backtest()` is fed **per-trade R values** — not daily returns. On a 1m/15m strategy, there could be 2–4 trades per day. Using √252 as the annualisation factor on a per-trade series inflates the Sharpe by the square root of average daily trade frequency.

If there are on average 3 trades/day, the true annualisation factor should be `√(252 × 3) = √756 ≈ 27.5`, but `√252 ≈ 15.9` is being used. This understates the annualised Sharpe. Conversely, if trades per day vary, the distortion is inconsistent across backtests, making comparison between backtests unreliable.

**Required fix:** Either (a) convert R-series to a daily P&L series before computing Sharpe, or (b) accept a `trades_per_day` parameter and use `np.sqrt(252 * trades_per_day)` as the annualisation factor, or (c) report Sharpe as "per-trade" without annualisation and label it clearly.

---

### HIGH — Fix before scaling up or sharing with Vera/Remy

---

**HIGH-01: `_nearest_target_level` fallback can violate minimum R:R**
**File:** `signal_engine.py`, lines 111–115

```python
return min(candidates) if candidates else entry * 1.01
```

When no key levels exist beyond the entry price, Target 2 defaults to `entry * 1.01` (1% away). In a volatile 15m instrument, Target 1 (at 1.5R) may already be further than 1% away, making Target 2 **closer than Target 1**. The signal then has T2 < T1 for longs — which is nonsensical and would cause Remy to exit early at a worse price.

Additionally, Vera's rule (Section 7.2): "Do not take any trade where Target 1 cannot be reached before hitting a major opposing key level." This check is not enforced in code.

**Required fix:** After computing T1 and T2, validate `T2 > T1` for longs and `T2 < T1` for shorts. If not, either find the next valid key level or invalidate the signal with reason "No valid Target 2 beyond Target 1."

---

**HIGH-02: Stop distance has no minimum bound — division-by-near-zero risk**
**File:** `signal_engine.py`, lines 335–340, 366

The code checks `if stop_distance > 0` before dividing, catching exact zero. But it does not guard against a near-zero stop distance (e.g., 0.0001 on a $5000 instrument), which would produce a position size in the tens of thousands of units. On instruments with any meaningful position size, this would immediately breach every exposure limit.

The ATR check (`stop_distance > 1.5 * atr_15m`) does catch excessively **wide** stops, but there is no lower bound check for stops that are suspiciously **tight**.

**Required fix:** Add a minimum stop distance check: `if stop_distance < 0.1 * atr_15m_val: invalidate — "Stop distance implausibly tight (< 0.1 × ATR)"`.

---

**HIGH-03: `backtest.py` quality gate threshold inconsistency**
**File:** `backtest.py`, line 52

```python
if self.total_trades < 30:
    failures.append(f"Sample too small ({self.total_trades} trades — minimum 50 for live)")
```

The gate fires at **30 trades** but the message says **50 for live**. Vera's strategy (Section 10) explicitly states: "Do not judge this strategy on fewer than 50 live trades." The gate should fire at 50, not 30. A 30-trade backtest passing the quality gate would be misread as sufficient for live deployment.

**Required fix:** Change threshold from 30 to 50.

---

**HIGH-04: `walk_forward_validate` has no purge gap**
**File:** `backtest.py`, lines 237–257

Per Pax Brief 01 (Combinatorial Purged Cross-Validation): walk-forward validation requires a **purge gap** between the end of the training window and the start of the test window. Without it, the trailing indicators (EMA, ATR, Stochastic — all of which have lookback periods of 9–50 bars) computed on training data will "leak" into the test window, because the first bars of the test window depend on values calculated using training data.

The current implementation has train end at `train_end` and test start immediately at `train_end` — zero gap.

**Required fix:** Insert a purge gap of at least `max(50, lookback_period)` bars between `train_end` and the start of the OOS slice:
```python
purge = 50  # bars
oos_start = train_end + purge
oos_slice = v.iloc[oos_start:split_end]
```

---

**HIGH-05: `trend_bias()` can misclassify ranging markets as trending**
**File:** `indicators.py`, lines 188–191

```python
elif bull_ema and not bear_structure:
    return "bullish"
elif bear_ema and not bull_structure:
    return "bearish"
```

`not bear_structure` means the last 5 bars are **not a perfect sequence of lower highs and lower lows**. This is true even in a ranging or mildly choppy market. The consequence: any instrument where EMAs are momentarily ordered (9 > 21 > 50) but price is choppy will be classified as "bullish" and trigger signal generation.

Ranging markets are explicitly a no-trade zone in Vera's strategy. The current logic allows signals through in conditions that Vera specifically excluded.

**Required fix:** Require **both** EMA alignment AND market structure confirmation for a trend call. Return "ranging" unless both are simultaneously true:
```python
if bull_ema and bull_structure:
    return "bullish"
elif bear_ema and bear_structure:
    return "bearish"
else:
    return "ranging"
```

---

### MEDIUM — Address in next iteration

---

**MED-01: Stop is computed on `tail(4)` regardless of pattern candle count**
**File:** `signal_engine.py`, lines 332–338

The stop extreme is taken from `df_15m.tail(4)` for all patterns. A 3-candle Morning Star pattern needs its stop below the lowest of all 3 candles. A single-candle Hammer needs its stop below that one candle's wick. Using a blanket `tail(4)` means:
- For single-candle patterns: stop may be placed below 3 irrelevant prior candles, making it unnecessarily wide
- For 3-candle patterns: correct in most cases, but not guaranteed if `tail(4)` includes an earlier unrelated low

The `FinnSignal` already carries `pattern_15m` and `best_pattern["candles"]` — use them to select the correct lookback.

---

**MED-02: `FinnSignal.to_dict()` will fail JSON serialisation**
**File:** `signal_engine.py`, line 67

```python
def to_dict(self) -> dict:
    return self.__dict__
```

The `timestamp` field is a `datetime` object. `json.dumps()` will raise `TypeError: Object of type datetime is not JSON serializable`. When Remy or any downstream system tries to log or transmit this signal, serialisation will fail silently or crash.

**Required fix:** Override `to_dict()` to convert `datetime` to ISO string:
```python
def to_dict(self) -> dict:
    d = self.__dict__.copy()
    d["timestamp"] = self.timestamp.isoformat()
    return d
```

---

**MED-03: `batch_scan` silently swallows errors**
**File:** `signal_engine.py`, line 466

```python
except Exception as e:
    print(f"[WARN] {ticker}: signal generation failed — {e}")
```

Errors during signal generation are printed to stdout but not logged, not counted, and not surfaced to Mira's monitoring. In a live environment, a persistent failure for one instrument (e.g., stale data, missing columns) would be invisible in any log aggregator.

**Required fix:** At minimum, collect failed instruments and return them alongside valid signals. Better: raise the error or pass it to a dedicated error handler that Mira can monitor.

---

**MED-04: ATR warm-up period not guarded**
**File:** `indicators.py` — `atr()`, and `signal_engine.py` — `generate_signal()`

Wilder's ATR via EWM begins computing from bar 1, but the first ~14 values are unreliable (the smoothing hasn't converged). If `df_15m` has fewer than ~20 bars, `atr_15m_val` will be significantly understated, making the ATR-based stop maximum check (`stop_distance > 1.5 * atr_15m`) effectively disabled — nearly any stop will pass.

**Required fix:** Add a minimum bar count guard at the start of `generate_signal()`:
```python
if len(df_15m) < 30 or len(df_30m) < 20 or len(df_1m) < 50:
    return None  # insufficient data for reliable indicator values
```

---

**MED-05: `risk_pct` can exceed 1.5% with no guard**
**File:** `signal_engine.py`, `generate_signal()` signature

`risk_pct` is a free parameter. Vera's strategy (Section 8.2) states the maximum per-trade risk is 1.5% under any circumstances. There is nothing stopping a caller from passing `risk_pct=0.05` (5% risk), which would generate a position size 3× Vera's hard cap.

**Required fix:** Clamp `risk_pct` at the top of `generate_signal()`:
```python
risk_pct = min(risk_pct, 0.015)  # Vera's hard cap: 1.5% maximum
```

---

## Summary Table

| ID | Severity | File | Issue | Line(s) |
|---|---|---|---|---|
| CRIT-01 | CRITICAL | signal_engine.py | No portfolio-level exposure guard | generate_signal() |
| CRIT-02 | CRITICAL | signal_engine.py | No drawdown circuit breaker | generate_signal() |
| CRIT-03 | CRITICAL | backtest.py | Sharpe annualisation wrong for per-trade R series | 92 |
| HIGH-01 | HIGH | signal_engine.py | T2 fallback can be closer than T1 | 111–115 |
| HIGH-02 | HIGH | signal_engine.py | No minimum stop distance bound | 335–340, 366 |
| HIGH-03 | HIGH | backtest.py | Quality gate threshold fires at 30, should be 50 | 52 |
| HIGH-04 | HIGH | backtest.py | No purge gap in walk-forward validation | 237–257 |
| HIGH-05 | HIGH | indicators.py | `trend_bias()` misclassifies ranging as trending | 188–191 |
| MED-01 | MEDIUM | signal_engine.py | Stop lookback ignores pattern candle count | 332–338 |
| MED-02 | MEDIUM | signal_engine.py | `to_dict()` will fail JSON serialisation | 67 |
| MED-03 | MEDIUM | signal_engine.py | Silent error swallowing in batch_scan | 466 |
| MED-04 | MEDIUM | signal_engine.py / indicators.py | No ATR warm-up guard | generate_signal() |
| MED-05 | MEDIUM | signal_engine.py | `risk_pct` uncapped — can exceed Vera's 1.5% limit | generate_signal() |

---

## What Finn Got Right

To be clear: the majority of the risk framework is correctly implemented.

- The ATR-based stop calculation and `1.5 × ATR` maximum are correctly enforced
- The 9-check `_validate_signal()` function is solid and catches all of Vera's listed invalidation conditions
- The `1.5 × stop_distance` Target 1 calculation is correct
- The fixed-fractional `risk_dollars / stop_distance` position sizing is correctly implemented
- The confluence scorer correctly weights all 5 factors and enforces the ≥3 minimum
- All pattern detectors correctly implement the source material logic
- The signal invalidation flag (`invalidated=True`) is propagated through to the output — Remy will not execute an invalidated signal

The foundation is good. The gaps above are primarily around portfolio-level controls and a few edge cases. None require rewriting the engine — they are targeted additions.

---

## Mira's Clearance Decision

| Status | Condition |
|---|---|
| ✓ Cleared for paper trading | Now |
| ✗ NOT cleared for live capital | Until CRIT-01, CRIT-02, CRIT-03 are fixed |
| ✗ NOT cleared for scale-up | Until all HIGH items are also fixed |

I will re-review once the critical fixes are in. Finn knows where to find me.

— Mira
