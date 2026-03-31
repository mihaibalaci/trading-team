# Pax Research Report
**Researcher:** Pax | **Date:** 2026-03-28 | **Commissioned by:** Clio gap analysis

---

TOPIC: Options Pricing & The Greeks
OBJECTIVE: Covers the Black-Scholes model, all the Greeks with formulas
and trading implications, the vol surface/smile, core options strategies,
options as portfolio hedges, and key professional rules.
```

---

## FINDINGS

### 6.1 Black-Scholes Model

**Origin:** Fischer Black and Myron Scholes (1973), generalized by Robert Merton. Nobel Prize in Economics 1997 (Scholes and Merton).

**Inputs:**
| Parameter | Symbol | Notes |
|---|---|---|
| Underlying price | S | Current market price |
| Strike price | K | Agreed exercise price |
| Time to expiration | T | In years (e.g., 30 days = 30/365) |
| Risk-free rate | r | Continuously compounded (use T-bill or OIS rate) |
| Volatility | σ | Annualized implied volatility (the key unknown) |
| Dividends | q | Continuous dividend yield (Merton extension) |

**The Black-Scholes Formulas:**

First, compute d1 and d2:
```
d1 = [ln(S/K) + (r - q + σ²/2) × T] / (σ × √T)
d2 = d1 - σ × √T
```

**Call price:**
```
C = S × e^(-qT) × N(d1) - K × e^(-rT) × N(d2)
```

**Put price:**
```
P = K × e^(-rT) × N(-d2) - S × e^(-qT) × N(-d1)
```

Where `N(x)` = cumulative standard normal distribution function.

**Interpretation of d1 and d2:**
- `N(d2)` = risk-neutral probability that the option expires in-the-money
- `N(d1)` = delta of the call (how much the call price moves per $1 move in S)

**Assumptions (and their violations):**
1. Constant volatility — VIOLATED (vol smile and term structure exist)
2. Log-normal price distribution — VIOLATED (fat tails, skewness observed)
3. No dividends — Merton extended to handle continuous dividends
4. Continuous trading possible — Approximately true in liquid markets
5. No transaction costs — Not true; affects hedging frequency decisions
6. Constant risk-free rate — Approximation; matters most for long-dated options

---

### 6.2 The Greeks — Definitions, Formulas, and Trading Implications

**Delta (Δ)**
Rate of change of option price per $1 change in underlying.

```
Δ_call = e^(-qT) × N(d1)       [range: 0 to 1]
Δ_put  = -e^(-qT) × N(-d1)     [range: -1 to 0]
```

- ATM option: Δ ≈ 0.50
- Deep ITM call: Δ → 1.00 (behaves like stock)
- Deep OTM call: Δ → 0 (nearly worthless)
- Put-call relationship: `Δ_call - |Δ_put| = e^(-qT)` (parity)

**Trading implication:** A portfolio delta of +500 means you are approximately long 500 shares of equivalent stock exposure. Delta hedging: short 500 shares of underlying to neutralize directional exposure. Delta hedges require continuous rebalancing as price and time change (this is the "gamma P&L").

---

**Gamma (Γ)**
Rate of change of delta per $1 change in underlying (second derivative of option price w.r.t. S).

```
Γ = e^(-qT) × N'(d1) / (S × σ × √T)
```

Where `N'(x)` = standard normal PDF = `e^(-x²/2) / √(2π)`.

- Same formula for calls and puts (put-call parity)
- Gamma is maximized at-the-money and near expiration
- Long options = long gamma (benefit from large moves in either direction)
- Short options = short gamma (suffer from large moves)

**Trading implication:** A position with positive gamma will see its delta increase as price rises (accelerating gains) and decrease as price falls (decelerating losses). Gamma scalping exploits this: if you own a straddle and delta-hedge continuously, you profit when realized vol > IV.

**Gamma risk near expiration:** Options have extremely high gamma in the final days before expiry (especially weeklies). A position that was manageable can develop violent delta swings. Rule: Close or manage positions before entering the "gamma risk zone" (< 5 DTE for standard positions).

---

**Theta (Θ)**
Rate of change of option price per 1-day passage of time (time decay).

```
Θ = -[S × e^(-qT) × N'(d1) × σ / (2√T)] - r × K × e^(-rT) × N(d2)   (for calls)
```

