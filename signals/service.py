"""
service.py — Multi-process orchestrator for the AI Trading Team.

Boot sequence:
  1. Kai    — broker connectivity check (must pass before anything else)
  2. Clio   — load strategies into memory
  3. Mira   — risk monitor (watches portfolio exposure, drawdown)
  4. Finn   — signal scanner (runs all strategies concurrently)
  5. Remy   — execution engine (listens for signals from Finn)
  6. Larry  — web dashboard (Flask UI on port 5050)

Each agent runs as a separate process. Inter-process communication
uses multiprocessing.Queue for signal passing (Finn → Remy) and
a shared Manager dict for state.

Usage:
    python3 signals/service.py              # foreground
    python3 signals/service.py --daemon     # background (for systemd)
"""
from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

import argparse
import signal as sig
import time
import logging
import logging.handlers
import multiprocessing as mp
from multiprocessing import Process, Queue, Event
from multiprocessing.managers import SyncManager
from datetime import datetime, date, time as dtime
from typing import Optional

LOG_DIR = os.path.join(os.path.dirname(__file__), "logs")
os.makedirs(LOG_DIR, exist_ok=True)

_LOG_FORMAT  = "%(asctime)s [%(levelname)-8s] %(name)s — %(message)s"
_LOG_DATEFMT = "%Y-%m-%d %H:%M:%S"

logging.basicConfig(
    level=logging.INFO,
    format=_LOG_FORMAT,
    datefmt=_LOG_DATEFMT,
)
log = logging.getLogger("service")

# Orchestrator log file
_svc_handler = logging.handlers.RotatingFileHandler(
    os.path.join(LOG_DIR, "service.log"),
    maxBytes=5 * 1024 * 1024,
    backupCount=5,
)
_svc_handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_LOG_DATEFMT))
logging.getLogger().addHandler(_svc_handler)


def _setup_agent_log(agent_name: str) -> logging.Logger:
    """
    Call at the top of each agent process.
    Returns the agent's logger with a dedicated rotating file handler.
    stdout output is preserved (via inherited basicConfig).
    """
    logger = logging.getLogger(agent_name.capitalize())
    fh = logging.handlers.RotatingFileHandler(
        os.path.join(LOG_DIR, f"{agent_name.lower()}.log"),
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
    )
    fh.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_LOG_DATEFMT))
    logger.addHandler(fh)
    return logger

# ─────────────────────────────────────────────────────────────────
# Shared state via Manager
# ─────────────────────────────────────────────────────────────────

def create_shared_state(manager: SyncManager) -> dict:
    """Create the shared state dict accessible by all agent processes."""
    return manager.dict({
        "equity": 100_000.0,
        "starting_equity": 100_000.0,
        "peak_equity": 100_000.0,
        "broker_connected": False,
        "broker_mode": "paper",
        "kai_ready": False,
        "clio_ready": False,
        "mira_ready": False,
        "finn_running": False,
        "remy_running": False,
        "larry_running": False,
        "active_trades": 0,
        "total_trades": 0,
        "total_pnl_r": 0.0,
        "drawdown_pct": 0.0,
        "mira_halt": False,
        "strategies_loaded": 0,
        # Per-agent lifecycle status: "starting", "running", "stopped", "failed"
        "status_larry": "stopped",
        "status_kai":   "stopped",
        "status_clio":  "stopped",
        "status_mira":  "stopped",
        "status_finn":  "stopped",
        "status_remy":  "stopped",
        # Per-agent commands written by the web UI, consumed by the main loop
        "cmd_larry": "",
        "cmd_kai":   "",
        "cmd_clio":  "",
        "cmd_mira":  "",
        "cmd_finn":  "",
        "cmd_sage":  "",
        "cmd_remy":  "",
        "cmd_cole":  "",
        # Per-executor active position counts (used by scanners for open risk calc)
        "remy_active": 0,
        "cole_active": 0,
        # Sage and Cole lifecycle
        "sage_running": False,
        "cole_running": False,
        "status_sage": "stopped",
        "status_cole": "stopped",
    })


# ─────────────────────────────────────────────────────────────────
# Agent: Kai — Broker Connectivity
# ─────────────────────────────────────────────────────────────────

