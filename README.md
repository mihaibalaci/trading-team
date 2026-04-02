# AI Trading Team

Automated trading system using multi-timeframe candlestick pattern analysis with Alpaca paper trading.

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Configure API keys
cp signals/.env.example signals/.env
# Edit signals/.env with your Alpaca credentials

# Run the volatile stock scanner
python signals/volatile_scanner.py

# Run tests
python -m pytest tests/ -v
```

## Architecture

```
signals/
├── indicators.py        — EMA, Stochastic, ATR, Pivots, Fibonacci
├── patterns.py          — 20+ candlestick pattern detectors
├── confluence.py        — 5-factor confluence scoring engine
├── signal_engine.py     — Finn's MTF signal generator (30m→15m→1m)
├── execution.py         — Order management, session guard, trade records
├── broker_connector.py  — Alpaca API connector (paper + live)
├── mt5_connector.py     — MetaTrader 5 connector
├── strategy_config.py   — Strategy parameter management
├── live_scanner.py      — Static watchlist scanner (60s interval)
├── volatile_scanner.py  — Dynamic top-10 volatile stock scanner (30s)
├── trade_launcher.py    — Trade launcher
├── market_study.py      — Historical pattern analysis (yfinance)
├── database.py          — SQLite trade logging
├── service.py           — Multi-process service orchestrator
├── web_app.py           — Flask web dashboard
└── templates/           — Dashboard HTML templates

tests/
├── test_indicators.py   — 23 tests (EMA, Stochastic, ATR, Pivots, Fibonacci, trend)
├── test_patterns.py     — 46 tests (all 20+ candlestick patterns)
├── test_confluence.py   — 6 tests (confluence scoring)
└── test_execution.py    — 18 tests (SessionGuard, SignalValidator, cost estimators)

Team/                    — AI team member profiles & research docs
deploy/                  — systemd service, install script
```

## Team Members

| Name | Role |
|------|------|
| Larry | Orchestrator & Head of Trading Operations |
| Nolan | Head of Human Resources |
| Pax | Senior Researcher |
| Vera | Strategy & Portfolio Manager |
| Finn | Scalp/Intraday Signal Analyst |
| Sage | Swing & Positional Signal Analyst |
| Remy | Intraday Execution Specialist |
| Cole | Swing Execution Specialist |
| Mira | Risk Officer |
| Clio | Data & Knowledge Manager |
| Kai | Platform Integration Engineer |

## Strategy

Multi-timeframe price action (Vera's MTF Scalp Strategy):
- **30m** — trend bias (EMA stack + market structure)
- **15m** — pattern detection at confluence zones (min 3/5 factors)
- **1m** — entry trigger timing

Risk rules (Mira): max 3 positions, 1% risk each, 3% total exposure, drawdown circuit breakers.

See `Team/VERA_STRATEGY_MTF_SCALP.md` for full strategy documentation.

## Testing

```bash
python -m pytest tests/ -v          # all 93 tests
python -m pytest tests/ -k pattern  # pattern tests only
python -m pytest tests/ -k guard    # session guard tests only
```

## Deployment

See `deploy/INSTALL.md` for full deployment guide (systemd service on Ubuntu/Debian).

```bash
# On the server
sudo bash deploy/install.sh
sudo systemctl start trading-team
# Dashboard at http://server-ip:5050
```

## Research & Knowledge Base

- `Team/CLIO_KNOWLEDGE_INDEX.md` — Index of 7 candlestick trading books
- `Team/PAX_RESEARCH_*.md` — 6 research briefs (quant strategies, risk models, volatility, etc.)
- `Team/FINN_6MONTH_MARKET_STUDY.md` — Empirical pattern performance study (20,013 patterns)
- `Team/PAX_CLIO_SIGNAL_STRATEGIES.md` — Signal strategy research