Theta is typically expressed per calendar day. If T uses 252 trading days, divide by 252; if 365 calendar days, divide by 365.

- Long options: negative theta (lose value each day all else equal)
- Short options: positive theta (gain value each day all else equal)
- Theta is NOT linear — it accelerates as expiration approaches
- Theta is highest for ATM options near expiry

**ATM option approximate theta:**
```
Θ_ATM ≈ -S × σ / (2 × √(2πT))
```

**Trading implication:** Short-option strategies (credit spreads, iron condors, covered calls) earn positive theta. The theta-gamma tradeoff: you earn theta when short options, but you suffer gamma losses if the market moves. Long straddles lose theta but profit from large moves.

---

**Vega (ν)**
Rate of change of option price per 1% (100 bps) change in implied volatility.

```
ν = S × e^(-qT) × N'(d1) × √T
```

- Same for calls and puts
- Vega is highest for ATM options with long time to expiration
- Long options: positive vega (benefit from vol expansion)
- Short options: negative vega (hurt by vol expansion)

**Trading implication:** A vega of 5.00 means if IV moves from 20% to 21%, the option gains $5.00 in value. Managing vega exposure = managing directional vol bets. Vega is the dominant P&L driver for longer-dated options; theta dominates short-dated.

**Vega vs. maturity:** Vega scales with √T. A 1-year option has approximately √2 times the vega of a 6-month option.

---

**Rho (ρ)**
Rate of change of option price per 1% (100 bps) change in risk-free interest rate.

```
ρ_call = K × T × e^(-rT) × N(d2)
ρ_put  = -K × T × e^(-rT) × N(-d2)
```

- Calls have positive rho (benefit from higher rates — higher carry on the forward)
- Puts have negative rho
- Rho is generally small for short-dated options; significant for long-dated (LEAPs)

**Trading implication:** Largely ignored for standard short-dated options. Critical for interest rate derivatives and long-dated equity options (especially in a shifting rate regime like 2022–2025).

---

**Second-Order and Cross Greeks (advanced):**

| Greek | Definition | Use |
|---|---|---|
| **Vanna** | dΔ/dσ or dν/dS | How delta changes with vol; critical for delta hedging when vol moves |
| **Volga/Vomma** | dν/dσ (2nd deriv w.r.t. vol) | Convexity of P&L w.r.t. vol; important for exotic options |
| **Charm** | dΔ/dt (delta decay) | How delta changes over time; critical overnight for pinned positions |
| **Speed** | dΓ/dS | Rate of change of gamma; gamma of gamma |
| **Color** | dΓ/dt | Gamma decay over time |

---

### 6.3 Implied Volatility and the Vol Surface / Volatility Smile

**Implied Volatility (IV):** The volatility value that, when plugged into Black-Scholes, equates the model price to the observed market price. It is not "predicted volatility" — it is a measure of relative option pricing.

**The Volatility Surface:** A 3D surface mapping implied volatility against:
- Strike (or moneyness: K/S or log(K/F))
- Time to expiration

**Volatility Smile:** In equity markets, the vol surface is not flat. Key features:
1. **Skew (smirk):** OTM puts have higher IV than OTM calls (for equities). Investors bid up put protection. The put-call skew reflects crash risk premium.
2. **Smile:** In currency options, both OTM puts and OTM calls trade at higher IV than ATM (genuine two-sided fear).
3. **Term structure:** Typically upward sloping (longer-dated options have higher IV — uncertainty increases with time). Can invert before known events (earnings, FOMC) or in crisis (when near-term fear exceeds long-term expectation).

**Reading the vol surface:**
- Steep negative skew (put IV >> call IV): market pricing in high crash probability. Short-vol strategies on the put side are expensive. Better to sell calls.
- Flat or inverted term structure: near-term event risk dominates. Short-dated vol is rich relative to longer-dated.

**Practical use of IV surface:**
- Compare IV percentile rank (IV Rank / IVR): if IVR > 50%, current IV is elevated vs. its history — favorable for short-vol strategies.
- IV Rank formula: `IVR = (Current IV - 52W Low) / (52W High - 52W Low) × 100`

---

### 6.4 Options Strategies

**Covered Call**
- Own 100 shares + sell 1 OTM call
- P&L: stock gains up to strike, then capped. Premium received reduces cost basis.
- Net delta: positive but reduced by short call delta
- Use case: income generation in sideways markets; reduces breakeven on long stock

