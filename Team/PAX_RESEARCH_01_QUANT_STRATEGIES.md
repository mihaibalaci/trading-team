# Pax Research Report
**Researcher:** Pax | **Date:** 2026-03-28 | **Commissioned by:** Clio gap analysis

---

TOPIC: Algorithmic & Quantitative Trading Strategy Frameworks
OBJECTIVE: Covers the core types of systematic trading strategies, how they
are built from signal to live deployment, quality control mechanisms
(alpha decay, overfitting, walk-forward validation), key software
frameworks, and factor models.
```

---

## FINDINGS

### 1.1 Core Strategy Archetypes

**Mean Reversion**
Rooted in the statistical premise that asset prices oscillate around a long-run equilibrium. When price deviates significantly from its historical mean (or from a co-integrated pair/basket), a position is taken betting on convergence. Key signal inputs: z-score of spread, Bollinger Band distance, RSI extremes, half-life of mean reversion (estimated via Ornstein-Uhlenbeck process).

- Pairs trading is the canonical implementation: find two co-integrated assets (e.g., Gold vs. GLD ETF, two bank stocks), compute the hedge ratio via OLS or Johansen cointegration test, enter when z-score of spread > 2.0, exit at z = 0.
- Half-life formula (from OU process): `HL = -ln(2) / λ` where λ is the mean-reversion speed from the regression `ΔSpread_t = λ × Spread_(t-1) + ε`.
- Regime sensitivity: works in range-bound, low-vol environments; destroys capital in trending regimes. Always combine with a trend filter.

**Momentum / Trend Following**
Operates on the empirical observation that assets exhibiting strong recent performance tend to continue outperforming. Two primary forms:
- *Time-series momentum (TSMOM):* Signal based on an asset's own trailing return (e.g., 12-1 month return). Go long if positive, short if negative.
- *Cross-sectional momentum:* Rank universe by trailing return, long top decile, short bottom decile.

Classic Jegadeesh-Titman (1993) finding: 3–12 month look-back, 1-month skip, 1-month holding period generates persistent alpha. Momentum factor (UMD / WML) is a core Fama-French-Carhart factor.

**Statistical Arbitrage (Stat Arb)**
A market-neutral strategy exploiting pricing inefficiencies across large portfolios of correlated securities. Simultaneously long undervalued and short overvalued names, targeting beta-neutral, factor-neutral exposure. Typical implementation:
1. Build a factor model (PCA or explicit factors)
2. Extract residual (idiosyncratic) returns
3. Score residuals using z-scores or machine learning predictions
4. Construct a dollar/beta/factor-neutral book
5. Hold positions until z-score reverts

Key risk: crowding. When many stat arb funds hold similar positions, simultaneous unwinding creates violent reversals (e.g., the "Quant Quake" of August 2007).

**Market Making**
Provides liquidity by posting bids and offers simultaneously, capturing the bid-ask spread. Profit = spread captured minus adverse selection cost (being picked off by informed traders). Key models: Avellaneda-Stoikov model for optimal quote placement. Requires ultra-low latency infrastructure and real-time inventory risk management. P&L drivers: spread × volume - adverse selection - inventory carrying cost.

---

### 1.2 Systematic Strategy Construction: Signal → Backtest → Live

**Stage 1 — Alpha Signal Research**
- Hypothesis generation (price-based, fundamental, alternative data)
- Feature engineering: normalize signals (z-score, percentile rank), remove look-ahead bias
- Correlate signal to forward returns across regimes

**Stage 2 — Backtesting**
- Point-in-time data (survivorship-bias-free)
- Realistic transaction cost assumptions (slippage, bid-ask, borrow costs)
- No parameter snooping: use train/test splits before parameter search
- Evaluate: Sharpe Ratio, Calmar Ratio, max drawdown, hit rate, profit factor

**Stage 3 — Walk-Forward Validation (WFA)**
The gold standard. The AlgoXpert protocol (2026) structures it in three stages:
1. **In-Sample (IS):** Identify stable parameter regions (not single optima — avoid fitting noise)
2. **Walk-Forward Analysis:** Rolling windows with purge gaps to prevent information leakage between train and test periods
3. **Out-of-Sample (OOS):** Parameters locked; no further tuning permitted

Combinatorial Purged Cross-Validation (CPCV) outperforms traditional k-fold for financial time series because it respects temporal ordering and purges overlapping samples.

**Stage 4 — Paper Trading / Sim**
- Test order routing, execution latency, and fill assumptions
- Compare simulated fills to live market data

**Stage 5 — Live Deployment**
- Start with reduced position sizes (25–50% of target)
- Monitor for performance consistency with backtest
- Track alpha decay continuously

---

### 1.3 Alpha Decay, Overfitting, Walk-Forward Validation

**Alpha Decay**
Alpha sources erode over time as they become known. The decay rate is a function of:
- Signal uniqueness and crowding
- Ease of replication
- Market adaptation (participants learn and arbitrage away the edge)

Practical reality: Over 90% of academic strategies fail when implemented with real capital. About 95% of backtested strategies fail in live markets. The only response is continuous signal research and portfolio diversification across uncorrelated edges.

Decay is typically faster for:
- High-frequency signals (days to weeks)
- Widely published academic factors (months to years)
- Proprietary alternative data-based signals (slowest to decay)

**Overfitting**
Occurs when a model captures noise in historical data rather than true signal. Symptoms: high in-sample Sharpe, collapsing OOS performance.

Metrics for detecting overfit:
- **Deflated Sharpe Ratio (DSR):** Adjusts Sharpe for multiple testing, non-normality of returns, and skewness. `DSR = SR × √((T-1)/T) × (1 - skew×SR + (kurt-1)/4 × SR²)^(-1/2)`
- **Probability of Backtest Overfitting (PBO):** From combinatorial cross-validation; measures probability that the selected strategy was just lucky
- **Minimum Track Record Length (MTRL):** Minimum time period to statistically confirm a Sharpe Ratio

---

### 1.4 Software Frameworks

| Framework | Best For | Notes |
|---|---|---|
| **Zipline** | Event-driven backtesting | Original Quantopian engine; handles corporate actions cleanly; slower |
| **Backtrader** | Flexible strategy prototyping | Good documentation, large community; less vectorized |
| **VectorBT** | Fast vectorized backtesting | Pandas/NumPy-based; excellent for parameter sweeps; limited event-driven logic |
| **QuantLib** | Derivatives pricing | Industry standard for fixed income and options |
| **PyPortfolioOpt** | Portfolio optimization | Kelly, MVO, risk parity in Python |
| **Alphalens / Alphalens-Reloaded** | Factor analysis | IC, IC decay, quantile return analysis |
| **PyFolio** | Performance analytics | Drawdown, rolling Sharpe, factor attribution |
| **Nautilus Trader** | High-performance live trading | Rust-backed; handles FX, crypto, equities |

---

### 1.5 Factor Models

**Fama-French 3-Factor Model**
`R_i - R_f = α + β_MKT(R_m - R_f) + β_SMB(SMB) + β_HML(HML) + ε`

- **MKT:** Market risk premium
- **SMB (Small Minus Big):** Small-cap premium — long small-cap, short large-cap
- **HML (High Minus Low):** Value premium — long high book-to-market, short low book-to-market

**Fama-French-Carhart 4-Factor (adds Momentum)**
Adds **UMD (Up Minus Down):** long past winners, short past losers (12-1 month return).

**Fama-French 5-Factor (2015)**
Adds **RMW (Robust Minus Weak):** profitability factor; **CMA (Conservative Minus Aggressive):** investment factor.

**Practical Use of Factor Models**
1. Run factor regression on portfolio returns to decompose alpha vs. factor beta
2. Ensure alpha is persistent after controlling for known factors (otherwise you are just running an undiversified factor tilt)
3. Use factor exposures for risk decomposition and hedging

**Information Coefficient (IC):** Rank correlation between predicted and actual forward returns. IC > 0.05 is considered meaningful; IC > 0.10 is strong. IC decays rapidly — measure IC decay (IC at lag 1, lag 5, lag 20) to understand signal shelf life.

---

```
CONFIDENCE: High
SOURCES/REASONING: Multiple academic papers (arxiv), quantitative finance
education platforms (QuantInsti, Build Alpha), recent 2026 research on
walk-forward validation frameworks, Fama-French original research, and
practitioner blogs. Core concepts are well-established; specific framework
comparisons validated against multiple sources.
RECOMMENDED NEXT STEPS:
- Run Alphalens factor tear sheets on any new candidate signals before
  committing to full backtests
- Implement PBO/DSR calculation as a standard backtest quality gate
- Build a factor attribution dashboard to monitor live alpha vs. factor
  contribution in real time
```

---
---

```
