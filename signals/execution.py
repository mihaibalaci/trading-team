"""
execution.py — Remy's execution layer.
Receives FinnSignal from Finn, validated by Mira, and manages the full
trade lifecycle: order selection → entry → stop management → tiered exits → TCA report.

Sources:
  - Vera's strategy (VERA_STRATEGY_MTF_SCALP.md) — order types, exit structure, time rules
  - Pax Brief 04 (PAX_RESEARCH_04_EXECUTION_MICROSTRUCTURE.md) — slippage, TCA, order types
  - Remy's persona (Team/REMY.md) — execution report format

Pipeline per trade:
  FinnSignal → validate → time checks → order sizing → entry order →
  stop order → T1 exit (50%) → move stop to BE → T2 exit (30%) →
  trail runner (20%) → RemyReport
"""

from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

import uuid
from dataclasses import dataclass, field
from datetime import datetime, time as dtime, timedelta
from enum import Enum
from typing import Optional

from signal_engine import FinnSignal

# Optional broker connector — imported lazily to avoid circular deps.
# Type-check only; runtime isinstance check uses string to stay optional.
try:
    from broker_connector import BrokerConnector as _BrokerConnector
except ImportError:
    _BrokerConnector = None  # type: ignore


# ─────────────────────────────────────────────────────────────────
# Enums
# ─────────────────────────────────────────────────────────────────

class OrderType(str, Enum):
    LIMIT       = "limit"
    STOP_LIMIT  = "stop_limit"
    STOP_MARKET = "stop_market"   # only used for protective stop — never for entry
    MARKET      = "market"        # emergency close only — never for strategy entry

class OrderSide(str, Enum):
    BUY  = "buy"
    SELL = "sell"

class OrderStatus(str, Enum):
    PENDING   = "pending"
    WORKING   = "working"
    FILLED    = "filled"
    PARTIAL   = "partial"
    CANCELLED = "cancelled"
    REJECTED  = "rejected"
    EXPIRED   = "expired"

class OrderPurpose(str, Enum):
    ENTRY       = "entry"
    STOP_LOSS   = "stop_loss"
    TARGET_1    = "target_1"      # 50% exit at 1.5R
    TARGET_2    = "target_2"      # 30% exit at key level
    RUNNER_STOP = "runner_stop"   # trailing stop on remaining 20%
    EMERGENCY   = "emergency"

class TradeStatus(str, Enum):
    PENDING_ENTRY = "pending_entry"
    ACTIVE        = "active"
    PARTIAL_EXIT  = "partial_exit"
    CLOSED        = "closed"
    CANCELLED     = "cancelled"


# ─────────────────────────────────────────────────────────────────
# Order dataclass
# ─────────────────────────────────────────────────────────────────

@dataclass
class Order:
    """Represents a single order at any stage of its lifecycle."""
    order_id:     str
    instrument:   str
    side:         OrderSide
    order_type:   OrderType
    purpose:      OrderPurpose
    quantity:     float
    limit_price:  Optional[float]    # for limit and stop-limit
    stop_trigger: Optional[float]    # for stop-limit and stop-market
    submitted_at: datetime
    expiry:       Optional[datetime] = None   # None = GTC
    status:       OrderStatus = OrderStatus.PENDING
    fill_price:   Optional[float] = None
    fill_qty:     float = 0.0
    filled_at:    Optional[datetime] = None
    cancel_reason: str = ""

    @property
    def is_open(self) -> bool:
        return self.status in (OrderStatus.PENDING, OrderStatus.WORKING,
                               OrderStatus.PARTIAL)

    @property
    def is_done(self) -> bool:
        return self.status in (OrderStatus.FILLED, OrderStatus.CANCELLED,
                               OrderStatus.REJECTED, OrderStatus.EXPIRED)

    def simulate_fill(self, market_price: float,
                      spread: float = 0.0,
                      slippage_factor: float = 0.0001) -> bool:
        """
        Simulate whether this order would fill at market_price.
        Returns True if filled. Updates status and fill_price.
        Used for paper trading and backtesting.

        slippage_factor: fraction of price added to buys / subtracted from sells.
        spread: half-spread added to buys / subtracted from sells.
        """
        if self.is_done:
            return False

        now = datetime.now()
        if self.expiry and now > self.expiry:
            self.status = OrderStatus.EXPIRED
            self.cancel_reason = "Order expired"
            return False

        slippage = market_price * slippage_factor
        half_spread = spread / 2

        if self.order_type == OrderType.LIMIT:
            # Buy limit: fills if market_price <= limit_price
            # Sell limit: fills if market_price >= limit_price
            if self.side == OrderSide.BUY and market_price <= self.limit_price:
                self.fill_price = min(market_price + slippage + half_spread,
                                      self.limit_price)
                self._mark_filled(now)
                return True
            elif self.side == OrderSide.SELL and market_price >= self.limit_price:
                self.fill_price = max(market_price - slippage - half_spread,
                                      self.limit_price)
                self._mark_filled(now)
                return True

        elif self.order_type == OrderType.STOP_LIMIT:
            # Trigger first, then fill as limit
            if self.side == OrderSide.BUY and market_price >= self.stop_trigger:
                # Triggered — now fill as limit if price still within limit
                fill = market_price + slippage + half_spread
                if fill <= self.limit_price:
                    self.fill_price = fill
                    self._mark_filled(now)
                    return True
                else:
                    # Price gapped through limit — no fill (stop-limit protection)
                    self.status = OrderStatus.WORKING  # trigger hit, limit not filled
            elif self.side == OrderSide.SELL and market_price <= self.stop_trigger:
                fill = market_price - slippage - half_spread
                if fill >= self.limit_price:
                    self.fill_price = fill
                    self._mark_filled(now)
                    return True
                else:
                    self.status = OrderStatus.WORKING

        elif self.order_type == OrderType.STOP_MARKET:
            if self.side == OrderSide.BUY and market_price >= self.stop_trigger:
                self.fill_price = market_price + slippage + half_spread
                self._mark_filled(now)
                return True
            elif self.side == OrderSide.SELL and market_price <= self.stop_trigger:
                self.fill_price = market_price - slippage - half_spread
                self._mark_filled(now)
                return True

        elif self.order_type == OrderType.MARKET:
            self.fill_price = market_price + (slippage + half_spread) * (
                1 if self.side == OrderSide.BUY else -1)
            self._mark_filled(now)
            return True

        return False

    def _mark_filled(self, ts: datetime) -> None:
        self.status   = OrderStatus.FILLED
        self.fill_qty = self.quantity
        self.filled_at = ts


