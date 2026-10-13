# Paper incident recovery

Duplicate alerts: while a `sillage-incident` issue remains open, subsequent paper
failures and missed-cycle checks do not create another issue or initial email.
They now send a redacted **still blocked** email at most once per incident per
24 hours. Failed email delivery does not consume the reminder interval. The
reminder does not launch another agent or approve recovery.
Successful scheduled cycles now close incidents created before that success,
with a dated recovery comment. Failure dispatch also uses the recorded success
marker to close a stale incident before opening a new one. A success confirms
one cycle, not continuous Gateway availability. Incidents created after the last
success are never closed using that older marker. Notification/closure failures
do not turn a completed trading cycle into a trading failure.
Manual diagnosis delivery retries do not email on failure; normal incidents do.

## Gateway readiness and overnight failures

The October 7 cycle succeeded following a cold restart; the subsequent nightly
11:45 p.m. restart returned to a logged-out dialog and later API connections timed
out. The exact IBKR/IBC session-resumption failure remains unconfirmed. Container
uptime alone is not a readiness check.

Before a scheduled trading cycle, `deploy/ensure-gateway.sh` performs a read-only
API check, retries once, then permits at most one Gateway-only cold restart with
a five-minute deadline. It clears the auto-restart token using the existing
restart procedure; no volumes, positions or orders are removed. An IB Key prompt
may still require the owner. This path is restricted to localhost port 4002 and
runs under `paper.lock`, before trading starts. It does not retry or restart after
an ambiguous submission. The 08:30 Toronto cutoff is checked again afterward.
If readiness is still unavailable, the cycle fails and normal incident diagnosis
starts. This is recovery containment, not proof that nightly restart is fixed.

Broker connection requires completed startup synchronization; positions are
fetched afresh and request timeouts are reported as unavailable broker evidence,
never as an empty portfolio. Multi-account sessions require an explicit account.
New incident creation triggers diagnosis on the `opened` event as well as manual
labeling; workflow concurrency and the diagnosis marker suppress repeat delivery.

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

## Same-day execution capture (October 13, 2026)

The paper deployment now captures execution evidence every five minutes during
XNYS trading days, 09:30-16:15 America/Toronto (DST-aware). The cron entry invokes
`deploy/capture-executions.sh`; `capture-executions.py` applies the calendar/window
guard, shared paper lock and a read-only localhost:4002 client using the owning
API client ID. It cannot submit/cancel orders, run strategy decisions or restart
Gateway. Failure sends a failure heartbeat and incident evidence; successful
capture does **not** claim a successful trading cycle or close an incident.

Confirmed executions and matching commissions are retained in the existing
`state/ibkr.db` table `broker_execution_evidence`, scoped by account/execution ID.
They therefore travel with the existing safe SQLite/S3 backup. Capturing evidence
does not itself change ledger fills, positions, NAV or pending orders. Subsequent
broker recovery merges available API evidence with this archive, still checking
broker positions before trading. Broker execution IDs are mapped to imported fill
rows in `broker_execution_imports`; repeat/partial recovery cannot re-import them.
Changed/corrected execution evidence and legacy partial fills without execution
IDs fail closed for human review rather than silently guessing or overwriting.

Check operation with `tail -n 30 state/execution-capture.log` and `crontab -l`.
To inspect safe aggregate evidence counts:

```bash
.venv/bin/python - <<'PY'
import sqlite3
with sqlite3.connect('file:state/ibkr.db?mode=ro', uri=True) as c:
    print('Retained execution records:', c.execute(
        'SELECT COUNT(*) FROM broker_execution_evidence').fetchone()[0])
PY
```

The table may not exist until the first successful in-hours capture. Private
online database and crontab backups precede installation. Unrelated website jobs
are preserved; deploy receives no sudo access. This poll-based safeguard still
depends on Gateway being available during that day's collection window; a full
day outage requires broker exports or a separately validated history API.

October 13 incident #3 remains unresolved until October 12 paper Activity Statement
CSV and Trade Confirmation records supply actual execution prices, timestamps and
fees. Both the owning client and diagnostic client returned zero executions;
positions alone are not sufficient evidence. Do not restart/retrade to conceal
this mismatch. Statement repair remains append-only, previewed against fresh broker
positions, and requires the actual private reports.

Verification for this change: 121 targeted journal, broker, recovery, collector and
engine tests pass; the touched typed state/core files pass mypy. The broader offline
suite was interrupted after it stopped making progress and is not claimed as a pass.
