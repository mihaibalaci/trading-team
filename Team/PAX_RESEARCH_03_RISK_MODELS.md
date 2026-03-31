# Pax Research Report
**Researcher:** Pax | **Date:** 2026-03-28 | **Commissioned by:** Clio gap analysis

---

TOPIC: Risk Management Models — VaR, CVaR, Drawdown Control
OBJECTIVE: Covers the three VaR methodologies, CVaR/Expected Shortfall
and why it supersedes VaR, maximum drawdown calculation and controls,
portfolio-level circuit breakers, volatility targeting, and stress testing.
```

---

## FINDINGS

### 3.1 Value at Risk (VaR)

**Definition:** VaR answers: "What is the maximum loss I will NOT exceed with X% confidence over a Y-period horizon?"

**Formal statement:** VaR at confidence level α over horizon T is the loss level L such that `P(Loss > L) = 1 - α`.

Common conventions: 95% or 99% confidence; 1-day, 10-day, or 1-year horizons.

---

**Method 1: Parametric VaR (Variance-Covariance)**
Assumes normally distributed returns.

```
VaR = Portfolio Value × z_α × σ_daily
```

Where:
- `z_α` = 1.645 for 95% confidence; 2.326 for 99%
- `σ_daily` = daily portfolio volatility (annualized vol / √252)

To scale across time horizons: `VaR_T = VaR_1day × √T` (valid only under i.i.d. normal returns — use with caution beyond 10 days).

**Pros:** Fast, analytically tractable, easy to decompose by asset.
**Cons:** Assumes normality (underestimates tail risk); poor for options, nonlinear instruments, or fat-tailed return distributions.

---

**Method 2: Historical Simulation**
Take the actual historical P&L series, sort from worst to best loss, and read off the percentile.

For 95% VaR with 500 days of history: sort losses, the 25th worst loss (500 × 5%) is the VaR estimate.

**Pros:** No distributional assumption; captures fat tails, skew, and kurtosis from history.
**Cons:** Completely dependent on the lookback window. A low-vol lookback underestimates risk; a high-vol lookback overestimates it. The 2008 crisis would have been invisible in a 2006 lookback.

---

**Method 3: Monte Carlo Simulation**
Simulate thousands (or millions) of portfolio paths under assumed (or empirically calibrated) distributions and correlations. Read the VaR from the simulated P&L distribution.

- **Parametric MC:** Simulate correlated normal random variables with estimated μ and Σ.
- **Full valuation MC:** Re-price all instruments at each simulated scenario (critical for nonlinear instruments like options).
- **Student-t or skewed distributions:** Better tail capture than normal.

**Pros:** Most flexible; handles nonlinearity and fat tails; can incorporate regime changes.
**Cons:** Computationally expensive; sensitive to correlation assumptions; "GIGO" — the output quality depends entirely on model inputs.

---

### 3.2 Conditional VaR (CVaR) / Expected Shortfall (ES)

**Definition:** CVaR is the expected loss *given that losses exceed VaR*. It answers: "Given that we have a bad day, how bad is it on average?"

```
CVaR_α = E[Loss | Loss > VaR_α]
       = (1 / (1-α)) × ∫_{VaR_α}^{∞} L × f(L) dL