# ─────────────────────────────────────────────────────────────────
# Fill record
# ─────────────────────────────────────────────────────────────────

@dataclass
class Fill:
    """Single execution fill record for TCA."""
    order_id:      str
    purpose:       OrderPurpose
    side:          OrderSide
    quantity:      float
    fill_price:    float
    arrival_price: float          # price at time of order submission
    filled_at:     datetime
    commission:    float = 0.0    # per-unit commission

    @property
    def slippage_bps(self) -> float:
        """Arrival-price slippage in basis points. Positive = cost, negative = benefit."""
        if self.arrival_price == 0:
            return 0.0
        side_sign = 1 if self.side == OrderSide.BUY else -1
        return side_sign * (self.fill_price - self.arrival_price) / self.arrival_price * 10_000

    @property
    def gross_cost(self) -> float:
        """Total gross execution cost (slippage + commission)."""
        slip_cost = abs(self.fill_price - self.arrival_price) * self.quantity
        return slip_cost + self.commission * self.quantity


# ─────────────────────────────────────────────────────────────────
# Trade record — full lifecycle
# ─────────────────────────────────────────────────────────────────

@dataclass
class TradeRecord:
    """
    Full lifecycle record of a single trade.
    Created when a signal is accepted; updated through all exit legs.
    """
    trade_id:     str
    signal:       FinnSignal
    status:       TradeStatus = TradeStatus.PENDING_ENTRY

    # Orders
    entry_order:  Optional[Order] = None
    stop_order:   Optional[Order] = None
    t1_order:     Optional[Order] = None
    t2_order:     Optional[Order] = None
    runner_stop:  Optional[Order] = None

    # Fill tracking
    fills:              list[Fill] = field(default_factory=list)
    open_qty:           float = 0.0
    avg_entry_price:    Optional[float] = None
    realized_pnl:       float = 0.0

    # Execution metadata
    decision_price:     Optional[float] = None  # price when signal was accepted
    opened_at:          Optional[datetime] = None
    closed_at:          Optional[datetime] = None
    stop_moved_to_be:   bool = False
    t1_hit:             bool = False
    t2_hit:             bool = False

    @property
    def direction_sign(self) -> int:
        return 1 if self.signal.direction == "long" else -1

    @property
    def unrealized_pnl(self) -> float:
        """Requires current_price to be meaningful — placeholder."""
        return 0.0

    def record_fill(self, order: Order, arrival_price: float,
                    commission_per_unit: float = 0.0) -> Fill:
        fill = Fill(
            order_id=order.order_id,
            purpose=order.purpose,
            side=order.side,
            quantity=order.fill_qty,
            fill_price=order.fill_price,
            arrival_price=arrival_price,
            filled_at=order.filled_at,
            commission=commission_per_unit,
        )
        self.fills.append(fill)
        return fill

    def entry_fill(self) -> Optional[Fill]:
        return next((f for f in self.fills if f.purpose == OrderPurpose.ENTRY), None)

    def total_slippage_bps(self) -> float:
        if not self.fills:
            return 0.0
        return sum(f.slippage_bps * f.quantity for f in self.fills) / \
               max(sum(f.quantity for f in self.fills), 1)

    def total_commission(self) -> float:
        return sum(f.commission * f.quantity for f in self.fills)


# ─────────────────────────────────────────────────────────────────
# Remy's execution report
# ─────────────────────────────────────────────────────────────────

