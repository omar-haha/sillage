# Paper incident recovery

Duplicate alerts: while a `sillage-incident` issue remains open, subsequent paper
failures and missed-cycle checks do not create another issue or initial email.
They now send a redacted **still blocked** email at most once per incident per
24 hours. Failed email delivery does not consume the reminder interval. The
reminder does not launch another agent or approve recovery.
Close the incident after resolution to rearm diagnosis for a new incident.
Manual diagnosis delivery retries do not email on failure; normal incidents do.

Missed-cycle checks run weekdays at 12:15 UTC and require today's local success
marker. They capture evidence even when the paper cron job never started. They
depend on the VPS and cron being available: a whole-host outage still relies on
Healthchecks' independent down email and cannot collect evidence from that host.

`approve:check-gateway` tests owner approval, pinned SSH, the forced-command key,
environment loading and broker API readiness without restarting Gateway. It does
not establish that a future restart will complete without interactive IBKR 2FA.

An unavailable executions API and a position mismatch are different failures. The
adapter retries execution timeouts three times with bounded backoff. If evidence
remains unavailable, keep pending orders and restore Gateway connectivity. Never
infer fill prices, commissions or order completion from current positions alone.

OpenHands reads the captured evidence and deployed code revision, then writes
`/workspace/project/comment_body.md`. GitHub Actions validates the report and
posts it. The report recommends one label; only the repository owner applies
`approve:restart-gateway` or `approve:rerun-cycle`. Conflicting approval labels
are refused. `needs-human` means read the diagnosis email and inspect the broker
records before proceeding. A cycle rerun after 08:30 Toronto is intentionally
refused; a Gateway restart can still restore API readiness.

For executions outside IBKR's replay window:

1. Export the paper account's trade confirmation/activity records from IBKR for
   the incident dates, including executions, timestamps, quantities, prices and
   commissions. Obtain order references where available.
2. Back up the journal using SQLite's backup API (not a file copy of an active WAL
   database). Retain the original export privately.
3. Match each missing execution to a journal order using the broker order
   reference and verify symbol, side and cumulative quantity. Do not reconstruct
   executions from net positions. Ambiguous mappings require manual investigation.
4. Preview an append-only import in a copy of the database. Compare existing fills
   to avoid duplicate imports and verify commissions and resulting cash.
5. Reconcile reconstructed holdings with a fresh, complete broker position
   snapshot. Import into the production journal only after the preview agrees.
6. Recover pending orders with complete broker evidence before resuming the next
   scheduled cycle. Never clear pending orders merely because they aged out.

## Statement-backed repair tool

`deploy/recover-statement.py` previews a repair in an online SQLite snapshot by
default. It requires a complete USD stock/ETF activity CSV, venue-level HTML
trade confirmations, and a fresh read-only broker snapshot. It rejects multiple
accounts, unexplained cash, split/ambiguous matches, unknown orders, open broker
orders, or holdings disagreements. It checks each execution against one unique
persisted order by symbol, signed quantity and a subsequent timestamp within
four days; the activity and confirmation records independently corroborate it.
These exports lack client order references, so this matching policy is deliberately
restricted and is not a general-purpose partial-fill importer.

The current supported statement starts with zero USD broker cash; Sillage's
$25,000 virtual USD capital is added to verified USD cash movements. The paper
account's separate CAD funding and whole-account CAD NAV are not treated as the
strategy's capital or performance.

```bash
cd /home/deploy/sillage
.venv/bin/python deploy/recover-statement.py \
  --statement /home/deploy/.local/share/sillage-recovery/activity.csv \
  --confirmations /home/deploy/.local/share/sillage-recovery/confirmations.htm \
  --through 2026-10-02
# Only after the preview agrees, with operator authorization:
# append --apply to the same command
```

The tool holds the paper-cycle lock, backs up the original journal, and performs
all ledger changes in one transaction. Raw fills are not rewritten: append-only
`fill_amendments` provide effective timestamps, prices and commissions.
`statement_recoveries` retains evidence hashes, prior NAV/pending state and the
matching policy. Confirmed missing fills are appended; only explained pending
orders are removed. Re-importing the same statement is a no-op. NAV is derived
and rebuilt using exact-session stored closing prices through statement coverage;
missing prices cause rollback. Keep the original evidence private and out of Git.

October 5 Toronto recovery: the reports confirmed three September 30 decisions
filled on October 1. Imported three fills, added eighteen price/time/commission
amendments, and rebuilt values through October 2. The preview and applied ledger
matched fresh broker holdings. Earlier raw fills and pre-repair backups were
retained. No trades were submitted. Keep incident #1 open until a scheduled
cycle also passes; a repaired journal alone is not a completed trading cycle.

IB Gateway execution collection now waits for a matching commission report;
missing fee evidence times out instead of being silently recorded as zero.

Evidence reports redact account identifiers, known gateway credentials, browser
keys and common token formats. Public issue sanitization cannot retract historical
copies. Rotate exposed reusable credentials through their issuing service.
