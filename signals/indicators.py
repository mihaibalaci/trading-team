"""
indicators.py — Technical indicator calculations for Finn's signal models.
All indicators used in Vera's Multi-Timeframe Price Action Strategy.
"""

import pandas as pd
import numpy as np


# ─────────────────────────────────────────────
# Moving Averages
# ─────────────────────────────────────────────

def ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential Moving Average."""
    return series.ewm(span=period, adjust=False).mean()


def ema_stack(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add EMA 9, 21, 50 to a DataFrame.
    Returns df with columns: ema9, ema21, ema50.
    """
    df = df.copy()
    df["ema9"]  = ema(df["close"], 9)
    df["ema21"] = ema(df["close"], 21)
    df["ema50"] = ema(df["close"], 50)
    return df


# ─────────────────────────────────────────────
# Stochastic Oscillator
# ─────────────────────────────────────────────

def stochastic(df: pd.DataFrame, k_period: int = 14,
               d_period: int = 3, smooth_k: int = 3) -> pd.DataFrame:
    """
    Full Stochastic Oscillator (14, 3, 3).
    Returns df with columns: stoch_k, stoch_d.
    """
    df = df.copy()
    low_min  = df["low"].rolling(k_period).min()
    high_max = df["high"].rolling(k_period).max()
    raw_k = 100 * (df["close"] - low_min) / (high_max - low_min + 1e-10)
    df["stoch_k"] = raw_k.rolling(smooth_k).mean()
    df["stoch_d"] = df["stoch_k"].rolling(d_period).mean()
    return df


def stoch_zone(stoch_k: float) -> str:
    """Classify stochastic value into zone."""
    if stoch_k < 20:
        return "oversold"
    elif stoch_k < 25:
        return "bullish_presignal"
    elif stoch_k > 80:
        return "overbought"
    elif stoch_k > 75:
        return "bearish_presignal"
    else:
        return "neutral"


# ─────────────────────────────────────────────
# ATR — Average True Range
# ─────────────────────────────────────────────

def atr(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """
    Average True Range (Wilder's smoothing).
    Returns df with column: atr.
    """
    df = df.copy()
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"]  - prev_close).abs()
    ], axis=1).max(axis=1)
    # Wilder smoothing = EMA with alpha = 1/period
    df["atr"] = tr.ewm(alpha=1 / period, adjust=False).mean()
    return df


# ─────────────────────────────────────────────
# Pivot Points (Daily)
# ─────────────────────────────────────────────

def pivot_points(prev_high: float, prev_low: float,
                 prev_close: float) -> dict:
    """
    Classic pivot point levels from previous day's H/L/C.
    Returns dict: pivot, r1, r2, r3, s1, s2, s3.
    Formula: Person, 'Complete Guide to Technical Trading Tactics', Ch.6.
    """
    p  = (prev_high + prev_low + prev_close) / 3
    r1 = 2 * p - prev_low
    s1 = 2 * p - prev_high
    r2 = p + (prev_high - prev_low)
    s2 = p - (prev_high - prev_low)
    r3 = prev_high + 2 * (p - prev_low)
    s3 = prev_low  - 2 * (prev_high - p)
    return {"pivot": p, "r1": r1, "r2": r2, "r3": r3,
            "s1": s1, "s2": s2, "s3": s3}


def nearest_pivot_level(price: float, pivots: dict,
                         tolerance_pct: float = 0.002) -> tuple[str | None, float | None]:
    """
    Returns (level_name, level_value) if price is within tolerance of a pivot level.
    tolerance_pct: default 0.2% of price.
    """
    tolerance = price * tolerance_pct
    for name, level in pivots.items():
        if abs(price - level) <= tolerance:
            return name, level
    return None, None


# ─────────────────────────────────────────────
# Fibonacci Retracement
# ─────────────────────────────────────────────

def fibonacci_levels(swing_high: float, swing_low: float) -> dict:
    """
    Standard Fibonacci retracement levels from a swing.
    Returns dict of retracement prices.
    """
    diff = swing_high - swing_low
    return {
        "0.0":   swing_high,
        "23.6":  swing_high - 0.236 * diff,
        "38.2":  swing_high - 0.382 * diff,
        "50.0":  swing_high - 0.500 * diff,
        "61.8":  swing_high - 0.618 * diff,
        "78.6":  swing_high - 0.786 * diff,
        "100.0": swing_low,
    }


def nearest_fib_level(price: float, fib_levels: dict,
                       tolerance_pct: float = 0.002) -> tuple[str | None, float | None]:
    """
    Returns (level_name, level_value) if price is within tolerance of a Fibonacci level.
    """
    tolerance = price * tolerance_pct
    for name, level in fib_levels.items():
        if abs(price - level) <= tolerance:
            return name, level
    return None, None


# ─────────────────────────────────────────────
# Trend Analysis
# ─────────────────────────────────────────────

def trend_bias(df: pd.DataFrame, lookback: int = 5) -> str:
    """
    Determine trend bias from EMA stack and market structure.
    Requires ema9, ema21, ema50 columns.
    Returns: 'bullish', 'bearish', or 'ranging'.
    """
    if len(df) < lookback + 1:
        return "ranging"

    last = df.iloc[-1]

    # EMA alignment
    bull_ema = last["ema9"] > last["ema21"] > last["ema50"]
    bear_ema = last["ema9"] < last["ema21"] < last["ema50"]

    # Market structure: higher highs/lows or lower highs/lows
    recent = df.tail(lookback)
    highs = recent["high"].values
    lows  = recent["low"].values

    hh = all(highs[i] >= highs[i - 1] for i in range(1, len(highs)))
    hl = all(lows[i]  >= lows[i - 1]  for i in range(1, len(lows)))
    lh = all(highs[i] <= highs[i - 1] for i in range(1, len(highs)))
    ll = all(lows[i]  <= lows[i - 1]  for i in range(1, len(lows)))

    bull_structure = hh or hl
    bear_structure = lh or ll

    # Require BOTH EMA alignment AND market structure — prevents ranging markets
    # from being classified as trending (Mira review: HIGH-05)
    if bull_ema and bull_structure:
        return "bullish"
    elif bear_ema and bear_structure:
        return "bearish"
    else:
        return "ranging"


def price_above_ema(df: pd.DataFrame, ema_col: str = "ema50") -> bool:
    """True if last close is above the specified EMA."""
    return df.iloc[-1]["close"] > df.iloc[-1][ema_col]
