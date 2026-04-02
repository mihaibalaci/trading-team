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
import multiprocessing as mp
from multiprocessing import Process, Queue, Event
from multiprocessing.managers import SyncManager
from datetime import datetime, date, time as dtime
from typing import Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(processName)-10s] %(levelname)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("service")

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
    proc_log = logging.getLogger("Kai")
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
                proc_log.info(f"Connected — {acct.trading_mode} mode, equity ${acct.equity:,.2f}")
                break
            else:
                proc_log.warning(f"Attempt {attempt+1}/5 failed: {detail}")
                time.sleep(5)

        if not shared["kai_ready"]:
            proc_log.error("Could not connect after 5 attempts. Kai shutting down.")
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
        shared["kai_ready"] = False
    finally:
        proc_log.info("Shutting down.")


# ─────────────────────────────────────────────────────────────────
# Agent: Clio — Strategy Loader
# ─────────────────────────────────────────────────────────────────

def clio_process(shared: dict, strategy_queue: Queue, shutdown: Event):
    """
    Clio loads all strategy profiles into memory and pushes them
    to the strategy_queue for Finn to consume.
    """
    proc_log = logging.getLogger("Clio")
    proc_log.info("Starting — loading strategies into memory...")

    from strategy_config import (
        Horizon, AssetClass, RiskLevel, build_profile,
        WATCHLISTS, TIMEFRAME_CONFIG, RISK_CONFIG,
    )

    # Build all strategy combinations that make sense
    STRATEGY_PRESETS = [
        # (name, horizon, asset_class, risk_level)
        ("MTF-Scalp-Stocks", Horizon.SHORT, AssetClass.STOCKS, RiskLevel.MODERATE),
        ("MTF-Scalp-Forex", Horizon.SHORT, AssetClass.FOREX, RiskLevel.MODERATE),
        ("Swing-Stocks", Horizon.MEDIUM, AssetClass.STOCKS, RiskLevel.MODERATE),
        ("Swing-Commodities", Horizon.MEDIUM, AssetClass.COMMODITIES, RiskLevel.CONSERVATIVE),
        ("Position-Stocks", Horizon.LONG, AssetClass.STOCKS, RiskLevel.CONSERVATIVE),
        ("Scalp-Stocks-Aggressive", Horizon.SHORT, AssetClass.STOCKS, RiskLevel.AGGRESSIVE),
    ]

    profiles = []
    for name, horizon, asset_class, risk_level in STRATEGY_PRESETS:
        profile = build_profile(horizon, asset_class, risk_level)
        profiles.append((name, profile))
        proc_log.info(f"  Loaded: {name} ({horizon.value}/{asset_class.value}/{risk_level.value})")

    # Push all profiles to the queue for Finn
    for name, profile in profiles:
        strategy_queue.put((name, profile))

    shared["strategies_loaded"] = len(profiles)
    shared["clio_ready"] = True
    proc_log.info(f"All {len(profiles)} strategies loaded. Clio standing by.")

    # Clio stays alive to handle reloads if needed
    while not shutdown.is_set():
        shutdown.wait(60)

    proc_log.info("Shutting down.")


# ─────────────────────────────────────────────────────────────────
# Agent: Mira — Risk Monitor
# ─────────────────────────────────────────────────────────────────

def mira_process(shared: dict, shutdown: Event):
    """
    Mira monitors portfolio risk continuously.
    Sets shared['mira_halt'] = True if drawdown exceeds limits.
    """
    proc_log = logging.getLogger("Mira")
    proc_log.info("Starting — risk monitoring active.")
    shared["mira_ready"] = True

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

    proc_log.info("Shutting down.")


# ─────────────────────────────────────────────────────────────────
# Agent: Finn — Signal Scanner (multi-strategy)
# ─────────────────────────────────────────────────────────────────

def finn_process(shared: dict, strategy_queue: Queue, signal_queue: Queue, shutdown: Event):
    """
    Finn consumes strategy profiles from Clio, then runs continuous
    scans across all strategies. Valid signals go to signal_queue for Remy.
    """
    proc_log = logging.getLogger("Finn")
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
                        current_open_risk_pct=shared.get("active_trades", 0) * profile.risk_per_trade,
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
    proc_log.info("Shutting down.")


# ─────────────────────────────────────────────────────────────────
# Agent: Remy — Execution Engine
# ─────────────────────────────────────────────────────────────────

def remy_process(shared: dict, signal_queue: Queue, shutdown: Event):
    """
    Remy listens for signals from Finn and executes them.
    Manages the full trade lifecycle for each accepted signal.
    """
    proc_log = logging.getLogger("Remy")
    proc_log.info("Starting — waiting for Kai...")

    while not shutdown.is_set():
        if shared.get("kai_ready"):
            break
        time.sleep(1)

    if shutdown.is_set():
        return

    proc_log.info("Kai ready. Listening for signals from Finn.")
    shared["remy_running"] = True

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
                    shared["active_trades"] = len(active_engines)
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
        shared["active_trades"] = len(active_engines)

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

    shared["remy_running"] = False
    proc_log.info("Shutdown complete.")


