"""
web_app.py — Flask web dashboard for the AI trading team.

Features:
  - Live dashboard: current positions, P&L, equity
  - Trade history: win/loss reports with R-multiple tracking
  - Strategy configurator: horizon, asset class, risk level
  - Platform settings: broker connection config
  - Scanner control: start/stop from the UI

Usage:
    python3 signals/web_app.py
    Open http://localhost:5050
"""
from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

import json
import threading
import time
import logging
from datetime import datetime, timedelta, time as dtime
from dataclasses import dataclass, field, asdict
from typing import Optional
from concurrent.futures import ThreadPoolExecutor

from flask import Flask, render_template_string, jsonify, request

from strategy_config import (
    Horizon, AssetClass, RiskLevel,
    build_profile, StrategyProfile,
    WATCHLISTS, TIMEFRAME_CONFIG, RISK_CONFIG, SESSION_CONFIG,
)

logging.basicConfig(level=logging.WARNING)
log = logging.getLogger(__name__)

app = Flask(__name__)

# ─────────────────────────────────────────────────────────────────
# In-memory state
# ─────────────────────────────────────────────────────────────────

@dataclass
class TradeRecord:
    id: int
    symbol: str
    direction: str
    pattern: str
    signal_strength: int
    confluence: int
    entry_price: float
    stop_loss: float
    target_1: float
    target_2: float
    position_size: float
    status: str          # "active", "won", "lost", "flat", "pending"
    pnl_r: float
    pnl_dollars: float
    entry_time: str
    exit_time: str
    duration_min: float
    horizon: str
    asset_class: str
    risk_level: str


class AppState:
    def __init__(self):
        self._lock = threading.RLock()
        self.trades: list[TradeRecord] = []
        self.next_id = 1
        self.equity = 100_000.0
        self.starting_equity = 100_000.0
        self.peak_equity = 100_000.0
        self.scanner_running = False
        self.scanner_thread: Optional[threading.Thread] = None
        self.current_profile: Optional[StrategyProfile] = None
        self.broker_config = {
            "platform": "alpaca",
            "mode": "paper",
            "connected": False,
            "equity": 0.0,
        }
        # Multi-scanner support: track multiple concurrent scanners
        self._scanners: dict[str, dict] = {}  # id -> {thread, profile, running}
        self._scanner_pool = ThreadPoolExecutor(max_workers=5, thread_name_prefix="scanner")
        self._check_broker()

    def _check_broker(self):
        try:
            from broker_connector import connect
            conn = connect()
            ok, detail = conn.health_check()
            if ok:
                acct = conn.get_account_state()
                with self._lock:
                    self.broker_config["connected"] = True
                    self.broker_config["equity"] = acct.equity
                    self.broker_config["mode"] = acct.trading_mode
                    self.equity = acct.equity
                    self.starting_equity = acct.equity
                    self.peak_equity = acct.equity
        except Exception:
            pass

    def add_trade(self, **kwargs) -> TradeRecord:
        with self._lock:
            t = TradeRecord(id=self.next_id, **kwargs)
            self.next_id += 1
            self.trades.append(t)
            return t

    def update_trade(self, symbol: str, **updates):
        with self._lock:
            for t in self.trades:
                if t.symbol == symbol and t.status in ("active", "pending"):
                    for k, v in updates.items():
                        setattr(t, k, v)
                    break

    def update_equity(self, equity: float):
        with self._lock:
            self.equity = equity
            if equity > self.peak_equity:
                self.peak_equity = equity
            self.broker_config["equity"] = equity

    @property
    def active_trades(self):
        with self._lock:
            return [t for t in self.trades if t.status in ("active", "pending")]

    @property
    def closed_trades(self):
        with self._lock:
            return [t for t in self.trades if t.status in ("won", "lost", "flat")]

    @property
    def wins(self):
        with self._lock:
            return [t for t in self.trades if t.status == "won"]

    @property
    def losses(self):
        with self._lock:
            return [t for t in self.trades if t.status == "lost"]

    @property
    def total_pnl_r(self):
        with self._lock:
            return sum(t.pnl_r for t in self.trades if t.status in ("won", "lost", "flat"))

    @property
    def win_rate(self):
        with self._lock:
            closed = [t for t in self.trades if t.status in ("won", "lost", "flat")]
            if not closed:
                return 0.0
            wins = [t for t in closed if t.status == "won"]
            return len(wins) / len(closed) * 100

    @property
    def drawdown_pct(self):
        with self._lock:
            if self.peak_equity <= 0:
                return 0.0
            return (self.peak_equity - self.equity) / self.peak_equity * 100

    @property
    def active_scanner_count(self):
        with self._lock:
            return sum(1 for s in self._scanners.values() if s["running"])

    def get_scanners_info(self) -> list[dict]:
        with self._lock:
            return [
                {
                    "id": sid,
                    "horizon": s["profile"].horizon.value,
                    "asset_class": s["profile"].asset_class.value,
                    "risk_level": s["profile"].risk_level.value,
                    "running": s["running"],
                }
                for sid, s in self._scanners.items()
            ]


