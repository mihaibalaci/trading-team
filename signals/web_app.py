"""
web_app.py — Flask web dashboard for the AI trading team.

Features:
  - Authentication: login, registration, session management
  - SQLite persistence: trades, daily stats, scanner sessions, strategies, signals
  - Live dashboard: current positions, P&L, equity
  - Trade history: win/loss reports with R-multiple tracking
  - Strategy configurator: horizon, asset class, risk level
  - Platform settings: broker connection config
  - Scanner control: start/stop from the UI (multi-threaded)

Usage:
    python3 signals/web_app.py
    Open http://localhost:5050
"""
from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

import json
import secrets
import threading
import time
import logging
from datetime import datetime, date, timedelta, time as dtime
from dataclasses import dataclass, field, asdict
from typing import Optional
from concurrent.futures import ThreadPoolExecutor
from functools import wraps

from flask import Flask, jsonify, request, session, redirect, url_for

from strategy_config import (
    Horizon, AssetClass, RiskLevel,
    build_profile, StrategyProfile,
    WATCHLISTS, TIMEFRAME_CONFIG, RISK_CONFIG, SESSION_CONFIG,
)
from database import (
    init_db, ensure_default_admin,
    create_user, verify_user, get_user, list_users,
    insert_trade, update_trade_by_id, get_active_trades, get_closed_trades, get_trade_stats,
    upsert_daily_stats, get_daily_stats,
    insert_scanner_session, close_scanner_session, get_scanner_sessions,
    save_strategy, get_strategies, delete_strategy,
    log_signal, get_signals,
)

logging.basicConfig(level=logging.WARNING)
log = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", secrets.token_hex(32))

# Injected by service.py larry_process when running in service mode.
# Routes read agent statuses and write commands through this reference.
_shared = None

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
# Auth helpers
# ─────────────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            if request.is_json or request.path.startswith("/api/"):
                return jsonify({"ok": False, "msg": "Not authenticated"}), 401
            return redirect("/login")
        return f(*args, **kwargs)
    return decorated


def current_user_id() -> int:
    return session.get("user_id", 0)


# ─────────────────────────────────────────────────────────────────
# Auth routes
# ─────────────────────────────────────────────────────────────────

@app.route("/login")
def login_page():
    if "user_id" in session:
        return redirect("/")
    template_path = os.path.join(os.path.dirname(__file__), "templates", "login.html")
    with open(template_path) as f:
        return f.read()


@app.route("/api/auth/login", methods=["POST"])
def api_login():
    data = request.json or {}
    username = data.get("username", "").strip()
    password = data.get("password", "")
    if not username or not password:
        return jsonify({"ok": False, "msg": "Username and password required"})
    user = verify_user(username, password)
    if not user:
        return jsonify({"ok": False, "msg": "Invalid username or password"})
    session["user_id"] = user["id"]
    session["username"] = user["username"]
    session["role"] = user["role"]
    return jsonify({"ok": True, "username": user["username"]})


@app.route("/api/auth/register", methods=["POST"])
def api_register():
    data = request.json or {}
    username = data.get("username", "").strip()
    password = data.get("password", "")
    if not username or len(username) < 2:
        return jsonify({"ok": False, "msg": "Username must be at least 2 characters"})
    if len(password) < 4:
        return jsonify({"ok": False, "msg": "Password must be at least 4 characters"})
    user_id = create_user(username, password)
    if user_id is None:
        return jsonify({"ok": False, "msg": "Username already taken"})
    session["user_id"] = user_id
    session["username"] = username
    session["role"] = "trader"
    return jsonify({"ok": True, "username": username})


@app.route("/api/auth/logout", methods=["POST"])
def api_logout():
    session.clear()
    return jsonify({"ok": True})


