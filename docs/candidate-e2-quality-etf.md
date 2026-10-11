# Candidate E2 — investable quality ETF expression

Status: research screen completed; no promotion. Specification committed at
`ebc0342` before the first downloaded curve/portfolio results.
New hypothesis authorized after E's factor screen; not a reweighted rescue of E.

## Practical expression

[QUAL issuer information](https://www.ishares.com/us/products/256101/ishares-msci-usa-quality-factor-etf)
identifies a US-listed long-only quality ETF, launched July 16, 2013, with a 0.15%
expense ratio. It selects high profitability, stable earnings and low-debt stocks.
[MSCI](https://www.msci.com/indexes/index/705911/msci-usa-sector-neutral-quality-index)
describes quality selection within sectors. This is not the long/short QMJ factor.

[The issuer's benchmark-change notice](https://ir.blackrock.com/news-and-events/press-releases/press-releases-details/2015/BlackRock-Announces-Benchmark-Changes-to-Four-iShares-ETFs/default.aspx)
and prospectus document the September 2015 switch to a sector-neutral quality index.
Use actual post-change fund prices only, not a retrospectively reconstructed index.
No full-cycle/GFC robustness claim is possible from this history.

One ETF avoids twenty small stock tickets and short borrow. The $5K primary slice
is mechanically plausible with whole shares, subject to contemporaneous prices.
[IBKR publishes](https://www.interactivebrokers.com/en/pricing/commissions-stocks.php)
US ETF commission schedules; the frozen test uses the conservative $1-minimum fixed
proxy, not a claim to know this account's exact fee plan. Existing US ETF capability
is encouraging, but QUAL contract identity, trading permissions and executable
quotes remain unverified. No login, subscription or order is needed for this screen.

## Frozen portfolio test

`research/quality-etf-spec.json` is authoritative: $25K, 80% unchanged core / 20%
QUAL, neighboring 10%/30% weights as robustness checks only, and a matching 20%
SPY substitution to distinguish quality from extra equities. All portfolios replay
one netted book through the existing engine: no weighted sum of independently
backtested returns or double commission/cash-interest credits. QUAL does not enter
the core momentum-ranking universe. Its fixed target can trade on core rebalance
opportunities when the existing no-trade band permits; slow fund constituents do
not guarantee quarterly investor trades.

Replay starts September 2015; scoring begins January 2016 after initial staging,
through the unchanged September 16, 2026 cutoff. Costs, excess-return benchmark,
chronological halves, drawdown depth/duration and numerical continuation criteria
are frozen before curves. No standalone Sharpe 1.1 or SPY-outperformance requirement.
Report paired bootstrap uncertainty rather than claiming a point estimate is proof.

## Promotion limits

This first pass uses the project's adjusted-price research convention. Distributions
and expense drag are embedded, but historical raw-price share sizing, distributions
to cash, FX, withholding/tax and corporate actions are not a faithful broker ledger.
Any pass is permission for better validation, never automatic paper deployment.
Current production strategy and registry remain untouched.

## Outcome — 2026-10-11

Scored January 4, 2016–September 16, 2026, retaining December 31, 2015 NAV as the
return denominator after September–December staging. All eight declared configurations
were evaluated; no fund, weight, date or gate was changed to rescue the result.

| Netted portfolio | CAGR | Excess Sharpe | Maximum drawdown |
| --- | ---: | ---: | ---: |
| Core only | 8.10% | 0.658 | -19.73% |
| 80% core / 20% QUAL | 9.12% | 0.672 | -22.48% |
| 80% core / 20% SPY control | 9.35% | 0.688 | -22.57% |
| QUAL standalone diagnostic | 13.62% | 0.677 | -34.01% |

The primary improves Sharpe by only +0.0132, below the frozen +0.05 threshold, and
trails the matching SPY substitution by 0.0162 rather than beating it by +0.02.
Drawdown deepens 2.75 percentage points, beyond the 2-point limit. These three
checks fail. Both-half improvement, neighboring weights, duration and doubled-cost
checks pass; this is not a finding that quality is untradeable or all long-term
quality strategies lose money.

The paired bootstrap improvement interval spans zero (approximately -0.048 to
+0.089); this modest estimate is not convincing evidence of a Sharpe edge. The
neighbors at 10% and 30% quality yield Sharpe 0.664/0.672; neither is a replacement
primary. Under doubled costs core/primary Sharpe becomes 0.587/0.609. All simulated
portfolios have zero rejected orders. These cost assumptions are stricter than the
project's default tiered fee proxy, so compare to the matched core here, not older
reports with a different period or commission model.

QUAL's standalone daily excess-return correlation with the core is 0.879; its
market beta versus SPY is about 0.986. The combined book is correlated 0.987 with
the core. In core worst-decile daily returns, average excess loss worsens from
-1.029% to -1.165%. This long-only expression did not preserve the academic QMJ
hedging behavior. It largely increases equity exposure, despite financial-quality
selection. Actual fund history includes methodology changes; it is not a constant
stock-selection algorithm or a GFC-tested strategy.

The primary generated 131 QUAL fills over the entire September 2015–September 2026
replay, roughly one per month rather than four per year. Portfolio-wide rebalancing
can trade QUAL when another holding breaches its band. Commission/spread totals in
the report include staging and are descriptive replay totals, whereas the scored
return period begins in 2016. No claim of strict quarterly execution is made.

**Decision:** reject this frozen QUAL addition for the Sharpe/diversification goal.
It increases modeled return but also equity risk; do not deploy it or start tuning
its allocation. A risk-management overlay could be a separately frozen next study,
but must be labeled an overlay rather than a new independent alpha source.

## Reproduce

Run `uv run python scripts/test_quality_etf.py` with the matching private vintage.
For a new download, `--prepare` only fetches QUAL and copies existing core data to
Git-ignored `data/quality-etf-research`; it never scores returns. Hashes for every
input and the dated Treasury benchmark are frozen in `research/quality-etf-inputs.json`.
Later restatements must not silently replace this experiment. Aggregates and
configuration are in `research/quality-etf-candidate.json`, not raw licensed prices.

Nine synthetic test cases cover allocation limits, no live registry/core-universe
changes, controls/endpoints, exact calendar coverage across timestamp precisions,
paired bootstrap repeatability, metric convention agreement and failed-primary
acceptance logic. Relevant blend/execution/financing/metrics/engine tests: 94 pass.
An initial calendar check falsely rejected equivalent second/millisecond timestamps;
it was corrected before metrics. A small bootstrap-volatility convention mismatch
was corrected after initial inspection; the portfolio metrics/gate outcomes did not
change. Both are recorded as implementation corrections, not extra parameter trials.
