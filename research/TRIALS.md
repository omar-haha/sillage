# Research trials ledger

This ledger tracks hypotheses and meaningful inspected variants, including failures.
It is not a count of statistically independent trials: variants share data and rules.
Never reset the history when a new candidate looks good. Record drafts before runs,
then append dates, artifacts, costs, changes and outcomes. Historical completeness is
limited; do not claim the rows below reconstruct every earlier research experiment.

| family | status | inspected configurations / caveats | evidence |
|---|---|---|---|
| Original momentum/balanced controls | provisional core, below target | Historical sensitivities, crypto and risk sweeps exist; comprehensive count not reconstructed. Existing validation already implements deflated-Sharpe diagnostics. | `baselines.json`, `docs/research-log.md`, Phase 4 validation |
| A: diversified futures trend | rejected on feasibility | Four qualifying products inspected for direct history/liquidity; no performance backtest. This is not four successful alpha trials. | `docs/phase8-strategy-specs.md`, 2026-09-24 research log |
| B: ETF relative value | rejected | Six reported configurations: base, free execution/no borrow, 2x costs, 4% borrow, two adjacent entry thresholds. A timezone/rate bug produced invalid preliminary metrics; corrected before final rejection. | 2026-09-29 research log |
| C: sector-neutral momentum | rejected | Seven final configurations in JSON. Six preliminary configurations also inspected with cash credited at Treasury rates; these optimistic-accounting screens are not acceptance evidence. Record both sets, not merely the final seven. Repeated runs of identical configurations are not additional independent trials. | `sector-candidate.json`, 2026-10-11 research log |
| D: stress-conditioned short-horizon reversal | rejected 2026-10-11 | Eight frozen configurations: primary $5K, unchanged $25K size diagnostic, VIX 20/30, entry delay, 2x costs, 4% borrow, crisis spread/borrow. Primary lost $2,192.58, both halves negative. Repeated identical runs are reproducibility checks, not extra independent trials. Quote/borrow/margin unverified; no paper promotion or blend. | `docs/candidate-d-reversal.md`, `reversal-feasibility.json`, `reversal-candidate.json` |
| E: slow value/quality | mechanism screen held 2026-10-11 | One frozen 50/50 US value/QMJ composite plus two predeclared individual diagnostics; 247 months. Composite correlation -0.316, second-half monthly mean -0.061%, so continuation fails. Quality positive in both halves is exploratory, not a replacement primary. Added market betas after first inspection for interpretation, not a gate/parameter change. No investable blend, weight search or paper promotion. | spec commit `7c912e1`, `value-quality-screen.json`, `docs/candidate-e-value-quality.md` |
| F: volatility management | deferred idea, not tested | May overlap with core risk controls; no performance screen or configuration yet. | `docs/candidate-e-value-quality.md` |

For every new experiment retain the frozen specification commit, data/rate hashes,
all meaningful configurations, metrics, chronology, inspected holdouts, accounting
corrections and rejection reason. A period examined for A/B/C is not a pristine
holdout for D. Chronological splits remain useful diagnostics, but selection-aware
statistics and a genuinely future/paper period must address repeated reuse.

Do not turn a raw variant count into an exact deflated-Sharpe probability without
justifying dependence and the assumed effective number of trials. The existing
diagnostic is not a substitute for a cross-experiment ledger.

Reference: [Bailey and Lopez de Prado, The Deflated Sharpe Ratio](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf).
