"""
patterns.py — Candlestick pattern detection for Finn's signal models.
Implements all patterns from Vera's approved setup list.
Pattern logic sourced from Clio's indexed library (7 books).

Each detector returns:
    None                    — pattern not present
    "bullish_<name>"        — bullish pattern detected
    "bearish_<name>"        — bearish pattern detected
    "neutral_<name>"        — neutral (doji-type) pattern detected
"""
from __future__ import annotations

import pandas as pd
import numpy as np


# ─────────────────────────────────────────────
# Candle anatomy helpers
# ─────────────────────────────────────────────

def _body(o: float, c: float) -> float:
    return abs(c - o)

def _upper_wick(o: float, h: float, c: float) -> float:
    return h - max(o, c)

def _lower_wick(o: float, l: float, c: float) -> float:
    return min(o, c) - l

def _range(h: float, l: float) -> float:
    return h - l

def _is_bullish(o: float, c: float) -> bool:
    return c > o

def _is_bearish(o: float, c: float) -> bool:
    return c < o

def _body_pct(o: float, h: float, l: float, c: float) -> float:
    """Body as a fraction of total candle range."""
    r = _range(h, l)
    return _body(o, c) / r if r > 0 else 0


# ─────────────────────────────────────────────
# Single-candle patterns
# ─────────────────────────────────────────────

def doji(o: float, h: float, l: float, c: float,
         threshold: float = 0.1) -> str | None:
    """
    Doji: body is very small relative to the total range.
    threshold: body must be < threshold * total_range.
    Source: All 7 library books. Do NOT trade doji alone — requires confirmation.
    """
    if _range(h, l) == 0:
        return None
    if _body_pct(o, h, l, c) < threshold:
        return "neutral_doji"
    return None


def dragonfly_doji(o: float, h: float, l: float, c: float,
                   body_threshold: float = 0.05,
                   lower_wick_min: float = 0.6) -> str | None:
    """
    Dragonfly Doji: tiny body at the top, long lower wick.
    Bullish reversal at support. Source: Candlestick Bible Ch. (Dragon Fly Doji).
    """
    r = _range(h, l)
    if r == 0:
        return None
    if (_body_pct(o, h, l, c) < body_threshold and
            _lower_wick(o, l, c) / r > lower_wick_min and
            _upper_wick(o, h, c) / r < 0.1):
        return "bullish_dragonfly_doji"
    return None


def gravestone_doji(o: float, h: float, l: float, c: float,
                    body_threshold: float = 0.05,
                    upper_wick_min: float = 0.6) -> str | None:
    """
    Gravestone Doji: tiny body at the bottom, long upper wick.
    Bearish reversal at resistance. Source: Candlestick Bible.
    """
    r = _range(h, l)
    if r == 0:
        return None
    if (_body_pct(o, h, l, c) < body_threshold and
            _upper_wick(o, h, c) / r > upper_wick_min and
            _lower_wick(o, l, c) / r < 0.1):
        return "bearish_gravestone_doji"
    return None


def hammer(o: float, h: float, l: float, c: float) -> str | None:
    """
    Hammer: small body near the top of the range, long lower wick (≥2× body),
    minimal upper wick. Bullish reversal at support.
    Source: All 7 books. Requires confirmation candle.
    """
    r = _range(h, l)
    if r == 0:
        return None
    body = _body(o, c)
    lower = _lower_wick(o, l, c)
    upper = _upper_wick(o, h, c)
    if (body > 0 and
            lower >= 2 * body and
            upper <= 0.3 * body and
            _body_pct(o, h, l, c) < 0.4):
        return "bullish_hammer"
    return None


def inverted_hammer(o: float, h: float, l: float, c: float) -> str | None:
    """
    Inverted Hammer: small body near the bottom, long upper wick.
    Bullish signal — requires strong confirmation. Source: Arul Pandi, Sadekar.
    """
    r = _range(h, l)
    if r == 0:
        return None
    body = _body(o, c)
    upper = _upper_wick(o, h, c)
    lower = _lower_wick(o, l, c)
    if (body > 0 and
            upper >= 2 * body and
            lower <= 0.3 * body and
            _body_pct(o, h, l, c) < 0.4):
        return "bullish_inverted_hammer"
    return None


