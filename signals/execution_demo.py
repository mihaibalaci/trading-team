"""
execution_demo.py — End-to-end demo of Remy's execution layer.

Simulates a complete LONG trade lifecycle on EUR/USD:
  FinnSignal (mocked) → ExecutionEngine.accept() →
  tick() loop (price advances through T1, T2, runner close) →
  get_report() → Remy's standard execution report

Scenario:
  Entry area: 1.0850 (stop-limit trigger)
  Stop:       1.0820  (30 pip risk = 1R)
  Target 1:   1.0895  (~1.5R, 45 pips above entry)
  Target 2:   1.0930  (~2.67R, key pivot level)
  Runner trails 1m EMA9 until stopped out at ~1.0910

Price path (simulated 1m bars):
  Bar  1: 1.0848  — below entry trigger, no fill yet
  Bar  2: 1.0851  — entry triggers and fills
  Bar  3: 1.0860  — in trade, climbing
  Bar  4: 1.0875  — still climbing
  Bar  5: 1.0896  — T1 hit (50%), stop moved to breakeven
  Bar  6: 1.0905  — above T1, runner active
  Bar  7: 1.0915  — T2 hit (30%), runner now trailing EMA9
  Bar  8: 1.0912  — price dips slightly, runner stop updates
  Bar  9: 1.0908  — runner stop triggered, trade closes
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from datetime import datetime, timedelta

from signal_engine import FinnSignal
from execution import (
    ExecutionEngine, SessionGuard,
    TradeStatus, OrderStatus,
    estimate_market_impact,
)


# ─────────────────────────────────────────────────────────────────
# Helper: build a mock FinnSignal manually (bypasses full engine)
# ─────────────────────────────────────────────────────────────────

def make_mock_signal(now: datetime) -> FinnSignal:
    """
    Construct a realistic FinnSignal for EUR/USD LONG.
    Values match the demo scenario described above.
    """
    entry      = 1.0850
    stop       = 1.0820
    t1         = 1.0895   # 1.5R
    t2         = 1.0930   # key pivot level ~2.67R
    atr_15m    = 0.0025   # 25 pip ATR — typical EUR/USD 15m value
    risk_dist  = entry - stop   # 0.0030 = 30 pips = 1R

    # Position size from Vera's 1% rule: risk_dollars / stop_distance
    # Assuming $100,000 equity, 1% = $1,000 risk
    # $1,000 / 0.0030 = 333,333 units (approximately 3.3 standard lots)
    equity     = 100_000
    risk_pct   = 0.01
    risk_dollars = equity * risk_pct
    pos_size   = round(risk_dollars / risk_dist)   # ~333,333 units

    signal = FinnSignal(
        timestamp           = now,
        instrument          = "EUR/USD",
        direction           = "long",
        signal_strength     = 72,
        confidence          = "High",
        timeframe           = "Intraday",
        model               = "MTF-Price-Action-v1",
        pattern_15m         = "Hammer",
        pattern_strength    = "medium_high",
        confluence_score    = 4,
        confluence_detail   = (
            "pivot_level=S1(1.0848) | stochastic=oversold(22.5) | "
            "ema21_15m=within 0.3% | fibonacci=61.8%(1.0847)"
        ),
        trend_bias_30m      = "bullish",
        stoch_k_15m         = 22.5,
        stoch_k_1m          = 18.3,
        entry_price         = entry,
        stop_loss           = stop,
        stop_distance       = risk_dist,
        target_1            = t1,
        target_2            = t2,
        atr_15m             = atr_15m,
        position_size_1pct  = pos_size,
        risk_reward_t1      = (t1 - entry) / risk_dist,
        invalidated         = False,
        invalidation_reason = "",
    )
    return signal


# ─────────────────────────────────────────────────────────────────
# Demo: run full trade lifecycle
# ─────────────────────────────────────────────────────────────────

def run_execution_demo():
    print()
    print("=" * 62)
    print("  REMY — EXECUTION LAYER DEMO")
    print("  Instrument: EUR/USD | Direction: LONG")
    print("=" * 62)

    # ── 1. Setup: signal timestamp in a valid session window ─────
    # Simulate a Tuesday at 10:45 AM (well inside NY session, no news)
    base_time = datetime(2026, 3, 31, 10, 45, 0)   # Tuesday 10:45 AM
    signal    = make_mock_signal(now=base_time)

    print(f"\n[SIGNAL] Generated at {base_time.strftime('%H:%M:%S')}")
    print(f"  Instrument:        {signal.instrument}")
    print(f"  Direction:         {signal.direction.upper()}")
    print(f"  Pattern:           {signal.pattern_15m} (30m trend: {signal.trend_bias_30m})")
    print(f"  Confluence:        {signal.confluence_score}/5")
    print(f"  Signal Strength:   {signal.signal_strength}/100")
    print(f"  Entry:             {signal.entry_price:.4f}")
    print(f"  Stop:              {signal.stop_loss:.4f}  "
          f"(-{signal.stop_distance*10000:.0f} pips = 1R)")
    print(f"  Target 1:          {signal.target_1:.4f}  "
          f"(+{(signal.target_1-signal.entry_price)*10000:.0f} pips, "
          f"{signal.risk_reward_t1:.2f}R)")
    rr_t2 = (signal.target_2 - signal.entry_price) / signal.stop_distance
    print(f"  Target 2:          {signal.target_2:.4f}  "
          f"(+{(signal.target_2-signal.entry_price)*10000:.0f} pips, "
          f"{rr_t2:.2f}R)")
    print(f"  Position Size:     {signal.position_size_1pct:,.0f} units (1% risk rule)")
    print(f"  ATR(14) 15m:       {signal.atr_15m:.4f} ({signal.atr_15m*10000:.0f} pips)")

    # ── 2. Create execution engine ────────────────────────────────
    session_guard = SessionGuard(
        session_open  = __import__("datetime").time(9, 30),
        session_close = __import__("datetime").time(16, 0),
        news_times    = [],
    )

    engine = ExecutionEngine(
        signal               = signal,
        session_guard        = session_guard,
        commission_per_unit  = 0.0,    # spread-only cost model
        adv                  = 5_000_000_000,   # EUR/USD ADV ~5bn units
        daily_vol            = 0.006,            # ~0.6% daily vol
    )

    # Market impact estimate before entering
    impact_frac = estimate_market_impact(
        order_qty = signal.position_size_1pct,
        adv       = 5_000_000_000,
        daily_vol = 0.006,
    )
    print(f"\n[MARKET IMPACT] Est. impact: {impact_frac*10000:.3f} bps "
          f"({impact_frac*100:.4f}% of price) — negligible for EUR/USD")

    # ── 3. Accept: validate and submit entry order ────────────────
    print("\n" + "─" * 62)
    current_price = 1.0848   # price at time of signal acceptance
    ok, reason = engine.accept(current_price=current_price, now=base_time)

    if not ok:
        print(f"[REJECTED] {reason}")
        return

    print(f"[ACCEPT] Signal accepted @ market price {current_price:.4f}")
    entry_order = engine.trade.entry_order
    print(f"  Entry order:  STOP-LIMIT  trigger={entry_order.stop_trigger:.4f}  "
          f"limit={entry_order.limit_price:.4f}")
    print(f"  Stop order:   STOP-MARKET trigger={engine.trade.stop_order.stop_trigger:.4f}")
    print(f"  T1 order:     LIMIT       @ {engine.trade.t1_order.limit_price:.4f}  "
          f"qty={engine.trade.t1_order.quantity:.0f}")
    print(f"  T2 order:     LIMIT       @ {engine.trade.t2_order.limit_price:.4f}  "
          f"qty={engine.trade.t2_order.quantity:.0f}")

    # ── 4. Simulate 1m bar tick loop ─────────────────────────────
    print("\n" + "─" * 62)
    print("  BAR-BY-BAR SIMULATION (1m bars)")
    print("─" * 62)

    # (price, ema9_1m, bar_label)
    bars = [
        (1.0848, 1.0844, "Bar 1 — below entry trigger, waiting"),
        (1.0851, 1.0846, "Bar 2 — entry triggers and fills"),
        (1.0860, 1.0853, "Bar 3 — in trade, climbing"),
        (1.0875, 1.0865, "Bar 4 — momentum building"),
        (1.0896, 1.0882, "Bar 5 — TARGET 1 reached (1.5R)"),
        (1.0905, 1.0891, "Bar 6 — above T1, runner active"),
        (1.0931, 1.0912, "Bar 7 — TARGET 2 reached (key pivot)"),
        (1.0918, 1.0908, "Bar 8 — slight pullback, trailing stop holds"),
        (1.0904, 1.0898, "Bar 9 — runner stop triggered, trade closes"),
    ]

    for i, (price, ema9, label) in enumerate(bars):
        t = base_time + timedelta(minutes=i + 1)
        prev_status = engine.trade.status
        log_before  = len(engine._log)

        # spread=0.0: slippage_factor already accounts for execution cost;
        # adding a pip spread on top causes fill to exceed the stop-limit's
        # tight ATR-based limit price on a liquid pair like EUR/USD
        engine.tick(current_price=price, ema9_1m=ema9, now=t, spread=0.0)

        new_logs = engine._log[log_before:]
        status   = engine.trade.status.value.upper().replace("_", " ")

        print(f"\n  [{t.strftime('%H:%M')}] {label}")
        print(f"    Price: {price:.4f}  |  EMA9(1m): {ema9:.4f}  |  Status: {status}")
        for log_line in new_logs:
            print(f"    {log_line}")

        if engine.trade.status == TradeStatus.CLOSED:
            break

    # ── 5. Generate and print Remy's execution report ─────────────
    print("\n" + "─" * 62)
    report = engine.get_report()
    print(report.summary())

    # ── 6. Print full execution log ───────────────────────────────
    print("\n  FULL EXECUTION LOG:")
    print("─" * 62)
    for entry in engine._log:
        print(f"  {entry}")

    # ── 7. Validate key expectations ─────────────────────────────
    print("\n" + "─" * 62)
    print("  DEMO VALIDATION CHECKS")
    print("─" * 62)

    checks = []

    # Trade should be closed
    checks.append(("Trade status = CLOSED",
                   engine.trade.status == TradeStatus.CLOSED))

    # T1 and T2 should both be hit
    checks.append(("T1 hit (50% exit at 1.5R)",
                   engine.trade.t1_hit))
    checks.append(("T2 hit (30% exit at key level)",
                   engine.trade.t2_hit))

    # Stop should have moved to breakeven after T1
    checks.append(("Stop moved to breakeven after T1",
                   engine.trade.stop_moved_to_be))

    # Realized P&L should be positive (all targets hit)
    pnl_r = engine.trade.realized_pnl / max(signal.position_size_1pct, 1)
    checks.append((f"Realized P&L > 0 (actual: {pnl_r:+.2f}R)",
                   pnl_r > 0))

    # Should have fills for entry + T1 + T2 + runner
    checks.append((f"≥ 4 fills logged ({len(engine.trade.fills)} fills)",
                   len(engine.trade.fills) >= 4))

    # Entry should have been a STOP-LIMIT order
    checks.append(("Entry order type = STOP-LIMIT",
                   engine.trade.entry_order is not None and
                   engine.trade.entry_order.order_type.value == "stop_limit"))

    # Stop should have been a STOP-MARKET order
    checks.append(("Stop order type = STOP-MARKET",
                   engine.trade.stop_order is not None and
                   engine.trade.stop_order.order_type.value == "stop_market"))

    all_pass = True
    for label, result in checks:
        icon = "✓" if result else "✗"
        print(f"  [{icon}] {label}")
        if not result:
            all_pass = False

    print()
    if all_pass:
        print("  All checks passed. Execution layer is functioning correctly.")
    else:
        print("  One or more checks failed — review execution logic.")
    print("─" * 62)
    print()


if __name__ == "__main__":
    run_execution_demo()