```

For discrete historical simulation:
```
CVaR_α = (1 / n_tail) × Σ Loss_i  [for all losses > VaR]
```

**Why CVaR is Superior to VaR:**

| Property | VaR | CVaR |
|---|---|---|
| Tells you about losses beyond the threshold | No | Yes |
| Sub-additive (diversification reduces risk) | Not always | Always |
| Coherent risk measure (satisfies all four axioms) | No | Yes |
| Convex (easier to optimize) | No | Yes |
| Regulatory adoption | Basel II legacy | Basel III/FRTB standard |

VaR can be sub-additive (two positions combined can have higher VaR than the sum of their individual VaRs — the opposite of diversification), violating the core principle that portfolios should be less risky than individual positions.

**Regulatory context:** Under Basel III's Fundamental Review of the Trading Book (FRTB), banks must now report Expected Shortfall at 97.5% confidence rather than VaR at 99%. This is a mandatory regulatory shift away from VaR.

---

### 3.3 Maximum Drawdown Calculation and Control

**Definition:** Maximum Drawdown (MDD) is the peak-to-trough decline in portfolio value from a historical high.

```
MDD = (Trough Value - Peak Value) / Peak Value
```

More formally:
```
MDD = max_{t ∈ [0,T]} [max_{τ ∈ [0,t]} V(τ) - V(t)] / max_{τ ∈ [0,t]} V(τ)
```

**Calmar Ratio:** `CAGR / |MDD|` — measures return per unit of maximum drawdown. Target: > 1.0.

**Related metrics:**
- **Time to Recovery:** How long to recover from the MDD to a new high. Longer = worse.
- **Average Drawdown:** Mean of all individual drawdown episodes.

---

### 3.4 Drawdown-Based Stop Rules (Circuit Breakers)

**Portfolio-level circuit breakers — standard practice:**

| Trigger Level | Action |
|---|---|
| 5% drawdown from HWM | Review — analyze P&L attribution, check for model degradation |
| 10% drawdown from HWM | Reduce risk by 50% — cut gross exposure |
| 15% drawdown from HWM | Liquidate to flat — cease all new position-taking |
| 20%+ drawdown from HWM | Full stop — escalate to risk committee, capital review |

**Strategy-level circuit breakers:**
- If a single strategy's rolling 30-day Sharpe drops below -1.0, halt that strategy.
- If strategy P&L exceeds 3× expected daily VaR loss, investigate before continuing.
- Track daily P&L vs. expected distribution. Consecutive 2-sigma loss days are a warning signal.

**Time-based stops:** In addition to loss-based stops, enforce a mandatory review period (e.g., 2 weeks flat) to assess whether market regime has changed before re-entering.

---

### 3.5 Volatility Targeting

Volatility targeting dynamically scales position sizes to maintain a constant portfolio volatility.

```
Position Scale Factor = Target Vol / Realized Vol
Scaled Position = Base Position × (σ_target / σ_realized)
```

**Example:** Target portfolio vol = 10% annualized. Current estimated vol (21-day rolling) = 20%.
Scale factor = 10% / 20% = 0.5. Cut all positions in half.

**Implementation details:**
- Use exponentially weighted volatility (EWMA) rather than simple rolling window: more responsive to recent vol spikes.
- EWMA: `σ²_t = λ × σ²_(t-1) + (1-λ) × r²_t` where λ ≈ 0.94 (RiskMetrics standard)
- Lag effects: vol estimates look back; they lag actual vol spikes. Build in a safety buffer (multiply realized vol estimate by 1.25 as a conservative adjustment).

**Benefits:** Automatically reduces size in high-vol regimes (when risk is elevated) and increases size in low-vol regimes. Improves risk-adjusted returns significantly for trend-following strategies.

---

### 3.6 Stress Testing and Scenario Analysis

**Historical Scenarios (must-have):**
- 2008 Global Financial Crisis (peak equity drawdown: -55%, vol spike 5×)
- March 2020 COVID crash (-34% in 23 trading days)
- 1987 Black Monday (-22.6% single day)
- 1998 LTCM collapse (correlation breakdown in FI and EM)
- 2022 rate shock (bond/equity correlation reversal)

**Hypothetical Scenarios:**
- Equity markets drop 20% in one week
- VIX spikes from 15 to 40 overnight
- Correlation between assets flips sign (risk-off regime switch)
- Credit spreads widen 200 bps
- Liquidity crisis: bid-ask spreads widen 5× and market impact costs triple

**Reverse Stress Testing:** Start with a defined loss threshold (e.g., -15% portfolio loss) and work backwards to identify what combination of market moves would cause it. More useful than forward scenarios for identifying hidden vulnerabilities.

---

```
CONFIDENCE: High
SOURCES/REASONING: Wikipedia (VaR, Expected Shortfall), analystprep.com
CFA Level II notes, quantinsti.com CVaR blog, Ryan O'Connell CFA
tutorials, Man Group institutional research on Expected Shortfall, Basel
III FRTB regulatory documents, PastPaperHero CFA Level 3 notes. All core
formulas validated across multiple independent sources.
RECOMMENDED NEXT STEPS:
- Replace all VaR limits with CVaR limits in the risk framework
- Build a real-time volatility targeting engine using EWMA vol estimates
- Implement an automated daily drawdown-from-HWM monitor with tiered
  alert notifications linked to position sizing rules
- Run the 2022 rate shock scenario against current book immediately —
  bond/equity correlation breakdown is the most underappreciated risk
```

---
---

```
