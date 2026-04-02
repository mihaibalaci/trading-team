"""
database.py — SQLite persistence layer for the AI trading team.

Tables:
  - users: login credentials (bcrypt hashed passwords)
  - trades: full trade history with P&L
  - daily_stats: daily equity snapshots and performance
  - scanner_sessions: scanner run history
  - strategy_configs: saved strategy profiles
  - signals_log: all signals generated (executed or not)
"""
from __future__ import annotations

import sqlite3
import hashlib
import os
import secrets
import threading
from datetime import datetime, date
from typing import Optional
from contextlib import contextmanager

DB_PATH = os.path.join(os.path.dirname(__file__), "trading_team.db")

_local = threading.local()


def _get_conn() -> sqlite3.Connection:
    """Thread-local SQLite connection."""
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA foreign_keys=ON")
    return _local.conn


@contextmanager
def get_db():
    conn = _get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


# ─────────────────────────────────────────────────────────────────
# Schema
# ─────────────────────────────────────────────────────────────────

def init_db():
    """Create all tables if they don't exist."""
    with get_db() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            salt TEXT NOT NULL,
            role TEXT DEFAULT 'trader',
            created_at TEXT DEFAULT (datetime('now')),
            last_login TEXT
        );

        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL,
            pattern TEXT,
            signal_strength INTEGER,
            confluence INTEGER,
            entry_price REAL,
            stop_loss REAL,
            target_1 REAL,
            target_2 REAL,
            position_size REAL,
            status TEXT DEFAULT 'pending',
            pnl_r REAL DEFAULT 0.0,
            pnl_dollars REAL DEFAULT 0.0,
            entry_time TEXT,
            exit_time TEXT,
            duration_min REAL DEFAULT 0,
            horizon TEXT,
            asset_class TEXT,
            risk_level TEXT,
            scanner_id TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS daily_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            equity_open REAL,
            equity_close REAL,
            pnl_r REAL DEFAULT 0.0,
            pnl_dollars REAL DEFAULT 0.0,
            trades_count INTEGER DEFAULT 0,
            wins INTEGER DEFAULT 0,
            losses INTEGER DEFAULT 0,
            win_rate REAL DEFAULT 0.0,
            max_drawdown_pct REAL DEFAULT 0.0,
            best_trade_r REAL DEFAULT 0.0,
            worst_trade_r REAL DEFAULT 0.0,
            FOREIGN KEY (user_id) REFERENCES users(id),
            UNIQUE(user_id, date)
        );

        CREATE TABLE IF NOT EXISTS scanner_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            scanner_id TEXT NOT NULL,
            horizon TEXT,
            asset_class TEXT,
            risk_level TEXT,
            started_at TEXT DEFAULT (datetime('now')),
            stopped_at TEXT,
            trades_executed INTEGER DEFAULT 0,
            total_pnl_r REAL DEFAULT 0.0,
            status TEXT DEFAULT 'running',
            FOREIGN KEY (user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS strategy_configs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            horizon TEXT NOT NULL,
            asset_class TEXT NOT NULL,
            risk_level TEXT NOT NULL,
            watchlist TEXT,
            notes TEXT,
            is_default INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS signals_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            direction TEXT,
            signal_strength INTEGER,
            confluence INTEGER,
            pattern TEXT,
            trend_bias TEXT,
            entry_price REAL,
            stop_loss REAL,
            target_1 REAL,
            invalidated INTEGER DEFAULT 0,
            invalidation_reason TEXT,
            executed INTEGER DEFAULT 0,
            scanner_id TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(id)
        );

        CREATE INDEX IF NOT EXISTS idx_trades_user ON trades(user_id);
        CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status);
        CREATE INDEX IF NOT EXISTS idx_daily_user_date ON daily_stats(user_id, date);
        CREATE INDEX IF NOT EXISTS idx_signals_user ON signals_log(user_id);
        """)


# ─────────────────────────────────────────────────────────────────
# User management
# ─────────────────────────────────────────────────────────────────

def _hash_password(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000).hex()


def create_user(username: str, password: str, role: str = "trader") -> Optional[int]:
    salt = secrets.token_hex(16)
    pw_hash = _hash_password(password, salt)
    try:
        with get_db() as db:
            cur = db.execute(
                "INSERT INTO users (username, password_hash, salt, role) VALUES (?, ?, ?, ?)",
                (username, pw_hash, salt, role),
            )
            return cur.lastrowid
    except sqlite3.IntegrityError:
        return None  # username taken


def verify_user(username: str, password: str) -> Optional[dict]:
    with get_db() as db:
        row = db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if not row:
            return None
        pw_hash = _hash_password(password, row["salt"])
        if pw_hash != row["password_hash"]:
            return None
        db.execute("UPDATE users SET last_login = datetime('now') WHERE id = ?", (row["id"],))
        return dict(row)


def get_user(user_id: int) -> Optional[dict]:
    with get_db() as db:
        row = db.execute("SELECT id, username, role, created_at, last_login FROM users WHERE id = ?",
                         (user_id,)).fetchone()
        return dict(row) if row else None


def list_users() -> list[dict]:
    with get_db() as db:
        rows = db.execute("SELECT id, username, role, created_at, last_login FROM users").fetchall()
        return [dict(r) for r in rows]


def change_password(user_id: int, new_password: str) -> bool:
    salt = secrets.token_hex(16)
    pw_hash = _hash_password(new_password, salt)
    with get_db() as db:
        db.execute("UPDATE users SET password_hash=?, salt=? WHERE id=?", (pw_hash, salt, user_id))
    return True


def delete_user(user_id: int) -> bool:
    with get_db() as db:
        db.execute("DELETE FROM users WHERE id=?", (user_id,))
    return True


def update_user_role(user_id: int, role: str) -> bool:
    with get_db() as db:
        db.execute("UPDATE users SET role=? WHERE id=?", (role, user_id))
    return True


# ─────────────────────────────────────────────────────────────────
# Platforms CRUD
# ─────────────────────────────────────────────────────────────────

def _init_platforms_table():
    with get_db() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS platforms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            platform_type TEXT NOT NULL,
            endpoint TEXT,
            api_key TEXT,
            api_secret TEXT,
            extra_config TEXT DEFAULT '{}',
            enabled INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now'))
        )""")

