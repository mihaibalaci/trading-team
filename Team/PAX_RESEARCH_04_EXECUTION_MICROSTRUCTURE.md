# Pax Research Report
**Researcher:** Pax | **Date:** 2026-03-28 | **Commissioned by:** Clio gap analysis

---

TOPIC: Market Microstructure & Trade Execution
OBJECTIVE: Covers order book mechanics, all order types, slippage
measurement, market impact models (especially the square root model),
execution algorithms (TWAP, VWAP, POV, IS), TCA, and best practices
for minimizing execution costs.
```

---

## FINDINGS

### 4.1 Order Book Mechanics

The **limit order book (LOB)** is a real-time, continuously updating record of all outstanding buy and sell limit orders. It has two sides:

- **Bid side:** Buyers, ranked highest to lowest price
- **Ask/Offer side:** Sellers, ranked lowest to highest price
- **Best bid:** Highest price any buyer is willing to pay
- **Best ask:** Lowest price any seller is willing to accept
- **Inside spread (NBBO):** `Ask - Bid`
- **Mid-price:** `(Ask + Bid) / 2`

**Depth / Liquidity:** The quantity available at each price level. Shallow books (thin depth) = orders move the market significantly with small size. Deep books (thick depth) = large orders fill with minimal price impact.

**Order flow:** The sequence of market orders consuming liquidity from the book. Order flow imbalance (more buy MOs than sell MOs) is predictive of short-term price direction. This is the foundation of microstructure signals.

**Adverse selection:** Informed traders (those with private information) tend to hit the bid or lift the ask at the optimal moment, causing the market maker to lose money. The bid-ask spread exists partly to compensate for this adverse selection cost.

---

### 4.2 Order Types

| Order Type | Mechanics | Use Case |
|---|---|---|
| **Market Order (MO)** | Executes immediately at best available price | When certainty of fill > certainty of price |
| **Limit Order (LO)** | Executes only at specified price or better; rests in book | When price certainty matters; risk of non-fill |
| **Stop Order** | Becomes a market order when trigger price is hit | Stop-losses; breaks of key levels |
| **Stop-Limit** | Becomes a limit order at trigger; no guarantee of fill | Stops with price protection |
| **TWAP** | Algorithmic: divide order into equal tranches over time | Reducing timing risk evenly across a window |
| **VWAP** | Algorithmic: weight tranches by intraday volume profile | Tracking intraday volume distribution |
| **POV / Inline** | Trade as a % of real-time market volume (e.g., 10% of vol) | Participatory; adapts to actual liquidity |
| **Iceberg Order** | Show only a fraction of total size; auto-replenishes | Hide large size from market participants |
| **Pegged Order** | Dynamically tracks best bid or offer | Used for passive liquidity provision |
| **Reserve Orders** | Similar to iceberg; exchange-native hidden reserve quantity | Equivalent to iceberg on exchange |

---

### 4.3 Slippage: Definition and Measurement

**Slippage** is the difference between the expected execution price and the actual fill price.

**Arrival Price Slippage (most common professional benchmark):**
```
Slippage = (Fill Price - Arrival Price) / Arrival Price × Side
```
Where Side = +1 for buys, -1 for sells. Positive slippage = you paid more than the price when the order was created.

**VWAP Slippage:**
```
Slippage vs. VWAP = (Fill Price - Day VWAP) / VWAP × Side
```

**Implementation Shortfall (IS):**
The most comprehensive TCA benchmark. It measures the difference between the return of the *theoretical* paper portfolio (executed at decision price) and the *actual* implemented portfolio.

```
IS = (Decision Price - Fill Price) × Shares × Side
   = Explicit Costs + Market Impact + Timing Cost + Opportunity Cost