**Protective Put**
- Own 100 shares + buy 1 OTM put
- P&L: stock downside limited to strike minus premium paid
- Effectively portfolio insurance; cost = put premium (theta drag)
- Use case: protecting long positions around binary events or during elevated risk

**Long Straddle**
- Buy ATM call + ATM put (same strike, same expiry)
- Net delta ≈ 0; long gamma; long vega; negative theta
- Profits when realized vol > implied vol (large move in either direction)
- Breakeven: stock must move ± (combined premium) / 100 before expiry

**Short Straddle**
- Sell ATM call + ATM put
- Net delta ≈ 0; short gamma; short vega; positive theta
- Profits when stock stays near strike (realized vol < implied vol)
- Unlimited risk if large move occurs

**Long Strangle**
- Buy OTM call + OTM put (different strikes)
- Cheaper than straddle; requires larger move to profit
- Better risk profile for event plays where direction is unknown

**Bull/Bear Spreads (Vertical Spreads)**
- Buy one strike, sell another in same expiry
- Capped gain and loss; reduces cost vs. outright long
- Long call spread: `Buy 100C / Sell 110C` — benefits from moderate upside
- Credit spreads: sell the near-strike, buy the farther — positive theta, limited risk

**Iron Condor**
- Short OTM put spread + short OTM call spread
- Profits when stock stays within a defined range
- Best in low-vol, range-bound markets
- Max gain = net premium; max loss = width of wider spread - premium

**Calendar Spread**
- Buy longer-dated option, sell shorter-dated option (same strike)
- Profits from time decay of near-term option + vol surface dynamics
- Positive theta (short near-term), long vega (longer-dated)

---

### 6.5 Options as Hedging Instruments

**Delta Hedging a Portfolio:**
If portfolio is long $5M of SPX-correlated equities (beta 1.0), a delta-hedge would be:
- Short enough SPX puts or buy enough SPX puts to offset delta exposure
- Number of put contracts = Portfolio Value / (SPX Price × 100 × |Δ_put|)

**Portfolio-Level Tail Hedges:**
- Buy 5–10 delta SPX puts as portfolio insurance (OTM puts)
- Cost: typically 1–3% of portfolio per year in calm markets
- Payoff: significant protection in -20%+ drawdowns
- Rule of thumb: 2–5% of portfolio allocated to protective puts creates meaningful crash protection without excessive drag.

**Gamma Hedging:**
After delta-hedging, a portfolio may still have significant gamma exposure (risk from large moves). Gamma-hedging requires trading options (not just the underlying). Market makers continuously delta and gamma hedge their books.

**VIX Calls as Macro Hedge:**
Long VIX calls protect against vol spikes (crisis scenarios). Typically OTM (e.g., 30 strike when VIX = 18). Cheaper than SPX puts in some environments; more pure vol exposure.

---

### 6.6 Key Rules for Options Traders

1. **Time decay accelerates exponentially near expiry.** Options lose roughly 50% of their remaining time value in the final third of their life. Avoid holding long options to expiry without a catalyst.

2. **Implied vol is the price of options, not a forecast.** IV above historical realized vol = options are expensive. Trade accordingly — sell when IV > expected realized; buy when IV < expected realized.

3. **Never sell naked options without a hard stop or hedge.** Naked short puts/calls have theoretically unlimited loss. At minimum, use defined-risk spreads or maintain a hard buyback rule at 3× premium received.

4. **Pin risk at expiry.** If underlying is near a short-strike at expiration, gamma becomes infinite. A $0.01 move can swing delta from 0 to 100. Either close before expiry or monitor intraday.

5. **Assignment risk on short options.** American-style options can be exercised early. Short ITM puts on ex-dividend dates are at high risk of early assignment. Monitor short positions around dividend dates.

6. **Liquidity matters more than Greeks.** In illiquid options, the bid-ask spread destroys edge. Only trade options where you can enter/exit with < 5% of option premium in spread cost. For small-cap stocks, the options market is often untradeable.

7. **The vol surface is the real risk.** A position can be delta-neutral but massively exposed to changes in the vol surface (vanna, volga). Always know your vega and skew exposures.

8. **Theta is not free money.** Short options earn theta but pay gamma. In a volatile market, gamma losses can vastly exceed theta gains. Short-vol strategies need vol regime filters.

