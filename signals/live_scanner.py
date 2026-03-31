"""
live_scanner.py — Finn's live signal scanner wired to Kai's Alpaca connector.

Scans a watchlist of instruments using real bar data from Alpaca.
Passes each instrument through generate_signal() (Finn's engine).
Applies Mira's portfolio constraints (3% max open risk, 3 positions max).
Executes valid signals via Remy's ExecutionEngine → Kai's AlpacaConnector.
Monitors all open trades on each scan cycle and reports.

Vera's strategy constraints enforced:
  - Max 3 simultaneous positions (3% total open risk at 1% each)
  - No trades in opening 5-min block
  - Drawdown circuit breaker (5% → halve risk, 10% → stop)
  - Minimum bar counts for indicator reliability
"""

from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

import time
import logging
from datetime import datetime, timedelta, timezone

import pandas as pd
import numpy as np

from broker_connector import connect, BrokerConnector
from signal_engine import generate_signal, FinnSignal
from execution import ExecutionEngine, SessionGuard, TradeStatus

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s  %(levelname)s  %(message)s",
)
log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────

WATCHLIST = [
    "SPY", "QQQ", "AAPL", "MSFT", "NVDA",
    "TSLA", "AMZN", "META", "GOOGL", "JPM",
]

MAX_POSITIONS   = 3      # Mira CRIT-01: Vera's hard cap
RISK_PER_TRADE  = 0.01   # 1% per trade
SCAN_INTERVAL_S = 60     # scan every 60 seconds
MIN_SIGNAL_STR  = 50     # only take signals with strength ≥ 50


# ─────────────────────────────────────────────────────────────────
# Data fetching — pulls real OHLCV bars from Alpaca
# ─────────────────────────────────────────────────────────────────

def fetch_bars(connector: BrokerConnector,
               symbol: str,
               timeframe_minutes: int,
               limit: int) -> pd.DataFrame:
    """
    Fetch recent bars from Alpaca and return as a standard OHLCV DataFrame.
    Columns: open, high, low, close, volume. Index: datetime.
    """
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

    if timeframe_minutes == 1:
        tf = TimeFrame.Minute
    elif timeframe_minutes == 15:
        tf = TimeFrame(15, TimeFrameUnit.Minute)
    elif timeframe_minutes == 30:
        tf = TimeFrame(30, TimeFrameUnit.Minute)
    else:
        tf = TimeFrame(timeframe_minutes, TimeFrameUnit.Minute)

    req  = StockBarsRequest(symbol_or_symbols=symbol, timeframe=tf, limit=limit)
    bars = connector._data.get_stock_bars(req)

    if symbol not in bars or len(bars[symbol]) == 0:
        return pd.DataFrame()

    rows = []
    for b in bars[symbol]:
        rows.append({
            "open":   float(b.open),
            "high":   float(b.high),
            "low":    float(b.low),
            "close":  float(b.close),
            "volume": float(b.volume),
        })

    df = pd.DataFrame(rows)
    return df


def fetch_prev_day_levels(connector: BrokerConnector, symbol: str) -> tuple[float, float, float]:
    """
    Fetch previous day's high, low, close for pivot point calculation.
    Returns (prev_high, prev_low, prev_close).
    """
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

    req  = StockBarsRequest(
        symbol_or_symbols = symbol,
        timeframe         = TimeFrame(1, TimeFrameUnit.Day),
        limit             = 3,
    )
    bars = connector._data.get_stock_bars(req)

    if symbol not in bars or len(bars[symbol]) < 2:
        return 0.0, 0.0, 0.0

    # Second-to-last bar = previous completed day
    prev = bars[symbol][-2]
    return float(prev.high), float(prev.low), float(prev.close)


# ─────────────────────────────────────────────────────────────────
# Portfolio state tracker
# ─────────────────────────────────────────────────────────────────

