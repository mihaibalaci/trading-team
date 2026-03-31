"""
signal_engine.py — Finn's main multi-timeframe signal detection engine.
Implements Vera's Multi-Timeframe Price Action Strategy end-to-end.

Pipeline:
    30m → trend bias + key levels
    15m → setup detection (pattern + confluence + stochastic)
    1m  → entry trigger detection

Output: FinnSignal dataclass in Finn's standard signal format.
"""

from __future__ import annotations
import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from indicators import (
    ema_stack, stochastic, atr,
    pivot_points, fibonacci_levels,
    trend_bias,
)
from patterns import scan_patterns, PATTERN_STRENGTH, pattern_direction
from confluence import score_confluence, describe_confluence


# ─────────────────────────────────────────────
# Signal output structure (Finn's standard format)
# ─────────────────────────────────────────────

@dataclass
class FinnSignal:
    """
    Standard signal output format per Finn's brief in FINN.md.
    All signals produced by this engine conform to this schema.
    """
    timestamp:          datetime
    instrument:         str
    direction:          str            # 'long' or 'short'
    signal_strength:    int            # 0–100
    confidence:         str            # 'High' / 'Medium' / 'Low'
    timeframe:          str            # 'Intraday'
    model:              str            # which model generated it
    pattern_15m:        str            # triggering candlestick pattern
    pattern_strength:   str            # 'high' / 'medium_high' / 'medium'
    confluence_score:   int            # 0–5
    confluence_detail:  str            # human-readable breakdown
    trend_bias_30m:     str            # 'bullish' / 'bearish' / 'ranging'
    stoch_k_15m:        float
    stoch_k_1m:         float
    entry_price:        float
    stop_loss:          float
    target_1:           float          # 1.5R
    target_2:           float          # nearest key level
    stop_distance:      float
    atr_15m:            float
    risk_reward_t1:     float
    position_size_1pct: float          # units per $1,000 risk at 1% equity
    invalidated:        bool = False
    invalidation_reason: str = ""
    backtest_stats:     dict = field(default_factory=dict)
    notes:              str = ""

    @property
    def direction_sign(self) -> int:
        """1 for long, -1 for short. Used in P&L calculations."""
        return 1 if self.direction == "long" else -1

    def to_dict(self) -> dict:
        # Mira MED-02: convert datetime to ISO string for JSON serialisation
        d = self.__dict__.copy()
        d["timestamp"] = self.timestamp.isoformat()
        return d

    def summary(self) -> str:
        dir_arrow = "▲ LONG" if self.direction == "long" else "▼ SHORT"
        lines = [
            f"{'─'*60}",
            f"  FINN SIGNAL — {self.instrument}  |  {dir_arrow}",
            f"{'─'*60}",
            f"  Time:          {self.timestamp}",
            f"  Model:         {self.model}",
            f"  Confidence:    {self.confidence} (strength {self.signal_strength}/100)",
            f"  Pattern (15m): {self.pattern_15m} [{self.pattern_strength}]",
            f"  Confluence:    {self.confluence_score}/5",
            f"  30m Bias:      {self.trend_bias_30m.upper()}",
            f"  Stoch 15m:     {self.stoch_k_15m:.1f}",
            f"  Stoch 1m:      {self.stoch_k_1m:.1f}",
            f"  ─────────────────────────────────────────────────────",
            f"  Entry:         {self.entry_price:.4f}",
            f"  Stop Loss:     {self.stop_loss:.4f}  (dist: {self.stop_distance:.4f})",
            f"  Target 1:      {self.target_1:.4f}  (1.5R)",
            f"  Target 2:      {self.target_2:.4f}  (key level)",
            f"  R:R (T1):      {self.risk_reward_t1:.2f}",
            f"  ATR (15m):     {self.atr_15m:.4f}",
            f"  Size/$1k risk: {self.position_size_1pct:.1f} units",
            f"  ─────────────────────────────────────────────────────",
        ]
        if self.invalidated:
            lines.append(f"  ⚠ INVALIDATED: {self.invalidation_reason}")
        if self.notes:
            lines.append(f"  Notes: {self.notes}")
        if self.confluence_detail:
            lines.append(f"\n{self.confluence_detail}")
        lines.append(f"{'─'*60}")
        return "\n".join(lines)


