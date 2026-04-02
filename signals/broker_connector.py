"""
broker_connector.py — Kai's unified broker abstraction layer.

Provides a single BrokerConnector interface that Remy's ExecutionEngine
calls regardless of which platform is live. Currently implements Alpaca
(paper and live). Adding a new broker means adding a new implementation
of BrokerConnector without touching any other team code.

Setup:
    1. Copy .env.example to .env
    2. Fill in ALPACA_API_KEY and ALPACA_SECRET_KEY from:
       https://app.alpaca.markets → Paper Trading → API Keys
    3. Leave TRADING_MODE=paper until ready for live capital

Usage:
    from broker_connector import connect
    connector = connect()                   # reads .env automatically
    connector.submit_order(remy_order)
    equity = connector.get_account_equity()
"""

from __future__ import annotations

import os
import time
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional
from enum import Enum

from dotenv import load_dotenv

# Alpaca SDK
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import (
    MarketOrderRequest,
    LimitOrderRequest,
    StopOrderRequest,
    StopLimitOrderRequest,
    GetOrdersRequest,
)
from alpaca.trading.enums import (
    OrderSide as AlpacaSide,
    TimeInForce,
    OrderStatus as AlpacaOrderStatus,
    QueryOrderStatus,
)
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.live import StockDataStream
from alpaca.data.requests import StockLatestQuoteRequest, StockBarsRequest
from alpaca.data.timeframe import TimeFrame

# Remy's order types (for translation)
from execution import Order, OrderType, OrderSide, OrderStatus


log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────
# Shared data structures
# ─────────────────────────────────────────────────────────────────

@dataclass
class Position:
    instrument:   str
    qty:          float          # positive = long, negative = short
    avg_entry:    float
    market_value: float
    unrealized_pnl: float


@dataclass
class AccountState:
    equity:           float
    cash:             float
    buying_power:     float
    portfolio_value:  float
    trading_mode:     str        # 'paper' or 'live'


# ─────────────────────────────────────────────────────────────────
# Abstract interface — every broker implementation must satisfy this
# ─────────────────────────────────────────────────────────────────

class BrokerConnector(ABC):
    """
    Unified broker interface. Remy's ExecutionEngine calls this.
    Platform-specific details are completely hidden from callers.
    """

    @abstractmethod
    def submit_order(self, order: Order) -> str:
        """
        Submit an order. Returns the broker-assigned order ID.
        Raises ConnectionError if the broker is unreachable.
        Raises ValueError if the order is rejected.
        Is idempotent: if order.order_id was already submitted, returns
        the existing broker order ID without re-submitting.
        """

    @abstractmethod
    def cancel_order(self, broker_order_id: str) -> bool:
        """Cancel a working order. Returns True if cancelled, False if already done."""

    @abstractmethod
    def get_order_status(self, broker_order_id: str) -> OrderStatus:
        """Map the broker's order state to Remy's OrderStatus enum."""

    @abstractmethod
    def get_position(self, instrument: str) -> Optional[Position]:
        """Return current position for an instrument, or None if flat."""

    @abstractmethod
    def get_account_equity(self) -> float:
        """Return total account equity (portfolio value)."""

    @abstractmethod
    def get_account_state(self) -> AccountState:
        """Return full account snapshot."""

    @abstractmethod
    def get_order_fill(self, broker_order_id: str) -> tuple[OrderStatus, float | None, float | None]:
        """
        Query the broker for the current state of an order.
        Returns (status, avg_fill_price, filled_qty).
        fill_price and filled_qty are None if the order has not filled.
        """

    @abstractmethod
    def get_latest_price(self, instrument: str) -> float:
        """Return latest mid-price for an instrument."""

    @abstractmethod
    def subscribe_fills(self, callback: Callable[[dict], None]) -> None:
        """
        Register a callback to receive fill updates in real time.
        Callback receives a dict with keys: broker_order_id, fill_price,
        fill_qty, timestamp, purpose (if tagged).
        """

    @abstractmethod
    def subscribe_prices(self,
                          instruments: list[str],
                          callback: Callable[[str, float, datetime], None]) -> None:
        """
        Register a callback for live price updates.
        Callback receives (instrument, price, timestamp).
        """

    @abstractmethod
    def health_check(self) -> tuple[bool, str]:
        """Returns (is_healthy, detail). Called every 30s by the watchdog."""


