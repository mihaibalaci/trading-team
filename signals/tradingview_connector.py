"""
tradingview_connector.py — Receive and parse TradingView webhook alerts.

TradingView sends a POST request to your webhook URL when an alert fires.
This module validates the token, converts the payload to a FinnSignal, and
pushes it onto the shared signal_queue so Remy executes it on the active broker.

Recommended TradingView alert message format (paste into the "Message" field):
{
  "token": "YOUR_TOKEN_HERE",
  "symbol": "{{ticker}}",
  "action": "{{strategy.order.action}}",
  "price": {{close}},
  "stop_loss": 0,
  "take_profit": 0,
  "comment": "{{strategy.order.comment}}"
}

Supported action values: buy, sell, long, short, close_long, close_short
"""
from __future__ import annotations

import os
import secrets
import logging
from collections import deque
from datetime import datetime, time as dtime

log = logging.getLogger("TradingView")

# ── In-memory ring buffer of recent incoming signals (last 50) ──────────────
recent_signals: deque[dict] = deque(maxlen=50)


# ── Token management ─────────────────────────────────────────────────────────

def get_or_create_token(env_path: str | None = None) -> str:
    """Return the TV_WEBHOOK_TOKEN from .env, creating and saving one if absent."""
    path = env_path or os.path.join(os.path.dirname(__file__), ".env")
    token = os.getenv("TV_WEBHOOK_TOKEN", "").strip()
    if token:
        return token

    # Read existing .env lines
    lines: list[str] = []
    if os.path.exists(path):
        with open(path) as f:
            lines = f.readlines()

    # Check if already in file (not yet loaded in env)
    for line in lines:
        if line.startswith("TV_WEBHOOK_TOKEN="):
            token = line.split("=", 1)[1].strip()
            os.environ["TV_WEBHOOK_TOKEN"] = token
            return token

    # Generate and persist
    token = secrets.token_hex(24)
    lines.append(f"\nTV_WEBHOOK_TOKEN={token}\n")
    with open(path, "w") as f:
        f.writelines(lines)
    os.environ["TV_WEBHOOK_TOKEN"] = token
    log.info(f"Generated new TV_WEBHOOK_TOKEN and saved to .env")
    return token


def validate_token(payload: dict) -> tuple[bool, str]:
    """Check the token field in the incoming payload."""
    expected = os.getenv("TV_WEBHOOK_TOKEN", "").strip()
    if not expected:
        return False, "TV_WEBHOOK_TOKEN not configured"
    received = str(payload.get("token", "")).strip()
    if not received:
        return False, "Missing 'token' field in payload"
    if not secrets.compare_digest(received, expected):
        return False, "Invalid token"
    return True, "ok"


# ── Payload → FinnSignal ──────────────────────────────────────────────────────

def parse_payload(data: dict) -> "FinnSignal":
    """
    Convert a TradingView webhook payload to a FinnSignal.

    Required fields: symbol, action, price
    Optional fields: stop_loss, take_profit, atr, quantity, comment
    """
    from signal_engine import FinnSignal

    symbol = str(data.get("symbol", "")).upper().replace("/", "").strip()
    if not symbol:
        raise ValueError("Missing 'symbol' in payload")

    raw_action = str(data.get("action", "")).lower().strip()
    if raw_action in ("buy", "long"):
        direction = "long"
    elif raw_action in ("sell", "short"):
        direction = "short"
    else:
        raise ValueError(f"Unsupported action '{raw_action}'. Use: buy/long/sell/short")

    try:
        entry = float(data["price"])
    except (KeyError, ValueError, TypeError):
        raise ValueError("Missing or invalid 'price' field")

    if entry <= 0:
        raise ValueError(f"Price must be positive, got {entry}")

    raw_sl = data.get("stop_loss", 0)
    sl = float(raw_sl) if raw_sl else 0.0

    raw_tp = data.get("take_profit", 0)
    tp = float(raw_tp) if raw_tp else 0.0

    raw_atr = data.get("atr", 0)
    atr = float(raw_atr) if raw_atr else 0.0

    # Derive stop distance
    if sl and sl > 0:
        stop_dist = abs(entry - sl)
        if direction == "long" and sl >= entry:
            raise ValueError(f"stop_loss ({sl}) must be below entry ({entry}) for a long")
        if direction == "short" and sl <= entry:
            raise ValueError(f"stop_loss ({sl}) must be above entry ({entry}) for a short")
    else:
        # Default: 1% of price
        stop_dist = entry * 0.01
        sl = (entry - stop_dist) if direction == "long" else (entry + stop_dist)

    if not atr:
        atr = stop_dist  # best estimate without live bars

    # Targets
    if tp and tp > 0:
        t1 = tp
        t2 = entry + (2.5 * stop_dist) if direction == "long" else entry - (2.5 * stop_dist)
    else:
        t1 = (entry + 1.5 * stop_dist) if direction == "long" else (entry - 1.5 * stop_dist)
        t2 = (entry + 2.5 * stop_dist) if direction == "long" else (entry - 2.5 * stop_dist)

    rr = round(abs(t1 - entry) / stop_dist, 2) if stop_dist else 1.5
    pos_size = round(1000.0 / stop_dist, 4) if stop_dist > 0 else 10.0

    comment = str(data.get("comment", "")).strip()
    note = f"TradingView alert{': ' + comment if comment else ''}"

    signal = FinnSignal(
        timestamp          = datetime.utcnow(),
        instrument         = symbol,
        direction          = direction,
        signal_strength    = 75,
        confidence         = "Medium",
        timeframe          = "TradingView",
        model              = "TradingView Alert",
        pattern_15m        = "tv_alert",
        pattern_strength   = "medium",
        confluence_score   = 3,
        confluence_detail  = f"Source: TradingView | Action: {raw_action}",
        trend_bias_30m     = "bullish" if direction == "long" else "bearish",
        stoch_k_15m        = 50.0,
        stoch_k_1m         = 50.0,
        entry_price        = entry,
        stop_loss          = sl,
        target_1           = t1,
        target_2           = t2,
        stop_distance      = stop_dist,
        atr_15m            = atr,
        risk_reward_t1     = rr,
        position_size_1pct = pos_size,
        notes              = note,
    )

    entry_dict = {
        "ts":        signal.timestamp.isoformat(),
        "symbol":    symbol,
        "direction": direction,
        "entry":     entry,
        "sl":        sl,
        "t1":        t1,
        "raw":       data,
    }
    recent_signals.appendleft(entry_dict)

    return signal


# ── Minimal strategy profile for TradingView signals ─────────────────────────

def tv_strategy_profile():
    """
    Return a minimal StrategyProfile-compatible object for TV signals.
    Uses a 24/7 session so signals fire any time (TV supports crypto + forex).
    """
    from strategy_config import build_profile, Horizon, AssetClass, RiskLevel
    profile = build_profile(Horizon.SHORT, AssetClass.STOCKS, RiskLevel.MODERATE)
    # Override session to 24 h so signals are never blocked by session guard
    profile.session_open  = dtime(0, 0)
    profile.session_close = dtime(23, 59)
    return profile
