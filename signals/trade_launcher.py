"""
trade_launcher.py — Interactive trade launcher with configurable strategy.

Flow:
  1. Owner selects horizon (short/medium/long)
  2. Owner selects asset class (stocks/forex/commodities)
  3. Pax runs market analysis on the selected watchlist
  4. Owner reviews Pax's analysis and selects risk level
  5. Scanner launches with the configured profile

Usage:
    python3 signals/trade_launcher.py
    python3 signals/trade_launcher.py --horizon short --assets stocks --risk moderate
"""
from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

import argparse
import time
import logging
from datetime import datetime, timedelta, time as dtime

import pandas as pd
import numpy as np

from strategy_config import (
    Horizon, AssetClass, RiskLevel,
    build_profile, StrategyProfile,
    WATCHLISTS, TIMEFRAME_CONFIG, RISK_CONFIG,
)
from broker_connector import connect, BrokerConnector
from signal_engine import generate_signal, FinnSignal
from execution import ExecutionEngine, SessionGuard, TradeStatus
from indicators import atr, ema_stack, trend_bias

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────
# Pax's quick market analysis
# ─────────────────────────────────────────────────────────────────

def pax_analyze(connector: BrokerConnector, watchlist: list[str],
                tf_trend_min: int, bars: int) -> str:
    """
    Pax runs a quick scan: trend bias, ATR, and volatility for each symbol.
    Returns a formatted analysis string for the owner to review.
    """
    from live_scanner import fetch_bars

    lines = [
        f"{'='*60}",
        f"  PAX — PRE-SESSION MARKET ANALYSIS",
        f"  {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"{'='*60}",
        "",
        f"  {'Symbol':<8} {'Trend':<10} {'ATR':>8} {'Vol%':>8} {'Verdict':<20}",
        f"  {'─'*54}",
    ]

    bullish_count = 0
    bearish_count = 0
    ranging_count = 0
    high_vol = []

    for symbol in watchlist:
        try:
            df = fetch_bars(connector, symbol, tf_trend_min, bars)
            if df.empty or len(df) < 55:
                lines.append(f"  {symbol:<8} {'—':<10} {'—':>8} {'—':>8} Insufficient data")
                continue

            df = ema_stack(df)
            df = atr(df)
            bias = trend_bias(df)
            current_atr = float(df["atr"].iloc[-1])
            price = float(df["close"].iloc[-1])
            vol_pct = (current_atr / price) * 100

            if bias == "bullish":
                bullish_count += 1
            elif bias == "bearish":
                bearish_count += 1
            else:
                ranging_count += 1

            if vol_pct > 0.5:
                high_vol.append(symbol)

            verdict = ""
            if bias == "bullish" and vol_pct < 1.0:
                verdict = "✓ Good long setup"
            elif bias == "bearish" and vol_pct < 1.0:
                verdict = "✓ Good short setup"
            elif bias == "ranging":
                verdict = "⚠ Ranging — caution"
            elif vol_pct > 1.5:
                verdict = "⚠ High volatility"
            else:
                verdict = "○ Neutral"

            lines.append(
                f"  {symbol:<8} {bias:<10} {current_atr:>8.4f} {vol_pct:>7.2f}% {verdict}"
            )
        except Exception as e:
            lines.append(f"  {symbol:<8} Error: {str(e)[:40]}")

    lines.append(f"  {'─'*54}")
    lines.append(f"")
    lines.append(f"  Market Summary:")
    lines.append(f"    Bullish: {bullish_count}  |  Bearish: {bearish_count}  |  Ranging: {ranging_count}")
    if high_vol:
        lines.append(f"    High volatility: {', '.join(high_vol)}")

    # Pax's recommendation
    total = bullish_count + bearish_count + ranging_count
    if total == 0:
        rec = "No data available — cannot assess market conditions."
    elif ranging_count > total * 0.6:
        rec = "Majority ranging — CONSERVATIVE risk recommended. Few clean setups expected."
    elif bullish_count > bearish_count * 2:
        rec = "Strong bullish bias — MODERATE or AGGRESSIVE risk appropriate for longs."
    elif bearish_count > bullish_count * 2:
        rec = "Strong bearish bias — MODERATE or AGGRESSIVE risk appropriate for shorts."
    elif high_vol and len(high_vol) > total * 0.3:
        rec = "Elevated volatility — CONSERVATIVE risk recommended until conditions settle."
    else:
        rec = "Mixed conditions — MODERATE risk recommended. Be selective."

    lines.append(f"")
    lines.append(f"  Pax's Recommendation: {rec}")
    lines.append(f"{'='*60}")

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────
# Portfolio tracker (same as live_scanner but uses StrategyProfile)
# ─────────────────────────────────────────────────────────────────