# ─────────────────────────────────────────────
# Helper: nearest key level for Target 2
# ─────────────────────────────────────────────

def _nearest_target_level(entry: float, direction: str,
                           levels: list[float]) -> float:
    """Return the nearest key level in the trade direction beyond entry."""
    if direction == "long":
        candidates = [l for l in levels if l > entry]
        return min(candidates) if candidates else entry * 1.01
    else:
        candidates = [l for l in levels if l < entry]
        return max(candidates) if candidates else entry * 0.99


# ─────────────────────────────────────────────
# Signal strength scoring
# ─────────────────────────────────────────────

def _compute_signal_strength(
    confluence_score: int,
    pattern_str: str,
    trend_match: bool,
    stoch_aligned: bool,
    atr_ratio: float,
) -> tuple[int, str]:
    """
    Compute 0–100 signal strength and confidence label.
    Based on Finn's scoring methodology.
    """
    score = 0

    # Confluence (max 40 pts)
    score += min(confluence_score, 5) * 8

    # Pattern strength (max 20 pts)
    ps_map = {"high": 20, "medium_high": 14, "medium": 8, "neutral": 0}
    score += ps_map.get(pattern_str, 0)

    # Trend alignment (max 15 pts)
    if trend_match:
        score += 15

    # Stochastic alignment (max 15 pts)
    if stoch_aligned:
        score += 15

    # ATR ratio — stop is tight relative to ATR (max 10 pts)
    # Ideal: stop ≈ 0.5–1.0 ATR. Penalty if stop > 1.5 ATR.
    if atr_ratio <= 1.0:
        score += 10
    elif atr_ratio <= 1.5:
        score += 5
    else:
        score += 0

    score = min(score, 100)

    if score >= 75:
        confidence = "High"
    elif score >= 50:
        confidence = "Medium"
    else:
        confidence = "Low"

    return score, confidence


# ─────────────────────────────────────────────
# Core validation checks (Vera's checklist)
# ─────────────────────────────────────────────

def _validate_signal(
    direction: str,
    trend_30m: str,
    confluence_score: int,
    stoch_k_15m: float,
    stoch_k_1m: float,
    stop_distance: float,
    atr_15m: float,
    pattern_strength: str,
) -> tuple[bool, str]:
    """
    Run Vera's pre-trade checklist validation.
    Returns (is_valid, reason_if_invalid).
    """
    if trend_30m == "ranging":
        return False, "30m trend is ranging — no directional bias"

    if direction == "long" and trend_30m != "bullish":
        return False, f"Long signal against 30m trend ({trend_30m})"
    if direction == "short" and trend_30m != "bearish":
        return False, f"Short signal against 30m trend ({trend_30m})"

    if confluence_score < 3:
        return False, f"Insufficient confluence ({confluence_score}/5 — minimum 3)"

    if direction == "long" and stoch_k_15m > 50:
        return False, f"Stoch 15m too high for long ({stoch_k_15m:.1f} > 50)"
    if direction == "short" and stoch_k_15m < 50:
        return False, f"Stoch 15m too low for short ({stoch_k_15m:.1f} < 50)"

    if direction == "long" and stoch_k_1m > 80:
        return False, f"1m stochastic overbought at entry ({stoch_k_1m:.1f})"
    if direction == "short" and stoch_k_1m < 20:
        return False, f"1m stochastic oversold at entry ({stoch_k_1m:.1f})"

    if atr_15m > 0 and stop_distance > 1.5 * atr_15m:
        return False, (
            f"Stop too wide: {stop_distance:.4f} > 1.5×ATR ({1.5*atr_15m:.4f}). "
            f"Setup not clean enough."
        )

    if pattern_strength == "neutral":
        return False, "Doji detected — wait for confirmation candle before entering"

    return True, ""


# ─────────────────────────────────────────────
# Main signal generator
# ─────────────────────────────────────────────

