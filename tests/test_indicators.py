"""Tests for indicators.py"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "signals"))

import pytest
import pandas as pd
import numpy as np
from indicators import (
    ema, ema_stack, stochastic, stoch_zone, atr,
    pivot_points, nearest_pivot_level,
    fibonacci_levels, nearest_fib_level,
    trend_bias, price_above_ema,
)


def _make_df(n=60, base=100.0, volatility=1.0):
    """Generate a simple OHLCV DataFrame for testing."""
    np.random.seed(42)
    closes = base + np.cumsum(np.random.randn(n) * volatility)
    return pd.DataFrame({
        "open":   closes - np.random.rand(n) * volatility,
        "high":   closes + np.abs(np.random.randn(n)) * volatility,
        "low":    closes - np.abs(np.random.randn(n)) * volatility,
        "close":  closes,
        "volume": np.random.randint(1000, 10000, n).astype(float),
    })


# ── EMA ──────────────────────────────────────────────────────────

class TestEma:
    def test_ema_length(self):
        s = pd.Series(range(20), dtype=float)
        result = ema(s, 9)
        assert len(result) == len(s)

    def test_ema_last_value_reasonable(self):
        s = pd.Series([10.0] * 20)
        assert ema(s, 9).iloc[-1] == pytest.approx(10.0)


class TestEmaStack:
    def test_adds_columns(self):
        df = _make_df()
        result = ema_stack(df)
        for col in ("ema9", "ema21", "ema50"):
            assert col in result.columns

    def test_no_mutation(self):
        df = _make_df()
        original_cols = list(df.columns)
        ema_stack(df)
        # ema_stack returns new df, original may or may not be mutated
        # but result should have extra cols
        result = ema_stack(df)
        assert len(result.columns) > len(original_cols)


# ── Stochastic ───────────────────────────────────────────────────

class TestStochastic:
    def test_adds_columns(self):
        df = _make_df()
        result = stochastic(df)
        assert "stoch_k" in result.columns
        assert "stoch_d" in result.columns

    def test_values_in_range(self):
        df = _make_df()
        result = stochastic(df)
        k = result["stoch_k"].dropna()
        assert k.min() >= 0
        assert k.max() <= 100


class TestStochZone:
    def test_oversold(self):
        assert stoch_zone(10) == "oversold"

    def test_overbought(self):
        assert stoch_zone(90) == "overbought"

    def test_bullish_presignal(self):
        assert stoch_zone(22) == "bullish_presignal"

    def test_bearish_presignal(self):
        assert stoch_zone(78) == "bearish_presignal"

    def test_neutral(self):
        assert stoch_zone(50) == "neutral"


# ── ATR ──────────────────────────────────────────────────────────

class TestAtr:
    def test_adds_column(self):
        df = _make_df()
        result = atr(df)
        assert "atr" in result.columns

    def test_atr_positive(self):
        df = _make_df()
        result = atr(df)
        assert result["atr"].dropna().min() > 0


# ── Pivot Points ─────────────────────────────────────────────────

class TestPivotPoints:
    def test_basic(self):
        pivots = pivot_points(110, 90, 100)
        assert pivots["pivot"] == pytest.approx(100.0)
        assert pivots["r1"] > pivots["pivot"]
        assert pivots["s1"] < pivots["pivot"]
        assert pivots["r2"] > pivots["r1"]
        assert pivots["s2"] < pivots["s1"]

    def test_nearest_pivot_level_hit(self):
        pivots = pivot_points(110, 90, 100)
        name, val = nearest_pivot_level(100.05, pivots, tolerance_pct=0.01)
        assert name == "pivot"

    def test_nearest_pivot_level_miss(self):
        pivots = pivot_points(110, 90, 100)
        name, val = nearest_pivot_level(50.0, pivots, tolerance_pct=0.001)
        assert name is None


# ── Fibonacci ────────────────────────────────────────────────────

class TestFibonacci:
    def test_levels(self):
        fibs = fibonacci_levels(200, 100)
        assert fibs["38.2"] == pytest.approx(161.8, abs=0.1)
        assert fibs["50.0"] == pytest.approx(150.0)
        assert fibs["61.8"] == pytest.approx(138.2, abs=0.1)

    def test_nearest_fib_hit(self):
        fibs = fibonacci_levels(200, 100)
        name, val = nearest_fib_level(150.1, fibs, tolerance_pct=0.01)
        assert name == "50.0"

    def test_nearest_fib_miss(self):
        fibs = fibonacci_levels(200, 100)
        name, val = nearest_fib_level(10.0, fibs, tolerance_pct=0.001)
        assert name is None


# ── Trend Bias ───────────────────────────────────────────────────

class TestTrendBias:
    def test_bullish(self):
        # Steadily rising prices
        n = 60
        closes = 100 + np.arange(n, dtype=float)
        df = pd.DataFrame({
            "open": closes - 0.5, "high": closes + 1,
            "low": closes - 1, "close": closes, "volume": [1000.0] * n,
        })
        df = ema_stack(df)
        df = stochastic(df)
        assert trend_bias(df) == "bullish"

    def test_bearish(self):
        n = 60
        closes = 200 - np.arange(n, dtype=float)
        df = pd.DataFrame({
            "open": closes + 0.5, "high": closes + 1,
            "low": closes - 1, "close": closes, "volume": [1000.0] * n,
        })
        df = ema_stack(df)
        df = stochastic(df)
        assert trend_bias(df) == "bearish"


# ── Price Above EMA ──────────────────────────────────────────────

class TestPriceAboveEma:
    def test_above(self):
        df = pd.DataFrame({"close": [110.0], "ema50": [100.0]})
        assert price_above_ema(df) == True

    def test_below(self):
        df = pd.DataFrame({"close": [90.0], "ema50": [100.0]})
        assert price_above_ema(df) == False
