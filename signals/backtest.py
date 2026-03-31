"""
backtest.py — Walk-forward backtesting harness for Finn's signal models.
Validates signal quality before live deployment.
Source: Pax Brief 01 (Walk-Forward Validation, PBO, Deflated Sharpe).
"""
from __future__ import annotations

import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class BacktestResult:
    """Standardized backtest output in Finn's format."""
    strategy_name:      str
    instrument:         str
    start_date:         str
    end_date:           str
    total_trades:       int
    winning_trades:     int
    losing_trades:      int
    win_rate:           float
    avg_win_r:          float       # average win in R units
    avg_loss_r:         float       # average loss in R units (positive number)
    profit_factor:      float
    expectancy_r:       float       # expected R per trade
    sharpe_ratio:       float
    calmar_ratio:       float
    max_drawdown_pct:   float
    total_return_r:     float
    avg_r_per_trade:    float
    consecutive_losses_max: int
    is_sample:          str         # 'in-sample' or 'out-of-sample'
    notes:              str = ""

    def passes_quality_gate(self) -> tuple[bool, list[str]]:
        """
        Check against Vera's performance targets (VERA_STRATEGY_MTF_SCALP.md, Section 10).
        Returns (passes, list_of_failures).
        """
        failures = []
        if self.win_rate < 0.40:
            failures.append(f"Win rate {self.win_rate:.1%} < 40% alert level")
        if self.profit_factor < 1.2:
            failures.append(f"Profit factor {self.profit_factor:.2f} < 1.2 alert level")
        if self.sharpe_ratio < 0.5:
            failures.append(f"Sharpe {self.sharpe_ratio:.2f} < 0.5 alert level")
        if self.max_drawdown_pct > 15:
            failures.append(f"Max drawdown {self.max_drawdown_pct:.1f}% > 15%")
        if self.total_trades < 50:  # Mira fix HIGH-03: threshold must match Vera Section 10
            failures.append(f"Sample too small ({self.total_trades} trades — minimum 50 for live)")
        return len(failures) == 0, failures

    def summary(self) -> str:
        passes, failures = self.passes_quality_gate()
        gate_str = "PASS ✓" if passes else f"FAIL ✗ ({len(failures)} issues)"
        lines = [
            f"{'─'*55}",
            f"  BACKTEST RESULT — {self.strategy_name}",
            f"  Instrument: {self.instrument} | {self.is_sample}",
            f"  Period: {self.start_date} → {self.end_date}",
            f"{'─'*55}",
            f"  Total Trades:          {self.total_trades}",
            f"  Win Rate:              {self.win_rate:.1%}",
            f"  Avg Win (R):           {self.avg_win_r:.2f}R",
            f"  Avg Loss (R):          -{self.avg_loss_r:.2f}R",
            f"  Profit Factor:         {self.profit_factor:.2f}",
            f"  Expectancy:            {self.expectancy_r:.3f}R per trade",
            f"  Total Return (R):      {self.total_return_r:.1f}R",
            f"  Sharpe Ratio:          {self.sharpe_ratio:.2f}",
            f"  Calmar Ratio:          {self.calmar_ratio:.2f}",
            f"  Max Drawdown:          {self.max_drawdown_pct:.1f}%",
            f"  Max Consec. Losses:    {self.consecutive_losses_max}",
            f"{'─'*55}",
            f"  Quality Gate:          {gate_str}",
        ]
        if failures:
            for f in failures:
                lines.append(f"    → {f}")
        if self.notes:
            lines.append(f"  Notes: {self.notes}")
        lines.append(f"{'─'*55}")
        return "\n".join(lines)


def _sharpe(returns: np.ndarray, risk_free: float = 0.0,
            trades_per_day: float = 1.0) -> float:
    """
    Per-trade Sharpe ratio, annualised correctly for intraday R-series.
    Mira fix CRIT-03: √252 is wrong for per-trade series — must scale by
    average trades per day. Default=1.0 (daily) keeps backward compatibility.
    Caller should pass actual average trades/day for intraday strategies.
    """
    excess = returns - risk_free
    if excess.std() == 0:
        return 0.0
    return float(np.mean(excess) / np.std(excess) * np.sqrt(252 * trades_per_day))


def _max_drawdown(equity_curve: np.ndarray) -> float:
    """Maximum drawdown as a positive percentage."""
    peak = np.maximum.accumulate(equity_curve)
    dd = (peak - equity_curve) / peak
    return float(dd.max()) * 100


def _calmar(total_return: float, max_dd: float) -> float:
    if max_dd == 0:
        return 0.0
    return total_return / (max_dd / 100)