@app.route("/api/auth/me")
def api_me():
    if "user_id" not in session:
        return jsonify({"ok": False})
    return jsonify({"ok": True, "user_id": session["user_id"],
                    "username": session["username"], "role": session.get("role", "trader")})


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
@login_required
def api_status():
    uid = current_user_id()
    db_stats = get_trade_stats(uid)
    return jsonify({
        "equity": state.equity,
        "starting_equity": state.starting_equity,
        "peak_equity": state.peak_equity,
        "drawdown_pct": round(state.drawdown_pct, 2),
        "total_pnl_r": round(db_stats["total_pnl_r"], 2),
        "win_rate": round(db_stats["win_rate"], 1),
        "total_trades": db_stats["total"],
        "wins": db_stats["wins"],
        "losses": db_stats["losses"],
        "best_r": round(db_stats["best_r"], 2),
        "worst_r": round(db_stats["worst_r"], 2),
        "avg_r": round(db_stats["avg_r"], 3),
        "active_count": len(state.active_trades),
        "scanner_running": state.scanner_running or state.active_scanner_count > 0,
        "active_scanners": state.active_scanner_count,
        "scanners": state.get_scanners_info(),
        "broker": state.broker_config,
        "username": session.get("username", ""),
        "profile": {
            "horizon": state.current_profile.horizon.value if state.current_profile else None,
            "asset_class": state.current_profile.asset_class.value if state.current_profile else None,
            "risk_level": state.current_profile.risk_level.value if state.current_profile else None,
        } if state.current_profile else None,
    })


@app.route("/api/trades")
@login_required
def api_trades():
    uid = current_user_id()
    return jsonify({
        "active": get_active_trades(uid),
        "closed": get_closed_trades(uid),
    })


@app.route("/api/strategies")
@login_required
def api_strategies():
    uid = current_user_id()
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
    saved = get_strategies(uid)
    return jsonify({"horizons": horizons, "risks": risks, "assets": assets, "saved": saved})


@app.route("/api/strategies/save", methods=["POST"])
@login_required
def api_save_strategy():
    uid = current_user_id()
    data = request.json or {}
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"ok": False, "msg": "Strategy name required"})
    sid = save_strategy(
        uid, name, data.get("horizon", "short"),
        data.get("asset_class", "stocks"), data.get("risk_level", "moderate"),
        data.get("watchlist", ""), data.get("notes", ""),
    )
    return jsonify({"ok": True, "id": sid})


@app.route("/api/strategies/delete", methods=["POST"])
@login_required
def api_delete_strategy():
    uid = current_user_id()
    data = request.json or {}
    delete_strategy(data.get("id", 0), uid)
    return jsonify({"ok": True})


@app.route("/api/daily_stats")
@login_required
def api_daily_stats():
    uid = current_user_id()
    days = request.args.get("days", 30, type=int)
    return jsonify(get_daily_stats(uid, days))


@app.route("/api/scanner_sessions")
@login_required
def api_scanner_sessions():
    uid = current_user_id()
    return jsonify(get_scanner_sessions(uid))


@app.route("/api/signals")
@login_required
def api_signals():
    uid = current_user_id()
    return jsonify(get_signals(uid))


@app.route("/api/users")
@login_required
def api_users():
    if session.get("role") != "admin":
        return jsonify({"ok": False, "msg": "Admin only"}), 403
    return jsonify(list_users())


@app.route("/api/scanner/start", methods=["POST"])
@login_required
def api_scanner_start():
    uid = current_user_id()
    data = request.json or {}
    horizon = Horizon(data.get("horizon", "short"))
    asset_class = AssetClass(data.get("asset_class", "stocks"))
    risk_level = RiskLevel(data.get("risk_level", "moderate"))

    scanner_id = f"{horizon.value}-{asset_class.value}-{risk_level.value}"

    with state._lock:
        if scanner_id in state._scanners and state._scanners[scanner_id]["running"]:
            return jsonify({"ok": False, "msg": f"Scanner '{scanner_id}' already running"})
        active = sum(1 for s in state._scanners.values() if s["running"])
        if active >= 5:
            return jsonify({"ok": False, "msg": "Max 5 concurrent scanners. Stop one first."})

    profile = build_profile(horizon, asset_class, risk_level)
    state.current_profile = profile
    state.scanner_running = True

    # Persist scanner session to DB
    db_session_id = insert_scanner_session(uid, scanner_id, horizon.value, asset_class.value, risk_level.value)

    with state._lock:
        state._scanners[scanner_id] = {"profile": profile, "running": True,
                                        "user_id": uid, "db_session_id": db_session_id}

    thread = threading.Thread(
        target=_scanner_worker,
        args=(profile, scanner_id),
        name=f"scanner-{scanner_id}",
        daemon=True,
    )
    thread.start()

    return jsonify({"ok": True, "msg": f"Scanner started: {scanner_id}",
                    "scanner_id": scanner_id, "active_scanners": state.active_scanner_count})