def generate_signal(
    instrument:           str,
    df_30m:               pd.DataFrame,
    df_15m:               pd.DataFrame,
    df_1m:                pd.DataFrame,
    prev_day_high:        float,
    prev_day_low:         float,
    prev_day_close:       float,
    swing_high:           float | None = None,
    swing_low:            float | None = None,
    equity:               float = 100_000,
    risk_pct:             float = 0.01,
    current_open_risk_pct: float = 0.0,   # Mira CRIT-01: total risk already deployed
    peak_equity:          float | None = None,  # Mira CRIT-02: for drawdown check
) -> FinnSignal | None:
    """
    Full multi-timeframe signal generation pipeline.
    Implements Vera's strategy: 30m bias → 15m setup → 1m entry.

    Parameters
    ----------
    instrument      : ticker or contract name
    df_30m          : 30-minute OHLCV DataFrame (columns: open,high,low,close,volume)
    df_15m          : 15-minute OHLCV DataFrame
    df_1m           : 1-minute OHLCV DataFrame
    prev_day_high   : previous session high (for PDH key level)
    prev_day_low    : previous session low  (for PDL key level)
    prev_day_close  : previous session close (for pivot point calculation)
    swing_high      : recent swing high for Fibonacci (optional)
    swing_low       : recent swing low  for Fibonacci (optional)
    equity          : account equity for position sizing
    risk_pct        : fraction of equity to risk per trade (default 1%)

    Returns
    -------
    FinnSignal if a valid signal is found, None otherwise.
    """

    # ── Mira CRIT-01: Portfolio exposure guard ───────────────────────────
    MAX_OPEN_RISK = 0.03  # Vera Section 8.3: 3% max total open risk
    risk_pct = min(risk_pct, 0.015)  # Mira MED-05: hard cap at Vera's 1.5% max
    if current_open_risk_pct + risk_pct > MAX_OPEN_RISK:
        risk_pct = MAX_OPEN_RISK - current_open_risk_pct
        if risk_pct <= 0:
            return None  # Portfolio fully loaded — no new positions

    # ── Mira CRIT-02: Drawdown circuit breaker ───────────────────────────
    if peak_equity is not None and peak_equity > 0:
        drawdown_pct = (peak_equity - equity) / peak_equity
        if drawdown_pct >= 0.10:
            return None  # Vera Section 8.3: drawdown > 10% → stop trading
        elif drawdown_pct >= 0.05:
            risk_pct = min(risk_pct, 0.005)  # Drawdown > 5% → max 0.5% risk

    # ── Mira MED-04: Minimum bar count guard ─────────────────────────────
    if len(df_15m) < 30 or len(df_30m) < 20 or len(df_1m) < 50:
        return None  # Insufficient data for reliable ATR/EMA values

    # ── Step 1: Prepare indicators on all timeframes ─────────────────────
    df_30m = ema_stack(df_30m)
    df_30m = stochastic(df_30m)

    df_15m = ema_stack(df_15m)
    df_15m = stochastic(df_15m)
    df_15m = atr(df_15m)

    df_1m = ema_stack(df_1m)
    df_1m = stochastic(df_1m)

    # ── Step 2: 30m trend bias ───────────────────────────────────────────
    trend_30m = trend_bias(df_30m)
    if trend_30m == "ranging":
        return None  # No-trade zone

    # ── Step 3: Pivot points + key levels ───────────────────────────────
    pivots = pivot_points(prev_day_high, prev_day_low, prev_day_close)
    all_key_levels = list(pivots.values()) + [prev_day_high, prev_day_low]

    fib_levels = None
    if swing_high is not None and swing_low is not None:
        fib_levels = fibonacci_levels(swing_high, swing_low)
        all_key_levels += list(fib_levels.values())

    # ── Step 4: 15m pattern detection ───────────────────────────────────
    detected_patterns = scan_patterns(df_15m)
    if not detected_patterns:
        return None

    # Use the highest-strength pattern that aligns with 30m trend
    direction_map = {"bullish": "bullish", "bearish": "bearish"}
    expected_direction = direction_map.get(trend_30m)

    best_pattern = None
    for pat in detected_patterns:
        if pat["direction"] == expected_direction:
            if best_pattern is None:
                best_pattern = pat
            else:
                # Prefer higher strength
                strength_rank = {"high": 3, "medium_high": 2, "medium": 1,
                                 "neutral": 0, "unknown": 0}
                if (strength_rank.get(pat["strength"], 0) >
                        strength_rank.get(best_pattern["strength"], 0)):
                    best_pattern = pat

    if best_pattern is None:
        return None  # No pattern aligned with 30m trend

    trade_direction = "long" if expected_direction == "bullish" else "short"

    # ── Step 5: Confluence scoring ───────────────────────────────────────
    current_price = df_15m["close"].iloc[-1]
    stoch_k_15m   = df_15m["stoch_k"].iloc[-1]
    stoch_k_1m    = df_1m["stoch_k"].iloc[-1]
    atr_15m_val   = df_15m["atr"].iloc[-1]

    confluence_result = score_confluence(
        price=current_price,
        direction=expected_direction,
        stoch_k_15m=stoch_k_15m,
        df_15m=df_15m,
        pivots=pivots,
        fib_levels=fib_levels,
        prev_day_high=prev_day_high,
        prev_day_low=prev_day_low,
    )

    # ── Step 6: Stop loss calculation ───────────────────────────────────
    # Mira MED-01: use pattern candle count for correct lookback
    pattern_candles = best_pattern.get("candles", 1)
    lookback = max(pattern_candles + 1, 3)  # at least 3 bars for context
    recent_15m = df_15m.tail(lookback)
    if trade_direction == "long":
        pattern_extreme = recent_15m["low"].min()
        stop_loss = pattern_extreme - 0.5 * atr_15m_val
    else:
        pattern_extreme = recent_15m["high"].max()
        stop_loss = pattern_extreme + 0.5 * atr_15m_val

    stop_distance = abs(current_price - stop_loss)

    # Mira HIGH-02: minimum stop distance bound (< 0.1 ATR = implausibly tight)
    if atr_15m_val > 0 and stop_distance < 0.1 * atr_15m_val:
        return None  # Stop distance implausibly tight — do not size position

    # ── Step 7: Validation ───────────────────────────────────────────────
    is_valid, inv_reason = _validate_signal(
        direction=trade_direction,
        trend_30m=trend_30m,
        confluence_score=confluence_result["score"],
        stoch_k_15m=stoch_k_15m,
        stoch_k_1m=stoch_k_1m,
        stop_distance=stop_distance,
        atr_15m=atr_15m_val,
        pattern_strength=best_pattern["strength"],
    )

    # ── Step 8: Targets ─────────────────────────────────────────────────
    if trade_direction == "long":
        target_1 = current_price + 1.5 * stop_distance
        target_2 = _nearest_target_level(current_price, "long", all_key_levels)
    else:
        target_1 = current_price - 1.5 * stop_distance
        target_2 = _nearest_target_level(current_price, "short", all_key_levels)

    rr_t1 = (abs(target_1 - current_price) / stop_distance) if stop_distance > 0 else 0

    # Mira HIGH-01: T2 must be beyond T1 — otherwise no valid second target
    t2_valid = (trade_direction == "long" and target_2 > target_1) or \
               (trade_direction == "short" and target_2 < target_1)
    if not t2_valid:
        # Find next level beyond T1 instead
        beyond_t1 = [l for l in all_key_levels if
                     (trade_direction == "long" and l > target_1) or
                     (trade_direction == "short" and l < target_1)]
        if beyond_t1:
            target_2 = min(beyond_t1) if trade_direction == "long" else max(beyond_t1)
        else:
            target_2 = target_1 * 1.005 if trade_direction == "long" else target_1 * 0.995

    # ── Step 9: Position sizing (1% risk, fixed fractional) ─────────────
    risk_dollars = equity * risk_pct
    position_size = risk_dollars / stop_distance if stop_distance > 0 else 0

    # ── Step 10: Signal strength ─────────────────────────────────────────
    atr_ratio = stop_distance / atr_15m_val if atr_15m_val > 0 else 99
    stoch_aligned = (
        (trade_direction == "long" and stoch_k_15m <= 25) or
        (trade_direction == "short" and stoch_k_15m >= 75)
    )

    signal_strength, confidence = _compute_signal_strength(
        confluence_score=confluence_result["score"],
        pattern_str=best_pattern["strength"],
        trend_match=True,  # already filtered above
        stoch_aligned=stoch_aligned,
        atr_ratio=atr_ratio,
    )

    # Downgrade confidence if invalidated
    if not is_valid:
        confidence = "Low"
        signal_strength = min(signal_strength, 30)

    # ── Step 11: Build signal ────────────────────────────────────────────
    signal = FinnSignal(
        timestamp=datetime.now(),
        instrument=instrument,
        direction=trade_direction,
        signal_strength=signal_strength,
        confidence=confidence,
        timeframe="Intraday",
        model="MTF-Price-Action-v1 (Vera Strategy)",
        pattern_15m=best_pattern["pattern"],
        pattern_strength=best_pattern["strength"],
        confluence_score=confluence_result["score"],
        confluence_detail=describe_confluence(confluence_result),
        trend_bias_30m=trend_30m,
        stoch_k_15m=stoch_k_15m,
        stoch_k_1m=stoch_k_1m,
        entry_price=current_price,
        stop_loss=stop_loss,
        target_1=target_1,
        target_2=target_2,
        stop_distance=stop_distance,
        atr_15m=atr_15m_val,
        risk_reward_t1=round(rr_t1, 2),
        position_size_1pct=round(position_size, 1),
        invalidated=not is_valid,
        invalidation_reason=inv_reason,
        notes=(
            f"Pattern direction: {best_pattern['direction']}. "
            f"ATR ratio: {atr_ratio:.2f}. "
            f"Stoch zone: {'aligned' if stoch_aligned else 'not aligned'}."
        ),
    )

    return signal


