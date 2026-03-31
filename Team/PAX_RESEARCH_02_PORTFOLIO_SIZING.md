# Pax Research Report
**Researcher:** Pax | **Date:** 2026-03-28 | **Commissioned by:** Clio gap analysis

---

TOPIC: Portfolio Construction & Position Sizing Models
OBJECTIVE: Covers the full toolkit for translating trade signals into actual
position sizes — Kelly Criterion (full and fractional), fixed fractional,
risk parity, mean-variance optimization, maximum Sharpe portfolio, and
practical construction rules.
```

---

## FINDINGS

### 2.1 Kelly Criterion

**The Core Idea**
Kelly maximizes the long-run geometric growth rate of a portfolio by sizing each bet according to its edge and odds. It was derived by J.L. Kelly Jr. (1956) at Bell Labs.

**Standard Binary Kelly Formula:**
```
f* = (b × p - q) / b
```
Where:
- `f*` = fraction of capital to bet
- `b` = net odds (profit per unit risked, e.g., 1.5 for 3:2 payout)
- `p` = probability of winning
- `q` = probability of losing = 1 - p

**Trading-Adapted Kelly Formula:**
```
f* = W - (1 - W) / R
```
Where:
- `W` = win rate (% of trades that are profitable)
- `R` = average win / average loss ratio

**Example:** If W = 60%, R = 1.5 → `f* = 0.60 - (0.40/1.5) = 0.60 - 0.267 = 33.3%` of capital per trade.

**Continuous / Gaussian Kelly (for portfolios):**
```
f* = μ / σ²
```
Where μ is expected excess return and σ² is variance of returns.

For a multi-asset portfolio: `f* = Σ⁻¹ × μ` (inverse covariance matrix times expected return vector). This is the Kelly-optimal portfolio weights vector.

**Full Kelly vs. Fractional Kelly**

| Version | Growth Rate | Drawdown Risk | Practical Use |
|---|---|---|---|
| Full Kelly (1.0×) | Maximum theoretical | Severe — 50% drawdowns are common | Academic/theoretical only |
| Half Kelly (0.5×) | ~75% of max growth | ~50% of full Kelly drawdown | Aggressive but usable |
| Quarter Kelly (0.25×) | ~56% of max growth | ~25% of full Kelly drawdown | Conservative institutional standard |

**Critical Practical Issues:**
- Kelly is extremely sensitive to the accuracy of estimated `p` and `R`. If you overestimate your edge, Kelly will oversize and create ruin risk.
- Parameters are estimated with error. Any estimation error pushes you above full Kelly, increasing drawdowns.
- Professional practice: **use ¼ to ½ Kelly** as a hard cap. Some prop shops cap at ¼ Kelly regardless of signal strength.
- Kelly portfolios maximize geometric growth but are NOT mean-variance efficient and have higher variance than Markowitz tangent portfolios.

---

### 2.2 Fixed Fractional Position Sizing

Simple, robust, and widely used. Risk a fixed percentage of equity per trade.

```
Position Size = (Account Equity × Risk%) / (Entry Price - Stop Loss Price)
```

**Example:** $500,000 account, 1% risk per trade, entry at $100, stop at $97:
```
Position = ($500,000 × 0.01) / ($100 - $97) = $5,000 / $3 = 1,667 shares
```

**Standard risk parameters:**
- Aggressive: 2–3% per trade
- Moderate: 0.5–1% per trade
- Conservative: 0.1–0.25% per trade

Advantage: Simple, prevents ruin, automatically scales positions as equity grows/shrinks.

---

### 2.3 Risk Parity

**Concept:** Allocate capital so that each asset/strategy contributes *equally* to total portfolio risk, rather than equal dollar amounts.

**Marginal Risk Contribution:**
```
MRC_i = w_i × (Σw)_i / σ_p
```
Where `w_i` is weight, `Σ` is covariance matrix, `σ_p` is portfolio volatility.

**Equal Risk Contribution (ERC) condition:**
```
w_i × (Σw)_i = w_j × (Σw)_j  ∀ i,j
```

**Practical implication:** In a stock/bond portfolio, equities carry ~3–4× the volatility of bonds, so risk parity dramatically overweights bonds vs. a traditional 60/40 allocation. The benchmark is Bridgewater's All-Weather fund.

**Inverse Volatility Weighting** (simplified risk parity, ignoring correlations):
```
w_i = (1/σ_i) / Σ(1/σ_j)
```

---

### 2.4 Mean-Variance Optimization (Markowitz, 1952)

**Objective:** Find the portfolio weight vector `w` that minimizes variance for a given level of expected return, or equivalently, maximizes expected return for a given level of risk.

**Optimization problem:**
```
Minimize: w' Σ w
Subject to: w' μ = μ_target
            w' 1 = 1
            (optionally: w_i ≥ 0 for long-only)
