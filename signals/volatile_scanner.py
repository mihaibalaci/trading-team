"""
volatile_scanner.py — Finn's Volatile Stock Scanner

Scans a broad universe of liquid US equities every 30 seconds.
Ranks them by intraday volatility (range / price).
Picks the top 10 most volatile and runs Finn's full MTF signal engine.
Auto-executes valid signals via Remy's ExecutionEngine → Kai's AlpacaConnector.

All of Vera's strategy rules and Mira's risk constraints are enforced
through the existing signal_engine and execution pipeline.
"""

from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

import time
import logging
from datetime import datetime, timezone

import pandas as pd

from broker_connector import connect, BrokerConnector
from signal_engine import generate_signal, FinnSignal
from execution import ExecutionEngine, SessionGuard, TradeStatus
from live_scanner import (
    fetch_bars, fetch_prev_day_levels, Portfolio,
    MAX_POSITIONS, RISK_PER_TRADE, MIN_SIGNAL_STR,
    TF_TREND_MIN, TF_SETUP_MIN, TF_ENTRY_MIN,
    BARS_TREND, BARS_SETUP, BARS_ENTRY,
)

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s  %(levelname)s  %(message)s",
)
log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────

SCAN_INTERVAL_S = 30   # scan every 30 seconds
TOP_N           = 10   # pick top 10 most volatile

# Broad universe of liquid US equities to screen for volatility
UNIVERSE = [
    "SPY", "QQQ", "AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "META", "GOOGL",
    "JPM", "AMD", "NFLX", "BA", "COIN", "MARA", "RIOT", "SOFI", "PLTR",
    "SNAP", "UBER", "SQ", "SHOP", "ROKU", "ENPH", "SMCI", "ARM", "MSTR",
    "RIVN", "LCID", "NIO", "INTC", "MU", "AFRM", "DKNG", "HOOD", "RBLX",
    "CRWD", "SNOW", "NET", "ABNB", "PYPL", "DIS", "BABA", "XOM", "CVX",
    "GS", "MS", "V", "WMT", "COST",
]


# ─────────────────────────────────────────────────────────────────
# Volatility ranking via Alpaca snapshots
# ─────────────────────────────────────────────────────────────────

def rank_by_volatility(connector: BrokerConnector, symbols: list[str]) -> list[str]:
    """
    Fetch latest snapshots for all symbols and rank by intraday
    volatility = (high - low) / close. Returns top N symbols.
    """
    from alpaca.data.requests import StockSnapshotRequest

    try:
        req = StockSnapshotRequest(symbol_or_symbols=symbols)
        snapshots = connector._data.get_stock_snapshot(req)
    except Exception as e:
        log.warning(f"Snapshot fetch failed: {e}")
        return symbols[:TOP_N]  # fallback to first N

    scored = []
    for sym, snap in snapshots.items():
        try:
            bar = snap.daily_bar
            if bar is None or float(bar.close) == 0:
                continue
            vol = (float(bar.high) - float(bar.low)) / float(bar.close)
            scored.append((sym, vol))
        except Exception:
            continue

    scored.sort(key=lambda x: x[1], reverse=True)
    return [sym for sym, _ in scored[:TOP_N]]


# ─────────────────────────────────────────────────────────────────
# Scan a single symbol (reuses live_scanner logic)
# ─────────────────────────────────────────────────────────────────