def kai_process(shared: dict, shutdown: Event):
    """
    Kai boots first. Tests broker connection, then monitors health
    every 30s. Sets shared['kai_ready'] = True when connected.
    If connection drops, sets kai_ready = False (halts Finn/Remy).
    """
    proc_log = _setup_agent_log("kai")
    shared["status_kai"] = "starting"
    proc_log.info("Starting — testing broker connection...")

    try:
        from broker_connector import connect
        connector = connect()

        # Initial connection test with retries
        for attempt in range(5):
            ok, detail = connector.health_check()
            if ok:
                acct = connector.get_account_state()
                shared["broker_connected"] = True
                shared["broker_mode"] = acct.trading_mode
                shared["equity"] = acct.equity
                shared["starting_equity"] = acct.equity
                shared["peak_equity"] = acct.equity
                shared["kai_ready"] = True
                shared["status_kai"] = "running"
                proc_log.info(f"Connected — {acct.trading_mode} mode, equity ${acct.equity:,.2f}")
                break
            else:
                proc_log.warning(f"Attempt {attempt+1}/5 failed: {detail}")
                time.sleep(5)

        if not shared["kai_ready"]:
            proc_log.error("Could not connect after 5 attempts. Kai shutting down.")
            shared["status_kai"] = "failed"
            return

        # Health monitoring loop
        while not shutdown.is_set():
            try:
                ok, detail = connector.health_check()
                if ok:
                    acct = connector.get_account_state()
                    shared["broker_connected"] = True
                    shared["equity"] = acct.equity
                    if acct.equity > shared["peak_equity"]:
                        shared["peak_equity"] = acct.equity
                else:
                    shared["broker_connected"] = False
                    proc_log.warning(f"Health check failed: {detail}")
            except Exception as e:
                shared["broker_connected"] = False
                proc_log.error(f"Health check error: {e}")

            shutdown.wait(30)

    except Exception as e:
        proc_log.error(f"Fatal error: {e}")
        shared["status_kai"] = "failed"
        shared["kai_ready"] = False
    finally:
        shared["kai_ready"] = False
        shared["broker_connected"] = False
        if shared.get("status_kai") != "failed":
            shared["status_kai"] = "stopped"
        proc_log.info("Shutting down.")


# ─────────────────────────────────────────────────────────────────
# Agent: Clio — Strategy Loader
# ─────────────────────────────────────────────────────────────────

def clio_process(shared: dict, strategy_queue_finn: Queue, strategy_queue_sage: Queue, shutdown: Event):
    """
    Clio loads all strategy profiles and routes them by horizon:
      SHORT  → strategy_queue_finn  (consumed by Finn)
      MEDIUM/LONG → strategy_queue_sage (consumed by Sage)
    """
    proc_log = _setup_agent_log("clio")
    shared["status_clio"] = "starting"
    proc_log.info("Starting — loading strategies into memory...")

    from strategy_config import (
        Horizon, AssetClass, RiskLevel, build_profile,
        WATCHLISTS, TIMEFRAME_CONFIG, RISK_CONFIG,
    )

    STRATEGY_PRESETS = [
        # (name, horizon, asset_class, risk_level)
        ("MTF-Scalp-Stocks",        Horizon.SHORT,  AssetClass.STOCKS,      RiskLevel.MODERATE),
        ("MTF-Scalp-Forex",         Horizon.SHORT,  AssetClass.FOREX,        RiskLevel.MODERATE),
        ("Scalp-Stocks-Aggressive", Horizon.SHORT,  AssetClass.STOCKS,      RiskLevel.AGGRESSIVE),
        ("Swing-Stocks",            Horizon.MEDIUM, AssetClass.STOCKS,      RiskLevel.MODERATE),
        ("Swing-Commodities",       Horizon.MEDIUM, AssetClass.COMMODITIES, RiskLevel.CONSERVATIVE),
        ("Position-Stocks",         Horizon.LONG,   AssetClass.STOCKS,      RiskLevel.CONSERVATIVE),
    ]

    finn_count = sage_count = 0
    for name, horizon, asset_class, risk_level in STRATEGY_PRESETS:
        profile = build_profile(horizon, asset_class, risk_level)
        if horizon == Horizon.SHORT:
            strategy_queue_finn.put((name, profile))
            finn_count += 1
            proc_log.info(f"  → Finn: {name} ({horizon.value}/{asset_class.value}/{risk_level.value})")
        else:
            strategy_queue_sage.put((name, profile))
            sage_count += 1
            proc_log.info(f"  → Sage: {name} ({horizon.value}/{asset_class.value}/{risk_level.value})")

    total = finn_count + sage_count
    shared["strategies_loaded"] = total
    shared["clio_ready"] = True
    shared["status_clio"] = "running"
    proc_log.info(f"All {total} strategies distributed ({finn_count} → Finn, {sage_count} → Sage). Clio standing by.")

    while not shutdown.is_set():
        shutdown.wait(60)

    shared["status_clio"] = "stopped"
    proc_log.info("Shutting down.")


# ─────────────────────────────────────────────────────────────────
# Agent: Mira — Risk Monitor
# ─────────────────────────────────────────────────────────────────