state = AppState()


# ─────────────────────────────────────────────────────────────────
# Scanner thread
# ─────────────────────────────────────────────────────────────────

def _scanner_worker(profile: StrategyProfile, scanner_id: str):
    """Background scanner thread — one per strategy profile. Thread-safe."""
    from broker_connector import connect
    from signal_engine import generate_signal
    from execution import ExecutionEngine, SessionGuard, TradeStatus
    from live_scanner import fetch_bars, fetch_prev_day_levels

    try:
        connector = connect()
        guard = SessionGuard(
            session_open=profile.session_open,
            session_close=profile.session_close,
        )
        acct = connector.get_account_state()
        state.update_equity(acct.equity)

        engines: dict[str, ExecutionEngine] = {}
        engines_lock = threading.Lock()
        cycle = 0

        while state._scanners.get(scanner_id, {}).get("running", False):
            cycle += 1
            now = datetime.now()

            # Update equity (thread-safe)
            try:
                acct = connector.get_account_state()
                state.update_equity(acct.equity)
            except Exception:
                pass

            # Tick open engines concurrently
            with engines_lock:
                engine_items = list(engines.items())

            for sym, engine in engine_items:
                if engine.trade.status in (TradeStatus.CLOSED, TradeStatus.CANCELLED):
                    pnl_r = engine.trade.realized_pnl / max(engine.signal.position_size_1pct, 1)
                    report = engine.get_report()
                    state.update_trade(
                        sym,
                        pnl_r=round(pnl_r, 2),
                        pnl_dollars=round(engine.trade.realized_pnl, 2),
                        status="won" if pnl_r > 0 else "lost" if pnl_r < 0 else "flat",
                        exit_time=now.strftime("%H:%M:%S"),
                        duration_min=report.hold_duration_min or 0,
                    )
                    with engines_lock:
                        engines.pop(sym, None)
                    continue
                try:
                    price = connector.get_latest_price(sym)
                    engine.tick(current_price=price, now=now)
                except Exception:
                    pass

            # Session check
            ok, _ = guard.check(now)
            with engines_lock:
                open_count = len([e for e in engines.values()
                                 if e.trade.status in (TradeStatus.ACTIVE, TradeStatus.PARTIAL_EXIT,
                                                       TradeStatus.PENDING_ENTRY)])
            open_risk = open_count * profile.risk_per_trade

            if ok and open_count < profile.max_positions:
                # Scan symbols in parallel using thread pool
                def _try_symbol(symbol):
                    nonlocal open_count, open_risk
                    with engines_lock:
                        if symbol in engines:
                            return
                        if open_count >= profile.max_positions:
                            return

                    try:
                        df_1m = fetch_bars(connector, symbol, profile.tf_entry_min, profile.bars_entry)
                        df_15m = fetch_bars(connector, symbol, profile.tf_setup_min, profile.bars_setup)
                        df_30m = fetch_bars(connector, symbol, profile.tf_trend_min, profile.bars_trend)

                        if df_1m.empty or df_15m.empty or df_30m.empty:
                            return

                        prev_h, prev_l, prev_c = fetch_prev_day_levels(connector, symbol)
                        if prev_h == 0:
                            return

                        swing_high = float(df_30m["high"].tail(20).max())
                        swing_low = float(df_30m["low"].tail(20).min())

                        signal = generate_signal(
                            instrument=symbol, df_30m=df_30m, df_15m=df_15m, df_1m=df_1m,
                            prev_day_high=prev_h, prev_day_low=prev_l, prev_day_close=prev_c,
                            swing_high=swing_high, swing_low=swing_low,
                            equity=state.equity, risk_pct=profile.risk_per_trade,
                            current_open_risk_pct=open_risk, peak_equity=state.peak_equity,
                        )

                        if not signal or signal.invalidated:
                            return
                        if signal.signal_strength < profile.min_signal_strength:
                            return
                        if signal.confluence_score < profile.min_confluence:
                            return

                        current_price = float(df_1m["close"].iloc[-1])
                        engine = ExecutionEngine(signal=signal, session_guard=guard, connector=connector)
                        accepted, reason = engine.accept(current_price=current_price, now=now)

                        if accepted:
                            with engines_lock:
                                if open_count >= profile.max_positions:
                                    return
                                engines[symbol] = engine
                                open_count += 1
                                open_risk += profile.risk_per_trade

                            state.add_trade(
                                symbol=symbol, direction=signal.direction,
                                pattern=signal.pattern_15m, signal_strength=signal.signal_strength,
                                confluence=signal.confluence_score, entry_price=signal.entry_price,
                                stop_loss=signal.stop_loss, target_1=signal.target_1,
                                target_2=signal.target_2, position_size=signal.position_size_1pct,
                                status="active", pnl_r=0.0, pnl_dollars=0.0,
                                entry_time=now.strftime("%H:%M:%S"), exit_time="",
                                duration_min=0, horizon=profile.horizon.value,
                                asset_class=profile.asset_class.value, risk_level=profile.risk_level.value,
                            )
                    except Exception:
                        pass

                # Fan out symbol scans across threads
                with ThreadPoolExecutor(max_workers=min(len(profile.watchlist), 5),
                                        thread_name_prefix="sym-scan") as pool:
                    pool.map(_try_symbol, profile.watchlist)

            time.sleep(profile.scan_interval_s)

    except Exception as e:
        log.error(f"Scanner {scanner_id} error: {e}")
    finally:
        with state._lock:
            if scanner_id in state._scanners:
                state._scanners[scanner_id]["running"] = False