def _scan_symbol(symbol: str, connector: BrokerConnector,
                 portfolio: Portfolio, guard: SessionGuard,
                 now: datetime) -> None:
    """Fetch bars, run Finn's engine, execute if valid."""

    df_1m  = fetch_bars(connector, symbol, TF_ENTRY_MIN, BARS_ENTRY)
    df_15m = fetch_bars(connector, symbol, TF_SETUP_MIN, BARS_SETUP)
    df_30m = fetch_bars(connector, symbol, TF_TREND_MIN, BARS_TREND)

    if df_1m.empty or df_15m.empty or df_30m.empty:
        return

    prev_h, prev_l, prev_c = fetch_prev_day_levels(connector, symbol)
    if prev_h == 0:
        return

    swing_high = float(df_30m["high"].tail(20).max())
    swing_low  = float(df_30m["low"].tail(20).min())

    signal = generate_signal(
        instrument            = symbol,
        df_30m                = df_30m,
        df_15m                = df_15m,
        df_1m                 = df_1m,
        prev_day_high         = prev_h,
        prev_day_low          = prev_l,
        prev_day_close        = prev_c,
        swing_high            = swing_high,
        swing_low             = swing_low,
        equity                = portfolio.equity,
        risk_pct              = RISK_PER_TRADE,
        current_open_risk_pct = portfolio.open_risk_pct,
        peak_equity           = portfolio.peak_equity,
    )

    if signal is None:
        print(f"    [{symbol:6s}] no signal")
        return

    if signal.invalidated:
        print(f"    [{symbol:6s}] invalidated — {signal.invalidation_reason}")
        return

    if signal.signal_strength < MIN_SIGNAL_STR:
        print(f"    [{symbol:6s}] weak ({signal.signal_strength}/100 < {MIN_SIGNAL_STR})")
        return

    print(f"    [{symbol:6s}] SIGNAL  {signal.direction.upper():5s}  "
          f"str={signal.signal_strength}/100  conf={signal.confidence}  "
          f"pattern={signal.pattern_15m}  "
          f"entry=${signal.entry_price:.2f}  "
          f"stop=${signal.stop_loss:.2f}  "
          f"T1=${signal.target_1:.2f}")

    current_price = float(df_1m["close"].iloc[-1])

    engine = ExecutionEngine(
        signal        = signal,
        session_guard = guard,
        connector     = connector,
    )

    accepted, reason = engine.accept(current_price=current_price, now=now)

    if accepted:
        portfolio.add(symbol, engine)
        broker_id = list(engine._broker_ids.values())[0] if engine._broker_ids else "—"
        print(f"    [{symbol:6s}] ✓ EXECUTED → {broker_id}  "
              f"size={signal.position_size_1pct:.0f} shares")
    else:
        print(f"    [{symbol:6s}] ✗ REJECTED — {reason}")


# ─────────────────────────────────────────────────────────────────
# Main scanner loop
# ─────────────────────────────────────────────────────────────────

