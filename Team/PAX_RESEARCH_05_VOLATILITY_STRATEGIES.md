# Pax Research Report
**Researcher:** Pax | **Date:** 2026-03-28 | **Commissioned by:** Clio gap analysis

---

TOPIC: Volatility-Based Trading Strategies
OBJECTIVE: Covers VIX mechanics and interpretation, implied vs.
realized volatility and the volatility risk premium, core volatility
trading strategies, regime detection, inverse-vol position sizing,
and ATR as a practical vol measure.
```

---

## FINDINGS

### 5.1 VIX: What It Is, How It's Calculated, What Levels Mean

**VIX (CBOE Volatility Index)** measures the market's expectation of 30-day annualized volatility of the S&P 500, derived from real-time SPX options prices. It is often called the "fear gauge."

**Calculation Methodology (CBOE official):**
VIX is model-free — it does not rely on Black-Scholes. It directly aggregates variance information from options prices across a wide range of strikes and two consecutive expirations:

```
VIX² = (2/T) × Σ [ΔK_i / K_i²] × e^(rT) × Q(K_i) - (1/T) × [F/K_0 - 1]²
```

Where:
- `T` = time to expiration
- `K_i` = strike price of i-th option
- `ΔK_i` = interval between strikes
- `Q(K_i)` = midpoint of bid-ask spread for option at K_i
- `F` = forward price (from put-call parity)
- `r` = risk-free rate

Two expirations (23–37 calendar days) are used and interpolated to give exactly 30-day implied variance.

**VIX Level Interpretation:**

| VIX Level | Regime | Implication |
|---|---|---|
| < 12 | Ultra-low vol | Complacency; often precedes volatility spikes |
| 12–16 | Low vol | "Normal" calm market conditions |
| 16–20 | Moderate | Mild uncertainty |
| 20–25 | Elevated | Risk-off sentiment growing |
| 25–30 | High | Significant market stress |
| 30–40 | Very high | Acute stress, recession fears or geopolitical crisis |
| > 40 | Extreme | Crisis conditions (COVID peak = 85.47 in March 2020; GFC peak = 89.53 in Oct 2008) |

**VIX Term Structure:** The VIX futures curve shows expected future volatility. In normal (contango) conditions: front-month VIX futures < second-month < third-month. In stressed (backwardation) conditions: spot VIX > front-month futures (market expects vol to fall). The VIX1D (1-day VIX) and VIX9D (9-day) were introduced by CBOE to complement the standard 30-day VIX.

---

### 5.2 Implied vs. Realized Volatility — The Volatility Risk Premium

**Implied Volatility (IV):** Forward-looking expectation of vol extracted from options prices. It is what the options market is *pricing in* for future vol.

**Realized Volatility (RV):** Actual historical volatility, typically computed as the annualized standard deviation of daily log returns over a trailing window (21-day for 1-month).

```
RV_21 = √(252/21) × √[Σ r_t²]   (zero-mean assumption common for daily)
```

**Volatility Risk Premium (VRP):**
```
VRP = IV - RV_realized (over same subsequent period)
```

**The VRP is consistently positive:** Implied volatility exceeds subsequent realized volatility the vast majority of the time. This is the empirical basis for short-vol strategies.

Quantitatively: VIX has exceeded the subsequent 21-day realized vol approximately 70–80% of the time historically. The average VRP has been approximately 2–5 volatility points (e.g., VIX = 18, realized = 13–15).

**Why the VRP exists:** Investors pay an insurance premium above actuarial cost to own options as portfolio protection. Option sellers (who bear left-tail risk) earn this premium systematically — but suffer severely during volatility spikes.

---

### 5.3 Volatility Trading Strategies

**Short Volatility Strategies (harvest the VRP)**

| Strategy | Mechanism | Risk Profile |
|---|---|---|
| Short straddle | Sell ATM call + ATM put | Unlimited loss if large move |
| Short strangle | Sell OTM call + OTM put | Unlimited loss with buffer zone |
| Iron condor | Short strangle + long wing protection | Capped loss; lower premium |
| Cash-secured put | Sell puts, secured by cash | Long exposure with bounded loss |
| Short VIX futures | Sell VIX futures front month | Earns contango roll; blows up in crises |
| Short variance swap | Sell variance at implied, receive realized | Pure VRP play; mark-to-market vol exposure |

**Critical risk:** Short-vol strategies exhibit negative skewness and large kurtosis (fat left tail). A single vol spike event can wipe out months or years of premium income. The XIV ETN (short VIX) lost over 90% of its value in a single day in February 2018 ("Volmageddon").

**Long Volatility Strategies (tail protection / vol directional)**

| Strategy | Mechanism |
|---|---|
| Long straddle/strangle | Buy ATM/OTM options; profits from large moves |
| Long VIX calls | Direct bet on vol spike |
| Tail risk funds | Long OTM puts as portfolio insurance |
| Variance swap long | Pay implied variance, receive realized; profits when realized > IV |
| Gamma scalping | Long gamma (long straddle), delta-hedge dynamically; profits from realized > IV |

**Long vol strategies** lose money in calm markets (theta decay) but protect in crises. They have positive skew. Allocating 2–5% of portfolio to a long-vol overlay can significantly smooth drawdowns.

**Volatility Mean Reversion Strategies**
VIX mean-reverts strongly. When VIX spikes above 30, it tends to revert toward 15–20 within 30–60 days under normal market conditions (not extended crises). Strategies:
- Calendar spreads: short near-term, long further-dated vol when term structure is inverted (backwardation)
- VIX contango roll: sell front-month VIX futures (earn roll yield as futures converge to lower spot)

---

### 5.4 Volatility Regime Detection

**Two-State Hidden Markov Model (HMM):**
The most common approach. Estimates two latent states (low-vol regime and high-vol regime) with transition probabilities. Each state has a different mean and variance of returns.

**Practical threshold-based approach:**

| Signal | Low Vol Regime | High Vol Regime |
|---|---|---|
| VIX level | < 20 | > 25 |
| 21-day realized vol | < 12% annualized | > 20% annualized |
| VIX term structure | Contango (normal) | Backwardation (inverted) |
| Cross-asset correlation | Low (risk-on) | High (risk-off, everything correlates to 1) |

**Research finding (Redalyc study):** Two distinct VIX regimes are statistically identifiable, with the high-vol regime characterized by much higher variance of the VIX index itself. The transition from low to high regime is fast (1–3 days); transition back is slow (weeks to months).

**Regime-dependent strategy allocation:**
- Low-vol regime: deploy short-vol strategies, increase position sizes (inverse vol)
- High-vol regime: reduce positions, run long-vol protection, widen stops

---

### 5.5 Using Volatility for Position Sizing (Inverse Vol Weighting)

**Core principle:** Size positions inversely proportional to their volatility so that each position contributes equally to portfolio risk.

```
Position Size_i = (Target Risk $ / Asset Vol_i)
```

Or, expressed as portfolio weight:
```
w_i = (Target Vol / σ_i) / Σ (Target Vol / σ_j)
```

**Dynamic vol-scaling:** Update position sizes when realized volatility changes. If an asset's 21-day vol doubles, halve the position.

**VIX-based portfolio scaling (macro overlay):**
```
Scale Factor = Target VIX / Current VIX
Overall Portfolio Size = Base Size × (15 / Current VIX)
```

Example: Target VIX = 15. Current VIX = 30. Scale portfolio to 50% of base size.

2025 research (arxiv:2508.16598) found that a hybrid Kelly + VIX scaling approach outperforms either method alone, particularly excelling at drawdown control in low-vol conditions (2024 market regime).

---

### 5.6 ATR (Average True Range) as a Practical Volatility Measure

**Definition:** Developed by J. Welles Wilder (1978). Measures the average of the *True Range* over N periods (typically 14).

**True Range (TR):**
```
TR = max(High - Low, |High - Previous Close|, |Low - Previous Close|)
```

The second and third terms capture gaps — moves that occur between sessions.

**ATR Calculation:**
Initial ATR: simple average of TR over first N periods.
Subsequent: `ATR_t = [(N-1) × ATR_(t-1) + TR_t] / N` (Wilder's smoothing, equivalent to EMA with α = 1/N)

**ATR-Based Position Sizing (Van Tharp / Turtle Traders method):**
```
Position Size = (Account × Risk%) / (ATR × Multiplier)
```

Example with $500,000 account:
- Risk per trade: 1% = $5,000
- ATR = $4.00 per share, Multiplier = 2.0
- Position = $5,000 / (4.00 × 2.0) = 625 shares

**Why ATR works:** It automatically adjusts position size to current market volatility without requiring calibration to specific price levels. High-ATR assets get smaller positions; low-ATR assets get larger positions. This keeps risk per trade consistent in dollar terms.

**ATR Applications:**
- Stop placement: `Stop = Entry ± (2× ATR)` — a dynamic stop that adjusts to volatility
- Profit target: `Target = Entry ± (3× ATR)` — gives 1.5:1 R/R at 2ATR stop
- Breakout filters: Only take breakouts when daily range > 1.5× ATR (genuine vol expansion)

---

```
CONFIDENCE: High
SOURCES/REASONING: CBOE official VIX methodology PDF, VIX Wikipedia,
macroption.com VIX calculation deep dive, Quantpedia VRP effect,
arxiv:2508.16598 (Kelly + VIX hybrid sizing), CBOE S&P Global VIX
practitioners guide, Redalyc VIX regime research, tastytrade VIX
education, Wikipedia ATR, ChartSchool StockCharts ATR reference,
Holaprime ATR position sizing guide, Long-Tail Alpha short vol research.
VIX calculation methodology verified against official CBOE publications.
RECOMMENDED NEXT STEPS:
- Build a VIX regime indicator (below/above 20, term structure shape) and
  link it to your position sizing rules as a live macro overlay
- Calculate the VRP daily on your primary market (SPX): VIX minus 21-day
  realized vol. Track its distribution to assess current short-vol
  attractiveness
- Implement ATR-based stops as the default stop discipline for all
  discretionary and systematic equity strategies — eliminates arbitrary
  fixed-dollar stops
```

---
---

```