def mira_process(shared: dict, shutdown: Event):
    """
    Mira monitors portfolio risk continuously.
    Sets shared['mira_halt'] = True if drawdown exceeds limits.
    """
    proc_log = _setup_agent_log("mira")
    shared["status_mira"] = "starting"
    proc_log.info("Starting — risk monitoring active.")
    shared["mira_ready"] = True
    shared["status_mira"] = "running"

    DRAWDOWN_WARN = 5.0    # warn at 5%
    DRAWDOWN_REDUCE = 8.0  # reduce risk at 8%
    DRAWDOWN_HALT = 10.0   # halt trading at 10%

    while not shutdown.is_set():
        try:
            equity = shared.get("equity", 0)
            peak = shared.get("peak_equity", 0)

            if peak > 0:
                dd = (peak - equity) / peak * 100
                shared["drawdown_pct"] = round(dd, 2)

                if dd >= DRAWDOWN_HALT:
                    if not shared.get("mira_halt", False):
                        proc_log.critical(f"DRAWDOWN HALT — {dd:.1f}% exceeds {DRAWDOWN_HALT}% limit. "
                                          f"All trading suspended.")
                        shared["mira_halt"] = True
                elif dd >= DRAWDOWN_REDUCE:
                    proc_log.warning(f"Drawdown {dd:.1f}% — risk reduction mode active.")
                    shared["mira_halt"] = False
                elif dd >= DRAWDOWN_WARN:
                    proc_log.info(f"Drawdown {dd:.1f}% — monitoring closely.")
                    shared["mira_halt"] = False
                else:
                    shared["mira_halt"] = False

        except Exception as e:
            proc_log.error(f"Risk check error: {e}")

        shutdown.wait(10)

    shared["status_mira"] = "stopped"
    proc_log.info("Shutting down.")


# ─────────────────────────────────────────────────────────────────
# Agent: Finn — Signal Scanner (multi-strategy)
# ─────────────────────────────────────────────────────────────────

def finn_process(shared: dict, strategy_queue: Queue, signal_queue: Queue, shutdown: Event):
    """
    Finn consumes strategy profiles from Clio, then runs continuous
    scans across all strategies. Valid signals go to signal_queue for Remy.
    """
    proc_log = _setup_agent_log("finn")
    shared["status_finn"] = "starting"
    proc_log.info("Starting — waiting for Kai and Clio...")

    # Wait for dependencies
    while not shutdown.is_set():
        if shared.get("kai_ready") and shared.get("clio_ready"):
            break
        time.sleep(1)

    if shutdown.is_set():
        return

    proc_log.info("Dependencies ready. Loading strategies from Clio...")

    # Consume all strategies from queue
    strategies = []
    while not strategy_queue.empty():
        try:
            name, profile = strategy_queue.get_nowait()
            strategies.append((name, profile))
        except Exception:
            break

    proc_log.info(f"Loaded {len(strategies)} strategies. Starting scan loop.")
    shared["finn_running"] = True
    shared["status_finn"] = "running"

    from broker_connector import connect
    from signal_engine import generate_signal
    from execution import SessionGuard
    from live_scanner import fetch_bars, fetch_prev_day_levels

    connector = connect()
    cycle = 0

    while not shutdown.is_set():
        cycle += 1

        # Check if Mira has halted trading
        if shared.get("mira_halt", False):
            proc_log.info(f"[Cycle {cycle}] Mira halt active — skipping scan.")
            shutdown.wait(30)
            continue

        # Check broker connection
        if not shared.get("broker_connected", False):
            proc_log.warning(f"[Cycle {cycle}] Kai reports broker disconnected — waiting.")
            shutdown.wait(10)
            continue

        now = datetime.now()

        for strat_name, profile in strategies:
            if shutdown.is_set():
                break

            guard = SessionGuard(
                session_open=profile.session_open,
                session_close=profile.session_close,
            )
            ok, reason = guard.check(now)
            if not ok:
                continue  # outside session for this strategy

            # Scan each symbol in the watchlist
            for symbol in profile.watchlist:
                if shutdown.is_set():
                    break

                try:
                    df_1m = fetch_bars(connector, symbol, profile.tf_entry_min, profile.bars_entry)
                    df_15m = fetch_bars(connector, symbol, profile.tf_setup_min, profile.bars_setup)
                    df_30m = fetch_bars(connector, symbol, profile.tf_trend_min, profile.bars_trend)

                    if df_1m.empty or df_15m.empty or df_30m.empty:
                        continue

                    prev_h, prev_l, prev_c = fetch_prev_day_levels(connector, symbol)
                    if prev_h == 0:
                        continue

                    swing_high = float(df_30m["high"].tail(20).max())
                    swing_low = float(df_30m["low"].tail(20).min())

                    signal = generate_signal(
                        instrument=symbol, df_30m=df_30m, df_15m=df_15m, df_1m=df_1m,
                        prev_day_high=prev_h, prev_day_low=prev_l, prev_day_close=prev_c,
                        swing_high=swing_high, swing_low=swing_low,
                        equity=shared.get("equity", 100_000),
                        risk_pct=profile.risk_per_trade,
                        current_open_risk_pct=(shared.get("remy_active", 0) + shared.get("cole_active", 0)) * profile.risk_per_trade,
                        peak_equity=shared.get("peak_equity", 100_000),
                    )

                    if not signal or signal.invalidated:
                        continue
                    if signal.signal_strength < profile.min_signal_strength:
                        continue
                    if signal.confluence_score < profile.min_confluence:
                        continue

                    # Valid signal — send to Remy
                    proc_log.info(
                        f"SIGNAL [{strat_name}] {symbol} {signal.direction.upper()} "
                        f"str={signal.signal_strength} conf={signal.confluence_score}/5 "
                        f"pattern={signal.pattern_15m} entry=${signal.entry_price:.2f}"
                    )
                    signal_queue.put((strat_name, signal, profile))

                except Exception as e:
                    proc_log.debug(f"[{strat_name}/{symbol}] scan error: {e}")

        # Use the shortest scan interval across all strategies
        min_interval = min(p.scan_interval_s for _, p in strategies) if strategies else 60
        proc_log.debug(f"[Cycle {cycle}] complete. Next in {min_interval}s.")
        shutdown.wait(min_interval)

    shared["finn_running"] = False
    shared["status_finn"] = "stopped"
    proc_log.info("Shutting down.")


