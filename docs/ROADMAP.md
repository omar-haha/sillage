# sillage — Design & Roadmap

> A systematic, rules-based multi-asset fund. Research it, backtest it honestly,
> trade it on paper, watch it on a dashboard. Same strategy code in every mode.

**Chosen scope** (2026-09-05)
- Market: multi-asset architecture from day one (US ETFs/equities first, crypto second)
- Strategy family: systematic asset allocation (trend/momentum + volatility targeting)
- Surface: Python core + FastAPI + React dashboard
- Timeline: ~10–12 weeks, built properly

---

## 1. What this system actually does

Once a day (and once a month for the full rebalance) the system:

1. **Pulls prices** for a fixed universe of liquid ETFs (later + crypto).
2. **Computes a signal** per asset — is it trending up, and how strongly, relative to the others?
3. **Decides target weights** — how much of the portfolio each asset should be, scaled so
   the *whole portfolio's* expected volatility hits a target (e.g. 10%/yr).
4. **Diffs targets against what it actually holds**, and emits orders only for meaningful drift.
5. **Checks risk limits** (position caps, leverage, drawdown kill-switch) and rejects violations.
6. **Sends orders to a broker** — a simulated one in backtest/paper, a real one later.
7. **Journals everything** — orders, fills, positions, NAV — into a database.
8. **Serves that state** to a dashboard and generates performance reports.

It is *not* a bot that day-trades or predicts prices. It's an allocator: it decides
*how much of what to own*, follows the rules without emotion, and controls risk.

## 2. The architectural idea that makes this project good

> **The backtest and live trading run the exact same strategy and portfolio code.
> Only the clock and the broker are swapped.**

```
                 ┌──────────────────────────────────────────┐
                 │            engine/loop.py                │
                 │  for each event from Clock:              │
                 │    bars   = DataSource.get(as_of)        │
                 │    weights= Strategy.target_weights(...) │
                 │    weights= Portfolio.size(weights)      │
                 │    orders = Rebalancer.diff(weights,pos) │
                 │    orders = Risk.filter(orders)          │
                 │    fills  = Broker.submit(orders)        │
                 │    Journal.record(...)                   │
                 └──────────────────────────────────────────┘
                      ▲                              ▲
        ┌─────────────┴─────────────┐   ┌────────────┴──────────────┐
        │ BacktestClock (replay)    │   │ SimulatedBroker (costs)   │
        │ LiveClock (wall time)     │   │ IBKRBroker    (paper/real)│
        └───────────────────────────┘   │ CcxtBroker    (crypto)    │
                                        └───────────────────────────┘
```

Most hobby trading repos have two codebases — a pandas backtest and a separate live
script — which silently disagree, and the live one loses money for reasons the backtest
never showed. Having one loop is the difference between a toy and a system, and it's the
thing to lead with in the README.

The cost: the loop must be event-driven and point-in-time correct, which is slower than
a vectorized pandas backtest. Mitigation: the universe is ~15 assets on daily bars, so
20 years is ~5,000 iterations — milliseconds. Speed only matters for parameter sweeps,
which get a separate fast path if needed.

## 3. Trading concepts you need (the short version)

**Return, volatility, drawdown.** Return is what you made. Volatility (annualized stdev
of daily returns) is how bumpy the ride was. Max drawdown is the worst peak-to-trough
loss — the number that actually makes people quit. XEQT is ~100% equity: expect ~15–18%
vol and a ~50% drawdown in a 2008-style event.

**Sharpe ratio** = (return − cash rate) / volatility. Return per unit of risk. A real,
honest long-run Sharpe of 0.8–1.2 for a systematic fund is very good. If your backtest
says 3.0, you have a bug or you've overfit. Assume the latter.

**Momentum / trend following.** The most robustly documented anomaly in finance across
200+ years and every asset class: things that have gone up over the last 3–12 months tend
to keep going up over the next month. It's not magic and it has long painful stretches
(2009, 2020 whipsaws), but it's real, simple, and it's what nearly every managed-futures
fund is built on.

**Absolute vs relative momentum.**
- *Relative (cross-sectional)*: rank assets, own the strongest ones.
- *Absolute (time-series/trend)*: only own something if it's above its own long-term
  average; otherwise hold cash/bonds. This is what cuts drawdowns.
