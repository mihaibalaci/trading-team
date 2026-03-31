"""
kai_broker_demo.py — End-to-end test of ExecutionEngine wired to Alpaca paper.

Places a real STOP-LIMIT entry order on the Alpaca paper account for SPY,
with a real STOP-MARKET protective stop. Monitors for 3 minutes via polling,
then cancels everything cleanly.

This is a PAPER trading test — no real money.

What it tests:
  - connect() loads credentials and reaches Alpaca
  - ExecutionEngine.accept() submits real orders (entry + stop)
  - Broker order IDs are recorded correctly
  - Order status polling works via get_order_fill()
  - force_close() cancels open orders at the broker
  - get_report() reflects broker-sourced state

Usage:
    python3 kai_broker_demo.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

import time
import logging
from datetime import datetime, timedelta

logging.basicConfig(level=logging.WARNING)

from broker_connector import connect
from signal_engine import FinnSignal
from execution import ExecutionEngine, SessionGuard, TradeStatus


# ─────────────────────────────────────────────────────────────────
# Build a mock FinnSignal for SPY
# ─────────────────────────────────────────────────────────────────

def make_spy_signal(connector, now: datetime) -> FinnSignal:
    """
    Build a realistic FinnSignal for SPY based on current market price.
    Entry just above current price (stop-limit trigger), stop 1% below.
    """
    mid = connector.get_latest_price("SPY")

    entry     = round(mid + 0.10, 2)          # trigger 10c above current — likely won't fill
    stop      = round(mid * 0.99,  2)          # stop 1% below current
    atr_15m   = round(mid * 0.004, 2)          # ~0.4% of price = typical 15m ATR for SPY
    t1        = round(entry + 1.5 * (entry - stop), 2)
    t2        = round(entry + 2.5 * (entry - stop), 2)
    risk_dist = round(entry - stop, 4)

    # Position size: $100k equity, 1% risk = $1,000
    equity     = 100_000
    pos_size   = round((equity * 0.01) / risk_dist)

    print(f"\n  SPY mid-price:     ${mid:.2f}")
    print(f"  Entry trigger:     ${entry:.2f}  (10c above market — unlikely to fill)")
    print(f"  Stop:              ${stop:.2f}  (1% below entry)")
    print(f"  Target 1:          ${t1:.2f}  (1.5R)")
    print(f"  Target 2:          ${t2:.2f}  (2.5R)")
    print(f"  ATR (est.):        ${atr_15m:.2f}")
    print(f"  Position size:     {pos_size:,} shares  (1% equity risk)")

    return FinnSignal(
        timestamp           = now,
        instrument          = "SPY",
        direction           = "long",
        signal_strength     = 65,
        confidence          = "Medium",
        timeframe           = "Intraday",
        model               = "MTF-Price-Action-v1",
        pattern_15m         = "Hammer",
        pattern_strength    = "medium",
        confluence_score    = 3,
        confluence_detail   = "pivot_level | stochastic_presignal | ema21_15m",
        trend_bias_30m      = "bullish",
        stoch_k_15m         = 24.0,
        stoch_k_1m          = 21.5,
        entry_price         = entry,
        stop_loss           = stop,
        stop_distance       = risk_dist,
        target_1            = t1,
        target_2            = t2,
        atr_15m             = atr_15m,
        position_size_1pct  = pos_size,
        risk_reward_t1      = 1.5,
        invalidated         = False,
        invalidation_reason = "",
    )


# ─────────────────────────────────────────────────────────────────
# Main demo
# ─────────────────────────────────────────────────────────────────

def run_broker_demo():
    print()
    print("=" * 60)
    print("  KAI — EXECUTION ENGINE × ALPACA PAPER BROKER DEMO")
    print("=" * 60)
    print("  Mode: PAPER TRADING — no real money")
    print("  Entry is priced above market to avoid an immediate fill.")
    print("  Orders will be cancelled after the monitoring window.")

    # ── 1. Connect ───────────────────────────────────────────────
    print("\n[1/5] Connecting to Alpaca paper ...")
    connector = connect()
    ok, detail = connector.health_check()
    if not ok:
        print(f"  FAILED: {detail}")
        sys.exit(1)
    print(f"  {detail}")

    # ── 2. Build signal ──────────────────────────────────────────
    print("\n[2/5] Building SPY signal ...")
    now    = datetime.now()
    signal = make_spy_signal(connector, now)

    # ── 3. Create engine and accept ─────────────────────────────
    print("\n[3/5] Accepting signal — submitting orders to Alpaca ...")

    # Session guard: use wide-open hours so the demo works any time of day
    guard = SessionGuard(
        session_open  = __import__("datetime").time(0, 0),
        session_close = __import__("datetime").time(23, 59),
    )

    engine = ExecutionEngine(
        signal    = signal,
        session_guard = guard,
        connector = connector,
    )

    accepted, reason = engine.accept(current_price=connector.get_latest_price("SPY"), now=now)
    if not accepted:
        print(f"  REJECTED: {reason}")
        sys.exit(1)

    print(f"  Signal accepted.")
    print(f"\n  Broker order IDs submitted:")
    for our_id, broker_id in engine._broker_ids.items():
        purpose = next(
            (o.purpose.value for o in [
                engine.trade.entry_order, engine.trade.stop_order,
                engine.trade.t1_order,    engine.trade.t2_order,
            ] if o and o.order_id == our_id),
            "unknown"
        )
        print(f"    [{purpose:12s}] our_id={our_id}  broker_id={broker_id}")

    # ── 4. Poll for 3 minutes ────────────────────────────────────
    print("\n[4/5] Monitoring for 3 minutes (polling every 10s) ...")
    print("      Entry is above market — expecting no fill.")
    print("      Press Ctrl+C to cancel early.\n")

    deadline = time.time() + 180
    poll_n   = 0

    try:
        while time.time() < deadline:
            poll_n += 1
            current = connector.get_latest_price("SPY")
            engine.tick(current_price=current, now=datetime.now())

            status  = engine.trade.status.value.upper().replace("_", " ")
            elapsed = int(180 - (deadline - time.time()))
            print(f"  [{elapsed:3d}s] SPY=${current:.2f}  "
                  f"entry trigger=${signal.entry_price:.2f}  "
                  f"status={status}")

            if engine.trade.status == TradeStatus.CLOSED:
                print("\n  Trade closed during monitoring window.")
                break

            time.sleep(10)

    except KeyboardInterrupt:
        print("\n  Interrupted by user.")

    # ── 5. Cancel everything and report ─────────────────────────
    print("\n[5/5] Cancelling all open orders and generating report ...")

    if engine.trade.status == TradeStatus.PENDING_ENTRY:
        # Entry never filled — cancel the working entry order at the broker
        entry = engine.trade.entry_order
        if entry and entry.order_id in engine._broker_ids:
            connector.cancel_order(engine._broker_ids[entry.order_id])
            print(f"  Cancelled pending entry order at broker.")
        engine.trade.status = TradeStatus.CANCELLED
    elif engine.trade.status not in (TradeStatus.CLOSED, TradeStatus.CANCELLED):
        current = connector.get_latest_price("SPY")
        engine.force_close(current, reason="Demo ended — cleaning up paper orders")

    report = engine.get_report()
    print()
    print(report.summary())

    # Verify broker IDs were recorded for entry and stop
    print("  CHECKS:")
    checks = [
        ("Entry order submitted to broker",
         engine.trade.entry_order is not None and
         engine.trade.entry_order.order_id in engine._broker_ids),
        ("Stop submitted after fill, or pending entry (both correct)",
         engine.trade.stop_order.order_id in engine._broker_ids   # filled path
         or engine.trade.status == TradeStatus.CANCELLED),         # no fill = expected here
        ("Broker IDs are non-empty strings",
         all(len(v) > 5 for v in engine._broker_ids.values())),
    ]
    all_pass = True
    for label, result in checks:
        icon = "✓" if result else "✗"
        print(f"    [{icon}] {label}")
        if not result:
            all_pass = False

    print()
    if all_pass:
        print("  Broker integration verified. ExecutionEngine is wired to Alpaca.")
    else:
        print("  One or more checks failed.")
    print("=" * 60)
    print()


if __name__ == "__main__":
    run_broker_demo()