@dataclass
class RemyReport:
    """
    Remy's standard execution report (format from Team/REMY.md).
    Produced at trade close or on demand.
    """
    trade_id:          str
    instrument:        str
    direction:         str
    signal_id:         str            # FinnSignal timestamp as ID
    target_size:       float
    filled_size:       float
    execution_method:  str            # order type / algo used
    entry_fill_price:  Optional[float]
    avg_exit_price:    Optional[float]
    arrival_price:     Optional[float]
    slippage_bps:      float
    realized_pnl_r:    float          # P&L in R units
    total_cost_est:    float          # commission + slippage cost in $
    fills_log:         list[dict]
    trade_status:      str
    opened_at:         Optional[datetime]
    closed_at:         Optional[datetime]
    hold_duration_min: Optional[float]
    notes:             str = ""

    def summary(self) -> str:
        dur = f"{self.hold_duration_min:.1f} min" if self.hold_duration_min else "open"
        dir_arrow = "▲ LONG" if self.direction == "long" else "▼ SHORT"
        lines = [
            f"{'─'*58}",
            f"  REMY EXECUTION REPORT — {self.instrument}  |  {dir_arrow}",
            f"{'─'*58}",
            f"  Trade ID:         {self.trade_id}",
            f"  Status:           {self.trade_status.upper()}",
            f"  Method:           {self.execution_method}",
            f"  ─────────────────────────────────────────────────────",
            f"  Target Size:      {self.target_size:.1f} units",
            f"  Filled Size:      {self.filled_size:.1f} units",
            f"  Entry Fill:       {self.entry_fill_price:.4f}" if self.entry_fill_price else "  Entry Fill:       pending",
            f"  Avg Exit Price:   {self.avg_exit_price:.4f}" if self.avg_exit_price else "  Avg Exit Price:   open",
            f"  Arrival Price:    {self.arrival_price:.4f}" if self.arrival_price else "  Arrival Price:    N/A",
            f"  Slippage:         {self.slippage_bps:+.2f} bps",
            f"  ─────────────────────────────────────────────────────",
            f"  Realized P&L:     {self.realized_pnl_r:+.2f}R",
            f"  Est. Total Cost:  ${self.total_cost_est:.2f}",
            f"  Hold Duration:    {dur}",
        ]
        if self.fills_log:
            lines.append(f"  ─────────────────────────────────────────────────────")
            lines.append(f"  Fills ({len(self.fills_log)}):")
            for f in self.fills_log:
                lines.append(
                    f"    [{f['purpose']:12s}] {f['side']:4s} "
                    f"{f['qty']:.1f} @ {f['price']:.4f}  "
                    f"slip={f['slippage_bps']:+.1f}bps"
                )
        if self.notes:
            lines.append(f"  ─────────────────────────────────────────────────────")
            lines.append(f"  Notes: {self.notes}")
        lines.append(f"{'─'*58}")
        return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────
# Time-based trading guards (Vera strategy Section 9.1)
# ─────────────────────────────────────────────────────────────────

class SessionGuard:
    """
    Enforces time-based trading restrictions from Vera's strategy (Section 9.1).
    All times are in the instrument's primary session timezone.
    """

    # Minutes blocked after session open (Vera: "do not enter in first 5 minutes")
    OPEN_BLOCK_MINUTES = 5

    # Minutes blocked before a scheduled news event
    PRE_NEWS_BLOCK_MINUTES = 10

    # Friday magnet window (Person Ch.12 — 10:30 AM rule)
    FRIDAY_CAUTION_START = dtime(10, 25)
    FRIDAY_CAUTION_END   = dtime(10, 35)

    def __init__(self,
                 session_open: dtime = dtime(9, 30),
                 session_close: dtime = dtime(16, 0),
                 news_times: list[dtime] | None = None):
        self.session_open  = session_open
        self.session_close = session_close
        self.news_times    = news_times or []

    def check(self, now: datetime) -> tuple[bool, str]:
        """
        Returns (allowed, reason_if_blocked).
        True = clear to trade. False = blocked with reason.
        """
        t = now.time()

        # Session boundary checks
        if t < self.session_open:
            return False, f"Pre-session (opens {self.session_open})"
        if t >= self.session_close:
            return False, f"Post-session (closed {self.session_close})"

        # Opening block (first N minutes)
        open_dt = datetime.combine(now.date(), self.session_open)
        if (now - open_dt).total_seconds() < self.OPEN_BLOCK_MINUTES * 60:
            return False, (
                f"Opening block — wait {self.OPEN_BLOCK_MINUTES} min "
                f"after session open (Vera Section 9.1)"
            )

        # Pre-news block
        for news_time in self.news_times:
            news_dt = datetime.combine(now.date(), news_time)
            mins_to_news = (news_dt - now).total_seconds() / 60
            if 0 <= mins_to_news <= self.PRE_NEWS_BLOCK_MINUTES:
                return False, (
                    f"Pre-news block — {mins_to_news:.0f} min before "
                    f"scheduled release at {news_time} (Vera Section 9.1)"
                )

        # Friday 10:30 caution window (Person Ch.12)
        if now.weekday() == 4:  # Friday
            if self.FRIDAY_CAUTION_START <= t <= self.FRIDAY_CAUTION_END:
                return False, (
                    "Friday 10:30 caution window — momentum frequently "
                    "reverses here (Person, Complete Guide Ch.12)"
                )

        return True, ""


# ─────────────────────────────────────────────────────────────────
# Signal freshness and entry validity checks
# ─────────────────────────────────────────────────────────────────