# ─────────────────────────────────────────────────────────────────
# API Routes
# ─────────────────────────────────────────────────────────────────

@app.route("/api/status")
def api_status():
    return jsonify({
        "equity": state.equity,
        "starting_equity": state.starting_equity,
        "peak_equity": state.peak_equity,
        "drawdown_pct": round(state.drawdown_pct, 2),
        "total_pnl_r": round(state.total_pnl_r, 2),
        "win_rate": round(state.win_rate, 1),
        "total_trades": len(state.closed_trades),
        "wins": len(state.wins),
        "losses": len(state.losses),
        "active_count": len(state.active_trades),
        "scanner_running": state.scanner_running or state.active_scanner_count > 0,
        "active_scanners": state.active_scanner_count,
        "scanners": state.get_scanners_info(),
        "broker": state.broker_config,
        "profile": {
            "horizon": state.current_profile.horizon.value if state.current_profile else None,
            "asset_class": state.current_profile.asset_class.value if state.current_profile else None,
            "risk_level": state.current_profile.risk_level.value if state.current_profile else None,
        } if state.current_profile else None,
    })


@app.route("/api/trades")
def api_trades():
    return jsonify({
        "active": [_trade_dict(t) for t in state.active_trades],
        "closed": [_trade_dict(t) for t in reversed(state.closed_trades)],
    })