class Portfolio:
    """Tracks open engines and enforces Mira's exposure constraints."""

    def __init__(self, equity: float):
        self.peak_equity    = equity
        self.equity         = equity
        self.engines:  dict[str, ExecutionEngine] = {}   # symbol → engine
        self.all_engines: list[ExecutionEngine]   = []   # full history

    @property
    def open_positions(self) -> list[ExecutionEngine]:
        return [e for e in self.engines.values()
                if e.trade.status in (TradeStatus.ACTIVE, TradeStatus.PARTIAL_EXIT,
                                      TradeStatus.PENDING_ENTRY)]

    @property
    def open_risk_pct(self) -> float:
        return len(self.open_positions) * RISK_PER_TRADE

    @property
    def can_add_position(self) -> bool:
        return (len(self.open_positions) < MAX_POSITIONS and
                self.open_risk_pct + RISK_PER_TRADE <= 0.03)

    def add(self, symbol: str, engine: ExecutionEngine) -> None:
        self.engines[symbol] = engine
        self.all_engines.append(engine)

    def update_equity(self, equity: float) -> None:
        self.equity = equity
        if equity > self.peak_equity:
            self.peak_equity = equity

    def cleanup_closed(self) -> None:
        closed = [sym for sym, e in self.engines.items()
                  if e.trade.status in (TradeStatus.CLOSED, TradeStatus.CANCELLED)]
        for sym in closed:
            del self.engines[sym]


# ─────────────────────────────────────────────────────────────────
# Main scanner
# ─────────────────────────────────────────────────────────────────

def run_scanner(max_cycles: int | None = None, scan_interval: int = SCAN_INTERVAL_S):
    print()
    print("=" * 64)
    print("  FINN / REMY / KAI — LIVE SIGNAL SCANNER")
    print(f"  Watchlist: {', '.join(WATCHLIST)}")
    print(f"  Max positions: {MAX_POSITIONS}  |  Risk/trade: {RISK_PER_TRADE:.0%}")
    print(f"  Min signal strength: {MIN_SIGNAL_STR}/100")
    print("=" * 64)

    # ── Connect ───────────────────────────────────────────────────
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

    # Session guard: NY session 9:30–16:00
    guard = SessionGuard(
        session_open  = __import__("datetime").time(9, 30),
        session_close = __import__("datetime").time(16, 0),
    )

    cycle      = 0
    total_sigs = 0
    total_exec = 0

    print(f"\n  Scanning every {scan_interval}s. Ctrl+C to stop.\n")
    print("─" * 64)

    try:
        while max_cycles is None or cycle < max_cycles:
            cycle += 1
            now   = datetime.now()
            print(f"\n[CYCLE {cycle}]  {now.strftime('%Y-%m-%d %H:%M:%S')}")

            # ── Update equity ─────────────────────────────────────
            try:
                acct = connector.get_account_state()
                portfolio.update_equity(acct.equity)
            except Exception as e:
                print(f"  [WARN] Could not fetch equity: {e}")

            # ── Tick all open engines ─────────────────────────────
            for sym, engine in list(portfolio.engines.items()):
                if engine.trade.status in (TradeStatus.CLOSED, TradeStatus.CANCELLED):
                    continue
                try:
                    price = connector.get_latest_price(sym)
                    engine.tick(current_price=price, now=now)
                    status = engine.trade.status.value.upper().replace("_", " ")
                    pnl    = engine.trade.realized_pnl / max(engine.signal.position_size_1pct, 1)
                    print(f"  [{sym:6s}] price=${price:.2f}  "
                          f"status={status}  pnl={pnl:+.2f}R")
                except Exception as e:
                    print(f"  [{sym:6s}] tick error: {e}")

            portfolio.cleanup_closed()

            # ── Check session guard ───────────────────────────────
            ok, reason = guard.check(now)
            if not ok:
                print(f"\n  [GUARD] {reason} — skipping scan.")
            else:
                # ── Scan watchlist for new signals ────────────────
                if not portfolio.can_add_position:
                    print(f"\n  [MIRA] Portfolio full "
                          f"({len(portfolio.open_positions)}/{MAX_POSITIONS} positions). "
                          f"Skipping scan.")
                else:
                    print(f"\n  [FINN] Scanning {len(WATCHLIST)} instruments "
                          f"({len(portfolio.open_positions)}/{MAX_POSITIONS} positions open) ...")

                    for symbol in WATCHLIST:
                        if symbol in portfolio.engines:
                            continue  # already in this symbol
                        if not portfolio.can_add_position:
                            break     # Mira's cap reached

                        try:
                            _scan_symbol(symbol, connector, portfolio, guard, now)
                            total_sigs += 1
                        except Exception as e:
                            print(f"    [{symbol:6s}] scan error: {e}")

            # ── Summary line ──────────────────────────────────────
            open_syms = [s for s, e in portfolio.engines.items()
                         if e.trade.status in (TradeStatus.ACTIVE,
                                               TradeStatus.PARTIAL_EXIT,
                                               TradeStatus.PENDING_ENTRY)]
            print(f"\n  Open positions: {open_syms if open_syms else 'none'}  "
                  f"|  Equity: ${portfolio.equity:,.2f}")

            if max_cycles and cycle >= max_cycles:
                break

            print(f"  Next scan in {scan_interval}s ...")
            time.sleep(scan_interval)

    except KeyboardInterrupt:
        print("\n\n  Interrupted by user.")

    # ── Shutdown: close all open trades ──────────────────────────
    print("\n" + "=" * 64)
    print("  SCANNER STOPPED — closing all open paper positions")
    print("=" * 64)

    for symbol, engine in portfolio.engines.items():
        if engine.trade.status not in (TradeStatus.CLOSED, TradeStatus.CANCELLED):
            try:
                price = connector.get_latest_price(symbol)
                engine.force_close(price, reason="Scanner stopped")
                print(f"  [{symbol}] force closed @ ${price:.2f}")
            except Exception as e:
                print(f"  [{symbol}] close error: {e}")

    # ── Final report ──────────────────────────────────────────────
    print("\n  TRADE SUMMARY:")
    print("─" * 64)
    total_r = 0.0
    for engine in portfolio.all_engines:
        report = engine.get_report()
        sig    = engine.signal
        pnl_r  = engine.trade.realized_pnl / max(sig.position_size_1pct, 1)
        total_r += pnl_r
        dur    = (f"{report.hold_duration_min:.0f}m"
                  if report.hold_duration_min else "—")
        print(f"  {sig.instrument:6s}  {sig.direction:5s}  "
              f"str={sig.signal_strength:3d}  "
              f"pnl={pnl_r:+.2f}R  dur={dur}  "
              f"status={engine.trade.status.value}")
    if portfolio.all_engines:
        print(f"{'─'*64}")
        print(f"  Total P&L:  {total_r:+.2f}R across "
              f"{len(portfolio.all_engines)} signal(s)")
    else:
        print("  No signals were executed this session.")
    print("=" * 64)
    print()


