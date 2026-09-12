/**
 * Does it actually render?
 *
 * Type-checking and bundling both pass on a component that throws the moment it is
 * mounted, so these put the real tree in a real DOM against canned API responses. The
 * cases that matter are the ones nobody builds for: a fund that has never run, and a
 * fund that is in trouble.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./App";

const status = {
  strategy: "balanced",
  universe: "core",
  last_session: "2026-09-11",
  sessions_recorded: 22,
  nav: 99660.16,
  cash: 729.22,
  high_water_mark: 100901.4,
  drawdown: 0.0123,
  positions: 2,
  pending_orders: 0,
  data_staleness_sessions: 0,
  counts: { orders: 2, fills: 2, rejections: 0, nav: 22 },
};

const nav = [
  { session: "2026-08-12", nav: 100000, cash: 100000, gross_exposure: 0, holdings: 0 },
  { session: "2026-09-11", nav: 99660.16, cash: 729.22, gross_exposure: 0.99, holdings: 2 },
];

const positions = [
  { symbol: "SPY", quantity: 78, price: 764.29, value: 59614.62, weight: 0.598 },
  { symbol: "IEF", quantity: 432, price: 91.01, value: 39316.32, weight: 0.394 },
];

const fills = [
  {
    ts: "2026-09-01T13:30:00Z",
    symbol: "SPY",
    quantity: 78,
    price: 762.04,
    commission: 0.35,
    order_id: "a",
  },
];

const metrics = {
  start: "2026-08-12",
  end: "2026-09-11",
  years: 0.082,
  total_return: -0.0034,
  cagr: -0.0406,
  volatility: 0.0436,
  sharpe: -0.92,
  sortino: -1.29,
  max_drawdown: -0.0166,
  calmar: -2.45,
  longest_drawdown_days: 8,
  positive_months: 0,
};

/** Routes a fetch to the right canned payload, or to a failure. */
function serve(overrides: Record<string, unknown> = {}, failWith?: string) {
  const bodies: Record<string, unknown> = {
    "/api/status": status,
    "/api/nav": nav,
    "/api/positions": positions,
    "/api/fills?limit=50": fills,
    "/api/rejections?limit=25": [],
    "/api/metrics": metrics,
    ...overrides,
  };
  vi.stubGlobal("fetch", (url: string) => {
    if (failWith) {
      return Promise.resolve({
        ok: false,
        status: 503,
        statusText: "Service Unavailable",
        json: () => Promise.resolve({ detail: failWith }),
      });
    }
    const path = Object.keys(bodies).find((key) => url.endsWith(key));
    return Promise.resolve({ ok: true, json: () => Promise.resolve(bodies[path ?? ""]) });
  });
}

function mount() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchInterval: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <App />
    </QueryClientProvider>,
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("the dashboard", () => {
  it("renders the fund without throwing", async () => {
    serve();
    mount();
    await waitFor(() => expect(screen.getByText("sillage")).toBeInTheDocument());
    expect(screen.getByText(/balanced on the core universe/)).toBeInTheDocument();
  });

  it("shows the numbers a reader opens it for", async () => {
    serve();
    mount();
    await waitFor(() => expect(screen.getByText("99,660.16")).toBeInTheDocument());
    expect(screen.getByText("Net asset value")).toBeInTheDocument();
    expect(screen.getByText("1.2%")).toBeInTheDocument();
  });

  it("lists every holding in a table, not only in a chart", async () => {
    serve();
    mount();
    await waitFor(() => expect(screen.getByText("Allocation")).toBeInTheDocument());
    // SPY appears in the allocation table and again in the blotter, which is correct;
    // the assertion is that the holdings have a table at all, not that they are unique.
    expect(screen.getAllByRole("rowheader", { name: "SPY" }).length).toBeGreaterThan(0);
    expect(screen.getByRole("rowheader", { name: "IEF" })).toBeInTheDocument();
    expect(screen.getByRole("rowheader", { name: "cash" })).toBeInTheDocument();
  });

  it("says buy or sell in words, not only in colour", async () => {
    serve();
    mount();
    await waitFor(() => expect(screen.getByText("Trade blotter")).toBeInTheDocument());
    expect(screen.getByText("buy")).toBeInTheDocument();
  });

  it("explains itself when the fund has never run", async () => {
    serve({}, "no fund at state/live.db: run `sillage live run-once`");
    mount();
    await waitFor(() =>
      expect(screen.getByText(/no fund at state\/live.db/)).toBeInTheDocument(),
    );
  });

  it("warns loudly when market data has gone stale", async () => {
    // The failure that looks healthiest: the fund keeps reporting a NAV.
    serve({ "/api/status": { ...status, data_staleness_sessions: 9 } });
    mount();
    await waitFor(() =>
      expect(screen.getByText(/Market data is 9 sessions behind/)).toBeInTheDocument(),
    );
  });

  it("warns when the kill-switch would have halted trading", async () => {
    serve({ "/api/status": { ...status, drawdown: 0.31 } });
    mount();
    await waitFor(() => expect(screen.getByText(/kill-switch/)).toBeInTheDocument());
  });

  it("says when there is nothing in the blotter rather than showing an empty table", async () => {
    serve({ "/api/fills?limit=50": [] });
    mount();
    await waitFor(() => expect(screen.getByText("No fills yet.")).toBeInTheDocument());
  });

  it("surfaces refused orders, which no other view would show", async () => {
    serve({
      "/api/rejections?limit=25": [
        { ts: "2026-09-01T13:30:00Z", symbol: "SPY", quantity: 10, reason: "insufficient cash" },
      ],
    });
    mount();
    await waitFor(() => expect(screen.getByText("insufficient cash")).toBeInTheDocument());
  });
});
