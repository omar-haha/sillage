# Candidate G — unleveraged Bitcoin ETP substitution

Specification: `research/bitcoin-etp-spec.json`, frozen before downloading or scoring
the candidate history. Select IBIT for USD stock/ETP execution fit, not a fund return
ranking. The issuer identifies Nasdaq listing, January 5, 2024 inception and a 0.25%
sponsor fee. Fund expenses are already in historical prices. The issuer/broker pages
incidentally displayed current performance, so this is not pristine discovery.

Primary allocation: 97% of capital to unchanged balanced, 3% to IBIT. At a $25K
budget that is a $750 target, financed by reducing balanced, not borrowing. The
1%/5% variants are predeclared diagnostics, not candidates to substitute after results.
The equity-risk control substitutes 3% SPY instead. All targets are long-only and
sum to at most 100%; existing share rounding, costs and drift bands still apply.

IBKR Canada lists spot Bitcoin ETP access under Complex or Leveraged ETP permissions.
That permission grouping is not a statement that IBIT itself is leveraged. Actual
account admission must be checked separately; read-only contract qualification is
not evidence of live buying permission. No paper orders or production changes are
authorized by this research experiment.

Product-only evidence begins in January 2024. The scored period begins February 1,
after a January staging period, and ends at the existing September 16, 2026 cutoff.
No spot/index history is substituted for product history. Results cannot establish
how this executable product would have behaved in 2008, 2020 or 2022.

Sources:

- https://www.ishares.com/us/products/333011/ishares-Bitcoin-trust-etf
- https://www.interactivebrokers.ca/en/trading/spot-bitcoin-etps.php

## Results — October 22, 2026

Specification commit `7032207`; input freeze `b1d7463`. Eight declared portfolios
were evaluated; no rules, economic criteria, weights or costs were changed after
the first scoring. Type annotations/provenance reporting were tightened afterward.
`research/bitcoin-etp-candidate.json` records results and source hashes;
`research/bitcoin-etp-inputs.json` binds the separate ignored Parquet vintage.
The exact private source vintage is required for reproduction and is not
automatically included in the existing production-journal backup whitelist.

Reproduce:

```bash
# Only on initial preparation; downloads IBIT, copies hash-checked core data,
# writes the input manifest, and does not score returns. Refuses to overwrite.
uv run python scripts/test_bitcoin_etp.py --prepare
# Thereafter, offline replay with matching frozen inputs:
uv run python scripts/test_bitcoin_etp.py
```

A fresh download may not match the committed manifest because providers restate
data. Never replace it silently. Other core inputs reuse the E2 frozen vintage;
the January 2024 product history is not synthesized from spot Bitcoin.

### Same-period USD, whole-share comparison

Replay starts January 11, 2024 with $25K; return scoring starts February 1 after
January staging and ends September 16, 2026. The January 31 prior close appears
in metrics as the denominator, not an extra scored return. Capital is not reset
on February 1. These are not comparable directly to full-history 0.650 Sharpe.

| Configuration | CAGR | Excess Sharpe | Maximum drawdown | Longest drawdown |
|---|---:|---:|---:|---:|
| Balanced control | 11.95% | 0.812 | -8.78% | 195d |
| **97% balanced / 3% IBIT** | **12.67%** | **0.853** | **-9.01%** | **183d** |
| 97% balanced / 3% SPY | 12.27% | 0.831 | -8.94% | 195d |
| 99% balanced / 1% IBIT | 12.25% | 0.832 | -8.76% | 183d |
| 95% balanced / 5% IBIT | 13.25% | 0.874 | -9.25% | 181d |
| IBIT standalone diagnostic | 24.12% | 0.598 | -53.12% | 345d |
| Balanced at doubled costs | 11.29% | 0.748 | -8.81% | 197d |
| Primary at doubled costs | 11.95% | 0.783 | -9.12% | 195d |

Full-period primary improvement over core is +0.0406 Sharpe and about +0.72
percentage points of CAGR. It beats the equity substitution control on point
Sharpe, stays inside the drawdown gates, and retains a point advantage at doubled
costs. However, first-half Sharpe improvement is +0.2102 and second-half -0.1744.
The split is May 23, 2025 (shared NAV denominator, not a duplicated return).
All declared Bitcoin weights show negative second-half Sharpe improvements; the
5% diagnostic cannot replace the frozen 3% primary just because its full-period
number is larger.

The primary-minus-core 95% paired-bootstrap interval is [-0.140, 0.236] with
21-session blocks and [-0.174, 0.266] with 63-session blocks (2,000 draws, seed
22102026). Neither resolves the improvement's sign. With fewer than three years,
there are few independent market regimes; these are descriptive uncertainty
diagnostics, not evidence for long-run multi-cycle performance.

IBIT/core daily excess-return correlation is 0.422 in this sample. Portfolio-level
primary/core correlation is still 0.990, unsurprising at 3% allocation. In core's
worst daily-return decile, primary mean excess return is -1.077% versus core
-1.045%. This expression did not act as a reliable downside hedge in that diagnostic.

### Capital and execution practicality

All eight replays have zero rejected orders, nonnegative cash and gross exposure
below 100%. No borrowing or leverage finances the Bitcoin allocation. The primary
has 16 IBIT fills over the full replay and reaches 3.70% marked IBIT weight despite
its 3% target. Minimum ticket size and no-trade bands mean targets are not hard
position caps or promises of a fixed monthly number of trades. The 1% diagnostic
has four IBIT fills, while 5% has 36; sizing affects actual ticket economics.

The 672-session product history passes the predeclared 500-session gate, median
recent daily volume is 51,359,550 shares, and no zero-volume sessions occur. These
daily data checks do not establish executable spreads or available liquidity at
an opening auction. The 2bp half-spread and impact assumptions remain proxies.
The standalone product's 53% drawdown illustrates the risk hidden by a small weight.

`research/bitcoin-etp-venue.json` records a lock-protected read-only paper Gateway
lookup: IBIT resolves to USD/Nasdaq `STK`, conId 677037673, $0.01 minimum tick,
with MKT in its reported order-type list. The current adapter can identify the
instrument without a new crypto exchange API, wallet or futures machinery.
No order, what-if request, permission change or Gateway restart was performed.
This does **not** prove live-account Complex or Leveraged ETP permission,
opening-auction acceptance, fills, or commissions. Those checks still precede any
promotion. Nonleveraged spot exposure can be in that permission category.

## Decision

The static feasibility screen passes and the full-period point improvement is
real in this simulation, but the frozen both-halves criterion fails. Status:
`reject_frozen_expression`; production remains unchanged. This rejects promotion
of this expression under these criteria, not all Bitcoin allocations or crypto
mechanisms. It is stronger evidence than saying crypto is mechanically impossible,
but weaker than a robust performance case. Do not tune a new trend filter, allocation
or split on these results and call that the same experiment. A later hypothesis
must be separately frozen and logged. There is no demonstrated 1.1 Sharpe or
long-history downside improvement, and no 2008/2020/2022 product stress test.

## Verification

All eight final results reproduce byte-identically in a separate temporary output.
This is reproducibility evidence, not eight additional independent trials. The 96
targeted Bitcoin, balanced-review, QUAL, metrics, blend/tranche and engine integration
tests pass, including whole-share next-open/no-borrowing replay, frozen-primary
rejection, insufficient volume/history, and live-registry isolation. Ruff passes for
the new source/script/test; mypy passes for the new source and test with source-path
imports configured. The full suite and live-account execution were not tested.