Doing both is "dual momentum", and it is the strategy this project starts with — built
in Phase 3, once there is an engine honest enough to judge it.

**Volatility targeting.** Instead of fixed weights, size positions inversely to how
volatile they've recently been, and scale the whole book so portfolio vol ≈ target. This
is the single highest-value risk technique in the system: it makes returns far more stable
and mechanically de-risks you going into crises (vol rises before crashes finish).

**Turnover and costs.** Every trade costs a commission plus half the bid/ask spread plus
slippage. A strategy that trades every day needs to overcome ~10–50bps/round-trip. Most
retail backtests are profitable only because they ignore this. Monthly rebalancing with
no-trade bands keeps turnover low enough that costs are a rounding error, which is
another reason to start with allocation rather than day-trading.

**Point-in-time correctness (lookahead bias).** If on 2015-03-10 your code can see any
data that wasn't knowable on 2015-03-10, your backtest is fiction. The classic killers:
using the same day's close to both decide and trade, using a current index membership
list to pick stocks in the past (survivorship bias), and using restated fundamentals.
The engine enforces this structurally: `DataSource.get(as_of)` never returns future rows,
and signals computed on close of day T can only trade at the open of T+1.

## 4. The first strategy, concretely

**Universe** (~12 liquid US ETFs, long history, tight spreads):
`SPY QQQ IWM EFA EEM TLT IEF LQD HYG GLD DBC VNQ` + `BIL` (T-bills) as the cash asset.
Later: `BTC/USD`, `ETH/USD`.

**Signal** — for each asset, each month-end:
- Momentum: total return over 3, 6 and 12 months, each skipping the most recent month
  (short-term reversal contaminates it), combined by averaging the *ranks* rather than
  the returns. Blending is close to free here and makes the score much less sensitive to
  any single lookback being the wrong one for a given regime — which one is right varies,
  and there is no way to know in advance which.
- Trend filter: price > 200-day moving average.

Note the two are **not independent layers of protection**. An asset with strong 12-1
momentum is usually already above its 200-day average, so the filter binds mainly at
turning points. That is exactly where it is wanted, but it should not be counted twice.

**Selection**: take the top 5 by momentum score; any that fail the trend filter get
their weight reallocated to `BIL`.

**Weighting**: inverse-volatility across the selected assets (60-day realized vol) for
*relative* sizing, then one scaling factor applied to the whole book so ex-ante
portfolio volatility ≈ 10% annualized, capped at 100% gross (no leverage in v1).

Two details that a first draft of this got wrong, both worth stating precisely:

- **Ex-ante portfolio vol is √(wᵀΣw), not a weighted sum of individual vols.** Inverse-
  vol sizing ignores correlations entirely, and a momentum screen is *systematically*
  prone to picking five assets that are secretly one trade — that is what a momentum
  screen does, it concentrates into whatever has been working. Assuming diversification
  you do not have means a book targeting 10% realizes considerably more. The covariance
  matrix is estimated on the same 60-day window with Ledoit–Wolf shrinkage; at ~5
  selected assets over 60 observations the raw sample estimate is usable, but shrinkage
  costs nothing and the estimator is not the place to be brave.
- **Covariance drives the single gross-scaling number, not the relative weights.**
  Inverse-vol needs no matrix and is robust; wᵀΣw puts a noisy estimate in charge of one
  scalar rather than five weights. Full mean-variance optimization is deliberately not
  used: it is famously an error-maximizer, allocating hardest to whichever asset's
  estimate is most wrong.

**The 10% target is one-sided, and "10% vol" overstates it.** In calm regimes the
strategy would need more than 100% gross to reach 10% and gets capped, so it runs under
target; in violent regimes it scales down correctly. Average realized vol will therefore
land *below* 10%, and returns below what a 10% target implies. That is an accepted
consequence of refusing leverage in v1, not an oversight — but the honest description is
"at most 10%, usually less".

**Rebalance**: monthly, with a no-trade band — only trade a position if its actual weight
has drifted more than 20% relative to target.

**Costs**: 1bp commission + half-spread per asset + a slippage model proportional to
order size vs. average daily volume.

**Benchmarks it must be compared against**: buy-and-hold SPY, a static 60/40, and XEQT.
If it doesn't beat 60/40 on risk-adjusted terms after costs, the report says so.

