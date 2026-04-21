# IMC Prosperity 4 — Team Manish

![IMC Prosperity 4 — Team Manish](media-kit.png)

> Algorithmic trading competition · April 14–20, 2026  
> Final rank: **#4479 Overall** · #5065 Algorithmic · #1741 Manual · #1211 Country  
> Team total: **1,52,515 SeaShells** (76% of 2,00,000)

---

## Leaderboard

![Leaderboard](leaderboard.png)

---

## Repository Structure

```
.
├── ROUND1.py                        # Round 1 — fixed fair MM + aggressive trend buyer
├── ROUND2.py                        # Round 2 — EMA fair value + adaptive MM
├── data/
│   ├── prices_round_{1,2}_day_{-2,-1,0,1}.csv
│   └── trades_round_{1,2}_day_{-2,-1,0,1}.csv
└── README.md
```

---

## Market Microstructure Analysis

All signals were derived empirically from historical price and trade data (Rounds 1–2, Days -2 to +1).

### `ASH_COATED_OSMIUM` — Mean-Reverting Market

| Metric | Value |
|--------|-------|
| True fair value | **10,000** (stationary across all 6 observed days) |
| Bid-ask spread | **16 ticks** (mean=16.2, median=16 — mechanically constant) |
| Mid-price std dev | **3.8 – 5.2 ticks** around fair |
| Mid-price range | 9,979 – 10,026 (46-tick total range) |
| Trade frequency | ~430–470 trades/day |
| Avg trade size | ~5.1 units |
| Daily volume | ~2,200 – 2,400 units |

**Key finding:** ASH is a textbook mean-reverting instrument. The mid price is bounded tightly around 10,000 with Gaussian noise (σ ≈ 4–5 ticks). The 16-tick spread is mechanical and constant — consistent with a single market maker operating on a fixed quote schedule. Pure market-making is the dominant alpha source.

### `INTARIAN_PEPPER_ROOT` — Deterministic Linear Trend

| Metric | Value |
|--------|-------|
| Daily trend | **+1,000 ticks/day** (observed: +998 to +1,003 across all 6 days) |
| Intraday std dev | **~289 ticks** (linear drift masked by noise) |
| Spread | ~14–16 ticks |
| Trade frequency | ~330–335 trades/day |
| Avg trade size | ~5.1 units |
| Daily volume | ~1,670 – 1,760 units |
| Price path (R1) | Day -2: 9,998→11,002 · Day -1: 10,998→11,998 · Day 0: 11,998→13,000 |
| Price path (R2) | Day -1: 11,002→12,000 · Day 0: 11,998→13,000 · Day 1: 13,000→14,000 |

**Key finding:** Pepper is not a noisy trend — it is a **deterministic ramp** of exactly +1,000 ticks per trading day without exception across all 6 observed days. The intraday path is a linear drift with additive Gaussian noise (σ ≈ 289 ticks). This renders EMA crossover or z-score strategies suboptimal; the correct approach is simply holding maximum long from open to close.

---

## Strategy Design

### Round 1 — `ROUND1.py`

Exploits the above structural properties directly.

#### `INTARIAN_PEPPER_ROOT` — Maximum Long, Hold

```
Signal:  Unconditional positive drift (+1000 ticks/day, deterministic)
Entry:   Sweep all ask levels (up to 3 price levels) at market open
Sizing:  Fill to position limit (80 units)
Exit:    None — hold full position throughout the day
Passive: Post bid 2 ticks above best bid to catch any residual capacity
```

No trend detection, no z-score, no EMA. The edge is structural: every unit held earns approximately 1,000 ticks of P&L per day. Position limit is the binding constraint.

#### `ASH_COATED_OSMIUM` — Tiered Market Making

