# Trading it at Interactive Brokers

> **Nothing in this document has been verified against a live gateway.** The adapter was
> written before the account existed. Its logic is tested against a fake that models the
> venue's behaviours, which catches design errors and cannot catch protocol errors. The
> first thing to run is `sillage broker-check`, and until that succeeds treat everything
> here as a plan rather than a report.

## Why a second broker at all

Every cost figure in this project so far — the spread estimates, the impact model, the
commission schedule — is a defensible guess, and the backtest that grades them is graded
by the same guesses. It will always agree with itself. Putting a real venue on the other
side of the same orders is the only way to find out whether the guesses were optimistic,
which is what `sillage live divergence` measures and the whole reason Phase 5 is split
in two.

## Setting it up

**1. Get a paper account.** IBKR issues one alongside a funded live account. It mirrors
the live environment closely enough for this purpose and has its own credentials.

**2. Install IB Gateway** (or TWS — Gateway is lighter and has no charting). Log into the
*paper* account.

**3. Enable the API.** In Gateway: `Configure → Settings → API → Settings`.

- Enable ActiveX and Socket Clients.
- Leave **Read-Only API** ticked while you are only inspecting. Untick it before placing
  orders; `broker-check` connects read-only and works either way.
- Add `127.0.0.1` to Trusted IPs.
- Note the socket port.

| | Live | Paper |
|---|---|---|
| TWS | 7496 | **7497** |
| Gateway | 4001 | **4002** |

The defaults in this codebase are the paper ports, deliberately. The live ports are
never a default anywhere.

**4. Install the optional dependency.**

```bash
uv sync --extra ibkr
```

`ib_async` is not in the base install. Backtesting is most of what this does and should
not depend on a wrapper around an undocumented socket protocol.

**5. Check the connection.**

```bash
uv run sillage broker-check --port 7497
```

It connects read-only, reports what the account holds, and disconnects. If it fails it
prints the checklist above.

## Running the fund against it

```bash
uv run sillage live run-once --broker ibkr --port 7497 --journal state/ibkr.db
```

Keep this journal **separate from the simulated one**. Two funds, two records, same
strategy and dates — that is what makes them comparable.

```bash
# every weekday evening, after the close
30 21 * * 1-5  cd /path/to/sillage && uv run sillage data sync && \
               uv run sillage live run-once --journal state/live.db && \
               uv run sillage live run-once --broker ibkr --journal state/ibkr.db
```

`run-once` exits non-zero and explains itself on every refusal — stale data, a
reconciliation mismatch, a tripped kill-switch — so a cron wrapper has something to page
on. It is safe to run twice; the second call finds nothing outstanding.

## How the order flow actually works

The engine's rule is *decide at the close, fill at the next open*. Against a simulator
that is easy: the replay reaches the next open and fills there. Against a real broker it
is not, because a market-on-open order has to be at the exchange **before** the auction.

So a live run submits tonight's decisions immediately, as `MKT` orders with `TIF=OPG`,
against tomorrow's open. The market is shut, so nothing fills; the venue reports them
working and they stay pending. When the next evening's run reaches that open for real,
the adapter recognises its own order references at the venue and **collects the fills
rather than sending a second set**.

That recognition is the load-bearing property of the whole adapter. Every order carries
the engine's `client_order_id` into IBKR as its `orderRef`, and before submitting
anything the adapter asks the venue what it already has — open orders, today's
executions, and the status of each reference. A crash between "sent" and "recorded" is
otherwise indistinguishable from "never sent", and the difference is a duplicated trade.

## What the venue does that the simulator cannot

| | Simulator | IBKR |
|---|---|---|
| Order resolution | always immediate | may still be working when the call returns |
| Fills per order | exactly one | as many as the book takes |
| Price improvement | impossible by construction | happens |
| Refusals | insufficient cash, no price, lot size | halts, margin, unqualifiable contracts, connectivity |
| Positions | whatever the caller says | its own record, which can disagree |

That last row is why reconciliation only becomes real here. With `--broker ibkr` the
fund asks the venue what it holds and refuses to trade on any mismatch. Against the
simulator the same check always passes, because the simulator's positions *are* the
journal's — a tautology, not a test.

## Comparing the two

```bash
uv run sillage live divergence --simulated state/live.db --live state/ibkr.db
```

Pairs each intended trade by session, symbol and direction — collapsing multiple real
fills to a volume-weighted price — and reports the gap in basis points **against the
trader**. Negative is price improvement, which the simulator can never produce.

It ends with a suggested `--cost-scale` for re-running the Phase 3 backtest on measured
assumptions instead of assumed ones. Phase 4 established the strategy survives 5x its
modelled costs, so that figure is the number to watch: comfortably under 5 and the
result stands; near or above it and the backtest needs rewriting rather than adjusting.

**It will take months to mean anything.** A monthly-rebalanced fund produces a handful of
trades per rebalance. Below thirty paired fills the report says so and declines to
suggest a correction.

## Things that will go wrong

- **Client id collisions.** Each connection needs a unique `--client-id`. A stale
  connection holds its id until the gateway notices, so a crashed process can lock you
  out of reconnecting for a minute or two.
- **The gateway restarts itself daily**, usually around midnight, and drops every API
  connection when it does. A process that holds a socket open has to survive that; one
  that connects, works and exits does not care, which is one more argument for
  `run-once` over a resident loop.
- **Paper accounts get delayed market data** unless you subscribe. It does not matter
  here: every decision is made from the local store, and the broker is used only to
  execute. That is a genuine advantage of keeping the data layer separate.
- **`TIF=OPG` is refused after the open.** Submitting during the session needs a plain
  day order, which fills at the prevailing price rather than the auction — a different
  thing, and one the divergence report would attribute to the cost model rather than to
  the timing.
- **Paper fills are not real fills.** IBKR's paper engine simulates the book too. It is a
  much better simulator than this one and it is still a simulator, so the divergence
  report measures the gap between two models, not the gap to reality. Only a funded
  account closes that last step, and nothing here should be traded with real money until
  it has run on paper long enough to surprise you at least twice.
