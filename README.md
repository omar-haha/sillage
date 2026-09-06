# sillage

> *dans le sillage de* — in the wake of.

A systematic multi-asset fund manager. It decides **how much of what to own**, sizes
positions by risk rather than conviction, rebalances on a schedule, and refuses to
deviate from its rules.

**The core idea: backtesting, paper trading, and live execution run the same code.**
Only the clock and the broker are swapped.

```
Clock ──▶ Data(as_of) ──▶ Strategy ──▶ Sizing ──▶ Rebalance ──▶ Risk ──▶ Broker ──▶ Journal
  │                                                                        │
  ├ BacktestClock  (replay history)                  SimulatedBroker (costs + slippage) ┤
  └ LiveClock      (wall time)                       IBKRBroker / CcxtBroker           ┘
```

Status: **Phase 1 complete** — a point-in-time data layer holding 21 years of history
for 13 ETFs, and an event-driven backtest engine with a cost-aware simulated broker.
Next: performance metrics and tearsheets, then the strategy itself.
See [docs/ROADMAP.md](docs/ROADMAP.md) for the full plan and
[docs/research-log.md](docs/research-log.md) for findings along the way, including the
ones that went nowhere.

```bash
uv sync
uv run sillage data sync                    # ~21y of daily bars for the core universe
uv run sillage data check                   # gaps, unadjusted splits, stale feeds
uv run sillage backtest --strategy 60-40    # replay it
```

## What the engine does, once per session

```
open   ──▶  execute whatever was decided at the previous close
close  ──▶  mark the book, then decide (if the schedule says so)
```

A decision made from data at a close can only be filled at the *next* open, at a price
that had not printed when the decision was made. There is no flag to disable this and
no fast path that skips it, because the moment there is one, someone will use it.

The data layer enforces the same rule from the other side: every read takes an `as_of`
and cannot return a bar that closed after it. A strategy that wants tomorrow's price
does not get a "no" — there is no code path that hands it over.

## Results so far

Twenty years of benchmarks, run through the real engine with commission, spread and a
volume-scaled impact model. These are not the strategy; they are what the strategy will
have to beat.

| 2007-01-03 → 2026-09-04 | Total return | Annualised | Fills | Costs |
|---|---|---|---|---|
| Buy & hold SPY | +678.8% | 11.00% | 1 | $8 |
| 60/40 SPY/IEF, rebalanced monthly | +387.6% | 8.39% | 16 | $41 |
| Equal weight, 12 ETFs, monthly | +293.3% | 7.21% | 318 | $195 |

Risk-adjusted numbers — the ones that actually decide whether any of this is worth
doing — arrive with the metrics module in Phase 2. A total return without a drawdown
next to it is not a result.

## Is the accounting right?

`tests/golden/` runs buy-and-hold SPY over 5,453 real sessions and checks the identity
that has to hold if every step from order to fill to position to cash to NAV is
correct: **total return equals the fraction of capital invested, times the price return
of what was bought.** It holds to within one basis point.

It deliberately does not assert that the fund matches the index exactly, because it
can't. A hundred thousand dollars does not divide evenly into whole SPY shares, so a
little is left in cash and earns nothing. That drag is real — a live account has it too
— and the test measures it rather than assuming it away.

## Why this exists

Most retail trading repositories keep two codebases: a vectorized pandas backtest and a
separate live script. They silently diverge, and the live one loses money for reasons the
backtest never revealed. `sillage` has one engine, and the backtest is structurally
prevented from seeing data that did not exist at the time it claims to trade.

## Development

```bash
make check      # ruff, mypy, and the full test suite
make test
```

`mypy --strict` covers `core/`, `engine/`, `portfolio/` and `risk/` — the layers where
a type error is a money error.

## License

MIT