def hanging_man(o: float, h: float, l: float, c: float) -> str | None:
    """
    Hanging Man: same shape as hammer but appears after an uptrend.
    Bearish reversal signal. Source: All 7 books.
    NOTE: Caller must verify uptrend context.
    """
    result = hammer(o, h, l, c)
    if result:
        return "bearish_hanging_man"
    return None


def shooting_star(o: float, h: float, l: float, c: float) -> str | None:
    """
    Shooting Star: small body near the bottom, long upper wick, minimal lower wick.
    Bearish reversal at resistance. Source: All 7 books.
    """
    r = _range(h, l)
    if r == 0:
        return None
    body = _body(o, c)
    upper = _upper_wick(o, h, c)
    lower = _lower_wick(o, l, c)
    if (body > 0 and
            upper >= 2 * body and
            lower <= 0.3 * body and
            _is_bearish(o, c) and
            _body_pct(o, h, l, c) < 0.4):
        return "bearish_shooting_star"
    return None


def pin_bar(o: float, h: float, l: float, c: float,
            direction: str = "auto") -> str | None:
    """
    Pin Bar: long wick in one direction (≥2/3 of total range), small body,
    short opposing wick. High-probability reversal pattern.
    Source: Candlestick Trading Bible, Candlestick Bible — Pin Bar Strategy.

    direction: 'bullish', 'bearish', or 'auto' (detect both).
    """
    r = _range(h, l)
    if r == 0:
        return None
    body = _body(o, c)
    upper = _upper_wick(o, h, c)
    lower = _lower_wick(o, l, c)

    bullish_pin = (lower >= 0.66 * r and body <= 0.25 * r and upper <= 0.15 * r)
    bearish_pin = (upper >= 0.66 * r and body <= 0.25 * r and lower <= 0.15 * r)

    if direction in ("bullish", "auto") and bullish_pin:
        return "bullish_pin_bar"
    if direction in ("bearish", "auto") and bearish_pin:
        return "bearish_pin_bar"
    return None


def belt_hold(o: float, h: float, l: float, c: float) -> str | None:
    """
    Belt Hold: opens at the extreme (no wick on one side), large body.
    Source: Arul Pandi (Pattern 12).
    """
    body = _body(o, c)
    r = _range(h, l)
    if r == 0 or body < 0.7 * r:
        return None
    if _is_bullish(o, c) and _lower_wick(o, l, c) < 0.02 * r:
        return "bullish_belt_hold"
    if _is_bearish(o, c) and _upper_wick(o, h, c) < 0.02 * r:
        return "bearish_belt_hold"
    return None


# ─────────────────────────────────────────────
# Two-candle patterns
# ─────────────────────────────────────────────

def engulfing(prev_o: float, prev_c: float,
              curr_o: float, curr_c: float) -> str | None:
    """
    Engulfing Bar: current body fully contains previous body.
    Most cited pattern across all 7 library books.
    Bearish engulfing at uptrend top = reversal. Bullish at downtrend bottom = reversal.
    """
    prev_body_top    = max(prev_o, prev_c)
    prev_body_bottom = min(prev_o, prev_c)
    curr_body_top    = max(curr_o, curr_c)
    curr_body_bottom = min(curr_o, curr_c)

    if (curr_body_top > prev_body_top and
            curr_body_bottom < prev_body_bottom):
        if _is_bullish(curr_o, curr_c) and _is_bearish(prev_o, prev_c):
            return "bullish_engulfing"
        if _is_bearish(curr_o, curr_c) and _is_bullish(prev_o, prev_c):
            return "bearish_engulfing"
    return None


def harami(prev_o: float, prev_h: float, prev_l: float, prev_c: float,
           curr_o: float, curr_h: float, curr_l: float, curr_c: float) -> str | None:
    """
    Harami: current candle is fully inside the previous candle's body.
    Reversal signal — requires confirmation. Source: All 7 books.
    Harami Cross (Doji inside prior body) = stronger signal.
    """
    prev_body_top    = max(prev_o, prev_c)
    prev_body_bottom = min(prev_o, prev_c)
    curr_body_top    = max(curr_o, curr_c)
    curr_body_bottom = min(curr_o, curr_c)

    inside = (curr_body_top < prev_body_top and
              curr_body_bottom > prev_body_bottom)

    if not inside:
        return None

    is_cross = doji(curr_o, curr_h, curr_l, curr_c) is not None

    if _is_bullish(prev_o, prev_c):  # Previous was bullish → bearish harami
        return "bearish_harami_cross" if is_cross else "bearish_harami"
    else:  # Previous was bearish → bullish harami
        return "bullish_harami_cross" if is_cross else "bullish_harami"


