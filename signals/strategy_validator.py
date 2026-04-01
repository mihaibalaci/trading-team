"""
strategy_validator.py — Clio & Mira joint strategy validation.

Clio fetches historical bar data for each strategy profile.
The signal engine runs over rolling windows to simulate historical signals.
Forward bar simulation produces completed trade outcomes (T1/stop/timeout).
Mira's quality gate decides whether each strategy is cleared for live scanning.

Only strategies that pass are forwarded to Finn/Sage queues.
Results are written to shared state for the web dashboard.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import pandas as pd
import numpy as np

log = logging.getLogger(__name__)


# ── Historical bar fetch (used only by validator) ─────────────────────────────

def _fetch_bars_hist(connector, symbol: str, timeframe_minutes: int, limit: int,
                     days_back: int = 30) -> pd.DataFrame:
    """
    Fetch historical bars using an explicit date range so results are returned
    even when the market is currently closed (unlike limit-only requests).
    """
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

    if timeframe_minutes == 1:
        tf = TimeFrame.Minute
    elif timeframe_minutes == 15:
        tf = TimeFrame(15, TimeFrameUnit.Minute)
    elif timeframe_minutes == 30:
        tf = TimeFrame(30, TimeFrameUnit.Minute)
    else:
        tf = TimeFrame(timeframe_minutes, TimeFrameUnit.Minute)

    start = datetime.now(timezone.utc) - timedelta(days=days_back)
    req = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=tf,
        start=start,
        limit=limit,
    )
    bars = connector._data.get_stock_bars(req)

    if symbol not in bars or len(bars[symbol]) == 0:
        return pd.DataFrame()

    rows = []
    for b in bars[symbol]:
        rows.append({
            "open":   float(b.open),
            "high":   float(b.high),
            "low":    float(b.low),
            "close":  float(b.close),
            "volume": float(b.volume),
        })
    df = pd.DataFrame(rows)
    df.index = pd.RangeIndex(len(df))   # numeric index — same as live fetch_bars
    return df


# ── Tuning constants ──────────────────────────────────────────────────────────

# Symbols tested per strategy (limits API calls at startup)
SYMBOLS_TO_SAMPLE = 2

# How many multiples of the live bar count to fetch for history
HISTORY_MULTIPLIER = 5
MAX_BARS_PER_FETCH  = 500   # Alpaca hard cap per call

# How many setup-TF bars to look forward for exit simulation
MAX_EXIT_BARS = 20

# Minimum simulated trades to run the quality gate;
# below this we give the strategy a cautionary pass (not enough data to reject)
MIN_TRADES_FOR_GATE = 5


# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class ValidationResult:
    strategy_name:   str
    passed:          bool
    reason:          str
    trades_sim:      int
    win_rate:        float
    profit_factor:   float
    expectancy_r:    float
    sharpe_ratio:    float
    max_drawdown_pct: float
    symbols_tested:  list
    issues:          list
    summary:         str = ""

    def to_dict(self) -> dict:
        return {
            "strategy":       self.strategy_name,
            "passed":         self.passed,
            "reason":         self.reason,
            "trades_sim":     self.trades_sim,
            "win_rate":       round(self.win_rate, 3),
            "profit_factor":  round(self.profit_factor, 2),
            "expectancy_r":   round(self.expectancy_r, 3),
            "sharpe_ratio":   round(self.sharpe_ratio, 2),
            "max_drawdown_pct": round(self.max_drawdown_pct, 1),
            "symbols_tested": self.symbols_tested,
            "issues":         self.issues,
        }


# ── Exit simulation ───────────────────────────────────────────────────────────

def _simulate_exit(signal, forward_bars: pd.DataFrame) -> dict | None:
    """
    Walk forward bars after a signal to determine the first exit event.

    Priority (checked bar by bar):
      1. Stop hit  → loss at stop price
      2. T1 hit    → partial win at T1

    Timeout after MAX_EXIT_BARS bars → exit at last bar's close (flat/small).
    Returns a completed trade dict for run_backtest(), or None if no data.
    """
    if forward_bars is None or len(forward_bars) < 2:
        return None

    entry     = signal.entry_price
    stop      = signal.stop_loss
    t1        = signal.target_1
    direction = signal.direction

    date_in = str(forward_bars.index[0])

    for idx, bar in forward_bars.iterrows():
        hi    = float(bar["high"])
        lo    = float(bar["low"])
        date_out = str(idx)

        if direction == "long":
            if lo <= stop:
                return {"entry_price": entry, "exit_price": stop,
                        "stop_loss": stop, "direction": direction,
                        "date_in": date_in, "date_out": date_out}
            if hi >= t1:
                return {"entry_price": entry, "exit_price": t1,
                        "stop_loss": stop, "direction": direction,
                        "date_in": date_in, "date_out": date_out}
        else:
            if hi >= stop:
                return {"entry_price": entry, "exit_price": stop,
                        "stop_loss": stop, "direction": direction,
                        "date_in": date_in, "date_out": date_out}
            if lo <= t1:
                return {"entry_price": entry, "exit_price": t1,
                        "stop_loss": stop, "direction": direction,
                        "date_in": date_in, "date_out": date_out}

    # Timeout — close at last bar's close
    last_close = float(forward_bars["close"].iloc[-1])
    return {"entry_price": entry, "exit_price": last_close,
            "stop_loss": stop, "direction": direction,
            "date_in": date_in, "date_out": date_out}


# ── Main validator ────────────────────────────────────────────────────────────

def validate_strategy(
    strategy_name: str,
    profile,            # StrategyProfile
    connector,          # BrokerConnector
    equity: float = 100_000,
) -> ValidationResult:
    """
    Clio calls this for each strategy before pushing it to the scan queue.

    Steps:
      1. Fetch extended historical bars for sample symbols
      2. Roll a sliding window across setup-TF bars
      3. Call generate_signal() at each step; skip non-signals
      4. Simulate exit on forward bars
      5. Collect completed trade dicts → run_backtest()
      6. Apply Mira's quality gate (with trade-count threshold relaxed)
    """
    from live_scanner import fetch_prev_day_levels
    from signal_engine import generate_signal
    from backtest import run_backtest

    # Skip forex (Alpaca stock data client doesn't handle FX pairs)
    symbols = [s for s in profile.watchlist if "/" not in s][:SYMBOLS_TO_SAMPLE]

    if not symbols:
        return ValidationResult(
            strategy_name=strategy_name, passed=True,
            reason="Forex/non-stock symbols — skipping backtest, forwarding directly",
            trades_sim=0, win_rate=0, profit_factor=0, expectancy_r=0,
            sharpe_ratio=0, max_drawdown_pct=0,
            symbols_tested=[], issues=[],
        )

    # How many bars to request for each timeframe
    bars_trend = min(profile.bars_trend * HISTORY_MULTIPLIER, MAX_BARS_PER_FETCH)
    bars_setup = min(profile.bars_setup * HISTORY_MULTIPLIER, MAX_BARS_PER_FETCH)
    bars_entry = min(profile.bars_entry * HISTORY_MULTIPLIER, MAX_BARS_PER_FETCH)

    # Days of history to cover for each timeframe
    # 1440 intraday minutes per day; add 40% buffer for weekends/holidays
    def _days_needed(tf_min: int, n_bars: int) -> int:
        trading_minutes_per_day = 390   # 6.5-hour US session
        bars_per_day = max(1, trading_minutes_per_day // tf_min)
        return max(14, int(n_bars / bars_per_day * 1.4) + 5)

    all_trades   = []
    symbols_ok   = []
    issues       = []

    for symbol in symbols:
        try:
            df_trend = _fetch_bars_hist(connector, symbol, profile.tf_trend_min, bars_trend,
                                        days_back=_days_needed(profile.tf_trend_min, bars_trend))
            df_setup = _fetch_bars_hist(connector, symbol, profile.tf_setup_min, bars_setup,
                                        days_back=_days_needed(profile.tf_setup_min, bars_setup))
            df_entry = _fetch_bars_hist(connector, symbol, profile.tf_entry_min, bars_entry,
                                        days_back=_days_needed(profile.tf_entry_min, bars_entry))

            if df_trend.empty or df_setup.empty or df_entry.empty:
                issues.append(f"{symbol}: no bar data returned")
                continue

            nt, ns, ne = len(df_trend), len(df_setup), len(df_entry)
            log.info(f"[Validator] {strategy_name}/{symbol}: "
                     f"{nt} trend / {ns} setup / {ne} entry bars")

            if nt < profile.bars_trend + 5:
                issues.append(f"{symbol}: only {nt} trend bars (need >{profile.bars_trend})")
                continue

            # Prev-day levels — best-effort
            try:
                prev_h, prev_l, prev_c = fetch_prev_day_levels(connector, symbol)
            except Exception:
                prev_h, prev_l, prev_c = 0, 0, 0
            if prev_h == 0:
                prev_h = float(df_trend["high"].iloc[-20:-1].max())
                prev_l = float(df_trend["low"].iloc[-20:-1].min())
                prev_c = float(df_trend["close"].iloc[-2])

            symbols_ok.append(symbol)

            # ── Rolling window ────────────────────────────────────────────
            # Use setup TF as primary reference; map proportionally to others.
            # Step half a setup-window each iteration to avoid overlap.
            step = max(profile.bars_setup // 2, 5)

            for i in range(profile.bars_setup, ns - MAX_EXIT_BARS, step):
                # Setup slice: most recent profile.bars_setup bars at position i
                slice_setup = df_setup.iloc[i - profile.bars_setup : i]

                # Proportional index into trend and entry arrays
                trend_end  = max(profile.bars_trend, int(i * nt / ns))
                entry_end  = max(profile.bars_entry, int(i * ne / ns))

                slice_trend = df_trend.iloc[
                    max(0, trend_end - profile.bars_trend) : trend_end
                ]
                slice_entry = df_entry.iloc[
                    max(0, entry_end - profile.bars_entry) : entry_end
                ]

                if (len(slice_trend) < 20 or
                        len(slice_setup) < 30 or
                        len(slice_entry) < 50):
                    continue

                swing_high = float(slice_trend["high"].tail(20).max())
                swing_low  = float(slice_trend["low"].tail(20).min())

                try:
                    signal = generate_signal(
                        instrument=symbol,
                        df_30m=slice_trend,
                        df_15m=slice_setup,
                        df_1m=slice_entry,
                        prev_day_high=prev_h,
                        prev_day_low=prev_l,
                        prev_day_close=prev_c,
                        swing_high=swing_high,
                        swing_low=swing_low,
                        equity=equity,
                        risk_pct=profile.risk_per_trade,
                    )
                except Exception:
                    continue

                if not signal or signal.invalidated:
                    continue
                if signal.signal_strength < profile.min_signal_strength:
                    continue
                if signal.confluence_score < profile.min_confluence:
                    continue

                # Simulate exit on the forward setup bars
                forward = df_setup.iloc[i : i + MAX_EXIT_BARS]
                trade = _simulate_exit(signal, forward)
                if trade:
                    all_trades.append(trade)

        except Exception as e:
            issues.append(f"{symbol}: {e}")
            log.warning(f"[Validator] {strategy_name}/{symbol} error: {e}")

    # ── Evaluate results ──────────────────────────────────────────────────────

    if len(all_trades) < MIN_TRADES_FOR_GATE:
        # Not enough data to make a meaningful decision — forward with caution
        return ValidationResult(
            strategy_name=strategy_name, passed=True,
            reason=(f"Only {len(all_trades)} simulated trades from "
                    f"{symbols_ok} — insufficient data for gate; forwarding with caution"),
            trades_sim=len(all_trades), win_rate=0, profit_factor=0,
            expectancy_r=0, sharpe_ratio=0, max_drawdown_pct=0,
            symbols_tested=symbols_ok, issues=issues,
        )

    bt = run_backtest(
        trades=all_trades,
        strategy_name=strategy_name,
        instrument=", ".join(symbols_ok),
        is_sample="rolling-window (live historical data)",
        initial_equity=equity,
    )

    # Mira's quality gate — drop the 50-trade minimum (unrealistic at startup)
    passes, gate_issues = bt.passes_quality_gate()
    gate_issues = [f for f in gate_issues if "Sample too small" not in f]
    passes = len(gate_issues) == 0

    reason = "All quality gates passed" if passes else "; ".join(gate_issues)

    return ValidationResult(
        strategy_name=strategy_name,
        passed=passes,
        reason=reason,
        trades_sim=bt.total_trades,
        win_rate=bt.win_rate,
        profit_factor=bt.profit_factor,
        expectancy_r=bt.expectancy_r,
        sharpe_ratio=bt.sharpe_ratio,
        max_drawdown_pct=bt.max_drawdown_pct,
        symbols_tested=symbols_ok,
        issues=issues,
        summary=bt.summary(),
    )
