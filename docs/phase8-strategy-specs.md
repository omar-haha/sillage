# Phase 8b candidate specifications

These rules are written before either backtest exists. A failed candidate remains in
the research log; parameters are not widened after seeing a result.

The deployment constraint is a **USD 25,000 Canadian IBKR margin account** using whole
contracts or whole ETF shares. A good backtest that cannot be sized or routed in that
account is not a candidate strategy.

## Candidate A — diversified time-series trend

### Hypothesis

Medium-term trends persist across unrelated liquid markets because positioning and
information diffuse gradually. Long and short exposures should therefore diversify the
existing long-only momentum/60-40 blend, especially during sustained equity declines.

### Frozen signal

- Daily settlement data; decisions weekly after Friday's close, or the preceding
  session when Friday is a holiday.
- For each market, signs of trailing 3-, 6- and 12-month excess returns, each skipping
  the most recent five sessions.
- Position direction is the sign of the equally weighted three-signal vote. A tied vote
  is flat.
- Volatility is estimated from the most recent 63 daily returns with finite
  exponentially weighted observations using the standard span convention
  (`alpha = 2 / (63 + 1)`). The same weighting estimates portfolio covariance. Both use
  only data available at the decision time; standalone volatility is floored at 5%
  annualized.
- Equal risk by market, then scale the sleeve to 10% ex-ante volatility. Gross leverage
  is capped at 2.0x before whole-contract rounding.
- No parameter search in round one. The only permitted comparison is single-horizon
  12-month trend versus the frozen three-horizon ensemble, counted as two trials.

### Contract feasibility

The table is a gate, not yet a tradable universe. Margin is broker- and date-dependent
and must be captured from IBKR immediately before paper deployment.

The first account-level check completed on 2026-09-17 using IBKR paper what-if orders.
Every family qualified without a permission warning; no order was transmitted.

| Family | Selected contract | Multiplier | Initial margin | Maintenance | Est. commission |
|---|---|---:|---:|---:|---:|
| NES | NESZ6 | 0.5 | $484.68 | $367.09 | $0.61 |
| NNQ | NNQZ6 | 0.2 | $913.05 | $599.13 | $0.61 |
| N2K | N2KZ6 | 0.5 | $178.35 | $155.09 | $0.61 |
| M6E | M6EZ6 | 12,500 | $512.37 | $445.54 | $0.40 |
| 10Y | 10YV6 | 1,000 | $849.02 | $734.66 | $0.56 |
| 1OZ | 1OZZ6 | 1 | $582.03 | $506.12 | $0.66 |
| MCL | MCLX6 | 100 | $3,026.53 | $2,409.82 | $0.76 |
| MES | MESZ6 | 5 | $4,845.74 | $3,670.39 | $0.61 |
| MGC | MGCV6 | 10 | $5,774.68 | $5,021.61 | $0.96 |

These are a dated feasibility snapshot, not constants. Regenerate with
`sillage broker-contract-check`; IBKR can change house margin at any time.

| Market | Candidate | Contract risk unit | History/roll issue | $25K verdict |
|---|---|---|---|---|
| US large equity | `NES` E-nano S&P 500 | $0.50 × index; $0.25 tick | Launched 2026-08-24; research must proxy the larger contract and execution has almost no history | Conditional: size works; require IBKR qualification and observed spread/volume |
| US tech equity | `NNQ` E-nano Nasdaq-100 | $0.20 × index; $0.10 tick | Same new-product problem | Conditional |
| US small equity | `N2K` E-nano Russell 2000 | $0.50 × index; $0.10 tick | Same new-product problem | Conditional |
| Developed FX | `M6E` Micro EUR/USD | EUR 12,500; $1.25 tick | Physical delivery; quarterly roll must precede delivery window | Conditional: coarse but potentially usable |
| Japanese FX | Micro JPY/USD | Verify exact IBKR contract and multiplier before inclusion | Physical delivery and quote-direction handling | Pending; excluded until qualified |
| Treasury duration | `10Y` Micro 10-Year Yield | $10 DV01; $1 tick | Monthly cash settlement; long contract profits when yield rises, so bond-return direction is inverted | Conditional: usable only with explicit sign convention |
| Gold | `1OZ` 1-ounce Gold | 1 troy ounce | Short live history; delivery and last-trade handling must be verified | Preferred over `MGC`; pending IBKR qualification |
| Crude oil | `MCL` Micro WTI | 100 barrels; $1 tick | Monthly roll, negative-price history, expiry risk | Reject initially: one contract is too large/volatile for its sleeve budget |
| Larger equity | `MES`, `MNQ`, `M2K` | One-tenth E-mini | Good liquidity, but roughly ten times E-nano exposure | Reject at $25K: whole-contract risk is too coarse |
| Micro gold | `MGC` | 10 troy ounces | Mature enough to research | Reject at $25K: whole-contract risk is too coarse |

CME contract references:

- E-nano specifications and multipliers:
  <https://www.cmegroup.com/articles/faqs/faq-e-nano-equity-index-futures.html>
- Micro Treasury/Yield comparison:
  <https://www.cmegroup.com/articles/2024/micro-treasury-futures-vs-yield-futures.html>
- Micro FX product guide:
  <https://www.cmegroup.com/markets/fx/fx-product-guide.html>
- Micro metals:
  <https://www.cmegroup.com/markets/microsuite/metals.html>
- Micro WTI specifications:
  <https://www.cmegroup.com/education/articles-and-reports/micro-wti-crude-oil-futures-faq>
- IBKR futures commissions:
  <https://www.interactivebrokers.com/en/pricing/commissions-futures.php>

### Data and execution requirements