## 5. Repository layout

```
sillage/
├── README.md                  # the portfolio piece: what, why, screenshots, results
├── pyproject.toml             # uv-managed
├── Makefile                   # make test / lint / backtest / dev
├── docker-compose.yml         # api + postgres + worker
├── .github/workflows/ci.yml   # ruff, mypy, pytest, coverage badge
├── docs/
│   ├── ROADMAP.md             # this file
│   ├── architecture.md        # diagrams + design decisions & tradeoffs
│   ├── strategy.md            # the strategy, its theory, its known weaknesses
│   └── research-log.md        # dated experiments, INCLUDING failures
├── src/sillage/
│   ├── core/        types.py calendar.py money.py     # domain model
│   ├── data/        providers/ store.py universe.py   # ingest → parquet/duckdb
│   ├── strategy/    base.py momentum.py benchmarks.py # signal → target weights
│   ├── portfolio/   sizing.py rebalance.py
│   ├── risk/        limits.py killswitch.py
│   ├── execution/   broker.py simulated.py ibkr.py ccxt_broker.py
│   ├── engine/      clock.py loop.py events.py feed.py journal.py  # the shared loop
│   ├── backtest/    runner.py metrics.py walkforward.py report.py
│   ├── live/        runner.py scheduler.py reconcile.py
│   ├── state/       models.py journal.py              # sqlalchemy
│   ├── api/         app.py routes/                    # fastapi
│   └── cli.py                                         # typer
├── tests/           unit/ integration/ golden/  # golden holds committed fixtures
├── notebooks/       exploratory research only, never imported by src
└── web/             React + Vite + TS dashboard
```

## 6. Stack decisions

| Layer | Choice | Why |
|---|---|---|
| Python | **3.13 via `uv`** (not the system 3.14) | mature, universal wheel coverage for the scientific stack |
| Numerics | numpy, pandas, pyarrow — **confined to `data/` and `backtest/metrics`** | boring and universal, but kept at the edges so domain logic stays plain Python |
| Storage (research) | Parquet + DuckDB | fast columnar reads, zero server, versionable |
| Storage (live state) | SQLite → Postgres | the order/fill/NAV journal needs ACID, not files |
| Market data | yfinance/Stooq (research), `ib_async` (live equities), ccxt→Kraken (crypto) | free tiers, behind one provider interface |
| Calendars | `exchange_calendars` | 24/7 crypto vs. NYSE sessions is a real problem; don't hand-roll |
| Config | pydantic-settings + YAML strategy configs | every backtest reproducible from one file |
| CLI | typer | `sillage backtest --config configs/dual_momentum.yaml` |
| Charts (reports) | plotly → static HTML tearsheet | self-contained, no server needed |
| API | FastAPI + SQLModel | typed, auto OpenAPI docs |
| Frontend | React + Vite + TS + TanStack Query + Recharts | fast, standard, hireable |
| Testing | pytest + hypothesis | property tests on the portfolio accounting |
| Quality | ruff + mypy --strict on `core/` and `engine/` | tight typing where correctness matters |
| Deploy | Docker Compose, optional Fly.io/Railway for a live demo | recruiters clicking a URL > reading code |

Explicitly *not* used: backtrader/zipline/vectorbt. Building the engine is the point of the
project. `PyPortfolioOpt` and `quantstats` are installed as **cross-checks** — the metrics
module is validated against them in tests, then they're kept out of the runtime path.

## 6.5 Brokers from Canada (decided 2026-09-05)

**Alpaca is out.** Canadian residents cannot open a live Alpaca account; paper-only
eligibility is undocumented and their market-data subscriptions have also been reported
as gated in Canada. Not worth planning around.

This costs the project almost nothing, because **the `SimulatedBroker` built in Phase 1
*is* a paper-trading engine** — point it at a live clock and live prices and it paper
trades, with no third party, no onboarding, and no jurisdiction restrictions. Alpaca paper
is just someone else's simulator with a fill model you can't inspect.

The blind spot to be honest about: a self-simulated paper run cannot detect
backtest-vs-reality divergence, because it *is* the backtest. It validates scheduling,
restart safety, state persistence, real-time data flow, reconciliation and alerting. It
cannot validate real fill prices, slippage, order rejections, partial fills, halts, or
3am API timeouts. Hence Phase 5 is split into 5a (own simulator) and 5b (IBKR paper).

