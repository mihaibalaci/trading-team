# Remy — Execution Layer Review
**Author:** Remy, Trade Execution Specialist
**Date:** 2026-03-28
**Scope:** `signals/execution.py`, `signals/execution_demo.py`
**Status:** GREEN — cleared for paper trading. Conditional items noted before live.

---

## What I Was Asked to Do

Review Finn's signal handoff and build the execution layer. Specifically: receive a `FinnSignal`, validate it for freshness and drift, enforce Vera's time rules, construct the correct order types for each leg, manage the full trade lifecycle through tiered exits, and produce a TCA report at close.

I've done that. This document covers the design decisions, what the demo proved, what I'm not satisfied with, and what needs to happen before this touches live capital.

---

## Design Decisions

### 1. Order Type Selection

Vera's strategy (Section 5.1) is explicit: no market orders for entry. I've enforced this at the code level — `OrderFactory.entry_order()` produces STOP-LIMIT only, and `emergency_close()` is the only method that produces MARKET. It's named accordingly and cannot be confused with an entry path.

**Entry:** STOP-LIMIT — trigger at the signal's `entry_price`, limit set at `entry_price + 0.10 × ATR`. The buffer improves fill probability on fast moves without creating uncapped slippage risk. On EUR/USD at 25-pip ATR, this is a 2.5-pip fill buffer. Tight but workable.

**Stop:** STOP-MARKET — not STOP-LIMIT. Pax Brief 04 makes the case clearly: stop-limit orders can fail to fill on gaps. A protective stop that fails to fill is worse than slippage. STOP-MARKET takes the fill.

**Targets (T1/T2):** LIMIT — no urgency on exits, price improvement is available, no gap risk on the profit side. Let the market come to us.

**Runner:** STOP-MARKET trailing stop, rebuilt each bar from 1m EMA9 minus 0.25 × ATR buffer (Vera Section 7.1). The 0.25 ATR buffer prevents stop hunts on normal EMA9 wicks. Only ever moves in the profit direction — never widens.

---

### 2. Signal Handoff Validation

Two checks before any order is submitted:

**Staleness:** Signal age > 180 seconds → reject. On a 1m strategy, a 3-bar-old signal has already formed 3 new candles. The setup that generated it may no longer exist.

**Price drift:** Price moved > 1.0 × ATR from `entry_price` → reject. Vera's language (Section 5.3): "moved more than 1.0 × ATR from confluence zone = missed entry." I'm enforcing this literally.

These run *before* any order is placed. They are separate from Mira's pre-trade risk checks (which run at signal generation). Remy's checks are about execution-time reality: is the signal still actionable right now?

---

### 3. Tiered Exit Architecture

Vera's split: 50% at T1 (1.5R) / 30% at T2 (key level) / 20% runner (trail 1m EMA9).

All three legs are submitted as separate orders at entry time (T1 and T2 as LIMIT orders). The runner stop is created dynamically after T2 fills. This means:

- T1 and T2 are working orders from the moment entry fills — no latency on the exit trigger
- Stop moves to breakeven automatically when T1 fills (Vera Section 6.3)
- Runner trails without human intervention — rebuilds on every 1m bar

The lot split rounds down on T1 and T2. The remainder (after rounding) goes to the runner. On large sizes this is immaterial; on small accounts with small position sizes, rounding can assign 0 units to the runner. The engine catches this: if `total_qty <= 0`, the signal is rejected.

---

### 4. Session and Time Guards

`SessionGuard` enforces three blocks from Vera's Section 9.1:

- **Opening 5-minute block** — first 5 minutes after session open are blocked
- **Pre-news 10-minute block** — configurable news times; blocks 10 minutes before each
- **Friday 10:30 caution window** — Person (Ch.12) Friday momentum reversal window; blocks 10:25–10:35

The caller supplies the session times and news schedule. The engine does not hardcode a timezone — that's the caller's responsibility. Wrong timezone = wrong blocks. Noted in the open items below.