_init_platforms_table()


def list_platforms() -> list[dict]:
    with get_db() as db:
        rows = db.execute("SELECT id, name, platform_type, endpoint, api_key, enabled, created_at FROM platforms").fetchall()
        return [dict(r) for r in rows]


def save_platform(name: str, platform_type: str, endpoint: str, api_key: str, api_secret: str, extra_config: str = "{}") -> int:
    with get_db() as db:
        cur = db.execute(
            "INSERT INTO platforms (name, platform_type, endpoint, api_key, api_secret, extra_config) VALUES (?,?,?,?,?,?)",
            (name, platform_type, endpoint, api_key, api_secret, extra_config),
        )
        return cur.lastrowid


def update_platform(platform_id: int, **kwargs):
    allowed = {"name", "platform_type", "endpoint", "api_key", "api_secret", "extra_config", "enabled"}
    sets = {k: v for k, v in kwargs.items() if k in allowed}
    if not sets:
        return
    sql = "UPDATE platforms SET " + ", ".join(f"{k}=?" for k in sets) + " WHERE id=?"
    with get_db() as db:
        db.execute(sql, list(sets.values()) + [platform_id])


def delete_platform(platform_id: int):
    with get_db() as db:
        db.execute("DELETE FROM platforms WHERE id=?", (platform_id,))


def get_platform(platform_id: int) -> Optional[dict]:
    with get_db() as db:
        row = db.execute("SELECT * FROM platforms WHERE id=?", (platform_id,)).fetchone()
        return dict(row) if row else None


# ─────────────────────────────────────────────────────────────────
# Trade CRUD
# ─────────────────────────────────────────────────────────────────

def insert_trade(user_id: int, **kwargs) -> int:
    cols = ["user_id"] + list(kwargs.keys())
    vals = [user_id] + list(kwargs.values())
    placeholders = ",".join(["?"] * len(vals))
    col_str = ",".join(cols)
    with get_db() as db:
        cur = db.execute(f"INSERT INTO trades ({col_str}) VALUES ({placeholders})", vals)
        return cur.lastrowid


def update_trade_by_id(trade_id: int, **updates):
    sets = ",".join(f"{k}=?" for k in updates)
    vals = list(updates.values()) + [trade_id]
    with get_db() as db:
        db.execute(f"UPDATE trades SET {sets} WHERE id=?", vals)