| Venue | Paper env | API | Role in this project |
|---|---|---|---|
| **IBKR** | Yes, free, mirrors live | `ib_async` (TWS API, needs IB Gateway; Docker images exist) or the newer Web API | Primary broker. Phase 5b + eventual real money |
| Questrade | **No** | REST, OAuth, manual tokens expire every 7 days | Rejected — no paper mode |
| Wealthsimple | No | Unofficial/reverse-engineered only | Rejected — ToS, not portfolio-appropriate |
| **Kraken** (or Coinbase) | Kraken sandbox | `ccxt` | Crypto sleeve, Phase 7. Binance exited Canada in 2023 |

Note: `ib_insync` — referenced by nearly every tutorial online — was archived after its
author died in 2024. Use the maintained fork `ib_async`.

Trading US-listed ETFs from Canada through IBKR is normal; the universe in §4 is
unaffected. If real money is ever involved, note that US-listed ETFs incur 15% US
withholding tax on dividends in a TFSA but not in an RRSP — an account-type consideration
to look into then, not a design constraint now.

**On building a broker abstraction as its own product**: the `Broker` protocol here, with
three implementations, already *is* one. If it looks clean after Phase 7, extract it as a
package then. Extract a library from working code; do not build one speculatively — broker
abstractions leak badly and every venue's order semantics differ.

## 7. Phase plan

Each phase ends with a **demoable milestone** and a commit worth showing.

### Phase 0 — Foundations (Week 1)
- `uv` project, ruff/mypy/pytest/pre-commit, CI on push.
- Domain types: `Instrument`, `Bar`, `Order`, `Fill`, `Position`, `Portfolio`, `NAV`.
  Use `Decimal` for money, never float.
- Trading calendar abstraction covering NYSE sessions *and* 24/7 crypto.
- Data providers + Parquet store + `sillage data sync`.
- ✅ **Milestone**: `sillage data sync --universe core` pulls 20y of daily bars for 13
  ETFs; `sillage data check` reports gaps/splits; CI green.

### Phase 1 — Backtest engine (Weeks 2–3) — **done, 2026-09-05**
- `Clock`, event loop, `SimulatedBroker` with commission/spread/slippage.
- Portfolio accounting: cash, positions, mark-to-market, dividends, NAV series.
- ✅ **Milestone**: a buy-and-hold SPY backtest reproduces the actual total return of SPY
  over 20 years to within a few basis points. **Do not skip this calibration test** — it is
  what proves the accounting is right, and it's the first thing a knowledgeable reader checks.

**How the milestone was actually met**, since the wording above turned out to hide a
subtlety. A hundred thousand dollars does not divide evenly into whole SPY shares, so a
buy-and-hold fund leaves a little in cash and *cannot* match the index exactly. The
calibration test therefore asserts the identity the accounting must satisfy — total
return equals the invested fraction of capital times the price return of what was
bought — which holds to within one basis point over 5,453 sessions, and separately
measures the cash drag rather than pretending it away. `tests/golden/` runs it against
a committed fixture, so it gives the same answer in CI and next year.

**Dividends need no code.** The stored series is adjusted for splits and distributions,
which makes `close` a total-return price and folds dividends into the price path. An
explicit dividend ledger only becomes necessary alongside a provider that reports
prices as first published — noted in `data/providers/yahoo.py`, not built speculatively.

**A live clock is deliberately not here.** The `Clock` protocol is written so that one
drops in, but writing it now would mean writing scheduling and blocking behaviour with
nothing to test it against. It lands in Phase 5a where it has a job.

### Phase 2 — Metrics & tearsheets (Week 3) — **done, 2026-09-05**
- CAGR, ann. vol, Sharpe, Sortino, max drawdown, Calmar, turnover, exposure, hit rate,
  best/worst month, rolling 12m return, underwater curve, monthly returns heatmap.
- Plotly HTML tearsheet, self-contained, written to `reports/`.
- ✅ **Milestone**: `sillage backtest --strategy 60-40 --report` produces a tearsheet
  whose Sharpe and maxDD match `quantstats` — to machine precision, not three decimals.
  CAGR and Calmar differ by ~1.3bp because quantstats divides elapsed days by 365 and
  this divides by 365.25; the difference is kept and documented rather than matched.

