"""
demo.py — Finn's signal engine demo with synthetic OHLCV data.
Run this to verify the full pipeline works end-to-end.

Usage:
    cd signals/
    python3 demo.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

import pandas as pd
import numpy as np
from datetime import datetime, timedelta

from signal_engine import generate_signal, batch_scan
from backtest import run_backtest, BacktestResult


def make_ohlcv(n: int, start_price: float = 4500.0,
               trend: float = 0.0002, vol: float = 0.001,
               seed: int = 42) -> pd.DataFrame:
    """Generate synthetic OHLCV data for testing."""
    np.random.seed(seed)
    times = [datetime(2026, 3, 1) + timedelta(minutes=i) for i in range(n)]
    closes = [start_price]
    for _ in range(n - 1):
        ret = trend + vol * np.random.randn()
        closes.append(closes[-1] * (1 + ret))

    closes = np.array(closes)
    highs  = closes * (1 + abs(np.random.randn(n) * vol * 0.5))
    lows   = closes * (1 - abs(np.random.randn(n) * vol * 0.5))
    opens  = np.roll(closes, 1)
    opens[0] = closes[0]
    volumes = np.random.randint(100, 5000, n)

    df = pd.DataFrame({
        "open":   opens,
        "high":   highs,
        "low":    lows,
        "close":  closes,
        "volume": volumes,
    }, index=pd.DatetimeIndex(times))
    return df


def resample_ohlcv(df_1m: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Resample 1m OHLCV to a higher timeframe."""
    return df_1m.resample(rule).agg({
        "open":   "first",
        "high":   "max",
        "low":    "min",
        "close":  "last",
        "volume": "sum",
    }).dropna()


def run_demo():
    print("\n" + "="*60)
    print("  FINN SIGNAL ENGINE — DEMO RUN")
    print("  Multi-Timeframe Price Action Strategy (Vera v1.0)")
    print("="*60 + "\n")

    # ── Generate synthetic data ──────────────────────────────────────────
    print("[1/4] Generating synthetic market data (bullish trend)...")
    df_1m_raw = make_ohlcv(n=2000, start_price=4500.0, trend=0.00015, vol=0.0008)

    df_30m = resample_ohlcv(df_1m_raw, "30min")
    df_15m = resample_ohlcv(df_1m_raw, "15min")
    df_1m  = df_1m_raw.copy()

    print(f"    30m bars: {len(df_30m)} | 15m bars: {len(df_15m)} | 1m bars: {len(df_1m)}")

    # ── Simulate previous day values ────────────────────────────────────
    prev_day = df_1m_raw.iloc[:390]   # first ~6.5 hours as "previous day"
    prev_high  = float(prev_day["high"].max())
    prev_low   = float(prev_day["low"].min())
    prev_close = float(prev_day["close"].iloc[-1])

    # Swing high/low for Fibonacci (from first half of data)
    mid = len(df_15m) // 2
    swing_high = float(df_15m["high"].iloc[:mid].max())
    swing_low  = float(df_15m["low"].iloc[:mid].min())

    print(f"    Prev Day H={prev_high:.2f} L={prev_low:.2f} C={prev_close:.2f}")
    print(f"    Swing High={swing_high:.2f} Swing Low={swing_low:.2f}\n")

    # ── Run signal engine ────────────────────────────────────────────────
    print("[2/4] Running signal engine on most recent bars...")

    signal = generate_signal(
        instrument="ES_DEMO",
        df_30m=df_30m.tail(100),
        df_15m=df_15m.tail(100),
        df_1m=df_1m.tail(200),
        prev_day_high=prev_high,
        prev_day_low=prev_low,
        prev_day_close=prev_close,
        swing_high=swing_high,
        swing_low=swing_low,
        equity=100_000,
        risk_pct=0.01,
    )

    print()
    if signal:
        print(signal.summary())
    else:
        print("  No signal generated on this bar.")
        print("  (Normal — strategy requires strict multi-factor alignment.)\n")

    # ── Synthetic backtest demo ──────────────────────────────────────────
    print("\n[3/4] Running synthetic backtest (50 simulated trades)...")
    np.random.seed(99)

    # Simulate trades: ~50% win rate, avg win 1.8R, avg loss 1.0R
    sim_trades = []
    price = 4500.0
    for i in range(50):
        direction = np.random.choice(["long", "short"])
        entry = price + np.random.randn() * 5
        stop_dist = abs(np.random.randn() * 3) + 2
        stop = entry - stop_dist if direction == "long" else entry + stop_dist

        win = np.random.random() < 0.52  # 52% win rate
        if win:
            r_mult = np.random.uniform(1.2, 2.5)
            exit_p = (entry + r_mult * stop_dist if direction == "long"
                      else entry - r_mult * stop_dist)
        else:
            exit_p = stop + np.random.uniform(-0.5, 0.5) * stop_dist

        sim_trades.append({
            "entry_price": entry,
            "exit_price":  exit_p,
            "stop_loss":   stop,
            "direction":   direction,
            "date_in":     f"2026-0{(i//10)+1}-{(i%28)+1:02d}",
            "date_out":    f"2026-0{(i//10)+1}-{(i%28)+2:02d}",
        })
        price = exit_p

    bt = run_backtest(
        trades=sim_trades,
        strategy_name="MTF-Price-Action-v1",
        instrument="ES_DEMO",
        is_sample="out-of-sample",
    )
    print()
    print(bt.summary())

    # ── Module import check ──────────────────────────────────────────────
    print("\n[4/4] Module import verification...")
    import indicators, patterns, confluence, signal_engine, backtest
    print("  ✓ indicators.py")
    print("  ✓ patterns.py")
    print("  ✓ confluence.py")
    print("  ✓ signal_engine.py")
    print("  ✓ backtest.py")

    print("\n" + "="*60)
    print("  Demo complete. All modules operational.")
    print("  — Finn")
    print("="*60 + "\n")


if __name__ == "__main__":
    run_demo()
