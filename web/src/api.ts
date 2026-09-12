/**
 * The API client, typed against what the server actually returns.
 *
 * Hand-written rather than generated from the OpenAPI document: there are nine
 * endpoints and a code generator would be more machinery than the thing it generates.
 * The types below are a second statement of the contract, and the tests on the Python
 * side are the first, so a mismatch shows up as a type error rather than an empty chart.
 */

const base = import.meta.env.VITE_API ?? "";

async function get<T>(path: string): Promise<T> {
  const response = await fetch(`${base}/api${path}`);
  if (!response.ok) {
    // The API explains itself on a 503 -- a fund that has never run is a normal state,
    // and the message says how to fix it. Surfacing that beats "Failed to fetch".
    const detail = await response.json().catch(() => null);
    throw new Error(detail?.detail ?? `${response.status} ${response.statusText}`);
  }
  return response.json() as Promise<T>;
}

export interface Status {
  strategy: string;
  universe: string;
  last_session: string | null;
  sessions_recorded: number;
  nav: number;
  cash: number;
  high_water_mark: number;
  drawdown: number;
  positions: number;
  pending_orders: number;
  data_staleness_sessions: number | null;
  counts: Record<string, number>;
}

export interface NavPoint {
  session: string;
  nav: number;
  cash: number;
  gross_exposure: number;
  holdings: number;
}

export interface Position {
  symbol: string;
  quantity: number;
  price: number | null;
  value: number | null;
  weight: number | null;
}

export interface Fill {
  ts: string;
  symbol: string;
  quantity: number;
  price: number;
  commission: number;
  order_id: string;
}

export interface Rejection {
  ts: string | null;
  symbol: string;
  quantity: number;
  reason: string;
}

export interface Metrics {
  start: string;
  end: string;
  years: number;
  total_return: number;
  cagr: number;
  volatility: number;
  sharpe: number;
  sortino: number;
  max_drawdown: number;
  calmar: number;
  longest_drawdown_days: number;
  positive_months: number;
}

export const api = {
  status: () => get<Status>("/status"),
  nav: () => get<NavPoint[]>("/nav"),
  positions: () => get<Position[]>("/positions"),
  fills: () => get<Fill[]>("/fills?limit=50"),
  rejections: () => get<Rejection[]>("/rejections?limit=25"),
  metrics: () => get<Metrics | null>("/metrics"),
};
