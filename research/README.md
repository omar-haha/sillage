# Frozen research controls

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