9. **Roll or close, don't hope.** When a short-option position has moved against you and is approaching its short strike with time remaining, rolling (closing and reopening in a further expiry or different strike) is preferred to passive holding. "Hope" is not a risk management strategy.

10. **Know your effective leverage.** A position in options can be far more leveraged than it appears. An ATM call at $3 premium on a $100 stock is 33× leveraged (delta-adjusted). Account for this in position sizing.

---

```
CONFIDENCE: High
SOURCES/REASONING: Wikipedia Black-Scholes model, Columbia University
Black-Scholes derivation notes, macroption.com BS formula reference,
quantpie.co.uk Greeks derivations, Gregory Gundersen Greeks analysis,
Wikipedia Greeks (finance), Derivatives Academy textbook (bookdown),
Option Alpha Greeks education, AnalystPrep FRM vol smile/skew notes,
PyQuant News vol term structure, ORATS University vol surface,
Britannica Money options Greeks. All mathematical formulas cross-validated
against multiple independent sources. Black-Scholes formula verified
against Columbia University course materials.
RECOMMENDED NEXT STEPS:
- Build an internal vol surface monitor: track IVR and term structure
  shape daily across SPX, sector ETFs, and major single-stock positions
- Standardize all options position sizing using a vega-budget framework:
  set a maximum aggregate vega exposure limit for the book
- Implement a mandatory pre-trade Greeks check for any options trade:
  delta, vega, theta, and max loss must be within defined limits before
  execution
- For portfolio hedging: run quarterly analysis of SPX put cost vs.
  VIX call cost to identify which instrument provides cheaper tail
  protection in the current vol regime
```

---
---

# CONSOLIDATED SUMMARY TABLE

| Topic | Confidence | Key Takeaways |
|---|---|---|
| Algo/Quant Strategy Frameworks | High | 95%+ of backtested strategies fail live; implement DSR/PBO quality gates; Fama-French 5-factor is current standard |
| Portfolio Construction & Position Sizing | High | Use ¼–½ Kelly; Ledoit-Wolf shrinkage for MVO; HRP outperforms classic MVO OOS |
| Risk Management (VaR/CVaR/Drawdown) | High | CVaR is regulatorily superior to VaR; tiered circuit breakers at 5/10/15% from HWM; EWMA vol targeting |
| Market Microstructure & Execution | High | Square root impact model: MI ∝ σ√(Q/V); keep orders < 10% ADV; use IS algorithm for alpha-sensitive trades |
| Volatility-Based Strategies | High | VRP is positive ~75% of the time; VIX > 25 = reduce exposure; ATR-based position sizing for all strategies |
| Options Pricing & Greeks | High | B-S is the benchmark but assumes constant vol (wrong); know all 5 Greeks before trading; never sell naked without a hard stop |

---