def get_active_trades(user_id: int) -> list[dict]:
    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM trades WHERE user_id=? AND status IN ('active','pending') ORDER BY id DESC",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_closed_trades(user_id: int, limit: int = 100) -> list[dict]:
    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM trades WHERE user_id=? AND status IN ('won','lost','flat') ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def get_trade_stats(user_id: int) -> dict:
    with get_db() as db:
        row = db.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN status='won' THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN status='lost' THEN 1 ELSE 0 END) as losses,
                COALESCE(SUM(pnl_r), 0) as total_pnl_r,
                COALESCE(SUM(pnl_dollars), 0) as total_pnl_dollars,
                COALESCE(MAX(pnl_r), 0) as best_r,
                COALESCE(MIN(pnl_r), 0) as worst_r,
                COALESCE(AVG(CASE WHEN status IN ('won','lost','flat') THEN pnl_r END), 0) as avg_r
            FROM trades WHERE user_id=? AND status IN ('won','lost','flat')
        """, (user_id,)).fetchone()
        d = dict(row)
        d["win_rate"] = (d["wins"] / d["total"] * 100) if d["total"] > 0 else 0
        return d


# ─────────────────────────────────────────────────────────────────
# Daily stats
# ─────────────────────────────────────────────────────────────────

def upsert_daily_stats(user_id: int, **kwargs):
    today = kwargs.pop("date", date.today().isoformat())
    cols = ["user_id", "date"] + list(kwargs.keys())
    vals = [user_id, today] + list(kwargs.values())
    placeholders = ",".join(["?"] * len(vals))
    col_str = ",".join(cols)
    updates = ",".join(f"{k}=excluded.{k}" for k in kwargs)
    with get_db() as db:
        db.execute(
            f"INSERT INTO daily_stats ({col_str}) VALUES ({placeholders}) "
            f"ON CONFLICT(user_id, date) DO UPDATE SET {updates}",
            vals,
        )


def get_daily_stats(user_id: int, days: int = 30) -> list[dict]:
    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM daily_stats WHERE user_id=? ORDER BY date DESC LIMIT ?",
            (user_id, days),
        ).fetchall()
        return [dict(r) for r in rows]


# ─────────────────────────────────────────────────────────────────
# Scanner sessions
# ─────────────────────────────────────────────────────────────────

def insert_scanner_session(user_id: int, scanner_id: str, horizon: str,
                           asset_class: str, risk_level: str) -> int:
    with get_db() as db:
        cur = db.execute(
            "INSERT INTO scanner_sessions (user_id, scanner_id, horizon, asset_class, risk_level) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, scanner_id, horizon, asset_class, risk_level),
        )
        return cur.lastrowid


def close_scanner_session(session_id: int, trades_executed: int, total_pnl_r: float):
    with get_db() as db:
        db.execute(
            "UPDATE scanner_sessions SET stopped_at=datetime('now'), status='stopped', "
            "trades_executed=?, total_pnl_r=? WHERE id=?",
            (trades_executed, total_pnl_r, session_id),
        )


def get_scanner_sessions(user_id: int, limit: int = 20) -> list[dict]:
    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM scanner_sessions WHERE user_id=? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]


# ─────────────────────────────────────────────────────────────────
# Strategy configs
# ─────────────────────────────────────────────────────────────────

def save_strategy(user_id: int, name: str, horizon: str, asset_class: str,
                  risk_level: str, watchlist: str = "", notes: str = "") -> int:
    with get_db() as db:
        cur = db.execute(
            "INSERT INTO strategy_configs (user_id, name, horizon, asset_class, risk_level, watchlist, notes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, name, horizon, asset_class, risk_level, watchlist, notes),
        )
        return cur.lastrowid


def get_strategies(user_id: int) -> list[dict]:
    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM strategy_configs WHERE user_id=? ORDER BY id DESC", (user_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def delete_strategy(strategy_id: int, user_id: int):
    with get_db() as db:
        db.execute("DELETE FROM strategy_configs WHERE id=? AND user_id=?", (strategy_id, user_id))


# ─────────────────────────────────────────────────────────────────
# Signals log
# ─────────────────────────────────────────────────────────────────

def log_signal(user_id: int, **kwargs) -> int:
    cols = ["user_id"] + list(kwargs.keys())
    vals = [user_id] + list(kwargs.values())
    placeholders = ",".join(["?"] * len(vals))
    col_str = ",".join(cols)
    with get_db() as db:
        cur = db.execute(f"INSERT INTO signals_log ({col_str}) VALUES ({placeholders})", vals)
        return cur.lastrowid


def get_signals(user_id: int, limit: int = 50) -> list[dict]:
    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM signals_log WHERE user_id=? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]


# ─────────────────────────────────────────────────────────────────
# Bootstrap: create default admin if no users exist
# ─────────────────────────────────────────────────────────────────

def ensure_default_admin():
    with get_db() as db:
        count = db.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"]
        if count == 0:
            create_user("admin", "admin123", role="admin")