def run_backtest(
    trades: list[dict],
    strategy_name: str = "MTF-Price-Action-v1",
    instrument: str = "Unknown",
    is_sample: str = "out-of-sample",
    initial_equity: float = 100_000,
) -> BacktestResult:
    """
    Run a backtest from a list of completed trade dicts.

    Each trade dict must contain:
        entry_price:  float
        exit_price:   float
        stop_loss:    float
        direction:    'long' or 'short'
        date_in:      str
        date_out:     str

    R is calculated as: (exit - entry) / (entry - stop) for longs,
                        (entry - exit) / (stop - entry) for shorts.
    """
    if not trades:
        return BacktestResult(
            strategy_name=strategy_name, instrument=instrument,
            start_date="", end_date="", total_trades=0,
            winning_trades=0, losing_trades=0, win_rate=0,
            avg_win_r=0, avg_loss_r=0, profit_factor=0,
            expectancy_r=0, sharpe_ratio=0, calmar_ratio=0,
            max_drawdown_pct=0, total_return_r=0, avg_r_per_trade=0,
            consecutive_losses_max=0, is_sample=is_sample,
            notes="No trades provided."
        )

    r_values = []
    for t in trades:
        risk = abs(t["entry_price"] - t["stop_loss"])
        if risk == 0:
            continue
        if t["direction"] == "long":
            r = (t["exit_price"] - t["entry_price"]) / risk
        else:
            r = (t["entry_price"] - t["exit_price"]) / risk
        r_values.append(r)

    r = np.array(r_values)
    wins  = r[r > 0]
    losses = r[r < 0]

    win_rate    = len(wins) / len(r) if len(r) > 0 else 0
    avg_win     = float(wins.mean())  if len(wins)   > 0 else 0
    avg_loss    = float(abs(losses.mean())) if len(losses) > 0 else 0
    pf          = (sum(wins) / abs(sum(losses))) if sum(losses) != 0 else float("inf")
    expectancy  = float(r.mean())
    total_r     = float(r.sum())
    avg_r       = float(r.mean())

    # Equity curve (in R units, starting at 0)
    equity_curve = np.cumsum(np.insert(r, 0, 0)) + initial_equity / 1000
    # Estimate trades per day for correct annualisation (Mira CRIT-03)
    trades_per_day = max(1.0, len(r) / max(1, (
        pd.to_datetime(trades[-1].get("date_out", "2026-01-01")) -
        pd.to_datetime(trades[0].get("date_in",  "2026-01-01"))
    ).days or 1))
    sharpe  = _sharpe(r, trades_per_day=trades_per_day)
    mdd     = _max_drawdown(equity_curve)
    calmar  = _calmar(total_r, mdd)

    # Consecutive losses
    max_consec = curr_consec = 0
    for rv in r:
        if rv < 0:
            curr_consec += 1
            max_consec = max(max_consec, curr_consec)
        else:
            curr_consec = 0

    return BacktestResult(
        strategy_name=strategy_name,
        instrument=instrument,
        start_date=trades[0].get("date_in", ""),
        end_date=trades[-1].get("date_out", ""),
        total_trades=len(r),
        winning_trades=len(wins),
        losing_trades=len(losses),
        win_rate=win_rate,
        avg_win_r=avg_win,
        avg_loss_r=avg_loss,
        profit_factor=pf,
        expectancy_r=expectancy,
        sharpe_ratio=sharpe,
        calmar_ratio=calmar,
        max_drawdown_pct=mdd,
        total_return_r=total_r,
        avg_r_per_trade=avg_r,
        consecutive_losses_max=max_consec,
        is_sample=is_sample,
    )


def walk_forward_validate(
    signal_fn: Callable,
    data: dict,
    n_splits: int = 5,
    train_pct: float = 0.7,
) -> list[BacktestResult]:
    """
    Walk-Forward Validation harness.
    Splits data into n_splits rolling windows (train/test).
    Runs signal_fn on each test window and collects results.

    Source: Pax Brief 01 — Walk-Forward Validation methodology.
    AlgoXpert protocol: IS → WFA → OOS.

    Parameters
    ----------
    signal_fn   : callable that takes a data slice and returns list of trade dicts
    data        : full dataset dict passed to signal_fn
    n_splits    : number of walk-forward windows
    train_pct   : fraction of each window used for training

    Returns
    -------
    List of BacktestResult for each OOS window.
    """
    results = []

    # Determine split points on df_30m (or df_15m) as the reference series
    ref_df = data.get("df_30m") or data.get("df_15m")
    if ref_df is None:
        raise ValueError("data must contain 'df_30m' or 'df_15m' key")

    n = len(ref_df)
    window = n // n_splits

    for i in range(n_splits):
        split_end   = (i + 1) * window
        split_start = max(0, split_end - window)
        train_end   = split_start + int((split_end - split_start) * train_pct)
        purge_gap   = 50  # bars — prevents indicator leakage (Mira fix HIGH-04)
        oos_start   = min(train_end + purge_gap, split_end)

        oos_slice = {
            k: (v.iloc[oos_start:split_end] if isinstance(v, pd.DataFrame) else v)
            for k, v in data.items()
        }

        try:
            trades = signal_fn(oos_slice)
            result = run_backtest(
                trades=trades,
                strategy_name="MTF-Price-Action-v1 WFA",
                instrument=data.get("instrument", "Unknown"),
                is_sample=f"OOS window {i+1}/{n_splits}",
            )
            results.append(result)
        except Exception as e:
            print(f"[WFA] Window {i+1} failed: {e}")

    return results
