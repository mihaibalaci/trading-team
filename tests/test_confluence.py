"""Tests for confluence.py"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "signals"))

import pytest
import pandas as pd
import numpy as np
from confluence import score_confluence, describe_confluence
from indicators import pivot_points, fibonacci_levels


def _make_15m_df(price=100.0):
    """Minimal 15m df with ema21."""
    return pd.DataFrame({"close": [price], "ema21": [price]})


class TestScoreConfluence:
    def test_returns_dict(self):
        pivots = pivot_points(110, 90, 100)
        result = score_confluence(
            price=100.0, direction="bullish", stoch_k_15m=15.0,
            df_15m=_make_15m_df(100.0), pivots=pivots,
        )
        assert isinstance(result, dict)
        assert "score" in result
        assert "valid" in result
        assert "factors" in result

    def test_high_confluence(self):
        pivots = pivot_points(110, 90, 100)
        fibs = fibonacci_levels(110, 90)
        result = score_confluence(
            price=100.0, direction="bullish", stoch_k_15m=15.0,
            df_15m=_make_15m_df(100.0), pivots=pivots,
            fib_levels=fibs, prev_day_high=110, prev_day_low=90,
        )
        assert result["score"] >= 3
        assert result["valid"] is True

    def test_low_confluence(self):
        pivots = pivot_points(200, 180, 190)
        result = score_confluence(
            price=50.0, direction="bullish", stoch_k_15m=50.0,
            df_15m=_make_15m_df(50.0), pivots=pivots,
        )
        assert result["score"] < 3
        assert result["valid"] is False

    def test_bearish_stoch_aligned(self):
        pivots = pivot_points(110, 90, 100)
        result = score_confluence(
            price=100.0, direction="bearish", stoch_k_15m=85.0,
            df_15m=_make_15m_df(100.0), pivots=pivots,
        )
        stoch_hit = result["factors"]["stochastic"]["hit"]
        assert stoch_hit is True

    def test_five_factors(self):
        pivots = pivot_points(110, 90, 100)
        result = score_confluence(
            price=100.0, direction="bullish", stoch_k_15m=15.0,
            df_15m=_make_15m_df(100.0), pivots=pivots,
        )
        assert len(result["factors"]) == 5


class TestDescribeConfluence:
    def test_returns_string(self):
        pivots = pivot_points(110, 90, 100)
        result = score_confluence(
            price=100.0, direction="bullish", stoch_k_15m=15.0,
            df_15m=_make_15m_df(100.0), pivots=pivots,
        )
        desc = describe_confluence(result)
        assert isinstance(desc, str)
        assert "Confluence Score" in desc