# ─────────────────────────────────────────────────────────────────
# Alpaca implementation
# ─────────────────────────────────────────────────────────────────

# Map Alpaca's order status strings → Remy's OrderStatus
_ALPACA_STATUS_MAP: dict[str, OrderStatus] = {
    AlpacaOrderStatus.NEW:              OrderStatus.WORKING,
    AlpacaOrderStatus.PARTIALLY_FILLED: OrderStatus.PARTIAL,
    AlpacaOrderStatus.FILLED:           OrderStatus.FILLED,
    AlpacaOrderStatus.DONE_FOR_DAY:     OrderStatus.EXPIRED,
    AlpacaOrderStatus.CANCELED:         OrderStatus.CANCELLED,
    AlpacaOrderStatus.EXPIRED:          OrderStatus.EXPIRED,
    AlpacaOrderStatus.REPLACED:         OrderStatus.CANCELLED,
    AlpacaOrderStatus.PENDING_CANCEL:   OrderStatus.WORKING,
    AlpacaOrderStatus.PENDING_REPLACE:  OrderStatus.WORKING,
    AlpacaOrderStatus.ACCEPTED:         OrderStatus.WORKING,
    AlpacaOrderStatus.PENDING_NEW:      OrderStatus.PENDING,
    AlpacaOrderStatus.ACCEPTED_FOR_BIDDING: OrderStatus.WORKING,
    AlpacaOrderStatus.STOPPED:          OrderStatus.CANCELLED,
    AlpacaOrderStatus.REJECTED:         OrderStatus.REJECTED,
    AlpacaOrderStatus.SUSPENDED:        OrderStatus.REJECTED,
    AlpacaOrderStatus.CALCULATED:       OrderStatus.WORKING,
    AlpacaOrderStatus.HELD:             OrderStatus.WORKING,
}


def _round_price(price: float) -> float:
    """
    Round to the minimum price increment Alpaca accepts for US equities.
    Stocks >= $1.00 must be in $0.01 increments (whole cents).
    Stocks < $1.00 allow $0.0001 increments.
    """
    if price >= 1.0:
        return round(price, 2)
    return round(price, 4)