class SignalValidator:
    """
    Remy's pre-execution signal checks.
    Validates that the signal is still actionable at time of execution.
    Separate from Mira's pre-trade risk checks (which run at signal generation).
    """

    # Max age of a signal before Remy considers it stale (seconds)
    # On a 1m strategy, a 3-minute-old signal is already 3 bars old.
    MAX_SIGNAL_AGE_SECONDS = 180

    # Max price drift from signal's entry_price before entry is abandoned
    # Vera Section 5.3: "moved more than 1.0 × ATR from confluence zone = missed entry"
    MAX_DRIFT_ATR_MULTIPLE = 1.0

    @staticmethod
    def validate(signal: FinnSignal, current_price: float,
                 now: datetime | None = None) -> tuple[bool, str]:
        """
        Returns (valid, reason_if_invalid).
        Checks: not invalidated, not stale, price not too far from entry.
        """
        now = now or datetime.now()

        if signal.invalidated:
            return False, f"Signal already invalidated: {signal.invalidation_reason}"

        if signal.direction == "none":
            return False, "Signal has no direction (error signal from batch_scan)"

        # Staleness check
        age_seconds = (now - signal.timestamp).total_seconds()
        if age_seconds > SignalValidator.MAX_SIGNAL_AGE_SECONDS:
            return False, (
                f"Signal stale — {age_seconds:.0f}s old "
                f"(max {SignalValidator.MAX_SIGNAL_AGE_SECONDS}s on 1m strategy)"
            )

        # Price drift check (Vera Section 5.3)
        drift = abs(current_price - signal.entry_price)
        max_drift = SignalValidator.MAX_DRIFT_ATR_MULTIPLE * signal.atr_15m
        if max_drift > 0 and drift > max_drift:
            return False, (
                f"Entry missed — price drifted {drift:.4f} "
                f"({drift/signal.atr_15m:.2f}× ATR) from signal entry "
                f"(max {SignalValidator.MAX_DRIFT_ATR_MULTIPLE}× ATR, Vera Section 5.3)"
            )

        return True, ""


# ─────────────────────────────────────────────────────────────────
# Order factory — builds the correct order type for each leg
# ─────────────────────────────────────────────────────────────────

class OrderFactory:
    """
    Constructs Order objects for each trade leg.
    Vera's rule: NO market orders for entry. Use stop-limit or limit only.
    Market orders reserved for emergency close only.
    """

    # Slippage allowance added to stop-limit's limit_price
    # to improve fill rate while bounding worst-case fill
    LIMIT_BUFFER_ATR_MULTIPLE = 0.10

    @staticmethod
    def entry_order(signal: FinnSignal,
                    quantity: float,
                    arrival_price: float) -> Order:
        """
        Build the entry order.
        - High/Medium confidence → stop-limit (Vera: limit order just above trigger high)
        - Stops trigger when price moves in signal direction past entry_price
        - Limit set with a small ATR buffer to improve fill probability
        """
        buf = signal.atr_15m * OrderFactory.LIMIT_BUFFER_ATR_MULTIPLE

        if signal.direction == "long":
            side          = OrderSide.BUY
            stop_trigger  = signal.entry_price                  # trigger when price breaks entry
            limit_price   = signal.entry_price + buf            # max acceptable fill
        else:
            side          = OrderSide.SELL
            stop_trigger  = signal.entry_price
            limit_price   = signal.entry_price - buf

        return Order(
            order_id     = str(uuid.uuid4())[:8],
            instrument   = signal.instrument,
            side         = side,
            order_type   = OrderType.STOP_LIMIT,
            purpose      = OrderPurpose.ENTRY,
            quantity     = quantity,
            limit_price  = limit_price,
            stop_trigger = stop_trigger,
            submitted_at = datetime.now(),
            expiry       = datetime.now() + timedelta(minutes=5),  # entry expires in 5 min
        )

    @staticmethod
    def stop_loss_order(signal: FinnSignal, quantity: float) -> Order:
        """
        Protective stop — stop-market to guarantee exit even on gaps.
        (Pax Brief 04: stop-limit can fail to fill on gaps — use stop-market for stops)
        """
        if signal.direction == "long":
            side         = OrderSide.SELL
            stop_trigger = signal.stop_loss
        else:
            side         = OrderSide.BUY
            stop_trigger = signal.stop_loss

        return Order(
            order_id     = str(uuid.uuid4())[:8],
            instrument   = signal.instrument,
            side         = side,
            order_type   = OrderType.STOP_MARKET,
            purpose      = OrderPurpose.STOP_LOSS,
            quantity     = quantity,
            limit_price  = None,
            stop_trigger = stop_trigger,
            submitted_at = datetime.now(),
            expiry       = None,  # GTC — stays until explicitly cancelled
        )

    @staticmethod
    def target_order(signal: FinnSignal, quantity: float,
                     target_price: float, purpose: OrderPurpose) -> Order:
        """Limit order for Target 1 or Target 2 exits."""
        if signal.direction == "long":
            side = OrderSide.SELL
        else:
            side = OrderSide.BUY

        return Order(
            order_id     = str(uuid.uuid4())[:8],
            instrument   = signal.instrument,
            side         = side,
            order_type   = OrderType.LIMIT,
            purpose      = purpose,
            quantity     = quantity,
            limit_price  = target_price,
            stop_trigger = None,
            submitted_at = datetime.now(),
            expiry       = None,
        )

    @staticmethod
    def trailing_stop_order(signal: FinnSignal, quantity: float,
                             current_stop: float) -> Order:
        """
        Runner trailing stop — stop-market at current EMA9-based level.
        Called repeatedly as EMA9 moves; cancels previous and submits new.
        """
        if signal.direction == "long":
            side         = OrderSide.SELL
            stop_trigger = current_stop
        else:
            side         = OrderSide.BUY
            stop_trigger = current_stop

        return Order(
            order_id     = str(uuid.uuid4())[:8],
            instrument   = signal.instrument,
            side         = side,
            order_type   = OrderType.STOP_MARKET,
            purpose      = OrderPurpose.RUNNER_STOP,
            quantity     = quantity,
            limit_price  = None,
            stop_trigger = stop_trigger,
            submitted_at = datetime.now(),
            expiry       = None,
        )

    @staticmethod
    def emergency_close(signal: FinnSignal, quantity: float) -> Order:
        """Market order for emergency close only. Never used for strategy entries."""
        side = OrderSide.SELL if signal.direction == "long" else OrderSide.BUY
        return Order(
            order_id     = str(uuid.uuid4())[:8],
            instrument   = signal.instrument,
            side         = side,
            order_type   = OrderType.MARKET,
            purpose      = OrderPurpose.EMERGENCY,
            quantity     = quantity,
            limit_price  = None,
            stop_trigger = None,
            submitted_at = datetime.now(),
            expiry       = None,
        )