def dark_cloud_cover(prev_o: float, prev_c: float,
                     curr_o: float, curr_c: float,
                     penetration: float = 0.5) -> str | None:
    """
    Dark Cloud Cover: bearish candle opens above prior bullish candle's high
    and closes below midpoint of prior body.
    Source: Arul Pandi (Pattern 10), Sadekar Ch.7.
    """
    if not (_is_bullish(prev_o, prev_c) and _is_bearish(curr_o, curr_c)):
        return None
    prev_mid = prev_o + (prev_c - prev_o) * (1 - penetration)
    if curr_o > prev_c and curr_c < prev_mid and curr_c > prev_o:
        return "bearish_dark_cloud_cover"
    return None


def piercing_line(prev_o: float, prev_c: float,
                  curr_o: float, curr_c: float,
                  penetration: float = 0.5) -> str | None:
    """
    Piercing Line: bullish candle opens below prior bearish candle's low
    and closes above midpoint of prior body.
    Source: Arul Pandi (Pattern 11), Sadekar Ch.7, Person Ch.4.
    """
    if not (_is_bearish(prev_o, prev_c) and _is_bullish(curr_o, curr_c)):
        return None
    prev_mid = prev_o - (prev_o - prev_c) * (1 - penetration)
    if curr_o < prev_c and curr_c > prev_mid and curr_c < prev_o:
        return "bullish_piercing_line"
    return None


def tweezer(prev_h: float, prev_l: float, prev_o: float, prev_c: float,
            curr_h: float, curr_l: float, curr_o: float, curr_c: float,
            tolerance_pct: float = 0.001) -> str | None:
    """
    Tweezer Tops/Bottoms: two candles with matching highs (top) or lows (bottom).
    Source: Candlestick Bible, Candlestick Trading Bible.
    """
    tol = prev_h * tolerance_pct
    # Tweezer top: matching highs, first bullish, second bearish
    if (abs(prev_h - curr_h) < tol and
            _is_bullish(prev_o, prev_c) and _is_bearish(curr_o, curr_c)):
        return "bearish_tweezer_top"
    # Tweezer bottom: matching lows, first bearish, second bullish
    if (abs(prev_l - curr_l) < tol and
            _is_bearish(prev_o, prev_c) and _is_bullish(curr_o, curr_c)):
        return "bullish_tweezer_bottom"
    return None


# ─────────────────────────────────────────────
# Three-candle patterns
# ─────────────────────────────────────────────

def morning_star(c1_o: float, c1_c: float,
                 c2_o: float, c2_h: float, c2_l: float, c2_c: float,
                 c3_o: float, c3_c: float,
                 star_body_max: float = 0.3,
                 gap_required: bool = False) -> str | None:
    """
    Morning Star: bearish candle → small-bodied star (gap or small candle) →
    bullish candle closing above midpoint of first candle.
    Source: All 7 books. One of the strongest bullish reversal patterns.
    """
    if not (_is_bearish(c1_o, c1_c) and _is_bullish(c3_o, c3_c)):
        return None
    c1_range = _body(c1_o, c1_c)
    c2_body  = _body(c2_o, c2_c)
    c1_mid   = c1_o - c1_range / 2  # midpoint of first (bearish) body

    star_small = c1_range > 0 and (c2_body / c1_range) < star_body_max
    c3_closes_high = c3_c > c1_mid

    doji_star = doji(c2_o, c2_h, c2_l, c2_c) is not None

    if star_small and c3_closes_high:
        if doji_star:
            return "bullish_morning_doji_star"
        return "bullish_morning_star"
    return None