class AlpacaConnector(BrokerConnector):
    """
    Alpaca broker implementation (paper and live).
    Paper endpoint:  https://paper-api.alpaca.markets
    Live endpoint:   https://api.alpaca.markets

    Credentials loaded from environment — never passed as arguments.
    """

    # Halt new order submissions if health check fails for this long (seconds)
    HALT_AFTER_SECONDS = 30

    def __init__(self, api_key: str, secret_key: str, paper: bool = True):
        if not api_key or not secret_key:
            raise ValueError(
                "Alpaca API key and secret key are required. "
                "Set ALPACA_API_KEY and ALPACA_SECRET_KEY in your .env file."
            )

        self._paper       = paper
        self._mode        = "paper" if paper else "live"
        self._last_seen   = time.time()
        self._halted      = False

        # Trading client (orders, positions, account)
        self._trading     = TradingClient(api_key, secret_key, paper=paper)

        # Market data client (quotes, bars)
        self._data        = StockHistoricalDataClient(api_key, secret_key)

        # Idempotency map: our order_id → Alpaca broker order ID
        self._submitted: dict[str, str] = {}

        log.info(f"[KAI] AlpacaConnector initialised — mode: {self._mode.upper()}")

    # ── Order submission ──────────────────────────────────────────

    def submit_order(self, order: Order) -> str:
        self._check_not_halted()

        # Idempotency: if already submitted, return existing broker ID
        if order.order_id in self._submitted:
            log.info(f"[KAI] Order {order.order_id} already submitted — "
                     f"broker ID: {self._submitted[order.order_id]}")
            return self._submitted[order.order_id]

        side = AlpacaSide.BUY if order.side == OrderSide.BUY else AlpacaSide.SELL
        qty  = str(int(order.quantity))

        try:
            if order.order_type == OrderType.MARKET:
                req = MarketOrderRequest(
                    symbol        = order.instrument,
                    qty           = qty,
                    side          = side,
                    time_in_force = TimeInForce.DAY,
                    client_order_id = order.order_id,
                )

            elif order.order_type == OrderType.LIMIT:
                req = LimitOrderRequest(
                    symbol        = order.instrument,
                    qty           = qty,
                    side          = side,
                    limit_price   = _round_price(order.limit_price),
                    time_in_force = TimeInForce.DAY,
                    client_order_id = order.order_id,
                )

            elif order.order_type == OrderType.STOP_MARKET:
                req = StopOrderRequest(
                    symbol        = order.instrument,
                    qty           = qty,
                    side          = side,
                    stop_price    = _round_price(order.stop_trigger),
                    time_in_force = TimeInForce.GTC,
                    client_order_id = order.order_id,
                )

            elif order.order_type == OrderType.STOP_LIMIT:
                req = StopLimitOrderRequest(
                    symbol        = order.instrument,
                    qty           = qty,
                    side          = side,
                    stop_price    = _round_price(order.stop_trigger),
                    limit_price   = _round_price(order.limit_price),
                    time_in_force = TimeInForce.DAY,
                    client_order_id = order.order_id,
                )

            else:
                raise ValueError(f"Unsupported order type: {order.order_type}")

            result = self._trading.submit_order(req)
            broker_id = str(result.id)
            self._submitted[order.order_id] = broker_id
            self._last_seen = time.time()

            log.info(
                f"[KAI] Submitted {order.order_type.value.upper()} "
                f"{side.value.upper()} {qty} {order.instrument} "
                f"→ broker ID {broker_id}"
            )
            return broker_id

        except Exception as e:
            log.error(f"[KAI] Order submission failed for {order.order_id}: {e}")
            raise

    # ── Order management ──────────────────────────────────────────

    def cancel_order(self, broker_order_id: str) -> bool:
        try:
            self._trading.cancel_order_by_id(broker_order_id)
            log.info(f"[KAI] Cancelled order {broker_order_id}")
            return True
        except Exception as e:
            # If already filled or cancelled, Alpaca raises — treat as non-error
            log.warning(f"[KAI] Cancel {broker_order_id} — {e}")
            return False

    def get_order_status(self, broker_order_id: str) -> OrderStatus:
        status, _, _ = self.get_order_fill(broker_order_id)
        return status

    def get_order_fill(self, broker_order_id: str) -> tuple[OrderStatus, float | None, float | None]:
        try:
            o = self._trading.get_order_by_id(broker_order_id)
            self._last_seen = time.time()
            status     = _ALPACA_STATUS_MAP.get(o.status, OrderStatus.PENDING)
            fill_price = float(o.filled_avg_price) if o.filled_avg_price else None
            fill_qty   = float(o.filled_qty)       if o.filled_qty       else None
            return status, fill_price, fill_qty
        except Exception as e:
            log.error(f"[KAI] get_order_fill({broker_order_id}) failed: {e}")
            raise ConnectionError(f"Cannot reach Alpaca: {e}")

    # ── Account and position queries ──────────────────────────────

    def get_position(self, instrument: str) -> Optional[Position]:
        try:
            p = self._trading.get_open_position(instrument)
            self._last_seen = time.time()
            return Position(
                instrument    = instrument,
                qty           = float(p.qty),
                avg_entry     = float(p.avg_entry_price),
                market_value  = float(p.market_value),
                unrealized_pnl= float(p.unrealized_pl),
            )
        except Exception:
            return None   # flat position — no error

    def get_account_equity(self) -> float:
        return self.get_account_state().equity

    def get_account_state(self) -> AccountState:
        try:
            acct = self._trading.get_account()
            self._last_seen = time.time()
            return AccountState(
                equity          = float(acct.equity),
                cash            = float(acct.cash),
                buying_power    = float(acct.buying_power),
                portfolio_value = float(acct.portfolio_value),
                trading_mode    = self._mode,
            )
        except Exception as e:
            log.error(f"[KAI] get_account_state failed: {e}")
            raise ConnectionError(f"Cannot reach Alpaca: {e}")

    # ── Market data ───────────────────────────────────────────────

    def get_latest_price(self, instrument: str) -> float:
        try:
            req    = StockLatestQuoteRequest(symbol_or_symbols=instrument)
            quotes = self._data.get_stock_latest_quote(req)
            quote  = quotes[instrument]
            mid    = (float(quote.bid_price) + float(quote.ask_price)) / 2
            self._last_seen = time.time()
            return mid
        except Exception as e:
            log.error(f"[KAI] get_latest_price({instrument}) failed: {e}")
            raise

    # ── Streaming ─────────────────────────────────────────────────

    def subscribe_fills(self, callback: Callable[[dict], None]) -> None:
        """
        Registers a callback for live fill (trade_update) events via
        Alpaca's WebSocket trade stream.

        The callback receives a dict:
            broker_order_id, fill_price, fill_qty, timestamp, event

        Note: runs in the background via Alpaca's async stream.
        Call stream.run() in a background thread or asyncio task.
        """
        import asyncio, threading

        stream = self._trading_stream_client()

        async def _on_trade_update(data):
            if data.event in ("fill", "partial_fill"):
                callback({
                    "broker_order_id": str(data.order.id),
                    "fill_price":  float(data.order.filled_avg_price or 0),
                    "fill_qty":    float(data.order.filled_qty or 0),
                    "timestamp":   data.timestamp,
                    "event":       data.event,
                })

        stream.subscribe_trade_updates(_on_trade_update)

        def _run():
            stream.run()

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        log.info("[KAI] Fill stream subscription started (background thread)")

    def subscribe_prices(self,
                          instruments: list[str],
                          callback: Callable[[str, float, datetime], None]) -> None:
        """
        Registers a callback for real-time quote updates via Alpaca's
        WebSocket data stream. Runs in a background thread.
        """
        import threading

        data_stream = StockDataStream(
            self._trading._api_key,
            self._trading._secret_key,
            feed="iex",   # IEX feed is free; use "sip" for paid SIP data
        )

        async def _on_quote(data):
            mid = (float(data.bid_price) + float(data.ask_price)) / 2
            callback(data.symbol, mid, data.timestamp)

        data_stream.subscribe_quotes(_on_quote, *instruments)

        def _run():
            data_stream.run()

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        log.info(f"[KAI] Price stream subscription started for: {instruments}")

    # ── Health check ──────────────────────────────────────────────

    def health_check(self) -> tuple[bool, str]:
        try:
            acct = self._trading.get_account()
            self._last_seen = time.time()
            self._halted    = False
            return True, f"Alpaca {self._mode} OK — equity ${float(acct.equity):,.2f}"
        except Exception as e:
            age = time.time() - self._last_seen
            if age > self.HALT_AFTER_SECONDS:
                self._halted = True
                log.critical(
                    f"[KAI] Alpaca unreachable for {age:.0f}s — "
                    f"new order submissions HALTED"
                )
            return False, f"Alpaca unreachable: {e}"

    # ── Private helpers ───────────────────────────────────────────

    def _check_not_halted(self) -> None:
        if self._halted:
            raise ConnectionError(
                "Kai's order submission is HALTED — Alpaca has been "
                f"unreachable for >{self.HALT_AFTER_SECONDS}s. "
                "Restore connectivity and call health_check() to resume."
            )

    def _trading_stream_client(self):
        """Lazy-import to avoid pulling in async deps unless streaming is used."""
        from alpaca.trading.stream import TradingStream
        return TradingStream(
            self._trading._api_key,
            self._trading._secret_key,
            paper=self._paper,
        )