def run_volatile_scanner(max_cycles: int | None = None):
    print()
    print("=" * 64)
    print("  FINN — VOLATILE STOCK SCANNER  [AUTO-TRADE]")
    print(f"  Universe: {len(UNIVERSE)} stocks → top {TOP_N} by volatility")
    print(f"  Scan interval: {SCAN_INTERVAL_S}s")
    print(f"  Max positions: {MAX_POSITIONS}  |  Risk/trade: {RISK_PER_TRADE:.0%}")
    print("=" * 64)

    print("\n[INIT] Connecting to Alpaca paper ...")
    connector = connect()
    ok, detail = connector.health_check()
    if not ok:
        print(f"  FAILED: {detail}")
        sys.exit(1)
    print(f"  {detail}")

    acct      = connector.get_account_state()
    portfolio = Portfolio(equity=acct.equity)
    print(f"  Starting equity: ${portfolio.equity:,.2f}")

    guard = SessionGuard(
        session_open  = __import__("datetime").time(9, 30),
        session_close = __import__("datetime").time(16, 0),
    )

    cycle = 0
    print(f"\n  Scanning every {SCAN_INTERVAL_S}s. Ctrl+C to stop.\n")
    print("─" * 64)

    try:
        while max_cycles is None or cycle < max_cycles:
            cycle += 1
            now = datetime.now()
            print(f"\n[CYCLE {cycle}]  {now.strftime('%Y-%m-%d %H:%M:%S')}")

            # Update equity
            try:
                acct = connector.get_account_state()
                portfolio.update_equity(acct.equity)
            except Exception as e:
                print(f"  [WARN] Equity fetch failed: {e}")

            # Tick open positions
            for sym, engine in list(portfolio.engines.items()):
                if engine.trade.status in (TradeStatus.CLOSED, TradeStatus.CANCELLED):
                    continue
                try:
                    price = connector.get_latest_price(sym)
                    engine.tick(current_price=price, now=now)
                    pnl = engine.trade.realized_pnl / max(engine.signal.position_size_1pct, 1)
                    print(f"  [{sym:6s}] ${price:.2f}  "
                          f"{engine.trade.status.value}  pnl={pnl:+.2f}R")
                except Exception as e:
                    print(f"  [{sym:6s}] tick error: {e}")

            portfolio.cleanup_closed()

            # Session check
            ok, reason = guard.check(now)
            if not ok:
                print(f"\n  [GUARD] {reason}")
            elif not portfolio.can_add_position:
                print(f"\n  [MIRA] Portfolio full "
                      f"({len(portfolio.open_positions)}/{MAX_POSITIONS})")
            else:
                # Rank by volatility
                print(f"\n  [FINN] Ranking {len(UNIVERSE)} stocks by volatility ...")
                top_volatile = rank_by_volatility(connector, UNIVERSE)
                print(f"  [FINN] Top {len(top_volatile)} volatile: {', '.join(top_volatile)}")

                print(f"  [FINN] Scanning for patterns ...")
                for symbol in top_volatile:
                    if symbol in portfolio.engines:
                        continue
                    if not portfolio.can_add_position:
                        break
                    try:
                        _scan_symbol(symbol, connector, portfolio, guard, now)
                    except Exception as e:
                        print(f"    [{symbol:6s}] error: {e}")

            # Summary
            open_syms = [s for s, e in portfolio.engines.items()
                         if e.trade.status in (TradeStatus.ACTIVE,
                                               TradeStatus.PARTIAL_EXIT,
                                               TradeStatus.PENDING_ENTRY)]
            print(f"\n  Positions: {open_syms or 'none'}  |  "
                  f"Equity: ${portfolio.equity:,.2f}")

            if max_cycles and cycle >= max_cycles:
                break

            print(f"  Next scan in {SCAN_INTERVAL_S}s ...")
            time.sleep(SCAN_INTERVAL_S)

    except KeyboardInterrupt:
        print("\n\n  Interrupted by user.")

    # Shutdown — close all open trades
    print("\n" + "=" * 64)
    print("  SCANNER STOPPED — closing all open positions")
    print("=" * 64)

    for symbol, engine in portfolio.engines.items():
        if engine.trade.status not in (TradeStatus.CLOSED, TradeStatus.CANCELLED):
            try:
                price = connector.get_latest_price(symbol)
                engine.force_close(price, reason="Scanner stopped")
                print(f"  [{symbol}] force closed @ ${price:.2f}")
            except Exception as e:
                print(f"  [{symbol}] close error: {e}")

    # Final report
    print("\n  TRADE SUMMARY:")
    print("─" * 64)
    total_r = 0.0
    for engine in portfolio.all_engines:
        sig   = engine.signal
        pnl_r = engine.trade.realized_pnl / max(sig.position_size_1pct, 1)
        total_r += pnl_r
        dur = (f"{engine.get_report().hold_duration_min:.0f}m"
               if engine.get_report().hold_duration_min else "—")
        print(f"  {sig.instrument:6s}  {sig.direction:5s}  "
              f"str={sig.signal_strength:3d}  pnl={pnl_r:+.2f}R  dur={dur}")
    if portfolio.all_engines:
        print(f"{'─'*64}")
        print(f"  Total: {total_r:+.2f}R across {len(portfolio.all_engines)} trade(s)")
    else:
        print("  No trades executed this session.")
    print("=" * 64)


if __name__ == "__main__":
    run_volatile_scanner()