```

**The Efficient Frontier** traces all optimal risk/return combinations. Any portfolio below the frontier is suboptimal.

**Practical limitations:**
- Highly sensitive to expected return estimates (garbage-in, garbage-out)
- Produces unstable, concentrated portfolios with small parameter changes
- Covariance estimates are noisy; use shrinkage methods (Ledoit-Wolf) to stabilize
- Real-world: apply constraints (max weight 10–20%, minimum diversification, turnover limits)

---

### 2.5 Maximum Sharpe Ratio Portfolio (Tangent Portfolio)

Where the Capital Market Line is tangent to the efficient frontier.

**Formula (unconstrained):**
```
w* = Σ⁻¹(μ - r_f × 1) / [1' Σ⁻¹(μ - r_f × 1)]
```

This gives the weights of the tangent portfolio. Combined with the risk-free asset, any point on the CML is achievable through leverage.

**Key insight:** The maximum Sharpe portfolio is the same as the Kelly-optimal portfolio when Kelly is applied to the multi-asset case using the log-utility framework. Both boil down to `Σ⁻¹μ` (scaled).

---

### 2.6 Practical Portfolio Construction Rules

**Concentration Limits:**
- Single position: typically 2–10% max (systematic equity), or higher for concentrated/conviction-based strategies
- Sector/factor: typically 25–30% max
- Correlated clusters: treat high-correlation positions as a single risk unit

**Correlation Awareness:**
- Build a correlation matrix and monitor it dynamically (correlations spike in crises)
- Cluster analysis (hierarchical risk parity / HRP): uses hierarchical clustering before optimization to produce more stable weights

**Hierarchical Risk Parity (HRP):**
Modern alternative to MVO by Lopez de Prado. Uses machine learning (hierarchical clustering) to allocate risk without inverting the covariance matrix (which amplifies estimation error). More robust OOS than classic MVO.

**Turnover and Rebalancing:**
- High turnover destroys net returns via transaction costs
- Optimal rebalancing: trade only when position drifts beyond a tolerance band (e.g., ±20% of target weight), not on a fixed calendar

**Leverage Constraints:**
- Gross leverage (sum of absolute weights): typically 1–2× for systematic equity strategies
- Net leverage: typically -0.5 to +1.5×

---

```
CONFIDENCE: High
SOURCES/REASONING: Kelly Criterion Wikipedia, academic papers (arxiv
1710.00431, 2508.16598), Alpha Theory blog, Bocconi BSIC analysis,
Frontiers in Applied Mathematics (2020), and multiple practitioner sources.
Markowitz and risk parity are foundational and well-documented.
RECOMMENDED NEXT STEPS:
- Implement Ledoit-Wolf covariance shrinkage for all MVO applications
- Test HRP vs. MVO vs. equal weight OOS on your target asset universe
- Build a dynamic Kelly calculator that updates estimates rolling on the
  last 252 trading days; cap output at ½ Kelly
```

---
---

```