def evening_star(c1_o: float, c1_c: float,
                 c2_o: float, c2_h: float, c2_l: float, c2_c: float,
                 c3_o: float, c3_c: float,
                 star_body_max: float = 0.3) -> str | None:
    """
    Evening Star: bullish candle → small-bodied star → bearish candle
    closing below midpoint of first candle.
    Source: All 7 books. Strongest bearish reversal, especially with Doji star.
    """
    if not (_is_bullish(c1_o, c1_c) and _is_bearish(c3_o, c3_c)):
        return None
    c1_range = _body(c1_o, c1_c)
    c2_body  = _body(c2_o, c2_c)
    c1_mid   = c1_o + c1_range / 2  # midpoint of first (bullish) body

    star_small = c1_range > 0 and (c2_body / c1_range) < star_body_max
    c3_closes_low = c3_c < c1_mid

    doji_star = doji(c2_o, c2_h, c2_l, c2_c) is not None

    if star_small and c3_closes_low:
        if doji_star:
            return "bearish_evening_doji_star"
        return "bearish_evening_star"
    return None


def three_white_soldiers(candles: list[tuple]) -> str | None:
    """
    Three White Soldiers: three consecutive bullish candles, each opening
    within prior body and closing progressively higher.
    High-conviction bullish momentum signal. Source: Arul Pandi (Pattern 13), Morris.

    candles: list of 3 tuples [(o,h,l,c), (o,h,l,c), (o,h,l,c)]
    """
    if len(candles) < 3:
        return None
    (o1,h1,l1,c1), (o2,h2,l2,c2), (o3,h3,l3,c3) = candles[-3:]
    if not (all(_is_bullish(o, c) for o, _, _, c in candles[-3:])):
        return None
    progressive = c1 < c2 < c3
    open_inside = (o1 < o2 < c1) and (o2 < o3 < c2)
    small_wicks = (
        _upper_wick(o1,h1,c1) < 0.3 * _body(o1,c1) and
        _upper_wick(o2,h2,c2) < 0.3 * _body(o2,c2) and
        _upper_wick(o3,h3,c3) < 0.3 * _body(o3,c3)
    )
    if progressive and open_inside and small_wicks:
        return "bullish_three_white_soldiers"
    return None


def three_black_crows(candles: list[tuple]) -> str | None:
    """
    Three Black Crows: three consecutive bearish candles, each opening
    within prior body and closing progressively lower.
    High-conviction bearish momentum signal. Source: Arul Pandi (Pattern 14), Morris.

    candles: list of 3 tuples [(o,h,l,c), (o,h,l,c), (o,h,l,c)]
    """
    if len(candles) < 3:
        return None
    (o1,h1,l1,c1), (o2,h2,l2,c2), (o3,h3,l3,c3) = candles[-3:]
    if not all(_is_bearish(o, c) for o, _, _, c in candles[-3:]):
        return None
    progressive = c1 > c2 > c3
    open_inside = (c1 < o2 < o1) and (c2 < o3 < o2)
    small_wicks = (
        _lower_wick(o1,l1,c1) < 0.3 * _body(o1,c1) and
        _lower_wick(o2,l2,c2) < 0.3 * _body(o2,c2) and
        _lower_wick(o3,l3,c3) < 0.3 * _body(o3,c3)
    )
    if progressive and open_inside and small_wicks:
        return "bearish_three_black_crows"
    return None


# ─────────────────────────────────────────────
# Pattern strength classification
# ─────────────────────────────────────────────

PATTERN_STRENGTH = {
    # High strength (4+ sources, high predictive value)
    "bullish_engulfing":            "high",
    "bearish_engulfing":            "high",
    "bullish_morning_star":         "high",
    "bearish_evening_star":         "high",
    "bullish_morning_doji_star":    "high",
    "bearish_evening_doji_star":    "high",
    "bullish_pin_bar":              "high",
    "bearish_pin_bar":              "high",
    "bullish_three_white_soldiers": "high",
    "bearish_three_black_crows":    "high",
    "bullish_tweezer_bottom":       "high",
    "bearish_tweezer_top":          "high",
    "bullish_dragonfly_doji":       "high",
    "bearish_gravestone_doji":      "high",
    # Medium-high (requires confirmation candle)
    "bullish_hammer":               "medium_high",
    "bearish_shooting_star":        "medium_high",
    "bullish_piercing_line":        "medium_high",
    "bearish_dark_cloud_cover":     "medium_high",
    "bullish_harami_cross":         "medium_high",
    "bearish_harami_cross":         "medium_high",
    # Medium (confirmation mandatory)
    "bullish_inverted_hammer":      "medium",
    "bearish_hanging_man":          "medium",
    "bullish_harami":               "medium",
    "bearish_harami":               "medium",
    "bullish_belt_hold":            "medium",
    "bearish_belt_hold":            "medium",
    # Neutral (context + confirmation required — never trade alone)
    "neutral_doji":                 "neutral",
}


