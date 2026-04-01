"""
mt5_connector.py — Kai's MetaTrader 5 broker integration.

Implements the BrokerConnector interface against a running MT5 terminal.
The MetaTrader5 Python library communicates with the MT5 desktop application
over a local named pipe — the terminal must be open and logged in on the
same machine (Windows).  It does NOT work on Linux without Wine + MT5.

Credentials stored in signals/.env:
    TRADING_MODE=mt5
    MT5_LOGIN=12345678          (integer account number)
    MT5_PASSWORD=your_password
    MT5_SERVER=YourBroker-Demo
    MT5_PATH=C:\\Program Files\\MetaTrader 5\\terminal64.exe   (optional)
    MT5_MAGIC=20250101          (optional — EA magic number, default 20250101)

Usage:
    from broker_connector import connect     # factory reads TRADING_MODE
    connector = connect()
    connector.submit_order(remy_order)
    equity = connector.get_account_equity()
"""

from __future__ import annotations

import threading
import time
import logging
from datetime import datetime, timezone
from typing import Callable, Optional

import pandas as pd
import numpy as np

from broker_connector import BrokerConnector, Position, AccountState
from execution import Order, OrderType, OrderSide, OrderStatus

log = logging.getLogger(__name__)

# Default magic number — identifies orders placed by this system in MT5
DEFAULT_MAGIC = 20250101


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _mt5_timeframe(timeframe_minutes: int):
    """Convert minutes → MT5 TIMEFRAME constant."""
    import MetaTrader5 as mt5
    mapping = {
        1:    mt5.TIMEFRAME_M1,
        2:    mt5.TIMEFRAME_M2,
        3:    mt5.TIMEFRAME_M3,
        4:    mt5.TIMEFRAME_M4,
        5:    mt5.TIMEFRAME_M5,
        6:    mt5.TIMEFRAME_M6,
        10:   mt5.TIMEFRAME_M10,
        12:   mt5.TIMEFRAME_M12,
        15:   mt5.TIMEFRAME_M15,
        20:   mt5.TIMEFRAME_M20,
        30:   mt5.TIMEFRAME_M30,
        60:   mt5.TIMEFRAME_H1,
        120:  mt5.TIMEFRAME_H2,
        180:  mt5.TIMEFRAME_H3,
        240:  mt5.TIMEFRAME_H4,
        360:  mt5.TIMEFRAME_H6,
        480:  mt5.TIMEFRAME_H8,
        720:  mt5.TIMEFRAME_H12,
        1440: mt5.TIMEFRAME_D1,
    }
    tf = mapping.get(timeframe_minutes)
    if tf is None:
        raise ValueError(
            f"MT5 does not support a {timeframe_minutes}-minute timeframe. "
            f"Supported: {sorted(mapping.keys())}"
        )
    return tf


def _to_lots(symbol_info, quantity: float) -> float:
    """
    Convert a share/unit quantity to lots, rounded to the symbol's
    minimum lot step.  For stock CFDs where contract_size=1, lots == shares.
    """
    contract_size = symbol_info.trade_contract_size or 1.0
    raw_lots      = quantity / contract_size
    step          = symbol_info.volume_step or 0.01
    lots          = round(round(raw_lots / step) * step, 8)
    lots          = max(lots, symbol_info.volume_min or 0.01)
    return lots


def _map_order_status(mt5_state: int) -> OrderStatus:
    """Map MT5 ORDER_STATE_* integer → Remy's OrderStatus."""
    import MetaTrader5 as mt5
    return {
        mt5.ORDER_STATE_STARTED:         OrderStatus.PENDING,
        mt5.ORDER_STATE_PLACED:          OrderStatus.WORKING,
        mt5.ORDER_STATE_CANCELED:        OrderStatus.CANCELLED,
        mt5.ORDER_STATE_PARTIAL:         OrderStatus.PARTIAL,
        mt5.ORDER_STATE_FILLED:          OrderStatus.FILLED,
        mt5.ORDER_STATE_REJECTED:        OrderStatus.REJECTED,
        mt5.ORDER_STATE_EXPIRED:         OrderStatus.EXPIRED,
        mt5.ORDER_STATE_REQUEST_ADD:     OrderStatus.PENDING,
        mt5.ORDER_STATE_REQUEST_MODIFY:  OrderStatus.WORKING,
        mt5.ORDER_STATE_REQUEST_CANCEL:  OrderStatus.WORKING,
    }.get(mt5_state, OrderStatus.PENDING)


# ─────────────────────────────────────────────────────────────────────────────
# MT5 connector
# ─────────────────────────────────────────────────────────────────────────────