class Portfolio:
    def __init__(self, equity: float, profile: StrategyProfile):
        self.peak_equity = equity
        self.equity = equity
        self.profile = profile
        self.engines: dict[str, ExecutionEngine] = {}
        self.all_engines: list[ExecutionEngine] = []

    @property
    def open_positions(self) -> list[ExecutionEngine]:
        return [e for e in self.engines.values()
                if e.trade.status in (TradeStatus.ACTIVE, TradeStatus.PARTIAL_EXIT,
                                      TradeStatus.PENDING_ENTRY)]

    @property
    def open_risk_pct(self) -> float:
        return len(self.open_positions) * self.profile.risk_per_trade

    @property
    def can_add_position(self) -> bool:
        return (len(self.open_positions) < self.profile.max_positions and
                self.open_risk_pct + self.profile.risk_per_trade <= self.profile.max_open_risk)

    @property
    def drawdown_pct(self) -> float:
        if self.peak_equity <= 0:
            return 0.0
        return (self.peak_equity - self.equity) / self.peak_equity * 100

    def add(self, symbol: str, engine: ExecutionEngine):
        self.engines[symbol] = engine
        self.all_engines.append(engine)

    def update_equity(self, equity: float):
        self.equity = equity
        if equity > self.peak_equity:
            self.peak_equity = equity

    def cleanup_closed(self):
        closed = [s for s, e in self.engines.items()
                  if e.trade.status in (TradeStatus.CLOSED, TradeStatus.CANCELLED)]
        for s in closed:
            del self.engines[s]


# ─────────────────────────────────────────────────────────────────
# Scanner loop (profile-driven)
# ─────────────────────────────────────────────────────────────────

