/**
 * The dashboard.
 *
 * One screen that answers, in order: is the fund alright, what has it been doing, what
 * does it hold, and what has it been refused. That order is deliberate -- the things
 * that indicate a problem come first, because a dashboard is read when something feels
 * wrong and skimmed the rest of the time.
 *
 * Two panels exist specifically because of failures found in Phase 5. Stale data has a
 * banner, because a fund whose prices stopped updating keeps reporting a NAV and looks
 * entirely healthy. Refusals have their own card, because a fund that places orders and
 * fills none is invisible from every other view.
 */
import { useQueries } from "@tanstack/react-query";
import { api } from "./api";
import { Allocation, Blotter, Card, EquityCurve, Refusals, Tile, Underwater } from "./components";
import { money, percent, signedPercent } from "./theme";

export default function App() {
  const [status, nav, positions, fills, rejections, metrics] = useQueries({
    queries: [
      { queryKey: ["status"], queryFn: api.status },
      { queryKey: ["nav"], queryFn: api.nav },
      { queryKey: ["positions"], queryFn: api.positions },
      { queryKey: ["fills"], queryFn: api.fills },
      { queryKey: ["rejections"], queryFn: api.rejections },
      { queryKey: ["metrics"], queryFn: api.metrics },
    ],
  });

  if (status.isPending) {
    return <main className="sheet"><p className="empty">Loading…</p></main>;
  }

  if (status.error) {
    return (
      <main className="sheet">
        <h1>sillage</h1>
        <Card title="Nothing to show yet">
          {/* The API explains itself on a 503, and that message says how to fix it. */}
          <p className="empty">{status.error.message}</p>
        </Card>
      </main>
    );
  }

  const fund = status.data!;
  const stale = fund.data_staleness_sessions;
  const halted = fund.drawdown > 0.25;

  return (
    <main className="sheet">
      <header>
        <h1>sillage</h1>
        <p className="lede">
          {fund.strategy} on the {fund.universe} universe · {fund.sessions_recorded} sessions
          recorded · last {fund.last_session ?? "never"}
        </p>
      </header>

      {stale != null && stale > 3 && (
        <div className="banner critical">
          Market data is {stale} sessions behind. The fund will refuse to trade until{" "}
          <code>sillage data sync</code> has run — but it will keep reporting a NAV, which
          is why this banner exists.
        </div>
      )}
      {halted && (
        <div className="banner critical">
          Drawdown is {percent(fund.drawdown)} from the high-water mark. The kill-switch
          halts trading past 25% and expects a human to decide whether to resume.
        </div>
      )}
      {fund.pending_orders > 0 && (
        <div className="banner">
          {fund.pending_orders} order{fund.pending_orders === 1 ? "" : "s"} waiting for the
          next open.
        </div>
      )}

      <div className="tiles">
        <Tile label="Net asset value" value={money(fund.nav)} />
        <Tile
          label="Drawdown from peak"
          value={percent(fund.drawdown)}
          tone={fund.drawdown > 0.15 ? "critical" : fund.drawdown > 0.05 ? "warning" : "plain"}
        />
        <Tile label="Cash" value={money(fund.cash)} />
        <Tile label="Positions" value={String(fund.positions)} />
        {metrics.data && (
          <>
            <Tile label="Sharpe" value={metrics.data.sharpe.toFixed(2)} />
            <Tile label="Annualised" value={signedPercent(metrics.data.cagr, 2)} />
          </>
        )}
      </div>

      {nav.data && nav.data.length > 1 && (
        <>
          <Card
            title="Value over time"
            note="What the fund is worth at every close, from the journal it writes as it trades."
          >
            <EquityCurve points={nav.data} />
          </Card>
          <Card
            title="Underwater"
            note="How far below the previous high-water mark. The chart that decides whether a strategy is holdable."
          >
            <Underwater points={nav.data} />
          </Card>
        </>
      )}

      {positions.data && (
        <Card
          title="Allocation"
          note="Bars rather than a donut: angle is a poor encoding for values this close together, and the table below is the readable fallback."
        >
          <Allocation positions={positions.data} cash={fund.cash} />
        </Card>
      )}

      {metrics.data && (
        <Card title="Risk and return" note="Computed on the same definitions the backtest uses.">
          <table>
            <tbody>
              {(
                [
                  ["Period", `${metrics.data.start} to ${metrics.data.end}`],
                  ["Total return", signedPercent(metrics.data.total_return, 2)],
                  ["Annualised", signedPercent(metrics.data.cagr, 2)],
                  ["Volatility", percent(metrics.data.volatility)],
                  ["Sharpe", metrics.data.sharpe.toFixed(2)],
                  ["Sortino", metrics.data.sortino.toFixed(2)],
                  ["Max drawdown", percent(metrics.data.max_drawdown)],
                  ["Longest drawdown", `${metrics.data.longest_drawdown_days} days`],
                ] as const
              ).map(([label, value]) => (
                <tr key={label}>
                  <th scope="row">{label}</th>
                  <td className="num">{value}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}

      <Card title="Trade blotter" note="Every fill, newest first.">
        <Blotter fills={fills.data ?? []} />
      </Card>

      <Card
        title="Refused orders"
        note="Its own panel because it is the surface every quiet live failure shows up on: a fund placing orders and filling none looks healthy everywhere else."
      >
        <Refusals rejections={rejections.data ?? []} />
      </Card>

      <footer>
        Read-only. The fund trades from <code>sillage live run-once</code>; nothing here can
        place an order. Money crosses the API as floating point — this is a view, the
        journal is the record.
      </footer>
    </main>
  );
}