def pattern_direction(pattern_name: str) -> str | None:
    """Return 'bullish', 'bearish', or 'neutral' from pattern name."""
    if pattern_name.startswith("bullish_"):
        return "bullish"
    if pattern_name.startswith("bearish_"):
        return "bearish"
    if pattern_name.startswith("neutral_"):
        return "neutral"
    return None


# ─────────────────────────────────────────────
# Scan a DataFrame for all patterns
# ─────────────────────────────────────────────

def scan_patterns(df: pd.DataFrame) -> list[dict]:
    """
    Scan the last few candles of a DataFrame for all known patterns.
    Returns list of detected pattern dicts, most recent first.

    DataFrame must have columns: open, high, low, close.
    """
    detected = []
    n = len(df)
    if n < 3:
        return detected

    rows = df[["open", "high", "low", "close"]].values
    idx  = df.index

    # Single-candle (check last candle)
    o, h, l, c = rows[-1]
    for fn in [doji, dragonfly_doji, gravestone_doji, hammer,
               inverted_hammer, shooting_star, belt_hold, pin_bar]:
        result = fn(o, h, l, c)
        if result:
            detected.append({"pattern": result,
                              "candles": 1,
                              "index": idx[-1],
                              "strength": PATTERN_STRENGTH.get(result, "unknown"),
                              "direction": pattern_direction(result)})

    # Context-dependent single candles (need prior trend — caller must verify)
    hm = hammer(o, h, l, c)
    if hm:
        detected.append({"pattern": "bearish_hanging_man",
                         "candles": 1,
                         "index": idx[-1],
                         "strength": PATTERN_STRENGTH["bearish_hanging_man"],
                         "direction": "bearish",
                         "note": "Verify uptrend context for hanging man vs hammer"})

    # Two-candle (check last 2 candles)
    if n >= 2:
        po, ph, pl, pc = rows[-2]
        o, h, l, c = rows[-1]

        for fn, args in [
            (engulfing,       (po, pc, o, c)),
            (harami,          (po, ph, pl, pc, o, h, l, c)),
            (dark_cloud_cover,(po, pc, o, c)),
            (piercing_line,   (po, pc, o, c)),
            (tweezer,         (ph, pl, po, pc, h, l, o, c)),
        ]:
            result = fn(*args)
            if result:
                detected.append({"pattern": result,
                                  "candles": 2,
                                  "index": idx[-1],
                                  "strength": PATTERN_STRENGTH.get(result, "unknown"),
                                  "direction": pattern_direction(result)})

    # Three-candle (check last 3 candles)
    if n >= 3:
        c1 = tuple(rows[-3])
        c2 = tuple(rows[-2])
        c3 = tuple(rows[-1])

        ms = morning_star(c1[0], c1[3], c2[0], c2[1], c2[2], c2[3], c3[0], c3[3])
        if ms:
            detected.append({"pattern": ms,
                              "candles": 3,
                              "index": idx[-1],
                              "strength": PATTERN_STRENGTH.get(ms, "unknown"),
                              "direction": "bullish"})

        es = evening_star(c1[0], c1[3], c2[0], c2[1], c2[2], c2[3], c3[0], c3[3])
        if es:
            detected.append({"pattern": es,
                              "candles": 3,
                              "index": idx[-1],
                              "strength": PATTERN_STRENGTH.get(es, "unknown"),
                              "direction": "bearish"})

        tws = three_white_soldiers([c1, c2, c3])
        if tws:
            detected.append({"pattern": tws,
                              "candles": 3,
                              "index": idx[-1],
                              "strength": PATTERN_STRENGTH.get(tws, "unknown"),
                              "direction": "bullish"})

        tbc = three_black_crows([c1, c2, c3])
        if tbc:
            detected.append({"pattern": tbc,
                              "candles": 3,
                              "index": idx[-1],
                              "strength": PATTERN_STRENGTH.get(tbc, "unknown"),
                              "direction": "bearish"})

    return detected
