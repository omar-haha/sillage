# Frozen research controls

The latest investable-expression test is [Candidate E2, QUAL](../docs/candidate-e2-quality-etf.md).
It is mechanically feasible but rejected for the frozen Sharpe/diversification
goal: primary Sharpe 0.672 versus core 0.658 and SPY substitution 0.688, with a
deeper drawdown. Reproduce via `uv run python scripts/test_quality_etf.py` using
the frozen private vintage; `--prepare` downloads only and does not score.
See `quality-etf-spec.json`, `quality-etf-inputs.json` and `quality-etf-candidate.json`.
No live strategy registry or paper allocation changed.

The preceding mechanism review is [Candidate E, slow value/quality](../docs/candidate-e-value-quality.md).
Its published-factor mechanism screen is complete: the frozen composite diversifies
the core but fails the positive-mean-in-both-halves continuation gate. Quality alone
is an exploratory lead, not a substituted pass or long-only implementation.
Reproduce with `uv run python scripts/screen_value_quality.py` using the frozen,
ignored source vintages; see `value-quality-screen.json` and the input/spec manifests.
Passive ETF comparisons remain controls for the combined system;
they do not require every candidate to beat a passive portfolio standalone.

The cross-experiment history is in [TRIALS.md](TRIALS.md), including failed and
preliminary screens. The completed Candidate D review is documented in
[stress-conditioned reversal](../docs/candidate-d-reversal.md); its daily-input/
cost review is complete and the frozen research screen was rejected. Execution validation
is not complete. Reproduce the coverage and illustrative budget review with
`uv run python scripts/reversal_feasibility.py --download-vix`, or omit the flag to
reuse the ignored local VIX vintage. No rankings or alpha curves are generated.

The subsequent frozen Candidate D screen is rejected and recorded in
`reversal-candidate.json`. Reproduce with
`uv run python scripts/test_reversal_candidate.py`. Input hashes must match the
feasibility vintage; unexpected restatements abort rather than silently change the
experiment. The primary case uses only $5K, the proposed 20% slice of $25K.
Its prerequisite net-trading-profit gate fails in both halves, so portfolio
combinations remain unevaluated. No broker-side reversal trading was enabled.

Candidate C's rejected monthly beta-neutral sector-momentum screen is recorded in
`sector-candidate.json`. Reproduce locally (no broker access) with:

```bash
uv run python scripts/test_sector_candidate.py --download
# Reuse the separate, ignored data/sector-research vintage:
uv run python scripts/test_sector_candidate.py
```

The evaluation ends at the frozen controls' 2026-09-16 date; 2005 supplies warm-up
and the scored period begins 2006-01-03. Input Parquet and rate hashes bind the
results to their vintage. Downloading again may restate adjusted historical prices.
The $25K screen conservatively credits zero cash interest, models no interest on
short-sale proceeds, uses trailing-only ADV, whole shares, month-end signals and
next-session opening execution. Borrow fees are declared proxies, not broker
history. Rate data remains the benchmark for excess Sharpe. The shared default
ETF cost model is used; terminal holdings are marked, not artificially liquidated.
Correlation/drawdown overlap, concentration/beta and live availability gates remain
unevaluated because the prerequisite performance gate failed. None of this enables
live or paper sector trading.

`baselines.json` is the Phase 8 control set. New strategy experiments compare against
these exact results rather than a baseline silently changed by a later data download.

Regenerate it with:

```bash
uv run python scripts/freeze_baselines.py --end 2026-09-16
```

Add `--refresh-rates` only when deliberately creating a new vintage. It downloads the
Federal Reserve's daily 3-month Treasury yield (`DGS3MO`) for excess-return metrics and
cash accrual, plus the effective federal funds rate (`DFF`). The margin curve is a
transparent proxy: `DFF + 1.5 percentage points`, matching IBKR Pro's published first
USD tier methodology. IBKR does not publish a convenient historical customer-rate
archive, so the proxy must not be described as one. Its current methodology is
documented at <https://www.interactivebrokers.com/en/accounts/fees/pricing-margin-rates.php>.

Rates are annual decimals, effective from each observation date and carried forward
over weekends and missing business days. SHA-256 hashes in `baselines.json` bind every
result to the committed inputs.