# ─────────────────────────────────────────────────────────────────
# Agent: Sage — Swing & Positional Signal Scanner
# ─────────────────────────────────────────────────────────────────

def sage_process(shared: dict, strategy_queue: Queue, signal_queue: Queue, shutdown: Event):
    """
    Sage consumes MEDIUM and LONG strategy profiles from Clio, then runs
    continuous scans at a slower cadence. Valid signals go to Cole.
    """
    proc_log = _setup_agent_log("sage")
    shared["status_sage"] = "starting"
    proc_log.info("Starting — waiting for Kai and Clio...")

    while not shutdown.is_set():
        if shared.get("kai_ready") and shared.get("clio_ready"):
            break
        time.sleep(1)

    if shutdown.is_set():
        return

    proc_log.info("Dependencies ready. Loading swing/positional strategies from Clio...")

    strategies = []
    while not strategy_queue.empty():
        try:
            name, profile = strategy_queue.get_nowait()
            strategies.append((name, profile))
        except Exception:
            break

    proc_log.info(f"Loaded {len(strategies)} strategies. Starting scan loop.")
    shared["sage_running"] = True
    shared["status_sage"] = "running"

    from broker_connector import connect
    from signal_engine import generate_signal
    from execution import SessionGuard
    from live_scanner import fetch_bars, fetch_prev_day_levels

    connector = connect()
    cycle = 0

    while not shutdown.is_set():
        cycle += 1

        if shared.get("mira_halt", False):
            proc_log.info(f"[Cycle {cycle}] Mira halt active — skipping scan.")
            shutdown.wait(60)
            continue

        if not shared.get("broker_connected", False):
            proc_log.warning(f"[Cycle {cycle}] Broker disconnected — waiting.")
            shutdown.wait(15)
            continue

        now = datetime.now()

        for strat_name, profile in strategies:
            if shutdown.is_set():
                break

            guard = SessionGuard(
                session_open=profile.session_open,
                session_close=profile.session_close,
            )
            ok, reason = guard.check(now)
            if not ok:
                continue

            for symbol in profile.watchlist:
                if shutdown.is_set():
                    break

                try:
                    df_1m  = fetch_bars(connector, symbol, profile.tf_entry_min,  profile.bars_entry)
                    df_15m = fetch_bars(connector, symbol, profile.tf_setup_min,  profile.bars_setup)
                    df_30m = fetch_bars(connector, symbol, profile.tf_trend_min,  profile.bars_trend)

                    if df_1m.empty or df_15m.empty or df_30m.empty:
                        continue

                    prev_h, prev_l, prev_c = fetch_prev_day_levels(connector, symbol)
                    if prev_h == 0:
                        continue

                    swing_high = float(df_30m["high"].tail(20).max())
                    swing_low  = float(df_30m["low"].tail(20).min())

                    signal = generate_signal(
                        instrument=symbol, df_30m=df_30m, df_15m=df_15m, df_1m=df_1m,
                        prev_day_high=prev_h, prev_day_low=prev_l, prev_day_close=prev_c,
                        swing_high=swing_high, swing_low=swing_low,
                        equity=shared.get("equity", 100_000),
                        risk_pct=profile.risk_per_trade,
                        current_open_risk_pct=(shared.get("remy_active", 0) + shared.get("cole_active", 0)) * profile.risk_per_trade,
                        peak_equity=shared.get("peak_equity", 100_000),
                    )

                    if not signal or signal.invalidated:
                        continue
                    if signal.signal_strength < profile.min_signal_strength:
                        continue
                    if signal.confluence_score < profile.min_confluence:
                        continue

                    proc_log.info(
                        f"SIGNAL [{strat_name}] {symbol} {signal.direction.upper()} "
                        f"str={signal.signal_strength} conf={signal.confluence_score}/5 "
                        f"pattern={signal.pattern_15m} entry=${signal.entry_price:.2f}"
                    )
                    signal_queue.put((strat_name, signal, profile))

                except Exception as e:
                    proc_log.debug(f"[{strat_name}/{symbol}] scan error: {e}")

        min_interval = min(p.scan_interval_s for _, p in strategies) if strategies else 120
        proc_log.debug(f"[Cycle {cycle}] complete. Next in {min_interval}s.")
        shutdown.wait(min_interval)

    shared["sage_running"] = False
    shared["status_sage"] = "stopped"
    proc_log.info("Shutting down.")


# ─────────────────────────────────────────────────────────────────
# Agent: Remy — Execution Engine
# ─────────────────────────────────────────────────────────────────

