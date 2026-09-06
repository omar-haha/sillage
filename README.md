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

Status: **Phase 3 complete** — a point-in-time data layer, an event-driven backtest
engine with a cost-aware simulated broker, risk metrics validated against an independent
implementation, and the strategy itself. Next: honest validation — held-out data,
walk-forward, parameter sensitivity.
See [docs/ROADMAP.md](docs/ROADMAP.md) for the full plan and
[docs/research-log.md](docs/research-log.md) for findings along the way, including the
ones that went nowhere.

```bash
uv sync
uv run sillage data sync                    # ~21y of daily bars for the core universe
uv run sillage data check                   # gaps, unadjusted splits, stale feeds
uv run sillage backtest -b spy -b 60-40 --report --attribution
uv run sillage timing-luck -s momentum-single     # how much did the rebalance date matter?
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

## The strategy

Once a month: rank twelve ETFs by blended 3/6/12-month momentum, take the top five, drop
any trading below its 200-day average and put that money in Treasury bills, then size the
survivors inversely to their volatility and scale the whole book — using the covariance
matrix, not a sum of individual volatilities — to a 10% volatility target. Run four
staggered copies a week apart and average them.

| 2006-01 → 2026-09 | Momentum | 60/40 | SPY | Equal weight |
|---|---|---|---|---|
| Annualised | 7.27% | 8.35% | **10.93%** | 6.52% |
| Volatility | **9.0%** | 10.9% | 18.9% | 11.6% |
| Sharpe | **0.83** | 0.79 | 0.64 | 0.61 |
| Max drawdown | **−21.8%** | −31.2% | −55.1% | −36.8% |
| Longest drawdown | **697d** | 1,092d | 1,773d | 967d |

Best risk-adjusted return, a third of the index's drawdown, recovers in two years where
the index took five — and the second-lowest return of the four. That is the trade, and
it is the whole trade.

**Where it comes from, and where it does not.** It beat the index in all three of the
sample's down years, but the margin is almost entirely 2008 (+10.6% against −36.2%).
Post-2010 a plain 60/40 beat it on every measure: 10.03% at a Sharpe of 1.00 against
7.04% and 0.80. You are buying crash insurance, and the last fifteen years are what the
premium looks like.

**It survives its own costs.** At five times the modelled commission, spread and impact
it still returns 6.64% at a Sharpe of 0.76. Most retail backtests die here.

The full argument, including the years it loses badly and why, is in
[docs/strategy.md](docs/strategy.md).

## Benchmarks

Run through the same engine, with the same costs. They are not decoration — a result
without them is meaningless. SPY made the most money and was the worst thing to hold: it
spent **four years and ten months** below its 2007 high, peaking 2007-10-09, bottoming
2009-03-09 at −55.1%, and recovering 2012-08-16. Those are the real dates, which is a
decent check that the engine is wired correctly.

`--report` writes a self-contained HTML tearsheet: equity curve, underwater chart,
rolling 12-month return, monthly heatmap, allocation over time, and per-holding
attribution.

## How much of a backtest is luck?

A strategy that rebalances monthly has to pick a day of the month, and nothing makes the
last session better than the third-to-last. Two runs differing only in that choice can
diverge by a percent a year — noise, reported as a result. `sillage timing-luck` runs
the same configuration across four dates a week apart and reports the spread:

```
rebalance date       annualised  Sharpe  max DD
month end                +7.16%    0.83  -16.0%
5 sessions earlier       +7.22%    0.76  -28.3%
10 sessions earlier      +6.25%    0.69  -25.2%
15 sessions earlier      +8.34%    0.94  -18.1%
```

**Two percentage points of annualised return, and a drawdown between −16% and −28%, from
a choice with no meaning.** The same measurement on a 60/40 gives a spread of 0.11 points
— timing luck is a property of *selection*, and a fixed-weight portfolio does not select.

Reporting the month-end run would have claimed a −16% maximum drawdown. Averaging four
staggered tranches gives −21.8%, which is the number above and the one to believe.
Turnover barely moved, because the tranches' trades partly cancel before an order is
produced.

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
