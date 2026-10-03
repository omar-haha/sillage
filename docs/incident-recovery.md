# Paper incident recovery

Duplicate alerts: while a `sillage-incident` issue remains open, subsequent paper
failures and missed-cycle checks do not create another issue or initial email.
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

An automated historical execution import command is still pending. This procedure
does not authorize resetting the journal, fabricating fills, or resubmitting
unknown orders. Incident #1 requires the historical broker export if Gateway no
longer returns the missing fills.

Evidence reports redact account identifiers, known gateway credentials, browser
keys and common token formats. Public issue sanitization cannot retract historical
copies. Rotate exposed reusable credentials through their issuing service.
