# Balanced review — frozen before new results

The October 22, 2026 read-only deployment check confirmed `balanced`, `core`,
$25,000 and deployed commit `a0c864b`, with an October 22 last-success marker.
This marker is operational evidence, not a full uptime or return audit.

The specification in `research/balanced-review-spec.json` is frozen before scoring.
The eleven declared replays examine the current rule, simpler controls, execution
costs, capital sensitivity and two coarse sleeve-weight diagnostics. They do not
search for a replacement or authorize a paper allocation change. The existing
E2 core input vintage and September 16 cutoff are reused without downloading.
Dates are already familiar; no interval is described as an untouched holdout.

The hypothesis to investigate is whether the current broad exposure leaves little
incremental diversification for proposed sleeves. Correlation and descriptive
market/bond regressions can assess overlap, not establish that every future
candidate is redundant or that a broad strategy exhausts all economic edges.

## Results

Specification commit: `f705c21`. Reproduce offline with:

```bash
uv run python scripts/evaluate_balanced.py
```

This verifies the existing E2 source hashes before scoring, requires every session
after each product's first observed bar, and records all eleven configurations in
`research/balanced-review.json`. Source Parquet files remain private/ignored; no
download, broker access, production journal mutation or strategy change occurs.
The implementation hash is recorded as well as the Git HEAD at execution (the
first run's HEAD predates the newly added script; the file hash identifies it).

### Matched whole-share USD replays, January 2005–September 16, 2026

Primary capital is $25K. Positive idle cash earns zero; BIL adjusted returns are
embedded. Excess Sharpe uses the dated Treasury curve. Costs are $0.005/share,
$1 minimum, existing spread and trailing-ADV impact estimates unless labeled.
These are model assumptions, not an audit of actual paper commissions.

| Configuration | CAGR | Excess Sharpe | Maximum drawdown | Longest drawdown |
|---|---:|---:|---:|---:|
| **Balanced** | **7.27%** | **0.650** | **-19.78%** | **763d** |
| Tranched momentum | 6.62% | 0.563 | -21.74% | 712d |
| 60/40 SPY/IEF | 8.22% | 0.612 | -31.23% | 1,092d |
| Buy/hold SPY | 10.79% | 0.542 | -55.12% | 1,773d |
| Monthly static 50/50 SPY/IEF | 7.53% | 0.639 | -24.27% | 828d |
| Equal-weight core | 6.37% | 0.440 | -36.13% | 969d |
| Balanced, tiered fee proxy | 7.58% | 0.685 | -19.75% | 759d |
| Balanced, doubled fixed costs | 6.68% | 0.585 | -19.94% | 774d |
| 25% momentum / 75% 60/40 | 7.55% | 0.631 | -23.15% | 803d |
| 75% momentum / 25% 60/40 | 6.90% | 0.620 | -20.61% | 727d |
| Balanced, $100K fixed-fee diagnostic | 7.63% | 0.686 | -19.91% | 759d |

All eleven replays have zero rejected orders. That validates these simulations,
not future broker admission. Equal-weight waits for all twelve ETFs to have
history; it therefore starts investing later and is a startup-sensitive control.
All variants replay continuously from 2005. Subperiods do not restart with fresh
$25K, and include the preceding close only as the first return's denominator.

The primary has 4,712 fills and annualized execution charges of about 0.434% of
average NAV, versus 0.171% under the tiered proxy. This cost statistic is not a
free-execution counterfactual CAGR difference. At $100K the fixed-fee model gives
0.686 Sharpe: capital helps costs/rounding but does not approach the 1.1 objective.
The old 0.712 frozen result uses $100K, different costs and positive-cash accrual;
it must not be compared as though it were the same account experiment.

### Stability, not untouched out-of-sample validation

| Period | Balanced excess Sharpe | 60/40 | Static 50/50 | SPY |
|---|---:|---:|---:|---:|
| After initial warm-up, 2006 onward | 0.672 | 0.624 | 0.653 | 0.553 |
| 2010 onward | 0.744 | 0.807 | 0.820 | 0.770 |
| 2005–2017 | 0.743 | 0.651 | 0.719 | 0.463 |
| 2018 onward | 0.546 | 0.569 | 0.543 | 0.657 |

The full-sample advantage is not stable leadership. Post-2010, 60/40 returns
9.78% annually versus balanced's 7.84%, with drawdowns -21.88% versus -19.78%.
In 2020 balanced returns 6.46%, while static 50/50 returns 14.62% and has a
shallower drawdown (-16.39% versus -19.78%). In 2022 balanced loses less than
static 50/50 (-11.27% versus -16.83%). Single-year Sharpe values, especially in
negative-return years, should not replace these return/drawdown comparisons.
Subperiod drawdowns restart their high-water mark at the subperiod denominator;
they do not retain a peak from before the window.

### Uncertainty and exposure overlap

Paired circular bootstrap, 2,000 draws, seed 22102026. Positive differences favor
balanced; both methods keep each portfolio's observations paired. Changing the
block length is a robustness diagnostic, not independent replication.

| Balanced minus control | Point Sharpe difference | 95% interval, 21-session blocks | 95% interval, 252-session blocks |
|---|---:|---:|---:|
| Momentum | +0.087 | [-0.103, 0.278] | [-0.123, 0.274] |
| 60/40 | +0.038 | [-0.125, 0.206] | [-0.151, 0.202] |
| SPY | +0.109 | [-0.090, 0.319] | [-0.116, 0.303] |
| Static 50/50 | +0.012 | [-0.153, 0.185] | [-0.180, 0.178] |

None excludes zero. This does not prove equality or no useful drawdown benefit;
it means these diagnostics do not resolve the sign of the Sharpe improvement.
No effective-trial-count estimate or deflated-Sharpe success probability is claimed.

Balanced's excess-return correlation is 0.913 with 60/40, 0.858 with momentum and
0.858 with SPY. Momentum and 60/40 correlate 0.580. A descriptive regression on
SPY and IEF adjusted excess returns has betas 0.424 and 0.347, R-squared 0.804.
That is about 80% of observed daily variance explained in sample, not 80% of
all economic edges, causal attribution or evidence that other strategies cannot
diversify it. Mean marked weights include SPY 34.77%, IEF 23.21%, BIL 9.66%; the
remaining allocations are spread across the other core ETFs. Average weights
are not fixed betas, and BIL is exposure rather than uninvested cash.

## Decision

Keep balanced provisional and unchanged on paper. Its full-history lower drawdown
is economically relevant, but its small Sharpe lead over a simple 50/50 and weaker
post-2010 comparison do not establish alpha or justify claims of passive superiority.
Do not choose either neighbor as a new winner. The broad exposure-overlap hypothesis
has descriptive support, but failed candidates also had independent signal, cost or
feasibility problems; overlap alone does not explain every rejection.

Next research decision: explicitly choose between a frozen portfolio-level downside
overlay (with turnover and lost-upside controls) and a genuinely different mechanism
that passes a cheap exposure/feasibility review first. Do not build another loosely
related ETF sleeve simply to chase 1.1. This review is a bounded baseline audit, not
the missing comprehensive balanced sensitivity/walk-forward battery. Historical data
has been reused, adjusted-price lot accounting is approximate, CAD/taxes are absent,
actual production outages and risk-limit interventions are not replayed, and a clean
prospective evaluation is still needed.

## Verification

All eleven runs were repeated to a separate temporary output and the complete
JSON was byte-identical. This is reproducibility evidence, not eleven additional
independent hypotheses. All 80 targeted balanced-review, QUAL, financing, metrics,
blend and tranche tests passed. Ruff passed for the new script/test and mypy passed
for the new test with the source import path configured. The full suite, real
execution quality and a replacement-server deployment were not tested in this review.