@app.route("/api/strategies")
def api_strategies():
    horizons = []
    for h in Horizon:
        tf = TIMEFRAME_CONFIG[h]
        horizons.append({"value": h.value, "label": tf["label"],
                         "trend": tf["tf_trend_min"], "setup": tf["tf_setup_min"],
                         "entry": tf["tf_entry_min"], "scan_s": tf["scan_interval_s"]})
    risks = []
    for r in RiskLevel:
        rc = RISK_CONFIG[r]
        risks.append({"value": r.value, "label": rc["label"],
                      "risk_pct": rc["risk_per_trade"], "max_pos": rc["max_positions"],
                      "min_str": rc["min_signal_strength"]})
    assets = []
    for a in AssetClass:
        sc = SESSION_CONFIG[a]
        assets.append({"value": a.value, "label": sc["label"],
                       "watchlist": WATCHLISTS[a]})
    return jsonify({"horizons": horizons, "risks": risks, "assets": assets})


@app.route("/api/scanner/start", methods=["POST"])
def api_scanner_start():
    data = request.json or {}
    horizon = Horizon(data.get("horizon", "short"))
    asset_class = AssetClass(data.get("asset_class", "stocks"))
    risk_level = RiskLevel(data.get("risk_level", "moderate"))

    # Generate unique scanner ID
    scanner_id = f"{horizon.value}-{asset_class.value}-{risk_level.value}"

    with state._lock:
        # Check if this exact config is already running
        if scanner_id in state._scanners and state._scanners[scanner_id]["running"]:
            return jsonify({"ok": False, "msg": f"Scanner '{scanner_id}' already running"})

        # Max 5 concurrent scanners
        active = sum(1 for s in state._scanners.values() if s["running"])
        if active >= 5:
            return jsonify({"ok": False, "msg": "Max 5 concurrent scanners. Stop one first."})

    profile = build_profile(horizon, asset_class, risk_level)
    state.current_profile = profile
    state.scanner_running = True

    with state._lock:
        state._scanners[scanner_id] = {"profile": profile, "running": True}

    thread = threading.Thread(
        target=_scanner_worker,
        args=(profile, scanner_id),
        name=f"scanner-{scanner_id}",
        daemon=True,
    )
    thread.start()

    return jsonify({
        "ok": True,
        "msg": f"Scanner started: {scanner_id}",
        "scanner_id": scanner_id,
        "active_scanners": state.active_scanner_count,
    })


@app.route("/api/scanner/stop", methods=["POST"])
def api_scanner_stop():
    data = request.json or {}
    scanner_id = data.get("scanner_id")

    with state._lock:
        if scanner_id and scanner_id in state._scanners:
            # Stop specific scanner
            state._scanners[scanner_id]["running"] = False
            msg = f"Scanner '{scanner_id}' stopping..."
        else:
            # Stop all scanners
            for s in state._scanners.values():
                s["running"] = False
            state.scanner_running = False
            msg = "All scanners stopping..."

    return jsonify({"ok": True, "msg": msg})


@app.route("/api/broker/check", methods=["POST"])
def api_broker_check():
    state._check_broker()
    return jsonify(state.broker_config)


def _trade_dict(t: TradeRecord) -> dict:
    return {
        "id": t.id, "symbol": t.symbol, "direction": t.direction,
        "pattern": t.pattern, "strength": t.signal_strength,
        "confluence": t.confluence, "entry": t.entry_price,
        "stop": t.stop_loss, "t1": t.target_1, "t2": t.target_2,
        "size": t.position_size, "status": t.status,
        "pnl_r": t.pnl_r, "pnl_dollars": t.pnl_dollars,
        "entry_time": t.entry_time, "exit_time": t.exit_time,
        "duration": t.duration_min, "horizon": t.horizon,
        "asset_class": t.asset_class, "risk_level": t.risk_level,
    }


# ─────────────────────────────────────────────────────────────────
# Main page
# ─────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    template_path = os.path.join(os.path.dirname(__file__), "templates", "dashboard.html")
    with open(template_path) as f:
        return f.read()


# ─────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print()
    print("=" * 60)
    print("  AI TRADING TEAM — WEB DASHBOARD")
    print("  Multi-threaded: Flask + up to 5 concurrent scanners")
    print("  Open http://localhost:5050")
    print("=" * 60)
    print()
    app.run(host="0.0.0.0", port=5050, debug=False, threaded=True)
