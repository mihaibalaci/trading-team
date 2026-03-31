"""
strategy_config.py — Configurable strategy profiles for the trading team.

Allows the owner to select:
  1. Trade horizon: short (1-5m), medium (5-30m), long (30m-2h)
  2. Asset class: stocks, forex, commodities
  3. Risk level: conservative, moderate, aggressive (input after Pax analysis)

Each combination produces a StrategyProfile that configures:
  - Timeframes for trend/setup/entry
  - Bar counts for indicator reliability
  - Scan interval
  - Risk per trade and max positions
  - Watchlist per asset class
  - Session times
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time as dtime
from enum import Enum
from typing import Optional


class Horizon(str, Enum):
    SHORT  = "short"    # 1-5 min holds (scalp)
    MEDIUM = "medium"   # 5-30 min holds (intraday swing)
    LONG   = "long"     # 30m-2h holds (intraday position)


class AssetClass(str, Enum):
    STOCKS      = "stocks"
    FOREX       = "forex"
    COMMODITIES = "commodities"


class RiskLevel(str, Enum):
    CONSERVATIVE = "conservative"   # 0.5% risk, max 2 positions
    MODERATE     = "moderate"       # 1.0% risk, max 3 positions
    AGGRESSIVE   = "aggressive"     # 1.5% risk, max 4 positions


# ─────────────────────────────────────────────────────────────────
# Watchlists per asset class
# ─────────────────────────────────────────────────────────────────

WATCHLISTS = {
    AssetClass.STOCKS: [
        "SPY", "QQQ", "AAPL", "MSFT", "NVDA",
        "TSLA", "AMZN", "META", "GOOGL", "JPM",
    ],
    AssetClass.FOREX: [
        # Alpaca supports these as crypto pairs on paper;
        # for true forex, swap to OANDA or similar via broker_connector
        "EUR/USD", "GBP/USD", "USD/JPY", "AUD/USD", "USD/CAD",
    ],
    AssetClass.COMMODITIES: [
        # ETF proxies for commodities on Alpaca
        "GLD", "SLV", "USO", "UNG", "COPX",
        "DBA", "PDBC",
    ],
}


# ─────────────────────────────────────────────────────────────────
# Timeframe configs per horizon
# ─────────────────────────────────────────────────────────────────

TIMEFRAME_CONFIG = {
    Horizon.SHORT: {
        "tf_trend_min": 15,    # trend bias on 15m
        "tf_setup_min": 5,     # setup detection on 5m
        "tf_entry_min": 1,     # entry trigger on 1m
        "bars_trend": 80,      # ~20h of 15m bars
        "bars_setup": 120,     # ~10h of 5m bars
        "bars_entry": 200,     # ~3.5h of 1m bars
        "scan_interval_s": 30, # scan every 30s
        "max_hold_min": 5,     # max hold 5 minutes
        "label": "Scalp (1-5 min holds)",
    },
    Horizon.MEDIUM: {
        "tf_trend_min": 30,
        "tf_setup_min": 15,
        "tf_entry_min": 5,
        "bars_trend": 60,      # ~30h of 30m bars
        "bars_setup": 80,      # ~20h of 15m bars
        "bars_entry": 120,     # ~10h of 5m bars
        "scan_interval_s": 60, # scan every 60s
        "max_hold_min": 30,
        "label": "Intraday Swing (5-30 min holds)",
    },
    Horizon.LONG: {
        "tf_trend_min": 60,
        "tf_setup_min": 30,
        "tf_entry_min": 15,
        "bars_trend": 40,      # ~40h of 1h bars
        "bars_setup": 60,      # ~30h of 30m bars
        "bars_entry": 80,      # ~20h of 15m bars
        "scan_interval_s": 120,# scan every 2 min
        "max_hold_min": 120,
        "label": "Intraday Position (30m-2h holds)",
    },
}


# ─────────────────────────────────────────────────────────────────
# Risk configs per level
# ─────────────────────────────────────────────────────────────────

RISK_CONFIG = {
    RiskLevel.CONSERVATIVE: {
        "risk_per_trade": 0.005,   # 0.5%
        "max_positions": 2,
        "max_open_risk": 0.015,    # 1.5% total
        "min_signal_strength": 60, # higher bar for entry
        "min_confluence": 4,       # need 4/5 confluences
        "drawdown_halt_pct": 3.0,  # halt at 3% drawdown
        "label": "Conservative (0.5% risk, tight filters)",
    },
    RiskLevel.MODERATE: {
        "risk_per_trade": 0.01,    # 1.0%
        "max_positions": 3,
        "max_open_risk": 0.03,     # 3% total
        "min_signal_strength": 45,
        "min_confluence": 3,       # Vera's standard
        "drawdown_halt_pct": 5.0,
        "label": "Moderate (1% risk, standard filters)",
    },
    RiskLevel.AGGRESSIVE: {
        "risk_per_trade": 0.015,   # 1.5%
        "max_positions": 4,
        "max_open_risk": 0.06,     # 6% total
        "min_signal_strength": 35, # lower bar
        "min_confluence": 3,
        "drawdown_halt_pct": 8.0,
        "label": "Aggressive (1.5% risk, wider filters)",
    },
}


# ─────────────────────────────────────────────────────────────────
# Session times per asset class
# ─────────────────────────────────────────────────────────────────

SESSION_CONFIG = {
    AssetClass.STOCKS: {
        "session_open": dtime(9, 30),
        "session_close": dtime(16, 0),
        "label": "NYSE/NASDAQ 9:30-16:00 ET",
    },
    AssetClass.FOREX: {
        "session_open": dtime(0, 0),
        "session_close": dtime(23, 59),
        "label": "Forex 24h (best: London/NY overlap 8:00-12:00 ET)",
    },
    AssetClass.COMMODITIES: {
        "session_open": dtime(9, 30),
        "session_close": dtime(16, 0),
        "label": "Commodity ETFs 9:30-16:00 ET",
    },
}


# ─────────────────────────────────────────────────────────────────
# Strategy Profile — the unified config object
# ─────────────────────────────────────────────────────────────────

@dataclass
class StrategyProfile:
    """Complete configuration for a trading session."""
    horizon:        Horizon
    asset_class:    AssetClass
    risk_level:     RiskLevel

    # Timeframes
    tf_trend_min:   int = 15
    tf_setup_min:   int = 5
    tf_entry_min:   int = 1
    bars_trend:     int = 80
    bars_setup:     int = 120
    bars_entry:     int = 200
    scan_interval_s: int = 30
    max_hold_min:   int = 5

    # Risk
    risk_per_trade: float = 0.01
    max_positions:  int = 3
    max_open_risk:  float = 0.03
    min_signal_strength: int = 45
    min_confluence: int = 3
    drawdown_halt_pct: float = 5.0

    # Session
    session_open:   dtime = dtime(9, 30)
    session_close:  dtime = dtime(16, 0)

    # Watchlist
    watchlist:      list = field(default_factory=list)

    # Pax analysis notes (filled after research)
    pax_analysis:   str = ""

    def summary(self) -> str:
        tf = TIMEFRAME_CONFIG[self.horizon]
        risk = RISK_CONFIG[self.risk_level]
        sess = SESSION_CONFIG[self.asset_class]
        lines = [
            f"{'='*60}",
            f"  STRATEGY PROFILE",
            f"{'='*60}",
            f"  Horizon:      {tf['label']}",
            f"  Asset Class:  {self.asset_class.value.upper()}",
            f"  Risk Level:   {risk['label']}",
            f"  Timeframes:   {self.tf_trend_min}m trend / {self.tf_setup_min}m setup / {self.tf_entry_min}m entry",
            f"  Scan Every:   {self.scan_interval_s}s",
            f"  Max Hold:     {self.max_hold_min} min",
            f"  Risk/Trade:   {self.risk_per_trade:.1%}",
            f"  Max Positions: {self.max_positions}",
            f"  Max Open Risk: {self.max_open_risk:.1%}",
            f"  Min Signal:   {self.min_signal_strength}/100",
            f"  Min Confluence: {self.min_confluence}/5",
            f"  Drawdown Halt: {self.drawdown_halt_pct:.1f}%",
            f"  Session:      {sess['label']}",
            f"  Watchlist:    {', '.join(self.watchlist[:5])}{'...' if len(self.watchlist) > 5 else ''}",
            f"{'='*60}",
        ]
        if self.pax_analysis:
            lines.insert(-1, f"  Pax Notes:    {self.pax_analysis[:80]}")
        return "\n".join(lines)


def build_profile(horizon: Horizon, asset_class: AssetClass,
                  risk_level: RiskLevel,
                  pax_analysis: str = "") -> StrategyProfile:
    """Build a StrategyProfile from the three user choices."""
    tf   = TIMEFRAME_CONFIG[horizon]
    risk = RISK_CONFIG[risk_level]
    sess = SESSION_CONFIG[asset_class]

    return StrategyProfile(
        horizon         = horizon,
        asset_class     = asset_class,
        risk_level      = risk_level,
        tf_trend_min    = tf["tf_trend_min"],
        tf_setup_min    = tf["tf_setup_min"],
        tf_entry_min    = tf["tf_entry_min"],
        bars_trend      = tf["bars_trend"],
        bars_setup      = tf["bars_setup"],
        bars_entry      = tf["bars_entry"],
        scan_interval_s = tf["scan_interval_s"],
        max_hold_min    = tf["max_hold_min"],
        risk_per_trade  = risk["risk_per_trade"],
        max_positions   = risk["max_positions"],
        max_open_risk   = risk["max_open_risk"],
        min_signal_strength = risk["min_signal_strength"],
        min_confluence  = risk["min_confluence"],
        drawdown_halt_pct = risk["drawdown_halt_pct"],
        session_open    = sess["session_open"],
        session_close   = sess["session_close"],
        watchlist       = WATCHLISTS[asset_class],
        pax_analysis    = pax_analysis,
    )
