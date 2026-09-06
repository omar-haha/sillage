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

Status: **Phase 2 complete** — a point-in-time data layer holding 21 years of history
for 13 ETFs, an event-driven backtest engine with a cost-aware simulated broker, and
risk metrics validated against an independent implementation. Next: the strategy.
See [docs/ROADMAP.md](docs/ROADMAP.md) for the full plan and
[docs/research-log.md](docs/research-log.md) for findings along the way, including the
ones that went nowhere.

```bash
uv sync
uv run sillage data sync                    # ~21y of daily bars for the core universe
uv run sillage data check                   # gaps, unadjusted splits, stale feeds
uv run sillage backtest -s 60-40 -b spy --report   # replay it, write a tearsheet
uv run sillage timing-luck -s 60-40               # how much did the rebalance date matter?
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

| 2007-01-03 → 2026-09-04 | SPY | 60/40 | Equal weight |
|---|---|---|---|
| Annualised | 11.00% | 8.39% | 7.21% |
| Volatility | 19.6% | 10.9% | 12.1% |
| Sharpe | 0.63 | **0.80** | 0.64 |
| Max drawdown | −55.1% | −31.0% | −36.8% |
| Longest drawdown | 4.9 years | 3.0 years | 2.6 years |
| Cost drag | 0.000%/yr | 0.001%/yr | 0.005%/yr |

The point of the risk columns is the one the return column hides: SPY made the most
money and was the worst investment to actually hold. It spent **four years and ten
months** below its 2007 high — peak 2007-10-09, trough 2009-03-09 at −55.1%, back to
even 2012-08-16. Those are the real dates, which is a decent check that the engine is
wired correctly.

`--report` writes a self-contained HTML tearsheet: equity curve, underwater chart,
rolling 12-month return, monthly heatmap, exposure, and a per-calendar-year table.

## How much of a backtest is luck?

A strategy that rebalances monthly has to pick a day of the month, and nothing makes the
last session better than the third-to-last. Two runs differing only in that choice can
diverge by a percent a year — noise, reported as a result. `sillage timing-luck` runs
the same configuration across four dates a week apart and reports the spread:

```
rebalance date       annualised  Sharpe  max DD
month end                +8.39%    0.80  -31.0%
5 sessions earlier       +8.49%    0.79  -29.2%
10 sessions earlier      +8.38%    0.78  -30.0%
15 sessions earlier      +8.42%    0.77  -30.4%
```

Eleven basis points — near zero, and that is the expected answer here. Timing luck comes
from *selection*, and a 60/40 does not select; it wants the same 60/40 whichever day it
looks. A momentum strategy that rotates its holdings will not get off so lightly, which
is why the measurement exists before the strategy does.

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
a type error is a money error. Risk statistics are cross-checked against `quantstats`,
an independent implementation, and agree to machine precision on Sharpe, Sortino,
volatility and max drawdown.

## License

MIT