@app.route("/api/scanner/stop", methods=["POST"])
@login_required
def api_scanner_stop():
    data = request.json or {}
    scanner_id = data.get("scanner_id")

    with state._lock:
        if scanner_id and scanner_id in state._scanners:
            state._scanners[scanner_id]["running"] = False
            # Close DB session
            db_sid = state._scanners[scanner_id].get("db_session_id")
            if db_sid:
                close_scanner_session(db_sid, 0, 0.0)
            msg = f"Scanner '{scanner_id}' stopping..."
        else:
            for sid, s in state._scanners.items():
                s["running"] = False
                db_sid = s.get("db_session_id")
                if db_sid:
                    close_scanner_session(db_sid, 0, 0.0)
            state.scanner_running = False
            msg = "All scanners stopping..."

    return jsonify({"ok": True, "msg": msg})


@app.route("/api/broker/check", methods=["POST"])
@login_required
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
# Agent management routes (service mode only)
# ─────────────────────────────────────────────────────────────────

_AGENT_DESCRIPTIONS = {
    "larry": "Web dashboard",
    "kai":   "Broker connectivity",
    "clio":  "Strategy loader",
    "mira":  "Risk monitor",
    "finn":  "Signal scanner",
    "remy":  "Execution engine",
}
_AGENT_ORDER = ["larry", "kai", "clio", "mira", "finn", "remy"]


@app.route("/api/agents")
@login_required
def api_agents():
    result = []
    for name in _AGENT_ORDER:
        entry = {"name": name, "desc": _AGENT_DESCRIPTIONS[name]}
        if _shared is not None:
            entry["status"] = _shared.get(f"status_{name}", "unknown")
        else:
            entry["status"] = "running" if name == "larry" else "standalone"
        result.append(entry)
    return jsonify(result)


@app.route("/api/agents/<name>/logs")
@login_required
def api_agent_logs(name):
    if name not in _AGENT_DESCRIPTIONS and name != "service":
        return jsonify({"ok": False, "msg": f"Unknown agent: {name}"}), 404
    log_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
    log_path = os.path.join(log_dir, f"{name}.log")
    lines = int(request.args.get("lines", 200))
    try:
        with open(log_path) as f:
            all_lines = f.readlines()
        return jsonify({"ok": True, "lines": all_lines[-lines:]})
    except FileNotFoundError:
        return jsonify({"ok": True, "lines": []})


@app.route("/api/agents/<name>/<action>", methods=["POST"])
@login_required
def api_agent_action(name, action):
    if name not in _AGENT_DESCRIPTIONS:
        return jsonify({"ok": False, "msg": f"Unknown agent: {name}"})
    if action not in ("start", "stop", "restart", "reload"):
        return jsonify({"ok": False, "msg": f"Unknown action: {action}"})
    if _shared is None:
        return jsonify({"ok": False, "msg": "Not running in service mode"})
    _shared[f"cmd_{name}"] = action
    return jsonify({"ok": True, "msg": f"{name}: {action} queued"})


# ─────────────────────────────────────────────────────────────────
# Main page
# ─────────────────────────────────────────────────────────────────

@app.route("/")
@login_required
def index():
    template_path = os.path.join(os.path.dirname(__file__), "templates", "dashboard.html")
    with open(template_path) as f:
        return f.read()


# ─────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    init_db()
    ensure_default_admin()
    print()
    print("=" * 60)
    print("  AI TRADING TEAM — WEB DASHBOARD")
    print("  Multi-threaded: Flask + SQLite + Auth")
    print("  Default login: admin / admin123")
    print("  Open http://localhost:5050")
    print("=" * 60)
    print()
    app.run(host="0.0.0.0", port=5050, debug=False, threaded=True)
