"""Tests for patterns.py"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "signals"))

import pytest
import pandas as pd
import numpy as np
from patterns import (
    _body, _upper_wick, _lower_wick, _range, _is_bullish, _is_bearish, _body_pct,
    doji, dragonfly_doji, gravestone_doji,
    hammer, inverted_hammer, hanging_man, shooting_star,
    pin_bar, belt_hold, engulfing, harami,
    dark_cloud_cover, piercing_line, tweezer,
    morning_star, evening_star,
    three_white_soldiers, three_black_crows,
    pattern_direction, scan_patterns,
)


# ── Helpers ──────────────────────────────────────────────────────

class TestHelpers:
    def test_body(self):
        assert _body(100, 110) == 10
        assert _body(110, 100) == 10

    def test_upper_wick_bullish(self):
        # bullish: o=100, h=115, c=110 → upper = 115-110 = 5
        assert _upper_wick(100, 115, 110) == 5

    def test_lower_wick_bullish(self):
        # bullish: o=100, l=90, c=110 → lower = min(100,110)-90 = 10
        assert _lower_wick(100, 90, 110) == 10

    def test_range(self):
        assert _range(120, 90) == 30

    def test_is_bullish(self):
        assert _is_bullish(100, 110) == True
        assert _is_bullish(110, 100) == False

    def test_is_bearish(self):
        assert _is_bearish(110, 100) == True
        assert _is_bearish(100, 110) == False

    def test_body_pct(self):
        # o=100, h=110, l=90, c=105 → body=5, range=20 → 0.25
        assert _body_pct(100, 110, 90, 105) == pytest.approx(0.25)


# ── Single Candle Patterns ───────────────────────────────────────

class TestDoji:
    def test_doji_detected(self):
        # Tiny body, big range
        assert doji(100, 110, 90, 100.5) == "neutral_doji"

    def test_not_doji(self):
        assert doji(100, 115, 95, 112) is None

    def test_zero_range(self):
        assert doji(100, 100, 100, 100) is None


class TestDragonfly:
    def test_detected(self):
        # Long lower wick, no upper wick, tiny body at top
        result = dragonfly_doji(100, 100.5, 90, 100.2)
        assert result == "bullish_dragonfly_doji"

    def test_not_dragonfly(self):
        assert dragonfly_doji(100, 110, 95, 105) is None


class TestGravestone:
    def test_detected(self):
        # Long upper wick, no lower wick, tiny body at bottom
        result = gravestone_doji(100, 110, 99.8, 100.2)
        assert result == "bearish_gravestone_doji"

    def test_not_gravestone(self):
        assert gravestone_doji(100, 105, 90, 102) is None


class TestHammer:
    def test_bullish_hammer(self):
        # Small body near top, long lower wick, tiny upper wick
        # o=99, h=100, l=90, c=100 → body=1, lower=9, upper=0
        assert hammer(99, 100, 90, 100) == "bullish_hammer"

    def test_not_hammer(self):
        assert hammer(100, 110, 95, 105) is None


class TestShootingStar:
    def test_detected(self):
        # Bearish: small body near bottom, long upper wick
        # o=101, h=110, l=100, c=100 → body=1, upper=9, lower=0
        assert shooting_star(101, 110, 100, 100) == "bearish_shooting_star"

    def test_not_shooting_star(self):
        assert shooting_star(100, 105, 95, 104) is None


class TestHangingMan:
    def test_detected(self):
        # Same shape as hammer but context differs (tested at function level)
        result = hanging_man(99, 100, 90, 100)
        assert result == "bearish_hanging_man"


class TestInvertedHammer:
    def test_detected(self):
        # Bullish: small body near bottom, long upper wick
        # o=100, h=110, l=100, c=101
        result = inverted_hammer(100, 110, 100, 101)
        assert result == "bullish_inverted_hammer"


class TestPinBar:
    def test_bullish_pin(self):
        # Long lower wick ≥66% of range, tiny body, tiny upper wick
        # range=10, lower=8, body=1, upper=1
        assert pin_bar(100.5, 101, 91, 101) == "bullish_pin_bar"

    def test_bearish_pin(self):
        # Long upper wick
        assert pin_bar(100.5, 110, 100, 100) == "bearish_pin_bar"

    def test_no_pin(self):
        assert pin_bar(100, 110, 90, 105) is None


class TestBeltHold:
    def test_bullish(self):
        # Opens at low, closes near high, big body
        result = belt_hold(100, 110, 100, 109)
        assert result == "bullish_belt_hold"

    def test_bearish(self):
        # Opens at high, closes near low
        result = belt_hold(110, 110, 100, 101)
        assert result == "bearish_belt_hold"


# ── Two-Candle Patterns ─────────────────────────────────────────

class TestEngulfing:
    def test_bullish_engulfing(self):
        # Prev bearish (o=105, c=100), curr bullish (o=99, c=106)
        assert engulfing(105, 100, 99, 106) == "bullish_engulfing"

    def test_bearish_engulfing(self):
        # Prev bullish (o=100, c=105), curr bearish (o=106, c=99)
        assert engulfing(100, 105, 106, 99) == "bearish_engulfing"

    def test_no_engulfing(self):
        assert engulfing(100, 105, 101, 104) is None


class TestHarami:
    def test_bullish_harami(self):
        # Prev bearish (o=110, c=100), curr small bullish inside
        result = harami(110, 112, 98, 100, 102, 106, 101, 105)
        assert result == "bullish_harami"

    def test_bearish_harami(self):
        # Prev bullish (o=100, c=110), curr small bearish inside
        result = harami(100, 112, 98, 110, 108, 109, 101, 103)
        assert result == "bearish_harami"

    def test_no_harami(self):
        # Curr body not inside prev body
        result = harami(100, 112, 98, 110, 90, 115, 85, 115)
        assert result is None


class TestDarkCloudCover:
    def test_detected(self):
        # Prev bullish (o=100, c=110), curr bearish opens above 110, closes below midpoint
        result = dark_cloud_cover(100, 110, 111, 103)
        assert result == "bearish_dark_cloud_cover"

    def test_not_detected(self):
        # Curr doesn't close below midpoint
        assert dark_cloud_cover(100, 110, 111, 108) is None


class TestPiercingLine:
    def test_detected(self):
        # Prev bearish (o=110, c=100), curr bullish opens below 100, closes above midpoint
        result = piercing_line(110, 100, 99, 107)
        assert result == "bullish_piercing_line"

    def test_not_detected(self):
        assert piercing_line(110, 100, 99, 102) is None


# ── Three-Candle Patterns ───────────────────────────────────────

class TestMorningStar:
    def test_detected(self):
        # c1 bearish, c2 small body, c3 bullish closing above c1 midpoint
        result = morning_star(
            c1_o=110, c1_c=100,
            c2_o=99, c2_h=101, c2_l=98, c2_c=99.5,
            c3_o=101, c3_c=108,
        )
        assert result in ("bullish_morning_star", "bullish_morning_doji_star")

    def test_not_morning_star(self):
        # c3 doesn't close high enough
        result = morning_star(
            c1_o=110, c1_c=100,
            c2_o=99, c2_h=101, c2_l=98, c2_c=99.5,
            c3_o=101, c3_c=103,
        )
        assert result is None


class TestEveningStar:
    def test_detected(self):
        result = evening_star(
            c1_o=100, c1_c=110,
            c2_o=111, c2_h=112, c2_l=110, c2_c=110.5,
            c3_o=109, c3_c=102,
        )
        assert result in ("bearish_evening_star", "bearish_evening_doji_star")


class TestThreeWhiteSoldiers:
    def test_detected(self):
        # Small upper wicks (<30% of body), opens within prev body, progressive closes
        candles = [
            (100, 104.5, 99, 104),   # body=4, upper=0.5 (12%)
            (102, 109.5, 101, 109),   # opens within prev body (100<102<104)
            (107, 114.5, 106, 114),   # opens within prev body (104<107<109)
        ]
        result = three_white_soldiers(candles)
        assert result == "bullish_three_white_soldiers"


class TestThreeBlackCrows:
    def test_detected(self):
        # Small lower wicks (<30% of body), opens within prev body, progressive closes
        candles = [
            (110, 111, 105.5, 106),  # bearish, body=4, lower=0.5
            (108, 109, 100.5, 101),  # opens within prev body (106<108<110)
            (103, 104, 95.5, 96),    # opens within prev body (101<103<108)
        ]
        result = three_black_crows(candles)
        assert result == "bearish_three_black_crows"


# ── Pattern Direction ────────────────────────────────────────────

class TestPatternDirection:
    def test_bullish(self):
        assert pattern_direction("bullish_engulfing") == "bullish"

    def test_bearish(self):
        assert pattern_direction("bearish_shooting_star") == "bearish"

    def test_neutral(self):
        assert pattern_direction("neutral_doji") == "neutral"

    def test_unknown(self):
        assert pattern_direction("unknown_pattern") is None


# ── Scan Patterns ────────────────────────────────────────────────

class TestScanPatterns:
    def test_returns_list(self):
        np.random.seed(42)
        n = 30
        closes = 100 + np.cumsum(np.random.randn(n))
        df = pd.DataFrame({
            "open": closes - np.random.rand(n),
            "high": closes + np.abs(np.random.randn(n)),
            "low": closes - np.abs(np.random.randn(n)),
            "close": closes,
            "volume": [5000.0] * n,
        })
        result = scan_patterns(df)
        assert isinstance(result, list)

    def test_pattern_dict_keys(self):
        # Create data that should produce at least one pattern
        np.random.seed(0)
        n = 30
        closes = 100 + np.cumsum(np.random.randn(n) * 2)
        df = pd.DataFrame({
            "open": closes - np.random.rand(n) * 2,
            "high": closes + np.abs(np.random.randn(n)) * 2,
            "low": closes - np.abs(np.random.randn(n)) * 2,
            "close": closes,
            "volume": [5000.0] * n,
        })
        result = scan_patterns(df)
        if result:
            pat = result[0]
            assert "pattern" in pat
            assert "direction" in pat
            assert "strength" in pat
