# Candidate E2 — investable quality ETF expression

Status: specification frozen before the first downloaded curve/portfolio results.
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