- Back-adjusted continuous series may measure returns and signals only. Orders and P&L
  use actual dated contracts and actual roll prices.
- The roll rule is fixed before results: move from the front contract five business
  days before its last trade date, unless next-contract volume exceeds front volume
  earlier; the earlier event wins.
- Store the raw contract chain. A vendor-supplied continuous ticker without its roll
  map is insufficient.
- Model multiplier, exchange/clearing/broker commission, half-spread, variation margin,
  initial/maintenance margin and two legs of every roll.
- Never hold into a physical-delivery window. A failed roll is a trading halt, not a
  request to retry after expiry.

### Acceptance gates

Proceed only if all are true:

1. At least four markets across three asset classes pass IBKR qualification, permission,
   liquidity, history and whole-contract sizing checks.
   Before querying the venue, direct product history is defined as at least 756 sessions,
   recent liquidity as a 20-session median of at least 100 contracts, and sparse trading
   as no more than 10% zero-volume sessions. All four markets must pass; mature proxy
   contracts do not satisfy this gate.
2. One contract contributes no more than 3% annualized volatility to the total $25K
   portfolio under its trailing estimate.
3. Expected initial margin stays below 35% of NAV and a doubled-margin stress below 70%.
4. After costs, standalone excess Sharpe is at least 0.60 and no chronological half is
   below 0.25.
5. Correlation with the frozen balanced control is below 0.35, and the sleeve is positive
   during the balanced control's five deepest drawdowns in aggregate.
6. Results survive 2x costs, one-session-later rolls, 6/12-month-only signals and four
   weekly rebalance weekdays without changing the conclusion.

Failure of gates 1–3 rejects the futures implementation regardless of performance. In
that case the same economic hypothesis may be retested with granular ETFs, but that is
a new declared candidate with borrow and financing assumptions—not a silent substitution.

## Candidate B — liquid ETF relative value

### Hypothesis

Closely related liquid ETFs sometimes diverge temporarily while retaining a stable
long-run relationship. A hedged spread may earn returns with little equity beta and
therefore complement the balanced control.

### Frozen pair set

Only these economically motivated pairs enter round one:

| Long/short orientation is signal-driven | Relationship |
|---|---|
| `SPY` / `IVV` | Same large-cap US index exposure, different fund plumbing |
| `IWM` / `VTWO` | Russell 2000 exposure |
| `EFA` / `VEA` | Developed ex-US equities, with index differences acknowledged |
| `LQD` / `VCIT` | Investment-grade corporate duration/credit |
| `GLD` / `IAU` | Physically backed gold trusts |

The pair list cannot change after viewing results. A pair lacking sufficient borrow or
point-in-time data is removed and counted as a failed trial, not replaced.

### Frozen model

- Daily adjusted closes; trailing 252-session log-price regression with intercept,
  estimated strictly through the previous close.
- Trade only if the trailing residual passes a predeclared stationarity threshold and
  the hedge ratio is positive and stable across the two preceding half-windows.
- Enter beyond an absolute 2.0 residual z-score, exit inside 0.5, hard-stop beyond 4.0,
  and close after 42 sessions regardless.
- Dollar-neutral at the estimated hedge ratio; maximum 20% gross portfolio exposure per
  pair and 60% across all pairs.
- Signal at close, execute both legs at the next open. If either leg fails, immediately
  flatten the filled leg; charge both attempted legs and the emergency exit.

### Acceptance gates

1. After costs and borrow, standalone excess Sharpe at least 0.75 and maximum drawdown
   below 15%.
2. Positive results in both chronological halves and in at least three pairs; no pair
   supplies more than 40% of total P&L.
3. Correlation with balanced below 0.30 and positive aggregate P&L during its five
   deepest drawdowns.
4. Survive 2x spread/commission, borrow cost +300 bp, one-day delayed entry and the
   adjacent entry thresholds 1.75 and 2.25.
5. IBKR paper confirms short availability and fee retrieval before every entry. Missing
   availability means no trade; recalls and buy-ins are explicit events.

This candidate cannot go live through the current adapter: it still needs pre-trade
short availability/fee checks, paired-order failure handling, recalls, buy-ins and
distributions owed on shorts. Those are built only if the research passes.

## Experiment order

1. Query IBKR paper for qualification, current margin and permissions on the conditional
   futures contracts.
2. Decide whether at least four futures markets pass the static gates.
3. Acquire a contract-level futures dataset with an explicit roll map; do not use Yahoo
   continuous tickers as execution history.
4. Run Candidate A and log it whether it passes or fails.
5. Acquire historical borrow assumptions and run Candidate B.
6. Only then compare combinations with the frozen controls in `research/baselines.json`.

The dated whole-contract screen is reproducible with `sillage broker-contract-risk`.
It reads IBKR history and transmits no orders.

The 2026-09-23 screen passed `NES` (1.75% of NAV), `N2K` (0.75%), `M6E` (2.56%)
and `10Y` (2.36%). `NNQ` (5.02%), `1OZ` (3.70%) and `MCL` (18.67%) failed the
3%-of-NAV contract-risk gate. This is exactly four markets across three asset classes,
so Candidate A remains conditional on direct liquidity/history checks; the failed
contracts do not enter the $25K backtest.

The 2026-09-24 direct-product screen rejected Candidate A before performance testing.
NES and N2K each supplied 31 sessions, M6E 322 and 10Y 290, below the frozen
756-session minimum. Recent median volume passed for all four, but NES and N2K also
recorded 25.8% zero-volume sessions in their short histories. Mature ES, M2K, 6E or ZN
history may study the economic idea, but cannot satisfy this executability gate. No paid
historical dataset is acquired for a candidate that already fails gates 1--3.