def remy_process(shared: dict, signal_queue: Queue, shutdown: Event):
    """
    Remy listens for signals from Finn and executes them.
    Manages the full trade lifecycle for each accepted signal.
    """
    proc_log = _setup_agent_log("remy")
    shared["status_remy"] = "starting"
    proc_log.info("Starting — waiting for Kai...")

    while not shutdown.is_set():
        if shared.get("kai_ready"):
            break
        time.sleep(1)

    if shutdown.is_set():
        return

    proc_log.info("Kai ready. Listening for signals from Finn.")
    shared["remy_running"] = True
    shared["status_remy"] = "running"

    from broker_connector import connect
    from execution import ExecutionEngine, SessionGuard, TradeStatus
    from database import insert_trade, update_trade_by_id

    connector = connect()
    active_engines: dict[str, tuple[ExecutionEngine, int]] = {}  # symbol -> (engine, db_trade_id)
    MAX_POSITIONS = 3

    while not shutdown.is_set():
        # Process incoming signals
        while not signal_queue.empty():
            try:
                strat_name, signal, profile = signal_queue.get_nowait()

                # Check Mira halt
                if shared.get("mira_halt", False):
                    proc_log.info(f"Mira halt — rejecting {signal.instrument}")
                    continue

                # Check position limits
                if len(active_engines) >= MAX_POSITIONS:
                    proc_log.info(f"Max positions ({MAX_POSITIONS}) — rejecting {signal.instrument}")
                    continue

                # Skip if already in this symbol
                if signal.instrument in active_engines:
                    continue

                guard = SessionGuard(
                    session_open=profile.session_open,
                    session_close=profile.session_close,
                )
                engine = ExecutionEngine(
                    signal=signal,
                    session_guard=guard,
                    connector=connector,
                )

                current_price = connector.get_latest_price(signal.instrument)
                accepted, reason = engine.accept(current_price=current_price, now=datetime.now())

                if accepted:
                    # Persist to DB (user_id=1 for service mode)
                    db_id = insert_trade(
                        user_id=1, symbol=signal.instrument, direction=signal.direction,
                        pattern=signal.pattern_15m, signal_strength=signal.signal_strength,
                        confluence=signal.confluence_score, entry_price=signal.entry_price,
                        stop_loss=signal.stop_loss, target_1=signal.target_1,
                        target_2=signal.target_2, position_size=signal.position_size_1pct,
                        status="active", horizon=profile.horizon.value,
                        asset_class=profile.asset_class.value, risk_level=profile.risk_level.value,
                        entry_time=datetime.now().strftime("%H:%M:%S"),
                        scanner_id=strat_name,
                    )
                    active_engines[signal.instrument] = (engine, db_id)
                    shared["remy_active"] = len(active_engines)
                    proc_log.info(f"ACCEPTED [{strat_name}] {signal.instrument} "
                                  f"{signal.direction.upper()} — {signal.position_size_1pct:.0f} shares")
                else:
                    proc_log.info(f"REJECTED {signal.instrument} — {reason}")

            except Exception as e:
                proc_log.error(f"Signal processing error: {e}")

        # Tick all active engines
        closed_syms = []
        for sym, (engine, db_id) in active_engines.items():
            try:
                if engine.trade.status in (TradeStatus.CLOSED, TradeStatus.CANCELLED):
                    pnl_r = engine.trade.realized_pnl / max(engine.signal.position_size_1pct, 1)
                    report = engine.get_report()
                    status = "won" if pnl_r > 0 else "lost" if pnl_r < 0 else "flat"
                    update_trade_by_id(db_id,
                        status=status, pnl_r=round(pnl_r, 2),
                        pnl_dollars=round(engine.trade.realized_pnl, 2),
                        exit_time=datetime.now().strftime("%H:%M:%S"),
                        duration_min=report.hold_duration_min or 0,
                    )
                    shared["total_trades"] = shared.get("total_trades", 0) + 1
                    shared["total_pnl_r"] = round(shared.get("total_pnl_r", 0) + pnl_r, 2)
                    proc_log.info(f"CLOSED {sym} — {status.upper()} {pnl_r:+.2f}R")
                    closed_syms.append(sym)
                else:
                    price = connector.get_latest_price(sym)
                    engine.tick(current_price=price, now=datetime.now())
            except Exception as e:
                proc_log.error(f"Tick error {sym}: {e}")

        for sym in closed_syms:
            del active_engines[sym]
        shared["remy_active"] = len(active_engines)

        shutdown.wait(2)  # tick every 2 seconds

    # Shutdown: close all positions
    proc_log.info("Shutting down — closing all positions...")
    for sym, (engine, db_id) in active_engines.items():
        try:
            price = connector.get_latest_price(sym)
            engine.force_close(price, reason="Service shutdown")
            pnl_r = engine.trade.realized_pnl / max(engine.signal.position_size_1pct, 1)
            update_trade_by_id(db_id, status="flat", pnl_r=round(pnl_r, 2),
                               exit_time=datetime.now().strftime("%H:%M:%S"))
            proc_log.info(f"Force closed {sym} @ ${price:.2f}")
        except Exception as e:
            proc_log.error(f"Close error {sym}: {e}")

    shared["remy_active"] = 0
    shared["remy_running"] = False
    shared["status_remy"] = "stopped"
    proc_log.info("Shutdown complete.")


