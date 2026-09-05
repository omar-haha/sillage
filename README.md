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

Status: **Phase 0 complete** — domain model, trading calendars, and a point-in-time
data layer holding 21 years of history for 13 ETFs. Next: the backtest engine.
See [docs/ROADMAP.md](docs/ROADMAP.md) for the full plan and
[docs/research-log.md](docs/research-log.md) for findings along the way.

```bash
uv sync
uv run sillage data sync      # ~21y of daily bars for the core universe
uv run sillage data check     # gaps, unadjusted splits, stale feeds
```

## Quickstart

```bash
uv sync
uv run sillage --help
```

## Why this exists

Most retail trading repositories keep two codebases: a vectorized pandas backtest and a
separate live script. They silently diverge, and the live one loses money for reasons the
backtest never revealed. `sillage` has one engine, and the backtest is structurally
prevented from seeing data that did not exist at the time it claims to trade.

## License

MIT