---

### 5. Market Impact Estimation

Square Root Model from Pax Brief 04: `MI = σ × η × √(Q / V_daily)`.

For the demo trade (333,333 units vs. EUR/USD ADV of ~5bn): estimated impact was **0.049 bps**. Negligible, as expected — EUR/USD is the most liquid instrument on earth. The model matters more for equities with lower ADV. I've kept it in for completeness and flagged the >5% ADV warning in `accept()`.

---

## Demo Results

Single EUR/USD LONG trade, 9 simulated 1m bars, all checks passed.

```
──────────────────────────────────────────────────────────
  REMY EXECUTION REPORT — EUR/USD  |  ▲ LONG
──────────────────────────────────────────────────────────
  Trade ID:         [uuid]
  Status:           CLOSED
  Method:           STOP-LIMIT entry / STOP-MARKET stop / LIMIT targets
  ─────────────────────────────────────────────────────
  Target Size:      333,333 units
  Filled Size:      333,333 units
  Entry Fill:       1.0852
  Avg Exit Price:   1.0907
  Arrival Price:    1.0848
  Slippage:         -18.52 bps (favorable — limit fills gave price improvement)
  ─────────────────────────────────────────────────────
  Realized P&L:     +1.83R
  Est. Total Cost:  $671.76
  Hold Duration:    7.0 min
  ─────────────────────────────────────────────────────
  Fills (4):
    [entry       ] buy  333,333 @ 1.0852   slip = +3.9 bps
    [target_1    ] sell 166,666 @ 1.0895   slip = -39.4 bps (limit improvement)
    [target_2    ] sell 100,000 @ 1.0930   slip = -71.6 bps (limit improvement)
    [runner_stop ] sell  66,667 @ 1.0903   slip = +1.1 bps
──────────────────────────────────────────────────────────
```

**Lifecycle milestones verified:**
| Bar | Event | Price | Note |
|---|---|---|---|
| 1 | Entry pending | 1.0848 | Below stop-limit trigger — no fill |
| 2 | Entry fills | 1.0852 | Trigger hit, limit within buffer |
| 3–4 | Active | 1.0860–1.0875 | Stop monitoring active |
| 5 | T1 fills | 1.0895 | 50% exited at 1.43R; stop → breakeven |
| 6 | Runner active | 1.0905 | Trailing stop now managing 20% |
| 7 | T2 fills | 1.0930 | 30% exited at 2.59R; runner trailing EMA9 |
| 8 | Pullback | 1.0918 | Stop holds — not lowered on pullback |
| 9 | Runner stop hit | 1.0904 | 20% closed at 1.68R; trade closed |

---

## What I'm Not Satisfied With

These are not blockers for paper trading. They are open items before live.

---

**EXEC-01 — No timezone enforcement on SessionGuard**

`SessionGuard` takes a `session_open` and `session_close` as naive `datetime.time` objects. It compares them against `datetime.now()` — also naive. If the machine running this is not in the instrument's session timezone, the blocks fire at the wrong times. A session block that fires at the wrong hour is worse than no block.

**Required before live:** Accept a `pytz` timezone argument. Localize `datetime.now()` to the session timezone before comparing. Default to `America/New_York` for US equity/forex sessions.

---

**EXEC-02 — Stop-limit entry can fail to fill on fast moves (by design, but must be communicated)**

The stop-limit entry has a fill buffer of `0.10 × ATR`. On a fast breakout bar — say EUR/USD moves 40 pips in a single 1m candle — the limit price may be below the bar's close and the order will sit in WORKING status without filling. The current code handles this correctly (stays PENDING_ENTRY, retries next bar), but the 5-minute entry expiry means repeated fast bars will eventually expire the order.

This is correct behavior — Vera's strategy does not chase missed entries. But the caller needs to know that a valid signal can be accepted by the engine and then silently expire without a trade. The expiry is logged, but there is no explicit callback or notification to Vera.