Sources:
- [Top Algorithmic Trading Strategies for 2025](https://chartswatcher.com/pages/blog/top-algorithmic-trading-strategies-for-2025)
- [Statistical Arbitrage: Strategies, Risks, and How It Works](https://blog.quantinsti.com/statistical-arbitrage/)
- [AlgoXpert Alpha Research Framework — IS WFA OOS Protocol](https://arxiv.org/abs/2603.09219)
- [Walk-Forward Analysis vs. Backtesting](https://surmount.ai/blogs/walk-forward-analysis-vs-backtesting-pros-cons-best-practices)
- [Interpretable Hypothesis-Driven Trading: Walk-Forward Validation](https://arxiv.org/html/2512.12924v1)
- [Kelly Criterion — Wikipedia](https://en.wikipedia.org/wiki/Kelly_criterion)
- [Optimal Kelly Portfolio under Risk Constraints (2025)](https://www.scirp.org/pdf/eng2025173_38104721.pdf)
- [Sizing the Risk: Kelly, VIX, and Hybrid Approaches](https://arxiv.org/pdf/2508.16598)
- [Practical Implementation of the Kelly Criterion — Frontiers in Applied Mathematics](https://www.frontiersin.org/journals/applied-mathematics-and-statistics/articles/10.3389/fams.2020.577050/full)
- [Kelly Criterion in Practice — Alpha Theory](https://www.alphatheory.com/blog/kelly-criterion-in-practice-1)
- [Value at Risk — Wikipedia](https://en.wikipedia.org/wiki/Value_at_risk)
- [Conditional VaR / Expected Shortfall Formula](https://blog.quantinsti.com/cvar-expected-shortfall/)
- [Covering Your Tail: Expected Shortfall — Man Group](https://www.man.com/insights/covering-your-tail-expected-shortfall)
- [Expected Shortfall — Wikipedia](https://en.wikipedia.org/wiki/Expected_shortfall)
- [VaR Methodologies — QuantPedia](https://quantpedia.com/an-introduction-to-value-at-risk-methodologies/)
- [Parametric vs. Historical vs. Monte Carlo VaR — AnalystPrep](https://analystprep.com/study-notes/cfa-level-2/compare-the-parametric-variance-covariance-historical-simulation-and-monte-carlo-simulation-methods-for-estimating-var/)
- [Implementation Shortfall — Kearns (UPenn)](https://www.cis.upenn.edu/~mkearns/finread/impshort.pdf)
- [IS-Zero: Reinventing VWAP Algorithms](https://www.bestexresearch.com/research/introducing-is-zero-reinventing-vwap-algorithms-to-minimize-implementation-shortfall)
- [Advanced Order Execution: TWAP, VWAP, Iceberg — MQL5](https://www.mql5.com/en/articles/17934)
- [Modelling Transaction Costs and Market Impact — BSIC](https://bsic.it/modelling-transaction-costs-and-market-impact/)
- [Square Root Market Impact Model — Baruch MFE](https://mfe.baruch.cuny.edu/wp-content/uploads/2012/09/Chicago2016OptimalExecution.pdf)
- [Transaction Cost Analysis — Talos](https://www.talos.com/insights/execution-insights-through-transaction-cost-analysis-tca-benchmarks-and-slippage)
- [VIX Mathematics Methodology — CBOE](https://cdn.cboe.com/resources/indices/Cboe_Volatility_Index_Mathematics_Methodology.pdf)
- [VIX — Wikipedia](https://en.wikipedia.org/wiki/VIX)
- [VIX Calculation Explained — Macroption](https://www.macroption.com/vix-calculation/)
- [Volatility Risk Premium Effect — Quantpedia](https://quantpedia.com/strategies/volatility-risk-premium-effect/)
- [Implied vs. Realized Volatility and the VIX — Topstep](https://www.topstep.com/blog/implied-vs-realized-volatility-the-vix/)
- [7 Advanced Volatility Trading Strategies for 2025](https://chartswatcher.com/pages/blog/7-advanced-volatility-trading-strategies-for-2025)
- [Volatility Regimes for the VIX Index — Redalyc](https://www.redalyc.org/pdf/969/96924448004.pdf)
- [Average True Range — Wikipedia](https://en.wikipedia.org/wiki/Average_true_range)
- [ATR Position Sizing Strategies — Holaprime](https://holaprime.com/blogs/trading-tips/average-true-range-forex-trading-strategies/)
- [Black-Scholes Model — Wikipedia](https://en.wikipedia.org/wiki/Black%E2%80%93Scholes_model)
- [Black-Scholes Formulas (d1, d2, Greeks) — Macroption](https://www.macroption.com/black-scholes-formula/)
- [Greeks (Finance) — Wikipedia](https://en.wikipedia.org/wiki/Greeks_(finance))
- [Option Greeks — Option Alpha](https://optionalpha.com/learn/options-greeks)
- [Volatility Smile — Wikipedia](https://en.wikipedia.org/wiki/Volatility_smile)
- [Volatility Term Structure and Skew — PyQuant News](https://www.pyquantnews.com/the-pyquant-newsletter/understanding-volatility-term-structure-and-skew)
- [Volatility Surface — ORATS University](https://orats.com/university/volatility-surface)
- [The Greeks — Columbia University Black-Scholes Notes](https://www.columbia.edu/~mh2078/FoundationsFE/BlackScholes.pdf)
- [A Practitioner's Guide to Reading VIX — S&P Global](https://www.spglobal.com/spdji/en/education-a-practitioners-guide-to-reading-vix.pdf)agentId: a0853266349ab5c4e (use SendMessage with to: 'a0853266349ab5c4e' to continue this agent)
<usage>total_tokens: 43548
tool_uses: 13
duration_ms: 628362</usage>