# ─────────────────────────────────────────────────────────────────
# Market impact estimator (Pax Brief 04 — Square Root Model)
# ─────────────────────────────────────────────────────────────────

def estimate_market_impact(
    order_qty:         float,
    adv:               float,
    daily_vol:         float,
    eta:               float = 0.1,
) -> float:
    """
    Square Root Market Impact Model (Pax Brief 04, Section 4.4).
    Returns estimated market impact as a fraction of price.

    MI = σ × η × √(Q / V_daily)

    order_qty   : total order size (units)
    adv         : average daily volume (units)
    daily_vol   : daily return volatility (e.g. 0.01 = 1%)
    eta         : market impact coefficient (0.1–1.0; use 0.1 for liquid large-caps)
    """
    if adv <= 0:
        return 0.0
    participation = order_qty / adv
    return daily_vol * eta * (participation ** 0.5)


def estimate_execution_cost(
    fill_price:   float,
    order_qty:    float,
    slippage_bps: float,
    commission_per_unit: float = 0.0,
) -> float:
    """
    Total estimated execution cost in dollars.
    slippage_bps: arrival-price slippage in basis points.
    """
    slippage_cost   = fill_price * order_qty * slippage_bps / 10_000
    commission_cost = commission_per_unit * order_qty
    return slippage_cost + commission_cost


# ─────────────────────────────────────────────────────────────────
# Execution Engine — main class
# ─────────────────────────────────────────────────────────────────

