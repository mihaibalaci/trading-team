"""
market_study.py — Finn & Pax 6-Month Market Study

Data source: Yahoo Finance (via yfinance)
- Daily bars: 6 months (trend, volatility, regime analysis)
- 15m bars: 60 days (pattern detection + forward return measurement)

Runs Finn's pattern detection on every 15m window, measures what happened
after each pattern, and outputs a full report for Vera and Mira.

Run:  python3 market_study.py
"""

from __future__ import annotations
import os, sys
sys.path.insert(0, os.path.dirname(__file__))

import pandas as pd
import numpy as np
from datetime import datetime
from collections import defaultdict

import yfinance as yf
from patterns import scan_patterns
from indicators import ema_stack, stochastic, atr

# ─── Config ───────────────────────────────────────────────────────

SYMBOLS = [
    "SPY", "QQQ", "TSLA", "NVDA", "AAPL", "AMD", "COIN", "MARA",
    "SMCI", "META", "NFLX", "BA", "PLTR", "ARM", "MSTR", "SNAP",
]

OUT_PATH = os.path.join(os.path.dirname(__file__), "..", "Team", "FINN_6MONTH_MARKET_STUDY.md")


# ─── Data fetch ───────────────────────────────────────────────────

def fetch_daily(symbol: str) -> pd.DataFrame:
    df = yf.download(symbol, period="6mo", interval="1d", progress=False)
    if df.empty:
        return df
    # Flatten multi-level columns if present
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0].lower() for c in df.columns]
    else:
        df.columns = [c.lower() for c in df.columns]
    return df.reset_index(drop=True)


def fetch_15m(symbol: str) -> pd.DataFrame:
    df = yf.download(symbol, period="60d", interval="15m", progress=False)
    if df.empty:
        return df
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0].lower() for c in df.columns]
    else:
        df.columns = [c.lower() for c in df.columns]
    return df.reset_index(drop=True)


# ─── Volatility analysis (daily) ─────────────────────────────────

def analyze_volatility(symbol: str) -> dict | None:
    print(f"  [VOL] {symbol} ...", end=" ", flush=True)
    df = fetch_daily(symbol)
    if len(df) < 20:
        print("skipped")
        return None

    df["range_pct"] = (df["high"] - df["low"]) / df["close"]
    df["return"] = df["close"].pct_change()

    total_ret = float(df["close"].iloc[-1] / df["close"].iloc[0] - 1)
    ann_vol = float(df["return"].std() * np.sqrt(252))

    print(f"{total_ret:+.1%} return, {ann_vol:.1%} vol")
    return {
        "symbol": symbol,
        "days": len(df),
        "total_return": total_ret,
        "avg_daily_range": float(df["range_pct"].mean()),
        "max_daily_range": float(df["range_pct"].max()),
        "daily_vol": ann_vol,
        "start_price": float(df["close"].iloc[0]),
        "end_price": float(df["close"].iloc[-1]),
    }


# ─── Pattern analysis (15m) ──────────────────────────────────────

def analyze_patterns(symbol: str) -> list[dict]:
    print(f"  [PAT] {symbol} ...", end=" ", flush=True)
    df = fetch_15m(symbol)
    if len(df) < 60:
        print(f"insufficient data ({len(df)} bars)")
        return []

    df = ema_stack(df)
    df = stochastic(df)
    df = atr(df)

    results = []
    for i in range(30, len(df) - 16):
        window = df.iloc[max(0, i - 10):i + 1].copy().reset_index(drop=True)
        detected = scan_patterns(window)
        if not detected:
            continue

        close_at = float(df.iloc[i]["close"])
        fwd_1h = float(df.iloc[i + 4]["close"]) if i + 4 < len(df) else None
        fwd_4h = float(df.iloc[i + 16]["close"]) if i + 16 < len(df) else None

        for pat in detected:
            r1h = ((fwd_1h - close_at) / close_at) if fwd_1h else None
            r4h = ((fwd_4h - close_at) / close_at) if fwd_4h else None

            if pat["direction"] == "bearish":
                r1h = -r1h if r1h is not None else None
                r4h = -r4h if r4h is not None else None

            results.append({
                "symbol": symbol,
                "pattern": pat["pattern"],
                "direction": pat["direction"],
                "strength": pat["strength"],
                "return_1h": r1h,
                "return_4h": r4h,
            })

    print(f"{len(results)} patterns")
    return results


