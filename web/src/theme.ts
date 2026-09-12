/**
 * The visual system, in one place.
 *
 * These are the same values the Plotly tearsheets use, from a palette validated for
 * colour-vision deficiency. Two charts of the same fund rendered by different tools
 * should not disagree about what colour the strategy is.
 *
 * Series colours are assigned by *identity* and never by rank, so filtering a series
 * out cannot repaint the survivors.
 */
export const theme = {
  surface: "#fcfcfb",
  page: "#f9f9f7",
  ink: "#0b0b0b",
  inkSecondary: "#52514e",
  inkMuted: "#898781",
  grid: "#e1e0d9",
  axis: "#c3c2b7",
  border: "rgba(11,11,11,0.10)",
  up: "#184f95",
  down: "#a02020",
  good: "#0ca30c",
  warning: "#fab219",
  critical: "#d03b3b",
  /** Categorical slots, in fixed order. A ninth series folds into "other". */
  series: ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#4a3aa7", "#008300", "#e34948"],
  font: 'system-ui, -apple-system, "Segoe UI", sans-serif',
} as const;

export const money = (value: number): string =>
  value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export const compact = (value: number): string =>
  value.toLocaleString(undefined, { maximumFractionDigits: 0 });

export const percent = (value: number, places = 1): string =>
  `${(value * 100).toFixed(places)}%`;

export const signedPercent = (value: number, places = 1): string =>
  `${value >= 0 ? "+" : ""}${(value * 100).toFixed(places)}%`;