def run_configured_scanner(profile: StrategyProfile,
                           max_cycles: int | None = None,
                           duration_hours: float | None = None):
    """Run the scanner with the given strategy profile."""
    from live_scanner import fetch_bars, fetch_prev_day_levels

    print(profile.summary())

    print("\n[INIT] Connecting to Alpaca paper ...")
    connector = connect()
    ok, detail = connector.health_check()
    if not ok:
        print(f"  FAILED: {detail}")
        sys.exit(1)
    print(f"  {detail}")

    acct = connector.get_account_state()
    portfolio = Portfolio(equity=acct.equity, profile=profile)
    print(f"  Starting equity: ${portfolio.equity:,.2f}")

    guard = SessionGuard(
        session_open=profile.session_open,
        session_close=profile.session_close,
    )

    cycle = 0
    start_time = datetime.now()
    end_time = start_time + timedelta(hours=duration_hours) if duration_hours else None

    print(f"\n  Scanning every {profile.scan_interval_s}s. Ctrl+C to stop.")
    if end_time:
        print(f"  Auto-stop at {end_time.strftime('%Y-%m-%d %H:%M')}")
    print("─" * 64)

    try:
        while True:
            cycle += 1
            now = datetime.now()

            if max_cycles and cycle > max_cycles:
                break
            if end_time and now > end_time:
                print(f"\n  Duration limit reached ({duration_hours}h). Stopping.")
                break

            # Drawdown circuit breaker
            if portfolio.drawdown_pct > profile.drawdown_halt_pct:
                print(f"\n  [MIRA] DRAWDOWN HALT — {portfolio.drawdown_pct:.1f}% > "
                      f"{profile.drawdown_halt_pct:.1f}% limit. Stopping scanner.")
                break

            print(f"\n[CYCLE {cycle}]  {now.strftime('%Y-%m-%d %H:%M:%S')}")

            # Update equity
            try:
                acct = connector.get_account_state()
                portfolio.update_equity(acct.equity)
            except Exception as e:
                print(f"  [WARN] Equity fetch failed: {e}")

            # Tick open engines
            for sym, engine in list(portfolio.engines.items()):
                if engine.trade.status in (TradeStatus.CLOSED, TradeStatus.CANCELLED):
                    continue
                try:
                    price = connector.get_latest_price(sym)
                    engine.tick(current_price=price, now=now)
                    status = engine.trade.status.value.upper().replace("_", " ")
                    pnl = engine.trade.realized_pnl / max(engine.signal.position_size_1pct, 1)
                    print(f"  [{sym:6s}] ${price:.2f}  {status}  pnl={pnl:+.2f}R")
                except Exception as e:
                    print(f"  [{sym:6s}] tick error: {e}")

            portfolio.cleanup_closed()

            # Session check
            ok, reason = guard.check(now)
            if not ok:
                print(f"\n  [GUARD] {reason}")
            elif not portfolio.can_add_position:
                print(f"\n  [MIRA] Portfolio full ({len(portfolio.open_positions)}/{profile.max_positions})")
            else:
                print(f"\n  [FINN] Scanning {len(profile.watchlist)} instruments ...")
                for symbol in profile.watchlist:
                    if symbol in portfolio.engines:
                        continue
                    if not portfolio.can_add_position:
                        break
                    try:
                        _scan_and_execute(symbol, connector, portfolio, guard, now, profile)
                    except Exception as e:
                        print(f"    [{symbol:6s}] error: {e}")

            open_syms = [s for s in portfolio.engines
                         if portfolio.engines[s].trade.status in
                         (TradeStatus.ACTIVE, TradeStatus.PARTIAL_EXIT, TradeStatus.PENDING_ENTRY)]
            print(f"\n  Open: {open_syms if open_syms else 'none'}  |  "
                  f"Equity: ${portfolio.equity:,.2f}  |  DD: {portfolio.drawdown_pct:.1f}%")
            print(f"  Next scan in {profile.scan_interval_s}s ...")
            time.sleep(profile.scan_interval_s)

    except KeyboardInterrupt:
        print("\n\n  Interrupted by user.")

    # Shutdown
    _shutdown(portfolio, connector)