class MT5Connector(BrokerConnector):
    """
    MetaTrader 5 implementation of BrokerConnector.

    Remy and Cole call this exactly the same way as the Alpaca connector —
    the MT5 terminal details are completely hidden from the execution layer.

    Demo vs. live is determined by the account type on the MT5 server side.
    Pass `demo=True` (default) to log the mode correctly; there is no separate
    endpoint distinction in MT5 — the server URL encodes the account type.
    """

    HALT_AFTER_SECONDS = 30

    def __init__(
        self,
        login:    int,
        password: str,
        server:   str,
        path:     str | None = None,
        magic:    int = DEFAULT_MAGIC,
        demo:     bool = True,
    ):
        import MetaTrader5 as mt5

        self._mt5      = mt5
        self._magic    = magic
        self._mode     = "demo" if demo else "live"
        self._last_seen = time.time()
        self._halted   = False

        # order_id (str) → MT5 ticket (int)
        self._submitted: dict[str, int] = {}

        kwargs: dict = dict(login=login, password=password, server=server)
        if path:
            kwargs["path"] = path

        if not mt5.initialize(**kwargs):
            code, msg = mt5.last_error()
            raise ConnectionError(
                f"MT5 initialize() failed — error {code}: {msg}. "
                "Check that MT5 terminal is open and the credentials are correct."
            )

        info = mt5.account_info()
        if info is None:
            mt5.shutdown()
            raise ConnectionError("MT5 connected but account_info() returned None.")

        log.info(
            f"[KAI] MT5Connector initialised — "
            f"account {info.login} on {info.server} — "
            f"mode: {self._mode.upper()} — "
            f"equity: ${info.equity:,.2f}"
        )

    # ── Order submission ──────────────────────────────────────────────────────

    def submit_order(self, order: Order) -> str:
        self._check_not_halted()
        mt5 = self._mt5

        # Idempotency — check comment field in pending orders first
        if order.order_id in self._submitted:
            ticket = self._submitted[order.order_id]
            log.info(
                f"[KAI] Order {order.order_id} already submitted — "
                f"MT5 ticket: {ticket}"
            )
            return str(ticket)

        sym_info = mt5.symbol_info(order.instrument)
        if sym_info is None:
            raise ValueError(
                f"Symbol '{order.instrument}' not found in MT5. "
                "Check the symbol name — MT5 brokers sometimes add suffixes "
                "like '#AAPL' or 'AAPLm'. "
                "Set MT5_SYMBOL_PREFIX or MT5_SYMBOL_SUFFIX in .env if needed."
            )

        if not sym_info.visible:
            mt5.symbol_select(order.instrument, True)

        volume = _to_lots(sym_info, order.quantity)
        tick   = mt5.symbol_info_tick(order.instrument)
        if tick is None:
            raise ConnectionError(
                f"Cannot get tick for {order.instrument}. "
                "Market may be closed or symbol not available."
            )

        is_buy = (order.side == OrderSide.BUY)
        price  = tick.ask if is_buy else tick.bid

        if order.order_type == OrderType.MARKET:
            request = {
                "action":      mt5.TRADE_ACTION_DEAL,
                "symbol":      order.instrument,
                "volume":      volume,
                "type":        mt5.ORDER_TYPE_BUY if is_buy else mt5.ORDER_TYPE_SELL,
                "price":       price,
                "deviation":   20,
                "magic":       self._magic,
                "comment":     order.order_id[:31],   # MT5 comment max 31 chars
                "type_time":   mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }

        elif order.order_type == OrderType.LIMIT:
            limit_type = (mt5.ORDER_TYPE_BUY_LIMIT if is_buy
                          else mt5.ORDER_TYPE_SELL_LIMIT)
            request = {
                "action":      mt5.TRADE_ACTION_PENDING,
                "symbol":      order.instrument,
                "volume":      volume,
                "type":        limit_type,
                "price":       order.limit_price,
                "deviation":   20,
                "magic":       self._magic,
                "comment":     order.order_id[:31],
                "type_time":   mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_RETURN,
            }

        elif order.order_type == OrderType.STOP_MARKET:
            stop_type = (mt5.ORDER_TYPE_BUY_STOP if is_buy
                         else mt5.ORDER_TYPE_SELL_STOP)
            request = {
                "action":      mt5.TRADE_ACTION_PENDING,
                "symbol":      order.instrument,
                "volume":      volume,
                "type":        stop_type,
                "price":       order.stop_trigger,
                "deviation":   20,
                "magic":       self._magic,
                "comment":     order.order_id[:31],
                "type_time":   mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_RETURN,
            }

        elif order.order_type == OrderType.STOP_LIMIT:
            stop_limit_type = (mt5.ORDER_TYPE_BUY_STOP_LIMIT if is_buy
                               else mt5.ORDER_TYPE_SELL_STOP_LIMIT)
            request = {
                "action":      mt5.TRADE_ACTION_PENDING,
                "symbol":      order.instrument,
                "volume":      volume,
                "type":        stop_limit_type,
                "price":       order.stop_trigger,    # trigger price
                "stoplimit":   order.limit_price,     # limit price after trigger
                "deviation":   20,
                "magic":       self._magic,
                "comment":     order.order_id[:31],
                "type_time":   mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_RETURN,
            }

        else:
            raise ValueError(f"Unsupported order type: {order.order_type}")

        result = mt5.order_send(request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            code = result.retcode if result else -1
            msg  = result.comment if result else mt5.last_error()
            raise ValueError(
                f"MT5 order_send rejected — retcode {code}: {msg}. "
                f"Order: {order.order_type.value} {order.side.value} "
                f"{order.quantity} {order.instrument}"
            )

        ticket = result.order
        self._submitted[order.order_id] = ticket
        self._last_seen = time.time()

        log.info(
            f"[KAI] Submitted {order.order_type.value.upper()} "
            f"{order.side.value.upper()} {order.quantity} {order.instrument} "
            f"→ MT5 ticket {ticket}"
        )
        return str(ticket)

    # ── Order management ──────────────────────────────────────────────────────

    def cancel_order(self, broker_order_id: str) -> bool:
        mt5 = self._mt5
        ticket = int(broker_order_id)

        request = {
            "action": mt5.TRADE_ACTION_REMOVE,
            "order":  ticket,
        }
        result = mt5.order_send(request)
        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            log.info(f"[KAI] Cancelled MT5 order ticket {ticket}")
            return True

        # Already filled or already cancelled — not an error for us
        code = result.retcode if result else -1
        log.warning(f"[KAI] Cancel ticket {ticket} — retcode {code}")
        return False

    def get_order_status(self, broker_order_id: str) -> OrderStatus:
        status, _, _ = self.get_order_fill(broker_order_id)
        return status

    def get_order_fill(
        self, broker_order_id: str
    ) -> tuple[OrderStatus, float | None, float | None]:
        mt5    = self._mt5
        ticket = int(broker_order_id)

        # 1. Check active pending orders
        pending = mt5.orders_get(ticket=ticket)
        if pending:
            o = pending[0]
            self._last_seen = time.time()
            return _map_order_status(o.state), None, None

        # 2. Check historical orders (filled / cancelled / expired)
        hist = mt5.history_orders_get(ticket=ticket)
        if hist:
            o = hist[0]
            self._last_seen = time.time()
            status = _map_order_status(o.state)
            # Retrieve deal(s) for this order to get fill price
            deals = mt5.history_deals_get(order=ticket)
            if deals:
                d          = deals[0]
                fill_price = float(d.price) if d.price else None
                fill_qty   = float(d.volume) if d.volume else None
                return status, fill_price, fill_qty
            return status, None, None

        raise ConnectionError(
            f"MT5 ticket {ticket} not found in pending orders or history. "
            "It may have been placed outside this session."
        )

    # ── Account and position queries ──────────────────────────────────────────

    def get_position(self, instrument: str) -> Optional[Position]:
        mt5 = self._mt5
        positions = mt5.positions_get(symbol=instrument)
        if not positions:
            return None

        # Aggregate all tickets for this symbol (unlikely but possible)
        total_qty   = sum(
            p.volume if p.type == mt5.POSITION_TYPE_BUY else -p.volume
            for p in positions
        )
        avg_price   = float(positions[0].price_open)
        market_val  = sum(float(p.price_current) * float(p.volume) for p in positions)
        unrealized  = sum(float(p.profit) for p in positions)

        self._last_seen = time.time()
        return Position(
            instrument    = instrument,
            qty           = total_qty,
            avg_entry     = avg_price,
            market_value  = market_val,
            unrealized_pnl= unrealized,
        )

    def get_account_equity(self) -> float:
        return self.get_account_state().equity

    def get_account_state(self) -> AccountState:
        mt5  = self._mt5
        info = mt5.account_info()
        if info is None:
            code, msg = mt5.last_error()
            raise ConnectionError(f"MT5 account_info() failed — {code}: {msg}")
        self._last_seen = time.time()
        return AccountState(
            equity          = float(info.equity),
            cash            = float(info.balance),
            buying_power    = float(info.margin_free),
            portfolio_value = float(info.equity),
            trading_mode    = self._mode,
        )

    # ── Market data ───────────────────────────────────────────────────────────

    def get_latest_price(self, instrument: str) -> float:
        mt5  = self._mt5
        tick = mt5.symbol_info_tick(instrument)
        if tick is None:
            raise ConnectionError(
                f"MT5 symbol_info_tick({instrument}) returned None. "
                "Market may be closed or symbol not found."
            )
        self._last_seen = time.time()
        mid = (tick.bid + tick.ask) / 2
        return float(mid)

    # ── Historical bars (used by strategy_validator) ──────────────────────────

    def fetch_bars_hist(
        self,
        symbol:            str,
        timeframe_minutes: int,
        count:             int,
        days_back:         int = 30,
    ) -> pd.DataFrame:
        """
        Fetch historical OHLCV bars from MT5.
        Returns a DataFrame with columns: open, high, low, close, volume.
        Used by Clio's strategy validator at startup.
        """
        import MetaTrader5 as mt5
        from datetime import timedelta

        tf    = _mt5_timeframe(timeframe_minutes)
        end   = datetime.now(timezone.utc) - __import__("datetime").timedelta(days=30)
        start = end - __import__("datetime").timedelta(days=days_back)

        rates = mt5.copy_rates_range(symbol, tf, start, end)
        if rates is None or len(rates) == 0:
            return pd.DataFrame()

        df = pd.DataFrame(rates)
        df = df.rename(columns={
            "open":   "open",
            "high":   "high",
            "low":    "low",
            "close":  "close",
            "tick_volume": "volume",
        })
        df = df[["open", "high", "low", "close", "volume"]].tail(count)
        df.index = pd.RangeIndex(len(df))
        return df

    # ── Streaming (polling-based for MT5) ─────────────────────────────────────

    def subscribe_fills(self, callback: Callable[[dict], None]) -> None:
        """
        Poll for position changes every 2 seconds and fire the callback
        when a tracked order transitions to FILLED.  MT5 has no push API
        for fills from Python — polling is the standard approach.
        """
        def _poll():
            known_filled: set[int] = set()
            while True:
                for order_id, ticket in list(self._submitted.items()):
                    if ticket in known_filled:
                        continue
                    try:
                        status, fill_price, fill_qty = self.get_order_fill(
                            str(ticket)
                        )
                        if status == OrderStatus.FILLED and fill_price is not None:
                            known_filled.add(ticket)
                            callback({
                                "broker_order_id": str(ticket),
                                "fill_price":  fill_price,
                                "fill_qty":    fill_qty or 0.0,
                                "timestamp":   datetime.now(timezone.utc),
                                "event":       "fill",
                            })
                    except Exception:
                        pass
                time.sleep(2)

        t = threading.Thread(target=_poll, daemon=True, name="mt5-fill-poller")
        t.start()
        log.info("[KAI] MT5 fill poller started (2-second polling)")

    def subscribe_prices(
        self,
        instruments: list[str],
        callback: Callable[[str, float, datetime], None],
    ) -> None:
        """
        Poll MT5 ticks every second for each requested symbol and fire
        the callback on each update.
        """
        def _poll():
            while True:
                for sym in instruments:
                    try:
                        price = self.get_latest_price(sym)
                        callback(sym, price, datetime.now(timezone.utc))
                    except Exception:
                        pass
                time.sleep(1)

        t = threading.Thread(
            target=_poll, daemon=True, name="mt5-price-poller"
        )
        t.start()
        log.info(f"[KAI] MT5 price poller started for: {instruments}")

    # ── Health check ──────────────────────────────────────────────────────────

    def health_check(self) -> tuple[bool, str]:
        mt5  = self._mt5
        info = mt5.account_info()
        if info is not None:
            self._last_seen = time.time()
            self._halted    = False
            return True, (
                f"MT5 {self._mode} OK — "
                f"account {info.login} — equity ${info.equity:,.2f}"
            )

        age = time.time() - self._last_seen
        code, msg = mt5.last_error()
        if age > self.HALT_AFTER_SECONDS:
            self._halted = True
            log.critical(
                f"[KAI] MT5 unreachable for {age:.0f}s — "
                f"new order submissions HALTED"
            )
        return False, f"MT5 unreachable — error {code}: {msg}"

    def shutdown(self) -> None:
        """Cleanly disconnect from the MT5 terminal."""
        self._mt5.shutdown()
        log.info("[KAI] MT5 connection closed.")

    # ── Private helpers ───────────────────────────────────────────────────────

    def _check_not_halted(self) -> None:
        if self._halted:
            raise ConnectionError(
                "Kai's order submission is HALTED — MT5 terminal has been "
                f"unreachable for >{self.HALT_AFTER_SECONDS}s. "
                "Restore the MT5 connection and call health_check() to resume."
            )