Added beyond the plan, and why:

- **Per-calendar-year breakdown**, in the terminal and the tearsheet. An aggregate
  figure spanning 2008 says nothing about the fifteen years since, and the regime you
  will actually trade in is the recent one.
- **`sillage timing-luck`** — the same strategy across four rebalance dates a week
  apart. See §7.5 below and `research-log.md`.
- **Peak-to-recovery drawdown duration**, rather than days-spent-under-water. The
  question is "how long until I was whole again", which is what decides whether a
  strategy gets abandoned.

Per-asset attribution is deferred to Phase 3, where there is a strategy whose asset
selection is worth attributing.

### Phase 3 — The strategy (Weeks 4–5) — **done, 2026-09-06**
- Dual-momentum implementation on the spec in §4 (`Strategy` protocol, benchmarks and
  the no-trade-band rebalancer landed in Phase 1).
- Inverse-vol sizing, covariance-based portfolio vol targeting with shrinkage.
- **Tranching** — see §7.5. Built here rather than earlier because it can only be
  validated against a strategy that actually has timing luck to remove, and it turned
  out to have a great deal: the untranched strategy's annualised return spans **2.09
  percentage points** across four rebalance dates, against 0.11 for a 60/40. The
  single-date backtest's headline drawdown of −16% was the luckiest of the four; the
  unluckiest was −28%. `momentum` is therefore tranched by default and
  `momentum-single` exists to reproduce the problem.
- Per-asset attribution: which sleeves earned the return, and which ones the strategy
  was holding when it lost. It reconciles exactly to NAV growth, and no single holding
  accounts for more than 17% of profit — the result is not one asset in costume.
- ✅ **Milestone**: full 2006–2026 backtest of the strategy vs SPY / 60-40 / XEQT, with a
  written interpretation in `docs/strategy.md` — including where it loses, and including
  a separate reading of 2010 onward on its own. Dual momentum's reputation is built
  disproportionately on 2000–02 and 2008; post-2009 the family has generally trailed
  plain equity, and 2020 moved too fast for a monthly signal — it sold near the bottom
  and bought back higher. If the last fifteen years alone are not acceptable, the
  twenty-year number is not the one to believe.


### 7.5 — Rebalance timing luck, and tranching