# ─────────────────────────────────────────────────────────────────
# Agent: Cole — Swing Trade Execution Engine
# ─────────────────────────────────────────────────────────────────

def cole_process(shared: dict, signal_queue: Queue, shutdown: Event):
    """
    Cole listens for swing/positional signals from Sage and executes them.
    Manages up to 2 concurrent positions with a 5s tick cycle.
    """
    proc_log = _setup_agent_log("cole")
    shared["status_cole"] = "starting"
    proc_log.info("Starting — waiting for Kai...")

    while not shutdown.is_set():
        if shared.get("kai_ready"):
            break
        time.sleep(1)

    if shutdown.is_set():
        return

    proc_log.info("Kai ready. Listening for signals from Sage.")
    shared["cole_running"] = True
    shared["status_cole"] = "running"

    from broker_connector import connect
    from execution import ExecutionEngine, SessionGuard, TradeStatus
    from database import insert_trade, update_trade_by_id

    connector = connect()
    active_engines: dict[str, tuple[ExecutionEngine, int]] = {}
    MAX_POSITIONS = 2  # swing trades hold longer and use more capital

    while not shutdown.is_set():
        while not signal_queue.empty():
            try:
                strat_name, signal, profile = signal_queue.get_nowait()

                if shared.get("mira_halt", False):
                    proc_log.info(f"Mira halt — rejecting {signal.instrument}")
                    continue

                if len(active_engines) >= MAX_POSITIONS:
                    proc_log.info(f"Max positions ({MAX_POSITIONS}) — rejecting {signal.instrument}")
                    continue

                if signal.instrument in active_engines:
                    continue

                guard = SessionGuard(
                    session_open=profile.session_open,
                    session_close=profile.session_close,
                )
                engine = ExecutionEngine(
                    signal=signal,
                    session_guard=guard,
                    connector=connector,
                )

                current_price = connector.get_latest_price(signal.instrument)
                accepted, reason = engine.accept(current_price=current_price, now=datetime.now())

                if accepted:
                    db_id = insert_trade(
                        user_id=1, symbol=signal.instrument, direction=signal.direction,
                        pattern=signal.pattern_15m, signal_strength=signal.signal_strength,
                        confluence=signal.confluence_score, entry_price=signal.entry_price,
                        stop_loss=signal.stop_loss, target_1=signal.target_1,
                        target_2=signal.target_2, position_size=signal.position_size_1pct,
                        status="active", horizon=profile.horizon.value,
                        asset_class=profile.asset_class.value, risk_level=profile.risk_level.value,
                        entry_time=datetime.now().strftime("%H:%M:%S"),
                        scanner_id=strat_name,
                    )
                    active_engines[signal.instrument] = (engine, db_id)
                    shared["cole_active"] = len(active_engines)
                    proc_log.info(f"ACCEPTED [{strat_name}] {signal.instrument} "
                                  f"{signal.direction.upper()} — {signal.position_size_1pct:.0f} shares")
                else:
                    proc_log.info(f"REJECTED {signal.instrument} — {reason}")

            except Exception as e:
                proc_log.error(f"Signal processing error: {e}")

        closed_syms = []
        for sym, (engine, db_id) in active_engines.items():
            try:
                if engine.trade.status in (TradeStatus.CLOSED, TradeStatus.CANCELLED):
                    pnl_r = engine.trade.realized_pnl / max(engine.signal.position_size_1pct, 1)
                    report = engine.get_report()
                    status = "won" if pnl_r > 0 else "lost" if pnl_r < 0 else "flat"
                    update_trade_by_id(db_id,
                        status=status, pnl_r=round(pnl_r, 2),
                        pnl_dollars=round(engine.trade.realized_pnl, 2),
                        exit_time=datetime.now().strftime("%H:%M:%S"),
                        duration_min=report.hold_duration_min or 0,
                    )
                    shared["total_trades"] = shared.get("total_trades", 0) + 1
                    shared["total_pnl_r"] = round(shared.get("total_pnl_r", 0) + pnl_r, 2)
                    proc_log.info(f"CLOSED {sym} — {status.upper()} {pnl_r:+.2f}R")
                    closed_syms.append(sym)
                else:
                    price = connector.get_latest_price(sym)
                    engine.tick(current_price=price, now=datetime.now())
            except Exception as e:
                proc_log.error(f"Tick error {sym}: {e}")

        for sym in closed_syms:
            del active_engines[sym]
        shared["cole_active"] = len(active_engines)

        shutdown.wait(5)  # 5s tick — swing trades don't need 2s resolution

    proc_log.info("Shutting down — closing all positions...")
    for sym, (engine, db_id) in active_engines.items():
        try:
            price = connector.get_latest_price(sym)
            engine.force_close(price, reason="Service shutdown")
            pnl_r = engine.trade.realized_pnl / max(engine.signal.position_size_1pct, 1)
            update_trade_by_id(db_id, status="flat", pnl_r=round(pnl_r, 2),
                               exit_time=datetime.now().strftime("%H:%M:%S"))
            proc_log.info(f"Force closed {sym} @ ${price:.2f}")
        except Exception as e:
            proc_log.error(f"Close error {sym}: {e}")

    shared["cole_active"] = 0
    shared["cole_running"] = False
    shared["status_cole"] = "stopped"
    proc_log.info("Shutdown complete.")