**Required before live:** Surface expired entries clearly. At minimum, flag `trade.status == TradeStatus.CANCELLED` at the end of any bar loop and route it to Vera's signal queue for review.

---

**EXEC-03 — No partial fill handling**

`Order.simulate_fill()` fills the entire quantity in one shot. Real broker fills can be partial — particularly on T1 and T2 limit orders near key levels where liquidity competes. The current model marks FILLED with the full quantity or not at all.

For paper trading this is fine — the simulation is clean. For live: partial fills on exits leave open exposure that the engine doesn't account for. If T1 fills 80,000 of 166,666 units, the stop move to breakeven should cover only the proportion of the position that has been hedged, not the full original stop.

**Required before live:** Extend `Order` to track `fill_qty` independently of `quantity`. Update `TradeRecord.open_qty` by actual fill quantity. Defer the stop-to-breakeven move until T1 is fully filled or after a configurable partial fill threshold (e.g., ≥90%).

---

**EXEC-04 — Runner stop is rebuilt every bar (cancel + replace)**

The current trailing stop implementation cancels the previous STOP-MARKET order and submits a new one on every 1m bar after T2. In a paper/simulation environment this is fine — orders are local objects. On a live broker connection, this would generate a cancel-and-replace API call every 60 seconds, which:
- Adds latency and API call volume
- Creates a brief window between cancel and replace where the runner has no stop protection
- May trigger rate limits on some brokers

**Required before live:** Either (a) use a trailing stop order type if the broker supports it natively, or (b) only update the stop when EMA9 has moved by at least `0.5 × ATR` since the last update — reduces noise orders without meaningfully reducing stop tracking quality.

---

**EXEC-05 — Commission model is currently zero**

`commission_per_unit` defaults to 0.0. The demo ran with no commission. Real execution costs money.

For EUR/USD retail forex, typical commission is $0 (spread-based) but institutional may charge $2–$7 per million. For equities, $0.005 per share is typical. The engine accepts the parameter but callers need to pass the right value per instrument.

**Required before live:** Vera or Mira to define per-instrument commission schedule and pass it at engine instantiation. Should be part of the instrument configuration, not a hardcoded constant.

---

## Integration Notes for Finn and Vera

The handoff is clean. `FinnSignal` carries everything the engine needs: `entry_price`, `stop_loss`, `stop_distance`, `target_1`, `target_2`, `atr_15m`, `position_size_1pct`, `direction`, `instrument`, `timestamp`, `invalidated`.

One addition I made to `FinnSignal` during integration: added `direction_sign` as a property (`+1` long, `-1` short). The engine uses it in R calculation. This is a computed property with no storage cost — Finn doesn't need to do anything.

The full engine call from Vera's side is four lines:

```python
engine = ExecutionEngine(signal, session_guard, commission_per_unit=0.65)
accepted, reason = engine.accept(current_price)
for bar in price_feed:
    engine.tick(bar.close, bar.ema9_1m)
report = engine.get_report()
```

Everything else — stop management, breakeven moves, runner trailing, exit sequencing — is internal.

---

## Clearance Decision

| Status | Condition |
|---|---|
| ✓ Cleared for paper trading | Now |
| ✗ NOT cleared for live capital | Until EXEC-01 (timezone) and EXEC-03 (partial fills) are resolved |
| ✗ NOT cleared for live at scale | Until EXEC-04 (trailing stop rebuild) is addressed for broker API limits |

EXEC-02 and EXEC-05 are operational hygiene items — they don't break the engine but they will cause confusion if not addressed before live deployment.

When Mira is ready to re-review, I'll want her eyes on EXEC-03 specifically. A partial fill on a stop-exit where the engine hasn't accounted for unhedged size is exactly the kind of gap that turns into a larger loss than expected.

— Remy