A strategy that rebalances monthly must rebalance on *some* day of the month, and
nothing makes the last session better than the third-to-last. But two runs of the
identical strategy differing only in that date will hold different things for weeks at
a time. The effect is documented (Hoffstein's "rebalance timing luck") and can reach a
percent a year or more. It is noise reported as a result.

**Measured first.** `sillage timing-luck` runs a configuration across four dates a week
apart. On the fixed-weight benchmarks the spread is 0.08–0.11% a year — near zero, and
correctly so: timing luck is a property of *selection*, and a 60/40 does not select.
A momentum strategy that rotates its holdings will not be so lucky.

**The remedy is tranching**, in Phase 3: split capital into four sub-portfolios on
staggered monthly schedules and average their target weights, so no single date drives
the book. Cheap here, because `target_weights` is already a pure function of `as_of` —
run it four times and average. Net turnover does not rise much, because the tranches'
trades partly net against each other at the aggregate level.

**A second effect found while measuring the first.** A single-asset book that never
rebalances still shows a 0.62% annual spread across the same four dates, because the
offset changes which day the money went in. Entry-date luck is larger than rebalance
luck there, and nobody thinks to vary a backtest's start date. Phase 4 should.

### Phase 4 — Honest validation (Week 6)
This is the phase that separates the project from every other GitHub trading repo.
- Train/test split (fit on 2006–2017, never touch 2018–2026 until the end).
- Walk-forward analysis with rolling re-optimization.
- Parameter sensitivity heatmaps — you want a broad *plateau* of decent results, not a
  lone spike. A spike means overfit.
- Cost sensitivity: at what cost level does the edge vanish?
- Monte Carlo / block bootstrap on returns for a confidence interval on Sharpe.
- Note the number of configurations tested, and deflate the Sharpe accordingly.
- Vary the *start date* as well as the parameters — see §7.5. An arbitrary start is a
  free parameter that never gets counted as one.
- On "these are the standard values, I did not optimize them": true, and weaker than it
  sounds. The literature optimized them, largely on this same US data. That is
  collective overfitting, and the deflation should account for trials nobody here ran.
- ✅ **Milestone**: `docs/research-log.md` with dated entries, including at least two
  documented failures and what you changed because of them.

### Phase 5 — Live paper trading (Weeks 7–9)

Split in two, because self-simulated paper trading and real-broker paper trading test
completely different failure modes.

**5a — Own simulator on a live clock (Week 7–8).** Tests the operational machinery.
- State DB + append-only journal, idempotent order submission, restart-safe.
- **Reconciliation**: on every start, compare held positions against the journal and
  refuse to trade on mismatch. This is where real systems break.
- Risk limits + drawdown kill-switch + alerting (email/Discord webhook).
- Scheduler (APScheduler in-process, or cron + `sillage live run-once`).
- ✅ **Milestone**: runs unattended for two weeks, rebalances on schedule, survives a
  kill -9 mid-session with no double-submitted or orphaned orders.

**5b — IBKR paper account (Week 9).** Same engine, `IBKRBroker` swapped for
`SimulatedBroker`. Tests reality.
- IB Gateway in Docker, connection lifecycle, reconnect-on-drop.
- Handle what a real venue does and a simulator doesn't: rejections, partial fills,
  price improvement, halts, timeouts.
- ✅ **Milestone**: a divergence report comparing 5a fills against 5b fills over the same
  period — where the simulator was optimistic and by how much. Feed the findings back into
  the cost model, then re-run the Phase 3 backtest with corrected assumptions. This
  before/after is one of the strongest sections the README can have.

### Phase 6 — API & dashboard (Weeks 9–10)
- FastAPI: `/nav`, `/positions`, `/orders`, `/metrics`, `/signals`, `/backtests/{id}`.
- React dashboard: equity curve vs benchmark, drawdown chart, current allocation donut,
  target-vs-actual weight table, trade blotter, live risk-limit status, strategy signals.
- ✅ **Milestone**: `docker compose up` → working dashboard on a fresh machine.

### Phase 7 — Multi-asset & polish (Weeks 10–12)
- `CcxtBroker` (Kraken) + crypto data, 24/7 calendar path, crypto sleeve added to the universe
  with its own weight cap (crypto vol is 4–5x equities; without a cap it dominates the
  vol-targeted book).
- README: hero screenshot, GIF of the dashboard, results table, architecture diagram,
  honest "limitations & what I'd do next" section.
- Deployed demo URL with seeded paper data.
- ✅ **Milestone**: someone who's never seen the repo understands what it does in 60s.

## 8. Traps to design against

| Trap | Where it bites | Defense in this design |
|---|---|---|
| Lookahead bias | signals see the future | `as_of` gating in the data layer; signal at close T → fill at open T+1 |
| Survivorship bias | dead tickers missing | fixed ETF universe in v1; if single stocks are added, use point-in-time constituents |
| Ignoring costs | backtest ≫ reality | explicit cost model, cost-sensitivity analysis is a required phase-4 output |
| Overfitting | tuning until it looks great | held-out test set, walk-forward, plateau check, logged number of trials |
| Float money errors | cents drift over 5,000 bars | `Decimal` in the accounting layer, property tests asserting the books balance |
| Backtest/live drift | live behaves differently | one shared engine; paper-vs-backtest divergence report |
| Broker desync | duplicate/orphan orders | idempotency keys, startup reconciliation, refuse-to-trade on mismatch |
| Silent failure | a cron job dies, you don't notice | heartbeat + alerting on missed rebalances |

## 9. Expectation setting

A well-built version of this, honestly backtested and after costs, plausibly delivers
something in the range of *similar returns to a stock index with meaningfully smaller
drawdowns* — a better Sharpe, not a bigger number. It will underperform a pure equity
portfolio during long bull markets, and that underperformance will be psychologically
brutal precisely because you built it. Trend systems also tend to have win rates near or
below 50% — they make money from a few large moves, not from being right often.

The real deliverables are: a system that provably does what it says, a research process
that doesn't lie to you, and enough understanding to judge whether the strategy is worth
your own money later. Treat "beats the market" as a hypothesis the code exists to test,
not the goal.

This document is a plan, not financial advice. Nothing here should be traded with real
money until it has run on paper long enough to surprise you at least twice.
