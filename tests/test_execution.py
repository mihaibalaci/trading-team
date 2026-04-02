"""Tests for execution.py — SessionGuard, SignalValidator, cost estimators."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "signals"))

import pytest
from datetime import datetime, time as dtime
from execution import (
    SessionGuard, SignalValidator, OrderType, OrderSide, OrderStatus,
    OrderPurpose, TradeStatus, Order, Fill, TradeRecord,
    estimate_market_impact, estimate_execution_cost,
)
from signal_engine import FinnSignal


# ── SessionGuard ─────────────────────────────────────────────────

class TestSessionGuard:
    def setup_method(self):
        self.guard = SessionGuard(
            session_open=dtime(9, 30),
            session_close=dtime(16, 0),
        )

    def test_pre_session_blocked(self):
        ok, reason = self.guard.check(datetime(2026, 4, 1, 9, 0))
        assert ok is False
        assert "Pre-session" in reason

    def test_post_session_blocked(self):
        ok, reason = self.guard.check(datetime(2026, 4, 1, 16, 5))
        assert ok is False
        assert "Post-session" in reason

    def test_opening_block(self):
        ok, reason = self.guard.check(datetime(2026, 4, 1, 9, 32))
        assert ok is False
        assert "Opening block" in reason

    def test_core_session_allowed(self):
        ok, reason = self.guard.check(datetime(2026, 4, 1, 10, 30))  # Tuesday
        assert ok is True

    def test_friday_caution(self):
        # April 3, 2026 is a Friday
        ok, reason = self.guard.check(datetime(2026, 4, 3, 10, 30))
        assert ok is False
        assert "Friday" in reason

    def test_pre_news_block(self):
        guard = SessionGuard(
            session_open=dtime(9, 30),
            session_close=dtime(16, 0),
            news_times=[dtime(14, 0)],
        )
        ok, reason = guard.check(datetime(2026, 4, 1, 13, 55))
        assert ok is False
        assert "Pre-news" in reason


# ── SignalValidator ──────────────────────────────────────────────

class TestSignalValidator:
    def _make_signal(self, **overrides):
        defaults = dict(
            timestamp=datetime(2026, 4, 1, 10, 0, 0),
            instrument="SPY", direction="long", signal_strength=70,
            confidence="High", timeframe="Intraday",
            model="test", pattern_15m="bullish_engulfing",
            pattern_strength="high", confluence_score=4,
            confluence_detail="", trend_bias_30m="bullish",
            stoch_k_15m=20.0, stoch_k_1m=30.0,
            entry_price=500.0, stop_loss=498.0,
            target_1=503.0, target_2=505.0,
            stop_distance=2.0, atr_15m=3.0,
            risk_reward_t1=1.5, position_size_1pct=500.0,
        )
        defaults.update(overrides)
        return FinnSignal(**defaults)

    def test_valid_signal(self):
        sig = self._make_signal()
        ok, reason = SignalValidator.validate(sig, 500.0, datetime(2026, 4, 1, 10, 1, 0))
        assert ok is True

    def test_stale_signal(self):
        sig = self._make_signal()
        ok, reason = SignalValidator.validate(sig, 500.0, datetime(2026, 4, 1, 10, 5, 0))
        assert ok is False
        assert "stale" in reason.lower()

    def test_invalidated_signal(self):
        sig = self._make_signal(invalidated=True, invalidation_reason="test")
        ok, reason = SignalValidator.validate(sig, 500.0, datetime(2026, 4, 1, 10, 1, 0))
        assert ok is False

    def test_price_drift(self):
        sig = self._make_signal(entry_price=500.0, atr_15m=2.0)
        ok, reason = SignalValidator.validate(sig, 505.0, datetime(2026, 4, 1, 10, 1, 0))
        assert ok is False
        assert "drift" in reason.lower()


# ── Enums ────────────────────────────────────────────────────────

class TestEnums:
    def test_order_types(self):
        assert OrderType.MARKET.value == "market"
        assert OrderType.LIMIT.value == "limit"

    def test_order_side(self):
        assert OrderSide.BUY.value == "buy"
        assert OrderSide.SELL.value == "sell"

    def test_trade_status(self):
        assert TradeStatus.ACTIVE.value == "active"
        assert TradeStatus.CLOSED.value == "closed"


# ── Cost Estimators ──────────────────────────────────────────────

class TestMarketImpact:
    def test_basic(self):
        mi = estimate_market_impact(
            order_qty=1000, adv=1_000_000, daily_vol=0.02, eta=0.1,
        )
        assert mi > 0
        assert mi < 0.01  # should be small for liquid stock

    def test_zero_adv(self):
        assert estimate_market_impact(1000, 0, 0.02) == 0.0

    def test_larger_order_more_impact(self):
        small = estimate_market_impact(100, 1_000_000, 0.02)
        large = estimate_market_impact(10_000, 1_000_000, 0.02)
        assert large > small


class TestExecutionCost:
    def test_basic(self):
        cost = estimate_execution_cost(
            fill_price=100.0, order_qty=100, slippage_bps=5.0,
        )
        # 100 * 100 * 5 / 10000 = 5.0
        assert cost == pytest.approx(5.0)

    def test_with_commission(self):
        cost = estimate_execution_cost(
            fill_price=100.0, order_qty=100, slippage_bps=5.0,
            commission_per_unit=0.01,
        )
        assert cost == pytest.approx(5.0 + 1.0)