class ExecutionEngine:
    """
    Remy's execution engine. Manages the full trade lifecycle for one signal at a time.

    Usage (paper/live):
        engine = ExecutionEngine(signal, session_guard, commission_per_unit=0.65)
        result = engine.accept(current_price)   # validates and places entry order
        engine.tick(current_price, ema9_1m)     # call on every 1m bar close
        report = engine.get_report()
    """

    # Vera exit splits: 50% T1 / 30% T2 / 20% runner
    T1_FRACTION      = 0.50
    T2_FRACTION      = 0.30
    RUNNER_FRACTION  = 0.20

    def __init__(self,
                 signal:               FinnSignal,
                 session_guard:        SessionGuard | None = None,
                 commission_per_unit:  float = 0.0,
                 adv:                  float = 0.0,
                 daily_vol:            float = 0.0,
                 connector=None):       # BrokerConnector | None
        self.signal              = signal
        self.session_guard       = session_guard or SessionGuard()
        self.commission          = commission_per_unit
        self.adv                 = adv
        self.daily_vol           = daily_vol
        self.connector           = connector   # None → simulation mode
        self.trade               = TradeRecord(
            trade_id=str(uuid.uuid4())[:12],
            signal=signal,
        )
        self._log: list[str]       = []
        self._broker_ids: dict[str, str] = {}  # order_id → broker order_id

    # ── Public interface ───────────────────────────────────────────

    def accept(self, current_price: float,
               now: datetime | None = None) -> tuple[bool, str]:
        """
        Step 1: Validate signal and place entry order.
        Returns (accepted, reason).
        Call once per signal.
        """
        now = now or datetime.now()

        # Time guard
        ok, reason = self.session_guard.check(now)
        if not ok:
            self._log.append(f"[BLOCKED] Time guard: {reason}")
            return False, reason

        # Signal validity (freshness + price drift)
        ok, reason = SignalValidator.validate(self.signal, current_price, now)
        if not ok:
            self._log.append(f"[BLOCKED] Signal invalid: {reason}")
            return False, reason

        # Calculate lot sizes
        total_qty   = round(self.signal.position_size_1pct, 0)
        t1_qty      = round(total_qty * self.T1_FRACTION,     0)
        t2_qty      = round(total_qty * self.T2_FRACTION,     0)
        runner_qty  = total_qty - t1_qty - t2_qty

        if total_qty <= 0:
            return False, "Position size is zero — signal too small for account"

        # Market impact check (Pax Brief 04: keep below 5% ADV)
        if self.adv > 0:
            participation = total_qty / self.adv
            if participation > 0.05:
                self._log.append(
                    f"[WARN] Order is {participation:.1%} of ADV — may have market impact"
                )

        # Build and record the entry order
        entry = OrderFactory.entry_order(self.signal, total_qty, current_price)
        stop  = OrderFactory.stop_loss_order(self.signal, total_qty)
        t1    = OrderFactory.target_order(
                    self.signal, t1_qty, self.signal.target_1, OrderPurpose.TARGET_1)
        t2    = OrderFactory.target_order(
                    self.signal, t2_qty, self.signal.target_2, OrderPurpose.TARGET_2)

        self.trade.entry_order    = entry
        self.trade.stop_order     = stop
        self.trade.t1_order       = t1
        self.trade.t2_order       = t2
        self.trade.decision_price = current_price
        self.trade.status         = TradeStatus.PENDING_ENTRY
        self._runner_qty          = runner_qty

        # Submit entry to broker now.
        # Stop, T1, and T2 are submitted only after entry confirms —
        # Alpaca rejects sell orders on a flat account (wash trade guard).
        self._submit(entry)

        self._log.append(
            f"[ACCEPT] Signal accepted. Entry order {entry.order_id} submitted. "
            f"Size: {total_qty:.0f} (T1:{t1_qty:.0f} T2:{t2_qty:.0f} Run:{runner_qty:.0f})"
        )
        return True, "Order submitted"

    def tick(self, current_price: float,
             ema9_1m: float | None = None,
             now: datetime | None = None,
             spread: float = 0.0) -> None:
        """
        Step 2: Call on every 1m bar close.
        Manages the full order lifecycle: entry fill → stop/target monitoring → exits.

        current_price : last 1m close price
        ema9_1m       : current 1m EMA9 value (required for runner trailing stop)
        spread        : current bid-ask spread (used in fill simulation)
        """
        now = now or datetime.now()
        slip = self.signal.atr_15m * 0.05   # 5% ATR as slippage factor estimate

        # ── Pending entry → try to fill ─────────────────────────────────
        if self.trade.status == TradeStatus.PENDING_ENTRY:
            entry = self.trade.entry_order
            if entry and self._check_fill(entry, current_price, spread, slip):
                self.trade.avg_entry_price = entry.fill_price
                self.trade.open_qty        = entry.fill_qty
                self.trade.status          = TradeStatus.ACTIVE
                self.trade.opened_at       = now
                self.trade.record_fill(
                    entry, self.trade.decision_price or current_price, self.commission)
                self._log.append(
                    f"[FILLED] Entry filled @ {entry.fill_price:.4f} "
                    f"(arrival: {self.trade.decision_price:.4f})"
                )
                # Position confirmed — now submit stop, T1, and T2
                self._submit(self.trade.stop_order)
                self._submit(self.trade.t1_order)
                self._submit(self.trade.t2_order)
            elif entry and entry.is_done and not entry.status == OrderStatus.FILLED:
                # Entry expired or cancelled without fill
                self._broker_cancel(self.trade.stop_order)
                self.trade.status = TradeStatus.CANCELLED
                self._log.append(f"[MISS] Entry order {entry.status} — trade cancelled")
            return

        if self.trade.status not in (TradeStatus.ACTIVE, TradeStatus.PARTIAL_EXIT):
            return

        # ── Stop loss monitoring ─────────────────────────────────────────
        stop = self.trade.stop_order
        if stop and stop.is_open:
            if self._check_fill(stop, current_price, spread, slip):
                # Stop hit — close everything remaining
                remaining = self.trade.open_qty
                self.trade.open_qty   = 0
                self.trade.closed_at  = now
                self.trade.status     = TradeStatus.CLOSED
                self._cancel_open_orders()
                exit_r = self._compute_r(stop.fill_price)
                self.trade.realized_pnl += exit_r * remaining
                self.trade.record_fill(
                    stop, stop.stop_trigger or current_price, self.commission)
                self._log.append(
                    f"[STOP] Stop triggered @ {stop.fill_price:.4f}. "
                    f"R on remaining: {exit_r:.2f}R"
                )
                return

        # ── Target 1 (50% exit at 1.5R) ─────────────────────────────────
        if not self.trade.t1_hit:
            t1 = self.trade.t1_order
            if t1 and t1.is_open:
                if self._check_fill(t1, current_price, spread, slip):
                    self.trade.t1_hit = True
                    self.trade.status = TradeStatus.PARTIAL_EXIT
                    self.trade.open_qty -= t1.fill_qty
                    exit_r = self._compute_r(t1.fill_price)
                    self.trade.realized_pnl += exit_r * t1.fill_qty
                    self.trade.record_fill(
                        t1, self.trade.avg_entry_price or current_price, self.commission)
                    self._log.append(
                        f"[T1 HIT] {t1.fill_qty:.0f} units @ {t1.fill_price:.4f}  "
                        f"({exit_r:.2f}R). Moving stop to breakeven."
                    )
                    # Move stop to breakeven (Vera Section 6.3)
                    self._move_stop_to_breakeven()

        # ── Target 2 (30% exit at key level) ────────────────────────────
        if self.trade.t1_hit and not self.trade.t2_hit:
            t2 = self.trade.t2_order
            if t2 and t2.is_open:
                if self._check_fill(t2, current_price, spread, slip):
                    self.trade.t2_hit = True
                    self.trade.open_qty -= t2.fill_qty
                    exit_r = self._compute_r(t2.fill_price)
                    self.trade.realized_pnl += exit_r * t2.fill_qty
                    self.trade.record_fill(
                        t2, self.trade.avg_entry_price or current_price, self.commission)
                    self._log.append(
                        f"[T2 HIT] {t2.fill_qty:.0f} units @ {t2.fill_price:.4f}  "
                        f"({exit_r:.2f}R). Runner active: {self.trade.open_qty:.0f} units."
                    )
                    # Switch stop to trailing (runner)
                    if ema9_1m:
                        self._update_runner_stop(ema9_1m)

        # ── Runner: update trailing stop on every tick ───────────────────
        if self.trade.t2_hit and self.trade.open_qty > 0:
            if ema9_1m:
                self._update_runner_stop(ema9_1m)

            runner_stop = self.trade.runner_stop
            if runner_stop and runner_stop.is_open:
                if self._check_fill(runner_stop, current_price, spread, slip):
                    exit_r = self._compute_r(runner_stop.fill_price)
                    self.trade.realized_pnl += exit_r * runner_stop.fill_qty
                    self.trade.record_fill(
                        runner_stop, current_price, self.commission)
                    self.trade.open_qty = 0
                    self.trade.closed_at = now
                    self.trade.status = TradeStatus.CLOSED
                    self._log.append(
                        f"[RUNNER] Trailing stop hit @ {runner_stop.fill_price:.4f}  "
                        f"({exit_r:.2f}R). Trade closed."
                    )

    def force_close(self, current_price: float,
                    reason: str = "Manual close") -> None:
        """Emergency market close of all remaining position."""
        if self.trade.open_qty > 0:
            emergency = OrderFactory.emergency_close(self.signal, self.trade.open_qty)
            if self.connector:
                self._submit(emergency)
                # Give the market order a moment then treat as filled at current price
                emergency.fill_price = current_price
                emergency.fill_qty   = self.trade.open_qty
                emergency.status     = OrderStatus.FILLED
                emergency.filled_at  = datetime.now()
            else:
                emergency.simulate_fill(current_price)
            exit_r = self._compute_r(emergency.fill_price or current_price)
            self.trade.realized_pnl += exit_r * self.trade.open_qty
            self.trade.record_fill(emergency, current_price, self.commission)
            self.trade.open_qty  = 0
            self.trade.closed_at = datetime.now()
            self.trade.status    = TradeStatus.CLOSED
            self._cancel_open_orders()
            self._log.append(f"[EMERGENCY] Force closed @ {current_price:.4f}. Reason: {reason}")

    def get_report(self) -> RemyReport:
        """Generate Remy's standard execution report."""
        t   = self.trade
        sig = self.signal

        entry_fill  = t.entry_fill()
        exit_fills  = [f for f in t.fills if f.purpose != OrderPurpose.ENTRY]

        avg_exit = None
        if exit_fills:
            total_qty = sum(f.quantity for f in exit_fills)
            avg_exit  = sum(f.fill_price * f.quantity for f in exit_fills) / max(total_qty, 1)

        hold_min = None
        if t.opened_at and t.closed_at:
            hold_min = (t.closed_at - t.opened_at).total_seconds() / 60

        fills_log = [{
            "purpose":       f.purpose.value,
            "side":          f.side.value,
            "qty":           f.quantity,
            "price":         f.fill_price,
            "slippage_bps":  f.slippage_bps,
        } for f in t.fills]

        # Market impact cost estimate
        impact_frac = 0.0
        if self.adv > 0 and self.daily_vol > 0 and entry_fill:
            impact_frac = estimate_market_impact(
                sig.position_size_1pct, self.adv, self.daily_vol)
        impact_cost = impact_frac * (entry_fill.fill_price if entry_fill else 0) * sig.position_size_1pct

        total_cost = t.total_commission() + impact_cost + (
            abs(t.total_slippage_bps()) / 10_000 *
            (entry_fill.fill_price if entry_fill else 0) *
            sig.position_size_1pct
        )

        return RemyReport(
            trade_id          = t.trade_id,
            instrument        = sig.instrument,
            direction         = sig.direction,
            signal_id         = sig.timestamp.isoformat(),
            target_size       = sig.position_size_1pct,
            filled_size       = t.open_qty + sum(f.quantity for f in exit_fills),
            execution_method  = "STOP-LIMIT entry / STOP-MARKET stop / LIMIT targets",
            entry_fill_price  = entry_fill.fill_price if entry_fill else None,
            avg_exit_price    = avg_exit,
            arrival_price     = t.decision_price,
            slippage_bps      = t.total_slippage_bps(),
            realized_pnl_r    = t.realized_pnl / max(sig.position_size_1pct, 1),
            total_cost_est    = total_cost,
            fills_log         = fills_log,
            trade_status      = t.status.value,
            opened_at         = t.opened_at,
            closed_at         = t.closed_at,
            hold_duration_min = hold_min,
            notes             = " | ".join(self._log[-5:]),
        )

    # ── Private helpers ────────────────────────────────────────────

    def _compute_r(self, exit_price: float) -> float:
        """Return P&L of this exit in R units (per unit of position)."""
        if not self.trade.avg_entry_price or self.signal.stop_distance == 0:
            return 0.0
        return (
            (exit_price - self.trade.avg_entry_price) * self.signal.direction_sign
            / self.signal.stop_distance
        )

    def _move_stop_to_breakeven(self) -> None:
        """Cancel current stop and replace with one at avg entry price."""
        if self.trade.stop_order:
            self._broker_cancel(self.trade.stop_order)
            self.trade.stop_order.status = OrderStatus.CANCELLED
            self.trade.stop_order.cancel_reason = "Moved to breakeven after T1"

        new_stop = OrderFactory.stop_loss_order(self.signal, self.trade.open_qty)
        new_stop.stop_trigger = self.trade.avg_entry_price
        self.trade.stop_order       = new_stop
        self.trade.stop_moved_to_be = True
        self._submit(new_stop)
        self._log.append(
            f"[BE STOP] Stop moved to breakeven @ {self.trade.avg_entry_price:.4f}")

    def _update_runner_stop(self, ema9_1m: float) -> None:
        """
        Update trailing stop to trail behind 1m EMA9 (Vera Section 7.1).
        Cancel previous runner stop, submit new one at current EMA9 level.
        Only move the stop in the profit direction — never widen it.
        """
        if self.signal.direction == "long":
            new_level = ema9_1m - 0.25 * self.signal.atr_15m   # small buffer below EMA9
        else:
            new_level = ema9_1m + 0.25 * self.signal.atr_15m

        # Only update if the new stop is better than current
        current = self.trade.runner_stop
        if current and current.is_open:
            if self.signal.direction == "long" and new_level <= current.stop_trigger:
                return   # Don't lower a long stop
            if self.signal.direction == "short" and new_level >= current.stop_trigger:
                return   # Don't raise a short stop
            self._broker_cancel(current)
            current.status        = OrderStatus.CANCELLED
            current.cancel_reason = "Replaced by updated trailing stop"

        new_stop = OrderFactory.trailing_stop_order(
            self.signal, self._runner_qty, new_level)
        self.trade.runner_stop = new_stop
        self._submit(new_stop)

    def _submit(self, order: Order) -> None:
        """
        Submit an order to the broker (if connected) and record the broker ID.
        No-op in simulation mode — simulate_fill() handles the local path.
        """
        if self.connector is None:
            return
        try:
            broker_id = self.connector.submit_order(order)
            self._broker_ids[order.order_id] = broker_id
            self._log.append(
                f"[BROKER] {order.purpose.value.upper()} submitted → broker ID {broker_id}"
            )
        except Exception as e:
            self._log.append(f"[ERROR] Order submission failed ({order.purpose.value}): {e}")
            raise

    def _check_fill(self, order: Order,
                    current_price: float, spread: float, slip: float) -> bool:
        """
        Check whether an order has filled.
        - Broker mode: query the connector; update order fields from broker response.
        - Simulation mode: delegate to order.simulate_fill().
        Returns True if the order is now filled.
        """
        if self.connector is None:
            return order.simulate_fill(current_price, spread, slip / max(current_price, 1e-9))

        broker_id = self._broker_ids.get(order.order_id)
        if not broker_id:
            return False  # not yet submitted

        try:
            status, fill_price, fill_qty = self.connector.get_order_fill(broker_id)
        except ConnectionError:
            return False  # transient — retry next tick

        order.status = status
        if status == OrderStatus.FILLED:
            order.fill_price = fill_price or current_price
            order.fill_qty   = fill_qty   or order.quantity
            order.filled_at  = order.filled_at or datetime.now()
            return True
        return False

    def _broker_cancel(self, order: Order) -> None:
        """Cancel an order at the broker. No-op in simulation mode."""
        if self.connector is None or order is None:
            return
        broker_id = self._broker_ids.get(order.order_id)
        if broker_id:
            try:
                self.connector.cancel_order(broker_id)
            except Exception as e:
                self._log.append(f"[WARN] Broker cancel failed ({order.purpose.value}): {e}")

    def _cancel_open_orders(self) -> None:
        """Cancel all remaining open orders when trade is closed."""
        for order in [self.trade.stop_order, self.trade.t1_order,
                      self.trade.t2_order, self.trade.runner_stop]:
            if order and order.is_open:
                self._broker_cancel(order)
                order.status = OrderStatus.CANCELLED
                order.cancel_reason = "Trade closed"