```

- **Explicit Costs:** Commissions, fees, taxes
- **Market Impact:** Price moves caused *by* your own order
- **Timing Cost:** Price moves while order is being worked
- **Opportunity Cost:** Cost of unexecuted shares (filled at worse implied price)

---

### 4.4 Market Impact Models

**The Square Root Model (Empirical Consensus):**
The most widely validated model for market impact of a meta-order (a large order worked over time):

```
MI = σ × η × √(Q / V_daily)
```

Where:
- `MI` = market impact (as fraction of price)
- `σ` = daily volatility of the asset
- `η` = market impact coefficient (typically 0.1 to 1.0; empirical)
- `Q` = total order quantity
- `V_daily` = average daily volume

**Key insight:** Impact scales as the *square root* of participation rate, not linearly. Doubling your order size does not double your impact — it increases it by √2 ≈ 1.41×.

**Linear Impact Model (simpler but less accurate):**
```
MI = λ × Q / V_daily
```
Where `λ` is a linear impact coefficient. Used as an approximation when Q/V is small.

**Practical rule:** For orders < 1% of ADV, linear approximation is acceptable. For orders > 5% ADV, use square root model. For orders > 20% ADV, impact becomes regime-changing and requires careful strategy.

**Temporary vs. Permanent Impact:**
- **Temporary impact:** Immediate price pressure that reverts after order is complete. Captured by bid-ask bounce and short-term volume absorption.
- **Permanent impact:** Lasting price change caused by information content of the order. Informed orders leave permanent impact; uninformed orders revert.

---

### 4.5 Execution Algorithms

**TWAP (Time-Weighted Average Price)**
- Splits order into equal-sized child orders sent at equal time intervals
- Effective when volume profile is unpredictable or you want timing diversification
- Modern TWAP includes timing randomization (±20% of interval) to reduce signal leakage
- Weakness: ignores volume patterns; may trade into illiquid periods

**VWAP (Volume-Weighted Average Price)**
- Weights child orders according to the historical intraday volume profile
- More child orders during high-volume periods (open, close); fewer at midday
- Benchmark: track your fills vs. the day's actual VWAP
- New research (2026): IS-Zero algorithm improves on VWAP by allocating less volume in the typically-expensive open and close periods, shifting execution to mid-day when prices are more stable (21% in first 2 hours vs. VWAP's 28%; 25% in final hour vs. VWAP's 34%)

**POV / Percentage of Volume**
- Participates as a fixed % of real-time market volume (e.g., 10%)
- Automatically adjusts to liquidity — trades more when market is active, less when quiet
- Risk: if market volume spikes (news), algo accelerates and may complete sooner than intended
- Best for: situations where you must trade in sync with the market to avoid front-running detection

**Implementation Shortfall (IS)**
- Explicitly minimizes the total cost vs. arrival price
- Trades faster early (when price is close to decision price) and slows if price has already moved
- Optimal under Almgren-Chriss framework: trades off urgency (timing risk) vs. market impact
- Almgren-Chriss solution: `ξ(t) = X/T × [sinh(κ(T-t)) / sinh(κT)]` — front-loaded execution schedule where κ depends on risk aversion and impact parameters

---

### 4.6 Bid-Ask Spread and Transaction Cost Analysis (TCA)

**Effective Spread:** A more accurate measure than the quoted spread. It captures the actual cost paid.
```
Effective Spread = 2 × |Fill Price - Mid Price|
```

**Realized Spread (market maker's profit):**
```
Realized Spread = 2 × (Fill Price - Mid Price 5 min later) × Side
```

**Price Impact (adverse selection component):**
```
Price Impact = Effective Spread - Realized Spread
```

**Components of TCA:**
1. **Pre-trade analysis:** Expected market impact, benchmark selection (VWAP vs. arrival)
2. **Intraday monitoring:** Fill vs. VWAP/arrival in real time
3. **Post-trade analysis:** Actual vs. expected cost; attribution (explicit, impact, timing, opportunity)

**TCA Best Practices:**
- Never use raw VWAP as sole benchmark — it is backward-looking and biases assessment
- Use arrival price as primary benchmark for alpha-sensitive orders
- Use VWAP as primary benchmark for index rebalancing/passive orders

---

### 4.7 Best Practices for Minimizing Execution Costs

1. **Size relative to ADV:** Keep orders below 5–10% of average daily volume. Above 20% ADV, expect significant market impact.
2. **Trade in high-liquidity windows:** First 30 minutes and last 30 minutes have highest volume but also highest spread and volatility. Midday (10:30–14:00 ET) often has the most stable pricing.
3. **Use limit orders for passive flow:** Posting limit orders adds liquidity and earns the spread (maker rebates on some venues). Only use for non-urgent, non-alpha-sensitive flow.
4. **Randomize timing:** Avoid fixed-interval trading that can be detected and front-run by HFT.
5. **Use dark pools for large orders:** Access dark pool liquidity (crossing networks) to fill large blocks without market impact. Typical dark fill rates: 15–40% of order.
6. **Separate alpha-sensitive and passive flow:** Route urgent alpha-sensitive orders more aggressively (take liquidity); route index/rebalancing flow passively.
7. **Monitor slippage daily:** Any strategy with > 0.1% average slippage on entry + exit is leaking significant alpha. Small-cap and microcap strategies are often unlivable due to execution costs.
8. **Account for borrow costs in short selling:** For short strategies, add estimated borrow rate to transaction cost model before evaluating signal strength.

---

```
CONFIDENCE: High
SOURCES/REASONING: BIS Markets Committee (FX execution algorithms),
Baruch MFE slides on optimal execution and square root law,
bestexresearch.com (IS-Zero research), Talos execution insights TCA blog,
protraderdashboard.com (execution algorithm explanations), QuestDB
execution algorithm glossary, Almgren-Chriss model (industry standard
for IS optimization), BSIC modelling transaction costs. Square root
model is empirically established across multiple academic papers.
RECOMMENDED NEXT STEPS:
- Build a post-trade TCA database: log arrival price, fill price, ADV,
  realized vol for every trade. This dataset is critical for calibrating
  internal market impact parameters.
- Calibrate your η coefficient (square root model) on your own trade
  history — it varies by asset class, market cap tier, and volatility
  regime.
- Evaluate dark pool access for large block executions
```

---
---

```