# ─────────────────────────────────────────────────────────────────
# Factory — reads .env and returns the right connector
# ─────────────────────────────────────────────────────────────────

def connect(env_path: str | None = None) -> BrokerConnector:
    """
    Load credentials from .env and return a configured BrokerConnector.

    env_path: path to .env file. Defaults to signals/.env

    TRADING_MODE selects the broker:
        paper   → Alpaca paper trading (default)
        live    → Alpaca live trading (requires ALPACA_LIVE_CONFIRMED=yes)
        mt5     → MetaTrader 5 (requires MT5 terminal running on same Windows machine)
        mt5live → MetaTrader 5 live account (same credentials, mode flag for logging)

    Example .env for Alpaca:
        TRADING_MODE=paper
        ALPACA_API_KEY=PKXXXXXXXXXXXXXXXX
        ALPACA_SECRET_KEY=your_secret_here

    Example .env for MT5:
        TRADING_MODE=mt5
        MT5_LOGIN=12345678
        MT5_PASSWORD=your_password
        MT5_SERVER=YourBroker-Demo
    """
    load_dotenv(env_path or os.path.join(os.path.dirname(__file__), ".env"))

    mode = os.getenv("TRADING_MODE", "paper").strip().lower()

    # ── MetaTrader 5 ──────────────────────────────────────────────────────────
    if mode in ("mt5", "mt5live", "mt5demo"):
        from mt5_connector import MT5Connector

        login_str = os.getenv("MT5_LOGIN", "").strip()
        password  = os.getenv("MT5_PASSWORD", "").strip()
        server    = os.getenv("MT5_SERVER", "").strip()

        if not login_str or not password or not server:
            raise EnvironmentError(
                "MT5_LOGIN, MT5_PASSWORD, and MT5_SERVER must be set in .env "
                "when TRADING_MODE=mt5.\n"
                "Example:\n"
                "  MT5_LOGIN=12345678\n"
                "  MT5_PASSWORD=your_password\n"
                "  MT5_SERVER=YourBroker-Demo"
            )

        try:
            login = int(login_str)
        except ValueError:
            raise EnvironmentError(
                f"MT5_LOGIN must be an integer account number, got: '{login_str}'"
            )

        path  = os.getenv("MT5_PATH", "").strip() or None
        magic = int(os.getenv("MT5_MAGIC", str(20250101)))
        demo  = (mode != "mt5live")

        connector = MT5Connector(
            login=login, password=password, server=server,
            path=path, magic=magic, demo=demo,
        )
        log.info(
            f"[KAI] BrokerConnector ready — "
            f"MT5 {'DEMO' if demo else 'LIVE'}"
        )
        return connector

    # ── Alpaca ────────────────────────────────────────────────────────────────
    api_key    = os.getenv("ALPACA_API_KEY", "").strip()
    secret_key = os.getenv("ALPACA_SECRET_KEY", "").strip()

    if not api_key or not secret_key:
        raise EnvironmentError(
            "ALPACA_API_KEY and ALPACA_SECRET_KEY must be set in your .env file.\n"
            "Get your paper trading keys at: "
            "https://app.alpaca.markets → Paper Trading → API Keys\n"
            "Copy .env.example to .env and fill in the values."
        )

    # Safety gate: default is paper. Live requires explicit opt-in.
    paper = (mode != "live")

    if not paper:
        confirm = os.getenv("ALPACA_LIVE_CONFIRMED", "").strip().lower()
        if confirm != "yes":
            raise EnvironmentError(
                "TRADING_MODE=live requires ALPACA_LIVE_CONFIRMED=yes in .env. "
                "Set both explicitly to confirm live trading intent. "
                "This is a safety gate — not an error."
            )

    connector = AlpacaConnector(api_key, secret_key, paper=paper)
    log.info(
        f"[KAI] BrokerConnector ready — "
        f"Alpaca {'PAPER' if paper else 'LIVE'}"
    )
    return connector
