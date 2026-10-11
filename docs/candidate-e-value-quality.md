# Candidate E: slow value/quality — mechanism review first

Status: research proposal, no Candidate E performance inspected, no frozen stock
selection rules, no paper promotion. Discovery is portfolio-first; passive ETFs
remain controls rather than an immediate prerequisite for testing a new sleeve.

## Hypothesis and limits

Cheap, financially strong businesses may offer a slower return source that
complements the current core without short borrow or frequent trading. The actual
question is covariance and combined net performance versus the existing balanced
core, not whether E independently beats SPY or has Sharpe 1.1.

[Value and Momentum Everywhere](https://www.aqr.com/insights/research/journal-article/value-and-momentum-everywhere)
provides evidence for the interaction of value and momentum across markets.
[Quality Minus Junk](https://www.aqr.com/-/media/AQR/Documents/Insights/Working-Papers/Quality-Minus-Junk.pdf)
provides evidence for profitability, growth and safety characteristics. These are
reasons to investigate, not proof of a tradable $25K stock-ranking implementation.
The core's asset-level trend/momentum plus diversified exposure is not identical
to academic cross-sectional equity momentum.

Most importantly, low correlation between long/short factor premiums does not imply
low correlation between two long-only equity portfolios. Long-only E retains market
beta. Diagnose shared market exposure separately before describing diversification.
Rejecting Candidate D does not prove all short-horizon trading is impossible; it
shows that this frozen implementation failed at the modeled costs and budgets.

## Stage 1: inexpensive mechanism screen

1. Inspect primary-provider definitions and available columns for
   [AQR monthly value/momentum factors](https://www.aqr.com/Insights/Datasets/Value-and-Momentum-Everywhere-Factors-Monthly)
   and [monthly QMJ](https://www.aqr.com/Insights/Datasets/Quality-Minus-Junk-Factors-Monthly).
   Check long/short versus long-only, excess versus total returns, units, regions,
   cash/financing conventions, publication lag, and permitted use. Retain raw inputs
   privately in ignored market-data storage with hashes; publish aggregate results
   and attribution rather than assuming raw-data redistribution is permitted.
2. Freeze a diagnostic before viewing results: primary US equity value and quality
   columns when available, equal-weight value/quality composite, no threshold or
   weight optimization. If definitions are incompatible, stop and document the
   mismatch instead of splicing series to fabricate a common strategy.
3. Use complete common months through the existing 2026-09-16 cutoff (therefore
   August is the last full month). Compound daily core returns into monthly returns;
   do not average daily returns. Score both full common period and chronological
   halves, distinguishing frozen $100K controls from a rerun at $25K.
4. Report covariance/correlation with the actual core, market-adjusted relationship,
   crisis co-losses and uncertainty. Academic factor blends are diagnostic exposures,
   not $25K investable portfolios, observed shortability or net expected returns.
   Report any normalized-risk calculation explicitly; never silently lever a factor
   series or credit interest twice.
5. Commit numerical mechanism-screen continuation criteria before running the
   first comparison. Negative covariance is not necessary, and a sample point
   estimate is not proof. If relationships are weak or unstable, log rejection or
   inconclusive evidence before building stock-ranking infrastructure.

No criterion is selected by viewing factor curves first. Include the composite,
individual diagnostics and any alternative regional/weight choices in TRIALS.md.
Repeated use of 2005–2026 is not a fresh holdout.

## Stage 2: implementation/data feasibility

Only if mechanism evidence warrants investigation:

- Prefer a comparison of available investable factor-ETF expressions first if
  point-in-time stock fundamentals are unavailable. Document prospectus rules,
  actual inception, fees, exposures and turnover; do not backfill pre-inception
  returns or call two ETFs a replication of a joint stock-level value/quality score.
- A custom stock sleeve requires historical fundamentals with publication/filing
  timestamps, a historical eligible universe, delisted stocks/returns and corporate
  actions. Today's ratios and current S&P membership cannot be historical inputs.
  A fixed blanket reporting lag is not equivalent to a verified point-in-time feed.
- Long-only liquid US equities, no microcaps, slow rebalancing, no discretionary
  overrides. Freeze eligibility, metrics, missing-data/financial-sector handling,
  combined scoring, count, weights, exit rules and cadence before any stock backtest.
  Do not tune the number of names or value/quality weights after results.
- Budget the realistic $5K primary sleeve. Twenty $250 positions imply forty tickets
  for a complete replacement. At an illustrative $1 minimum, that is $40, or 0.8%
  of sleeve capital per full rotation before spread/impact. Quarterly frequency is
  not by itself proof of low annual cost; actual turnover must be modeled. Whole
  shares, ETF fees, FX and tax-account assumptions remain explicit.
- No data purchase, new paid broker subscription or change to live/paper allocation
  without separate authorization. Current feasibility is not yet established.

## Stage 3: actual combined portfolio

Primary question: does capital substitution into E improve the core after costs,
without merely adding equity beta/leverage or hidden concentration? Freeze primary
allocation and neighboring robustness weights before results. Assess paired excess
Sharpe improvement, drawdown depth/duration, tail co-losses, turnover and stability.
Recompute netted portfolio orders, minimum commissions, cash and financing rather
than advertising a weighted sum of frictionless factor returns as executable.

Do not require E alone to beat SPY or reach 1.1. Do require credible net returns and
an improvement robust enough to justify complexity. Eventually compare the finished
portfolio with CAD-valued passive equity and balanced controls on matched periods.
The original core remains provisional, not a proven investable alpha product.

## Candidate F remains deferred

[Moreira and Muir, Volatility Managed Portfolios](https://www.nber.org/papers/w22208)
is a possible later mechanism review. Existing core volatility controls may overlap
with it; it is not an independent sleeve merely because its label is different.
No F variants are being tested in parallel with E.