# ─────────────────────────────────────────────────────────────────
# Agent: Larry — Web Dashboard (Flask)
# ─────────────────────────────────────────────────────────────────

def larry_process(shared: dict, shutdown: Event):
    """
    Larry runs the Flask web dashboard. Reads shared state for display.
    """
    proc_log = _setup_agent_log("larry")
    shared["status_larry"] = "starting"
    proc_log.info("Starting web dashboard on port 5050...")

    # Import and patch the web app to use shared state
    from database import init_db, ensure_default_admin
    init_db()
    ensure_default_admin()

    # Inject shared state into web_app so routes can read statuses and write commands
    import web_app
    web_app._shared = shared

    def _sync_shared():
        """Sync shared multiprocessing state into web_app.state."""
        web_app.state.equity = shared.get("equity", 100_000)
        web_app.state.peak_equity = shared.get("peak_equity", 100_000)
        web_app.state.starting_equity = shared.get("starting_equity", 100_000)
        web_app.state.broker_config["connected"] = shared.get("broker_connected", False)
        web_app.state.broker_config["mode"] = shared.get("broker_mode", "paper")
        web_app.state.broker_config["equity"] = shared.get("equity", 0)
        web_app.state.scanner_running = shared.get("finn_running", False) or shared.get("sage_running", False)

    # Add a before_request hook to sync state
    @web_app.app.before_request
    def sync_state():
        _sync_shared()

    shared["larry_running"] = True
    shared["status_larry"] = "running"
    proc_log.info("Dashboard ready at http://localhost:5050")

    try:
        web_app.app.run(host="0.0.0.0", port=5050, debug=False, threaded=True,
                        use_reloader=False)
    except Exception as e:
        proc_log.error(f"Flask error: {e}")
    finally:
        shared["larry_running"] = False
        shared["status_larry"] = "stopped"
        proc_log.info("Shutting down.")