```
Fair value:  10,000 (fixed anchor — EMA drift destroys edge)
Strategy:    Two-layer passive market making with inventory skew

Layer 1 — Aggressive taker:
  Buy any ask < 10,000 (guaranteed positive edge)
  Sell any bid > 10,000 (guaranteed positive edge)

Layer 2 — Tiered passive quotes:
  Inner quote (70% capacity):  best_bid+1 / best_ask-1, skewed by inventory
  Outer quote (30% capacity):  fair-3 / fair+3, skewed by inventory

Inventory skew formula (nonlinear):
  skew = sign(pos) x round(9 x sqrt(|pos| / limit))

  Comparison vs linear skew (7 x pos/limit):
  pos=10  -> linear=1.4,  sqrt=2.8  (stronger at moderate positions)
  pos=25  -> linear=3.5,  sqrt=4.5
  pos=40  -> linear=5.6,  sqrt=5.7
  pos=50  -> linear=7.0,  sqrt=9.0  (much stronger at limits)

Emergency unwind:  triggered at |pos| > 35 (70% of 50-unit limit)
  Forces ask <= fair-1 (if long) or bid >= fair+1 (if short)
```

**Why fixed fair=10,000?** Across 6 observed days, the unconditional mean of ASH mid-price is: 9,998 / 10,001 / 10,002 / 10,001 / 10,002 / 10,000. An EMA of this process lags and introduces noise into quote anchoring, reducing fill quality. The stationary mean is the theoretically correct fair value.

---

### Round 2 — `ROUND2.py`

Introduces adaptive fair value estimation and multi-signal logic. More general framework at the cost of some Round 1 edge simplicity.

#### Architecture

```python
mid_price -> update_history() -> EMA(span) -> fair_value
                              -> zscore(window) -> signal
```

| Component | Description |
|-----------|-------------|
| `take_liquidity()` | Sweeps all levels with edge > threshold vs EMA fair |
| `make_market()` | Posts bid/ask offset from fair; inventory-skewed |
| `trader_data` | Serialises last 60 price ticks — reconstructs history across iterations |

#### `ASH_COATED_OSMIUM`

```
Fair:        EMA-15 of mid price (converges to ~10,000 within ~30 ticks)
Taker edge:  4 ticks vs EMA fair
MM offset:   +/-2 ticks from fair (inventory-skewed)
Z-score:     Aggressive market order at |z| > 1.8 (window=40), size=20
```

#### `INTARIAN_PEPPER_ROOT`

```
Fair:        EMA-20 of mid price (tracks the +1000/day drift)
Trend:       Fast EMA-5 vs Slow EMA-30 crossover; enter if |fast-slow| > 4
Z-score:     Mean-reversion signal at |z| > 2.5 (window=40), size=15
Taker edge:  3 ticks vs EMA fair
MM offset:   +/-2 ticks from fair (inventory-skewed)
```

> **Note:** The R2 trend-following logic on Pepper is theoretically sound but redundant given the instrument's deterministic drift. The EMA crossover will always signal long after sufficient warmup (~10 ticks), reproducing R1 behaviour with additional latency.

---

## Position Limits

| Product | Round 1 | Round 2 |
|---------|---------|---------|
| `INTARIAN_PEPPER_ROOT` | **80** | 50 |
| `ASH_COATED_OSMIUM` | 50 | 50 |

> Round 1 uses limit=80 for Pepper — correctly sized to maximise exposure to the deterministic +1,000/day drift. Round 2 reduces this to 50, leaving significant edge on the table.

---

## Dependencies

Runs inside the IMC Prosperity sandbox. No external dependencies.

```python
from datamodel import Order, OrderDepth, TradingState  # Provided by sandbox
# stdlib only: json, statistics, math
```

---

## Backtesting

```bash
pip install prosperity2bt

prosperity2bt ROUND1.py 0    # Day 0 backtest
prosperity2bt ROUND2.py 1    # Day 1 backtest
```

Community backtester: [jmerle/imc-prosperity-2-backtester](https://github.com/jmerle/imc-prosperity-2-backtester)

---

## Key Takeaways

| Finding | Implication |
|---------|-------------|
| Pepper drift = exactly +1,000 ticks/day across all 6 days | Max-long from open is optimal; no signal needed |
| ASH fair value = 10,000, stationary across all days | Fixed anchor outperforms adaptive EMA for quoting |
| ASH spread = 16 ticks, mechanically constant | MM edge is stable; inner quotes at +/-1 tick reliably fill |
| Nonlinear inventory skew (sqrt) > linear | Stronger protection at extremes; reduces position lock-up risk |
| R1 limit=80 for Pepper vs R2 limit=50 | Higher limit directly scales P&L on a trend instrument |

---

*IMC Prosperity 4 · Mission Start: April 14, 2026 · Mission End: April 20, 2026*