# ─────────────────────────────────────────────
# Batch scanner — run across multiple instruments
# ─────────────────────────────────────────────

def batch_scan(
    instruments: dict[str, dict],
    equity: float = 100_000,
    risk_pct: float = 0.01,
    min_confidence: str = "Medium",
) -> list[FinnSignal]:
    """
    Scan multiple instruments and return valid signals sorted by strength.

    instruments: dict of {ticker: {
        'df_30m': pd.DataFrame,
        'df_15m': pd.DataFrame,
        'df_1m':  pd.DataFrame,
        'prev_day_high': float,
        'prev_day_low':  float,
        'prev_day_close': float,
        'swing_high': float | None,
        'swing_low':  float | None,
    }}

    Returns list of FinnSignal objects (valid only, sorted by signal_strength desc).
    """
    conf_rank = {"High": 3, "Medium": 2, "Low": 1}
    min_rank  = conf_rank.get(min_confidence, 2)

    signals = []
    for ticker, data in instruments.items():
        try:
            sig = generate_signal(
                instrument=ticker,
                equity=equity,
                risk_pct=risk_pct,
                **data,
            )
            if sig and not sig.invalidated:
                if conf_rank.get(sig.confidence, 0) >= min_rank:
                    signals.append(sig)
        except Exception as e:
            # Mira MED-03: collect failures rather than swallowing silently
            signals.append(FinnSignal(
                timestamp=datetime.now(), instrument=ticker,
                direction="none", signal_strength=0, confidence="Low",
                timeframe="Intraday", model="MTF-Price-Action-v1",
                pattern_15m="", pattern_strength="", confluence_score=0,
                confluence_detail="", trend_bias_30m="", stoch_k_15m=0,
                stoch_k_1m=0, entry_price=0, stop_loss=0, target_1=0,
                target_2=0, stop_distance=0, atr_15m=0, risk_reward_t1=0,
                position_size_1pct=0, invalidated=True,
                invalidation_reason=f"Signal generation error: {e}",
            ))

    signals.sort(key=lambda s: s.signal_strength, reverse=True)
    return signals