def _scan_symbol(symbol: str, connector: BrokerConnector,
                 portfolio: Portfolio, guard: SessionGuard,
                 now: datetime) -> None:
    """Fetch bars for one symbol, run Finn's engine, execute if valid."""

    # Fetch multi-timeframe bars
    df_1m  = fetch_bars(connector, symbol, 1,  200)
    df_15m = fetch_bars(connector, symbol, 15, 100)
    df_30m = fetch_bars(connector, symbol, 30,  60)

    if df_1m.empty or df_15m.empty or df_30m.empty:
        return

    # Previous day levels for pivot points
    prev_h, prev_l, prev_c = fetch_prev_day_levels(connector, symbol)
    if prev_h == 0:
        return

    # Swing high/low from recent 30m data for Fibonacci
    swing_high = float(df_30m["high"].tail(20).max())
    swing_low  = float(df_30m["low"].tail(20).min())

    # Run Finn's signal engine
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
        print(f"    [{symbol:6s}] signal invalidated — {signal.invalidation_reason}")
        return

    if signal.signal_strength < MIN_SIGNAL_STR:
        print(f"    [{symbol:6s}] signal too weak "
              f"({signal.signal_strength}/100 < {MIN_SIGNAL_STR} threshold)")
        return

    # Valid signal — attempt execution
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
        print(f"    [{symbol:6s}] ACCEPTED → broker order {broker_id}  "
              f"size={signal.position_size_1pct:.0f} shares")
    else:
        print(f"    [{symbol:6s}] REJECTED — {reason}")


# ─────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Run 5 scan cycles (5 minutes at 60s interval) then produce final report.
    # Remove max_cycles to run indefinitely.
    run_scanner(max_cycles=5, scan_interval=60)