# ─────────────────────────────────────────────────────────────────
# Agent: Larry — Web Dashboard (Flask)
# ─────────────────────────────────────────────────────────────────

def larry_process(shared: dict, shutdown: Event):
    """
    Larry runs the Flask web dashboard. Reads shared state for display.
    """
    proc_log = logging.getLogger("Larry")
    proc_log.info("Starting web dashboard on port 5050...")

    # Import and patch the web app to use shared state
    from database import init_db, ensure_default_admin
    init_db()
    ensure_default_admin()

    # Monkey-patch the web app's state to read from shared dict
    import web_app
    original_check = web_app.state._check_broker

    def _sync_shared():
        """Sync shared multiprocessing state into web_app.state."""
        web_app.state.equity = shared.get("equity", 100_000)
        web_app.state.peak_equity = shared.get("peak_equity", 100_000)
        web_app.state.starting_equity = shared.get("starting_equity", 100_000)
        web_app.state.broker_config["connected"] = shared.get("broker_connected", False)
        web_app.state.broker_config["mode"] = shared.get("broker_mode", "paper")
        web_app.state.broker_config["equity"] = shared.get("equity", 0)
        web_app.state.scanner_running = shared.get("finn_running", False)

    # Add a before_request hook to sync state
    @web_app.app.before_request
    def sync_state():
        _sync_shared()

    shared["larry_running"] = True
    proc_log.info("Dashboard ready at http://localhost:5050")

    try:
        web_app.app.run(host="0.0.0.0", port=5050, debug=False, threaded=True,
                        use_reloader=False)
    except Exception as e:
        proc_log.error(f"Flask error: {e}")
    finally:
        shared["larry_running"] = False
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
    print("  Boot sequence: Kai → Clio → Mira → Finn → Remy → Larry")
    print("=" * 64)
    print()

    # Init database
    from database import init_db, ensure_default_admin
    init_db()
    ensure_default_admin()

    # Shared state and queues
    manager = mp.Manager()
    shared = create_shared_state(manager)
    strategy_queue = Queue()
    signal_queue = Queue()
    shutdown = Event()

    # Define agent processes in boot order
    agents = [
        ("Kai",  kai_process,  (shared, shutdown)),
        ("Clio", clio_process, (shared, strategy_queue, shutdown)),
        ("Mira", mira_process, (shared, shutdown)),
        ("Finn", finn_process, (shared, strategy_queue, signal_queue, shutdown)),
        ("Remy", remy_process, (shared, signal_queue, shutdown)),
        ("Larry", larry_process, (shared, shutdown)),
    ]

    processes: list[Process] = []

    def graceful_shutdown(signum=None, frame=None):
        print("\n\n  Shutting down all agents...")
        shutdown.set()
        # Save state before stopping
        try:
            import json as _json
            CHECKPOINT_PATH = os.path.join(os.path.dirname(__file__), "checkpoint.json")
            snap = {k: shared[k] for k in shared.keys()}
            with open(CHECKPOINT_PATH, "w") as f:
                _json.dump(snap, f, default=str)
            print("  Checkpoint saved.")
        except Exception:
            pass
        for p in reversed(processes):
            if p.is_alive():
                print(f"  Stopping {p.name}...")
                p.join(timeout=10)
                if p.is_alive():
                    p.terminate()
        print("  All agents stopped.")
        print("=" * 64)

    sig.signal(sig.SIGINT, graceful_shutdown)
    sig.signal(sig.SIGTERM, graceful_shutdown)

    # Boot agents sequentially
    for name, target, proc_args in agents:
        print(f"  [{name:6s}] Starting...")
        p = Process(target=target, args=proc_args, name=name, daemon=True)
        p.start()
        processes.append(p)

        # Kai must be ready before others proceed
        if name == "Kai":
            timeout = 30
            start = time.time()
            while time.time() - start < timeout:
                if shared.get("kai_ready", False):
                    print(f"  [{name:6s}] Ready ✓")
                    break
                if not p.is_alive():
                    print(f"  [{name:6s}] FAILED — broker not connected. Dashboard will start without trading.")
                    break
                time.sleep(0.5)
            else:
                print(f"  [{name:6s}] TIMEOUT — starting dashboard without broker.")

        # Wait for Clio to load strategies
        elif name == "Clio":
            timeout = 10
            start = time.time()
            while time.time() - start < timeout:
                if shared.get("clio_ready", False):
                    print(f"  [{name:6s}] Ready ✓ — {shared.get('strategies_loaded', 0)} strategies loaded")
                    break
                time.sleep(0.5)

        # Small delay between other agents
        else:
            time.sleep(0.5)
            if p.is_alive():
                print(f"  [{name:6s}] Running ✓")

    print()
    print("=" * 64)
    print("  ALL AGENTS RUNNING")
    print(f"  Kai:  {'Connected' if shared.get('kai_ready') else 'Disconnected'}")
    print(f"  Clio: {shared.get('strategies_loaded', 0)} strategies loaded")
    print(f"  Mira: Risk monitoring active")
    print(f"  Finn: Scanning with {shared.get('strategies_loaded', 0)} strategies")
    print(f"  Remy: Listening for signals")
    print(f"  Larry: Dashboard at http://localhost:5050")
    print()
    print("  Press Ctrl+C to stop all agents.")
    print("=" * 64)

    # ── Agent registry for restart support ──────────────────────────
    agent_registry = {}
    for (name, target, proc_args), p in zip(agents, processes):
        agent_registry[name.lower()] = {
            "target": target, "args": proc_args, "process": p,
        }
        shared[f"status_{name.lower()}"] = "running" if p.is_alive() else "failed"

    # ── State checkpoint: dump to disk every 30s ─────────────────
    import json as _json
    CHECKPOINT_PATH = os.path.join(os.path.dirname(__file__), "checkpoint.json")

    def save_checkpoint():
        """Persist shared state to disk so work isn't lost on crash."""
        try:
            snap = {k: shared[k] for k in shared.keys()}
            with open(CHECKPOINT_PATH, "w") as f:
                _json.dump(snap, f, default=str)
        except Exception as e:
            log.debug(f"Checkpoint save error: {e}")

    def load_checkpoint():
        """Restore shared state from last checkpoint if available."""
        try:
            if os.path.exists(CHECKPOINT_PATH):
                with open(CHECKPOINT_PATH) as f:
                    snap = _json.load(f)
                for k, v in snap.items():
                    if k not in ("kai_ready", "clio_ready", "mira_ready",
                                 "finn_running", "remy_running", "larry_running"):
                        shared[k] = v
                log.info(f"Restored checkpoint: equity=${snap.get('equity', 0):,.2f}, "
                         f"trades={snap.get('total_trades', 0)}")
        except Exception as e:
            log.debug(f"Checkpoint load error: {e}")

    load_checkpoint()

    # ── Main loop: health monitor + command handler + checkpoint ──
    last_checkpoint = time.time()

    try:
        while not shutdown.is_set():
            now = time.time()

            for name_lower, reg in agent_registry.items():
                p = reg["process"]

                # Update status
                if p.is_alive():
                    shared[f"status_{name_lower}"] = "running"
                elif not shutdown.is_set():
                    shared[f"status_{name_lower}"] = "failed"
                    log.warning(f"Agent {name_lower} died (exit code {p.exitcode})")

                # Handle commands from dashboard
                cmd = shared.get(f"cmd_{name_lower}")
                if cmd:
                    shared[f"cmd_{name_lower}"] = ""  # clear command
                    log.info(f"Command: {name_lower} → {cmd}")

                    if cmd == "stop" and p.is_alive():
                        p.terminate()
                        p.join(timeout=5)
                        if p.is_alive():
                            p.kill()
                        shared[f"status_{name_lower}"] = "stopped"
                        log.info(f"Stopped {name_lower}")

                    elif cmd == "start" and not p.is_alive():
                        new_p = Process(
                            target=reg["target"], args=reg["args"],
                            name=name_lower.capitalize(), daemon=True,
                        )
                        new_p.start()
                        reg["process"] = new_p
                        shared[f"status_{name_lower}"] = "starting"
                        log.info(f"Started {name_lower}")

                    elif cmd == "restart":
                        if p.is_alive():
                            p.terminate()
                            p.join(timeout=5)
                            if p.is_alive():
                                p.kill()
                        save_checkpoint()
                        new_p = Process(
                            target=reg["target"], args=reg["args"],
                            name=name_lower.capitalize(), daemon=True,
                        )
                        new_p.start()
                        reg["process"] = new_p
                        shared[f"status_{name_lower}"] = "starting"
                        log.info(f"Restarted {name_lower}")

                    elif cmd == "reload":
                        shared[f"reload_{name_lower}"] = True
                        log.info(f"Reload signal sent to {name_lower}")

            # Periodic checkpoint
            if now - last_checkpoint >= 30:
                save_checkpoint()
                last_checkpoint = now

            time.sleep(3)

    except KeyboardInterrupt:
        save_checkpoint()
        graceful_shutdown()

    # Wait for all processes
    for p in processes:
        p.join(timeout=5)

    print("\n  Service stopped.\n")


if __name__ == "__main__":
    main()