def _scan_and_execute(symbol, connector, portfolio, guard, now, profile):
    from live_scanner import fetch_bars, fetch_prev_day_levels

    df_1m  = fetch_bars(connector, symbol, profile.tf_entry_min, profile.bars_entry)
    df_15m = fetch_bars(connector, symbol, profile.tf_setup_min, profile.bars_setup)
    df_30m = fetch_bars(connector, symbol, profile.tf_trend_min, profile.bars_trend)

    if df_1m.empty or df_15m.empty or df_30m.empty:
        return

    prev_h, prev_l, prev_c = fetch_prev_day_levels(connector, symbol)
    if prev_h == 0:
        return

    swing_high = float(df_30m["high"].tail(20).max())
    swing_low  = float(df_30m["low"].tail(20).min())

    signal = generate_signal(
        instrument=symbol, df_30m=df_30m, df_15m=df_15m, df_1m=df_1m,
        prev_day_high=prev_h, prev_day_low=prev_l, prev_day_close=prev_c,
        swing_high=swing_high, swing_low=swing_low,
        equity=portfolio.equity, risk_pct=profile.risk_per_trade,
        current_open_risk_pct=portfolio.open_risk_pct,
        peak_equity=portfolio.peak_equity,
    )

    if signal is None:
        print(f"    [{symbol:6s}] no signal")
        return
    if signal.invalidated:
        print(f"    [{symbol:6s}] invalidated — {signal.invalidation_reason}")
        return
    if signal.signal_strength < profile.min_signal_strength:
        print(f"    [{symbol:6s}] weak ({signal.signal_strength}/100 < {profile.min_signal_strength})")
        return
    if signal.confluence_score < profile.min_confluence:
        print(f"    [{symbol:6s}] low confluence ({signal.confluence_score}/5 < {profile.min_confluence})")
        return

    print(f"    [{symbol:6s}] SIGNAL {signal.direction.upper():5s} "
          f"str={signal.signal_strength} conf={signal.confluence_score}/5 "
          f"pattern={signal.pattern_15m} "
          f"entry=${signal.entry_price:.2f} stop=${signal.stop_loss:.2f}")

    current_price = float(df_1m["close"].iloc[-1])
    engine = ExecutionEngine(signal=signal, session_guard=guard, connector=connector)
    accepted, reason = engine.accept(current_price=current_price, now=now)

    if accepted:
        portfolio.add(symbol, engine)
        print(f"    [{symbol:6s}] ACCEPTED — {signal.position_size_1pct:.0f} shares")
    else:
        print(f"    [{symbol:6s}] REJECTED — {reason}")


def _shutdown(portfolio, connector):
    print(f"\n{'='*64}")
    print("  SCANNER STOPPED — closing open positions")
    print(f"{'='*64}")

    for sym, engine in portfolio.engines.items():
        if engine.trade.status not in (TradeStatus.CLOSED, TradeStatus.CANCELLED):
            try:
                price = connector.get_latest_price(sym)
                engine.force_close(price, reason="Scanner stopped")
                print(f"  [{sym}] force closed @ ${price:.2f}")
            except Exception as e:
                print(f"  [{sym}] close error: {e}")

    print(f"\n  TRADE SUMMARY:")
    print(f"{'─'*64}")
    total_r = 0.0
    for engine in portfolio.all_engines:
        report = engine.get_report()
        sig = engine.signal
        pnl_r = engine.trade.realized_pnl / max(sig.position_size_1pct, 1)
        total_r += pnl_r
        dur = f"{report.hold_duration_min:.0f}m" if report.hold_duration_min else "—"
        print(f"  {sig.instrument:6s} {sig.direction:5s} str={sig.signal_strength:3d} "
              f"pnl={pnl_r:+.2f}R dur={dur} {engine.trade.status.value}")
    if portfolio.all_engines:
        print(f"{'─'*64}")
        print(f"  Total: {total_r:+.2f}R across {len(portfolio.all_engines)} trade(s)")
    else:
        print("  No trades executed.")
    print(f"{'='*64}\n")


# ─────────────────────────────────────────────────────────────────
# Interactive menu + CLI entry point
# ─────────────────────────────────────────────────────────────────

