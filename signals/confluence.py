"""
confluence.py — Confluence scoring engine for Finn's signal models.
Implements Vera's 5-factor confluence requirement (minimum 3 required).
Source: VERA_STRATEGY_MTF_SCALP.md — Section 4.1
"""
from __future__ import annotations

import pandas as pd
from indicators import (
    pivot_points, nearest_pivot_level,
    fibonacci_levels, nearest_fib_level,
    stoch_zone,
)


def score_confluence(
    price: float,
    direction: str,                  # 'bullish' or 'bearish'
    stoch_k_15m: float,
    df_15m: pd.DataFrame,            # needs ema21
    pivots: dict,                    # from pivot_points()
    fib_levels: dict | None = None,  # from fibonacci_levels(), optional
    prev_day_high: float | None = None,
    prev_day_low: float | None = None,
    tolerance_pct: float = 0.002,
) -> dict:
    """
    Score the 5 confluence factors from Vera's strategy.
    Returns a dict with factor results and total score.

    Factors (each scores 1 point):
      1. Price at a marked 30m key level (pivot level)
      2. Stochastic in presignal zone (≤25 for long, ≥75 for short)
      3. Price at or near 15m 21 EMA
      4. Price at a Fibonacci level (38.2%, 50%, or 61.8%)
      5. Price at daily S/R level (PDH/PDL or round number)

    Vera's rule: minimum 3 factors = valid setup.
    """
    factors = {}

    # ── Factor 1: Pivot Level ────────────────────────────────────────────
    level_name, level_val = nearest_pivot_level(price, pivots, tolerance_pct)
    factors["pivot_level"] = {
        "hit": level_name is not None,
        "detail": f"Near {level_name} ({level_val:.4f})" if level_name else "No pivot level hit",
    }

    # ── Factor 2: Stochastic Presignal Zone ──────────────────────────────
    zone = stoch_zone(stoch_k_15m)
    if direction == "bullish":
        stoch_hit = zone in ("oversold", "bullish_presignal")
        stoch_detail = f"Stoch K={stoch_k_15m:.1f} — {zone}"
    else:
        stoch_hit = zone in ("overbought", "bearish_presignal")
        stoch_detail = f"Stoch K={stoch_k_15m:.1f} — {zone}"
    factors["stochastic"] = {"hit": stoch_hit, "detail": stoch_detail}

    # ── Factor 3: Price Near 15m 21 EMA ─────────────────────────────────
    ema21_val = df_15m["ema21"].iloc[-1] if "ema21" in df_15m.columns else None
    if ema21_val is not None:
        ema_hit = abs(price - ema21_val) / price <= tolerance_pct * 2
        factors["ema21_15m"] = {
            "hit": ema_hit,
            "detail": f"15m EMA21={ema21_val:.4f}, price={price:.4f}, "
                      f"dist={abs(price - ema21_val) / price * 100:.3f}%",
        }
    else:
        factors["ema21_15m"] = {"hit": False, "detail": "EMA21 not available"}

    # ── Factor 4: Fibonacci Level ────────────────────────────────────────
    if fib_levels:
        tradeable_fibs = {k: v for k, v in fib_levels.items()
                         if k in ("38.2", "50.0", "61.8")}
        fib_name, fib_val = nearest_fib_level(price, tradeable_fibs, tolerance_pct)
        factors["fibonacci"] = {
            "hit": fib_name is not None,
            "detail": f"Near Fib {fib_name}% ({fib_val:.4f})" if fib_name else "No Fib level hit",
        }
    else:
        factors["fibonacci"] = {"hit": False, "detail": "No Fibonacci levels provided"}

    # ── Factor 5: Daily S/R (PDH/PDL or round number) ───────────────────
    daily_sr_hit = False
    daily_sr_detail = []

    if prev_day_high is not None and abs(price - prev_day_high) / price <= tolerance_pct:
        daily_sr_hit = True
        daily_sr_detail.append(f"Near PDH ({prev_day_high:.4f})")
    if prev_day_low is not None and abs(price - prev_day_low) / price <= tolerance_pct:
        daily_sr_hit = True
        daily_sr_detail.append(f"Near PDL ({prev_day_low:.4f})")

    # Round number check (nearest 00 and 50 handle)
    rounded_00 = round(price / 100) * 100
    rounded_50 = round(price / 50) * 50
    for rn in set([rounded_00, rounded_50]):
        if abs(price - rn) / price <= tolerance_pct:
            daily_sr_hit = True
            daily_sr_detail.append(f"Near round number ({rn:.0f})")

    factors["daily_sr"] = {
        "hit": daily_sr_hit,
        "detail": "; ".join(daily_sr_detail) if daily_sr_detail else "No daily S/R hit",
    }

    # ── Final Score ──────────────────────────────────────────────────────
    score = sum(1 for f in factors.values() if f["hit"])
    valid = score >= 3

    return {
        "score": score,
        "valid": valid,
        "direction": direction,
        "factors": factors,
        "verdict": (
            f"VALID ({score}/5 confluences)" if valid
            else f"SKIP ({score}/5 confluences — minimum 3 required)"
        ),
    }


def describe_confluence(result: dict) -> str:
    """Return a human-readable confluence summary string."""
    lines = [
        f"Confluence Score: {result['score']}/5 — {result['verdict']}",
        f"Direction: {result['direction'].upper()}",
        "",
        "Factors:",
    ]
    for name, data in result["factors"].items():
        tick = "✓" if data["hit"] else "✗"
        lines.append(f"  [{tick}] {name:15s}: {data['detail']}")
    return "\n".join(lines)