# ─── Report ───────────────────────────────────────────────────────

def generate_report(all_patterns: list[dict], vol_stats: list[dict]) -> str:
    lines = [
        "# Finn & Pax — 6-Month Market Study",
        f"**Period:** Oct 2025 – Mar 2026",
        f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"**Symbols:** {len(SYMBOLS)}  |  **Data:** Yahoo Finance",
        f"**Daily bars:** 6 months  |  **15m bars:** 60 days",
        "",
        "---",
        "",
        "## 1. Volatility & Trend Summary (6 months, daily)",
        "",
        "| Symbol | 6M Return | Avg Daily Range | Ann. Vol | Start | End |",
        "|--------|-----------|-----------------|----------|-------|-----|",
    ]

    for v in sorted(vol_stats, key=lambda x: x["daily_vol"], reverse=True):
        lines.append(
            f"| {v['symbol']} | {v['total_return']:+.1%} | "
            f"{v['avg_daily_range']:.2%} | {v['daily_vol']:.1%} | "
            f"${v['start_price']:.2f} | ${v['end_price']:.2f} |"
        )

    if not all_patterns:
        lines += ["", "## 2. Pattern Analysis", "", "No patterns detected.", ""]
        return "\n".join(lines)

    pdf = pd.DataFrame(all_patterns)

    lines += [
        "", "---", "",
        "## 2. Pattern Performance (15m, last 60 days)",
        "",
        f"Total pattern occurrences: **{len(pdf)}**",
        "",
    ]

    # By pattern
    grouped = pdf.groupby("pattern").agg(
        count=("return_1h", "size"),
        win_1h=("return_1h", lambda x: (x.dropna() > 0).mean() if len(x.dropna()) > 0 else 0),
        avg_1h=("return_1h", lambda x: x.dropna().mean() if len(x.dropna()) > 0 else 0),
        win_4h=("return_4h", lambda x: (x.dropna() > 0).mean() if len(x.dropna()) > 0 else 0),
        avg_4h=("return_4h", lambda x: x.dropna().mean() if len(x.dropna()) > 0 else 0),
    ).sort_values("count", ascending=False)

    lines += [
        "| Pattern | Count | Win% 1h | Avg 1h | Win% 4h | Avg 4h |",
        "|---------|-------|---------|--------|---------|--------|",
    ]
    for pat, r in grouped.iterrows():
        lines.append(
            f"| {pat} | {int(r['count'])} | "
            f"{r['win_1h']:.0%} | {r['avg_1h']:+.3%} | "
            f"{r['win_4h']:.0%} | {r['avg_4h']:+.3%} |"
        )

    # Reliable patterns
    reliable = grouped[(grouped["count"] >= 15) & (grouped["win_4h"] >= 0.55)]
    if not reliable.empty:
        lines += ["", "### ✓ Reliable Patterns (≥15 samples, ≥55% win at 4h)", ""]
        for pat, r in reliable.iterrows():
            lines.append(f"- **{pat}**: {r['win_4h']:.0%} win, "
                         f"{r['avg_4h']:+.3%} avg ({int(r['count'])} samples)")

    # Avoid
    avoid = grouped[(grouped["count"] >= 15) & (grouped["win_4h"] < 0.45)]
    if not avoid.empty:
        lines += ["", "### ✗ Patterns to Avoid (<45% win at 4h)", ""]
        for pat, r in avoid.iterrows():
            lines.append(f"- **{pat}**: {r['win_4h']:.0%} win, "
                         f"{r['avg_4h']:+.3%} avg ({int(r['count'])} samples)")

    # By strength
    by_str = pdf.groupby("strength").agg(
        count=("return_4h", "size"),
        win=("return_4h", lambda x: (x.dropna() > 0).mean() if len(x.dropna()) > 0 else 0),
        avg=("return_4h", lambda x: x.dropna().mean() if len(x.dropna()) > 0 else 0),
    )
    lines += [
        "", "### By Pattern Strength", "",
        "| Strength | Count | Win% 4h | Avg 4h |",
        "|----------|-------|---------|--------|",
    ]
    for s, r in by_str.iterrows():
        lines.append(f"| {s} | {int(r['count'])} | {r['win']:.0%} | {r['avg']:+.3%} |")

    # Per symbol
    by_sym = pdf.groupby("symbol").agg(
        patterns=("return_4h", "size"),
        win=("return_4h", lambda x: (x.dropna() > 0).mean() if len(x.dropna()) > 0 else 0),
        avg=("return_4h", lambda x: x.dropna().mean() if len(x.dropna()) > 0 else 0),
    ).sort_values("win", ascending=False)

    lines += [
        "", "---", "",
        "## 3. Per-Symbol Pattern Reliability", "",
        "| Symbol | Patterns | Win% 4h | Avg 4h |",
        "|--------|----------|---------|--------|",
    ]
    for sym, r in by_sym.iterrows():
        lines.append(f"| {sym} | {int(r['patterns'])} | {r['win']:.0%} | {r['avg']:+.3%} |")

    # Recommendations
    lines += [
        "", "---", "",
        "## 4. Recommendations for Vera & Mira", "",
        "*Based on empirical data from the last 60 days of 15m pattern analysis.*", "",
    ]

    if not reliable.empty:
        lines.append("### Patterns to Prioritize")
        lines.append("These patterns showed statistically significant edge:")
        for pat, r in reliable.iterrows():
            lines.append(f"- **{pat}** — {r['win_4h']:.0%} win rate over {int(r['count'])} occurrences")
        lines.append("")

    if not avoid.empty:
        lines.append("### Patterns to Deprioritize or Filter Out")
        lines.append("These patterns underperformed — consider adding extra confluence requirements:")
        for pat, r in avoid.iterrows():
            lines.append(f"- **{pat}** — only {r['win_4h']:.0%} win rate over {int(r['count'])} occurrences")
        lines.append("")

    # Volatility-based recommendations
    high_vol = [v for v in vol_stats if v["daily_vol"] > 0.5]
    low_vol = [v for v in vol_stats if v["daily_vol"] < 0.25]
    if high_vol:
        lines.append("### High-Volatility Stocks (wider stops needed)")
        for v in sorted(high_vol, key=lambda x: x["daily_vol"], reverse=True):
            lines.append(f"- **{v['symbol']}** — {v['daily_vol']:.0%} annualized vol, "
                         f"{v['avg_daily_range']:.2%} avg daily range")
        lines.append("")

    if low_vol:
        lines.append("### Low-Volatility Stocks (tighter setups possible)")
        for v in sorted(low_vol, key=lambda x: x["daily_vol"]):
            lines.append(f"- **{v['symbol']}** — {v['daily_vol']:.0%} annualized vol")
        lines.append("")

    lines += [
        "---",
        "",
        "*Study complete. Vera should review pattern filters. Mira should review "
        "stop distance multipliers for high-vol names.*",
    ]

    return "\n".join(lines)


# ─── Main ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("  FINN & PAX — 6-MONTH MARKET STUDY")
    print("=" * 60)

    print("\n[PHASE 1] Volatility (daily, 6 months)")
    vol_stats = []
    for sym in SYMBOLS:
        v = analyze_volatility(sym)
        if v:
            vol_stats.append(v)

    print(f"\n[PHASE 2] Pattern analysis (15m, 60 days)")
    all_patterns = []
    for sym in SYMBOLS:
        pats = analyze_patterns(sym)
        all_patterns.extend(pats)

    print(f"\n[PHASE 3] Report ({len(all_patterns)} patterns)")
    report = generate_report(all_patterns, vol_stats)

    with open(OUT_PATH, "w") as f:
        f.write(report)

    print(f"\n  ✓ Saved to {OUT_PATH}")
    print("=" * 60)