def interactive_setup() -> StrategyProfile:
    """Walk the owner through strategy configuration."""
    print()
    print("=" * 60)
    print("  LARRY — TRADE SESSION CONFIGURATOR")
    print("  Configure your strategy before we start scanning.")
    print("=" * 60)

    # Step 1: Horizon
    print("\n  STEP 1 — Trade Horizon")
    print("  ─────────────────────────────────")
    print("  [1] Short   — 1-5 min holds (scalp)")
    print("  [2] Medium  — 5-30 min holds (intraday swing)")
    print("  [3] Long    — 30m-2h holds (intraday position)")
    while True:
        choice = input("\n  Select horizon [1/2/3]: ").strip()
        if choice == "1":
            horizon = Horizon.SHORT
            break
        elif choice == "2":
            horizon = Horizon.MEDIUM
            break
        elif choice == "3":
            horizon = Horizon.LONG
            break
        print("  Invalid choice. Enter 1, 2, or 3.")

    # Step 2: Asset class
    print(f"\n  STEP 2 — Asset Class")
    print("  ─────────────────────────────────")
    print(f"  [1] Stocks      — {', '.join(WATCHLISTS[AssetClass.STOCKS][:5])}...")
    print(f"  [2] Forex       — {', '.join(WATCHLISTS[AssetClass.FOREX][:4])}...")
    print(f"  [3] Commodities — {', '.join(WATCHLISTS[AssetClass.COMMODITIES][:4])}...")
    while True:
        choice = input("\n  Select asset class [1/2/3]: ").strip()
        if choice == "1":
            asset_class = AssetClass.STOCKS
            break
        elif choice == "2":
            asset_class = AssetClass.FOREX
            break
        elif choice == "3":
            asset_class = AssetClass.COMMODITIES
            break
        print("  Invalid choice. Enter 1, 2, or 3.")

    # Step 3: Pax analysis
    print(f"\n  STEP 3 — Pax Market Analysis")
    print("  ─────────────────────────────────")
    print("  Connecting to Alpaca for live market data...")

    try:
        connector = connect()
        tf = TIMEFRAME_CONFIG[horizon]
        analysis = pax_analyze(connector, WATCHLISTS[asset_class],
                               tf["tf_trend_min"], tf["bars_trend"])
        print()
        print(analysis)
    except Exception as e:
        analysis = f"Analysis unavailable: {e}"
        print(f"\n  [WARN] Could not run Pax analysis: {e}")
        print("  Proceeding without market analysis.")

    # Step 4: Risk level (after seeing Pax's analysis)
    print(f"\n  STEP 4 — Risk Level (choose after reviewing Pax's analysis)")
    print("  ─────────────────────────────────")
    print("  [1] Conservative — 0.5% risk, 2 max positions, tight filters")
    print("  [2] Moderate     — 1.0% risk, 3 max positions, standard filters")
    print("  [3] Aggressive   — 1.5% risk, 4 max positions, wider filters")
    while True:
        choice = input("\n  Select risk level [1/2/3]: ").strip()
        if choice == "1":
            risk_level = RiskLevel.CONSERVATIVE
            break
        elif choice == "2":
            risk_level = RiskLevel.MODERATE
            break
        elif choice == "3":
            risk_level = RiskLevel.AGGRESSIVE
            break
        print("  Invalid choice. Enter 1, 2, or 3.")

    profile = build_profile(horizon, asset_class, risk_level, pax_analysis=analysis)
    return profile


def main():
    parser = argparse.ArgumentParser(description="Configurable trade launcher")
    parser.add_argument("--horizon", choices=["short", "medium", "long"], default=None)
    parser.add_argument("--assets", choices=["stocks", "forex", "commodities"], default=None)
    parser.add_argument("--risk", choices=["conservative", "moderate", "aggressive"], default=None)
    parser.add_argument("--hours", type=float, default=None, help="Auto-stop after N hours")
    parser.add_argument("--cycles", type=int, default=None, help="Max scan cycles")
    args = parser.parse_args()

    if args.horizon and args.assets and args.risk:
        # CLI mode — skip interactive
        profile = build_profile(
            Horizon(args.horizon),
            AssetClass(args.assets),
            RiskLevel(args.risk),
        )
    else:
        # Interactive mode
        profile = interactive_setup()

    print(f"\n  Duration: {'indefinite' if not args.hours else f'{args.hours}h'}")
    confirm = input("  Start scanning? [y/n]: ").strip().lower()
    if confirm != "y":
        print("  Cancelled.")
        return

    run_configured_scanner(
        profile,
        max_cycles=args.cycles,
        duration_hours=args.hours,
    )


if __name__ == "__main__":
    main()