# ─────────────────────────────────────────────────────────────────
# Main Orchestrator
# ─────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="AI Trading Team Service")
    parser.add_argument("--daemon", action="store_true", help="Run in background mode")
    args = parser.parse_args()

    print()
    print("=" * 64)
    print("  AI TRADING TEAM — SERVICE ORCHESTRATOR")
    print("  Boot sequence: Larry → Kai → Clio → Mira → Finn → Sage → Remy → Cole")
    print("=" * 64)
    print()

    # Init database
    from database import init_db, ensure_default_admin
    init_db()
    ensure_default_admin()

    # Shared state and queues
    manager = mp.Manager()
    shared = create_shared_state(manager)
    strategy_queue_finn = Queue()   # Clio → Finn  (SHORT strategies)
    strategy_queue_sage = Queue()   # Clio → Sage  (MEDIUM/LONG strategies)
    signal_queue_finn   = Queue()   # Finn → Remy
    signal_queue_sage   = Queue()   # Sage → Cole
    shutdown = Event()

    # Boot order — Larry first so the web UI is always available
    BOOT_ORDER = ["larry", "kai", "clio", "mira", "finn", "sage", "remy", "cole"]

    AGENT_TARGETS = {
        "larry": larry_process,
        "kai":   kai_process,
        "clio":  clio_process,
        "mira":  mira_process,
        "finn":  finn_process,
        "sage":  sage_process,
        "remy":  remy_process,
        "cole":  cole_process,
    }

    # Args are lambdas so they can be re-evaluated on restart
    AGENT_ARGS = {
        "larry": lambda: (shared, shutdown),
        "kai":   lambda: (shared, shutdown),
        "clio":  lambda: (shared, strategy_queue_finn, strategy_queue_sage, shutdown),
        "mira":  lambda: (shared, shutdown),
        "finn":  lambda: (shared, strategy_queue_finn, signal_queue_finn, shutdown),
        "sage":  lambda: (shared, strategy_queue_sage, signal_queue_sage, shutdown),
        "remy":  lambda: (shared, signal_queue_finn, shutdown),
        "cole":  lambda: (shared, signal_queue_sage, shutdown),
    }

    # Live process registry
    processes: dict[str, Process] = {}

    def spawn_agent(name: str) -> Process:
        shared[f"status_{name}"] = "starting"
        p = Process(
            target=AGENT_TARGETS[name],
            args=AGENT_ARGS[name](),
            name=name.capitalize(),
            daemon=True,
        )
        p.start()
        processes[name] = p
        return p

    # Ready-flag resets per agent (used when stopping/restarting)
    _READY_FLAGS: dict[str, list[tuple]] = {
        "kai":   [("kai_ready", False), ("broker_connected", False)],
        "clio":  [("clio_ready", False), ("strategies_loaded", 0)],
        "mira":  [("mira_ready", False)],
        "finn":  [("finn_running", False)],
        "sage":  [("sage_running", False)],
        "remy":  [("remy_running", False), ("remy_active", 0)],
        "cole":  [("cole_running", False), ("cole_active", 0)],
        "larry": [("larry_running", False)],
    }

    def stop_agent(name: str):
        p = processes.get(name)
        if p and p.is_alive():
            p.terminate()
            p.join(timeout=5)
            if p.is_alive():
                p.kill()
        for key, val in _READY_FLAGS.get(name, []):
            shared[key] = val
        shared[f"status_{name}"] = "stopped"

    def graceful_shutdown(signum=None, frame=None):
        print("\n\n  Shutting down all agents...")
        shutdown.set()
        for name in reversed(BOOT_ORDER):
            p = processes.get(name)
            if p and p.is_alive():
                print(f"  Stopping {name.capitalize()}...")
                p.join(timeout=10)
                if p.is_alive():
                    p.terminate()
        print("  All agents stopped.")
        print("=" * 64)

    sig.signal(sig.SIGINT, graceful_shutdown)
    sig.signal(sig.SIGTERM, graceful_shutdown)

    # ── Boot sequence ──────────────────────────────────────────────
    for name in BOOT_ORDER:
        print(f"  [{name.capitalize():6s}] Starting...")
        p = spawn_agent(name)

        if name == "larry":
            # Give Flask a moment to bind, then continue regardless
            time.sleep(1)
            if p.is_alive():
                print(f"  [Larry ] Running ✓  — dashboard at http://0.0.0.0:5050")

        elif name == "kai":
            # Wait up to 30s but do NOT exit if Kai fails — Larry is already up
            timeout = 30
            start_t = time.time()
            while time.time() - start_t < timeout:
                if shared.get("kai_ready", False):
                    print(f"  [Kai   ] Connected ✓")
                    break
                if not p.is_alive():
                    print(f"  [Kai   ] WARNING — broker unreachable. Kai marked failed.")
                    print(f"           Restart Kai from the web UI once the broker is available.")
                    break
                time.sleep(0.5)
            else:
                print(f"  [Kai   ] WARNING — connection timeout. Kai marked failed.")
                print(f"           Restart Kai from the web UI once the broker is available.")

        elif name == "clio":
            timeout = 10
            start_t = time.time()
            while time.time() - start_t < timeout:
                if shared.get("clio_ready", False):
                    print(f"  [Clio  ] Ready ✓ — {shared.get('strategies_loaded', 0)} strategies loaded")
                    break
                time.sleep(0.5)

        else:
            time.sleep(0.5)
            if p.is_alive():
                print(f"  [{name.capitalize():6s}] Running ✓")

    print()
    print("=" * 64)
    print("  SERVICE RUNNING")
    print(f"  Larry: http://0.0.0.0:5050")
    print(f"  Kai:   {'Connected' if shared.get('kai_ready') else 'FAILED — restart from web UI'}")
    print(f"  Clio:  {shared.get('strategies_loaded', 0)} strategies loaded")
    print(f"  Finn + Sage scanning | Remy + Cole executing")
    print()
    print("  Press Ctrl+C to stop all agents.")
    print("=" * 64)

    # ── Main loop: health monitoring + web command dispatch ────────
    try:
        while not shutdown.is_set():
            # Mark processes that died unexpectedly
            for name in BOOT_ORDER:
                p = processes.get(name)
                if p and not p.is_alive():
                    current = shared.get(f"status_{name}", "")
                    if current not in ("stopped", "failed"):
                        code = p.exitcode
                        shared[f"status_{name}"] = "failed" if (code is not None and code != 0) else "stopped"
                        if not shutdown.is_set():
                            log.warning(f"Agent {name} died (exit code {code})")

            # Process commands written by the web UI
            for name in BOOT_ORDER:
                cmd = shared.get(f"cmd_{name}", "")
                if not cmd:
                    continue
                shared[f"cmd_{name}"] = ""  # clear before acting

                p = processes.get(name)
                if cmd == "start":
                    if not p or not p.is_alive():
                        spawn_agent(name)
                        log.info(f"[CMD] {name}: started")
                    else:
                        log.info(f"[CMD] {name}: already running, ignoring start")
                elif cmd == "stop":
                    stop_agent(name)
                    log.info(f"[CMD] {name}: stopped")
                elif cmd in ("restart", "reload"):
                    stop_agent(name)
                    # When restarting Clio, also restart scanners so they re-consume strategies
                    if name == "clio":
                        stop_agent("finn")
                        stop_agent("sage")
                    time.sleep(0.5)
                    spawn_agent(name)
                    if name == "clio":
                        time.sleep(1)
                        spawn_agent("finn")
                        spawn_agent("sage")
                    log.info(f"[CMD] {name}: restarted")

            time.sleep(1)

    except KeyboardInterrupt:
        graceful_shutdown()

    # Wait for all processes to exit
    for name in BOOT_ORDER:
        p = processes.get(name)
        if p:
            p.join(timeout=5)

    print("\n  Service stopped.\n")


if __name__ == "__main__":
    main()
