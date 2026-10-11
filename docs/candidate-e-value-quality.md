# Candidate E: slow value/quality — mechanism review first

Status: initial mechanism screen completed; frozen equal-weight composite is held
at the continuation gate. Criteria were committed at `7c912e1` before performance
inspection. No stock selection implementation or paper promotion.
Discovery is portfolio-first; passive ETFs
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

## Initial screen outcome — 2026-10-11

`research/value-quality-screen.json` records 247 complete months, January 2006
through July 2026 (the latest common factor month, not a fabricated August value).
The comparator is a new $25K balanced replay with normal whole-share costs and
zero interest on positive idle cash; it is not the $100K frozen cash-accrual control.
Core monthly total returns are reduced by the provider's US monthly risk-free rate
once. Long/short factor premiums are not reduced by that rate again or credited
cash interest. These are diagnostics, not net implementable factor portfolios.

| Diagnostic | Core correlation | First-half mean/month | Second-half mean/month |
| --- | ---: | ---: | ---: |
| US value | -0.026 | +0.108% | -0.275% |
| US quality | -0.457 | +0.390% | +0.153% |
| Frozen 50/50 composite | -0.316 | +0.249% | -0.061% |

Halves are January 2006–April 2016 and May 2016–July 2026. Composite correlation
is negative in both (-0.342/-0.317); its paired 12-month-block bootstrap correlation
interval is approximately [-0.433, -0.195]. In the core's worst-decile months it
averaged +1.541%, but its 2020 monthly average was -2.169%: not a universal hedge.

Five of six continuation checks pass; positive mean in both halves fails. We do
not change the weights, remove that gate or promote a quality-only diagnostic after
inspection. This heuristic gate holds this proposal; it does not establish that
every long-only value/quality implementation is bad or that a negative-mean component
can never improve a portfolio. No combined Sharpe/net blend has been measured.

Important interpretation: composite market beta is -0.171 (quality -0.381) versus
core beta +0.409. Removing shared market exposure reduces composite correlation to
-0.044. Much of the apparent complementarity is consistent with opposing market
exposure, which a long-only ETF need not retain. Quality is a useful follow-up lead,
not a secretly substituted successful primary candidate. Any quality-led ETF
feasibility study must be a separately recorded hypothesis using actual inception,
exposures and costs, not a claim to replicate QMJ.

### Reproduction and data provenance

Run `.venv/bin/python scripts/screen_value_quality.py` (or `uv run python ...`).
No broker access, refresh, raw-series publication or paper deployment occurs.
The input manifest `research/value-quality-inputs.json` checks every core/cash-proxy
Parquet file and both private workbooks before replay. Changed provider vintages
abort instead of silently restating the experiment. The official workbook download
URLs are recorded in the report; store them as `data/value-quality-research/vme.xlsx`
and `qmj.xlsx`, which are Git-ignored. Current public downloads may be updated later;
reproducing this exact result requires the matching privately retained vintage.

Eight synthetic tests cover decimal-percent parsing, unsafe/missing/duplicate
inputs, monthly compounding with its prior denominator, market residualization and
repeatable diagnostics/gate behavior. The broader relevant suite passes 60 tests.
