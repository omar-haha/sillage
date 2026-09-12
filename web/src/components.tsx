/**
 * The pieces the dashboard is made of.
 *
 * Chart conventions follow the same rules as the tearsheets, which are worth restating
 * because they are easy to break by accident:
 *
 * - **One y-axis, ever.** Two measures of different scale get two charts or a common
 *   base, never a second axis. It is the single most misleading thing a chart can do.
 * - **Colour follows the entity, not its rank**, so a filter cannot repaint a series.
 * - **Every chart has a readable fallback.** The allocation is a bar chart *and* a
 *   table; the equity curve sits above the numbers it summarises.
 * - **Allocation is bars, not a donut.** A fund holding two positions at 60% and 40% is
 *   exactly the case where a pie is hardest to read, and angle is a worse encoding than
 *   length for values this close.
 */
import type { ReactNode } from "react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { Fill, NavPoint, Position, Rejection } from "./api";
import { compact, money, percent, signedPercent, theme } from "./theme";

export function Card({
  title,
  note,
  children,
}: {
  title?: string;
  note?: string;
  children: ReactNode;
}) {
  return (
    <section className="card">
      {title && <h2>{title}</h2>}
      {note && <p className="note">{note}</p>}
      {children}
    </section>
  );
}

export function Tile({
  label,
  value,
  tone = "plain",
}: {
  label: string;
  value: string;
  tone?: "plain" | "good" | "warning" | "critical";
}) {
  return (
    <div className="tile">
      <div className="tile-label">{label}</div>
      <div className={`tile-value tone-${tone}`}>{value}</div>
    </div>
  );
}

const axis = {
  stroke: theme.axis,
  tick: { fill: theme.inkMuted, fontSize: 11 },
  tickLine: false,
};

/** Shared tooltip styling, so every chart explains itself the same way. */
const tooltip = {
  contentStyle: {
    background: theme.surface,
    border: `1px solid ${theme.border}`,
    borderRadius: 6,
    fontSize: 12,
  },
  labelStyle: { color: theme.inkSecondary },
};

export function EquityCurve({ points }: { points: NavPoint[] }) {
  return (
    <ResponsiveContainer width="100%" height={260}>
      <LineChart data={points} margin={{ top: 8, right: 16, bottom: 4, left: 8 }}>
        <CartesianGrid stroke={theme.grid} vertical={false} />
        <XAxis dataKey="session" {...axis} minTickGap={48} />
        <YAxis
          {...axis}
          domain={["auto", "auto"]}
          tickFormatter={(v) => compact(Number(v))}
          width={70}
        />
        {/* Recharts types a formatter's value loosely, so it is narrowed here rather
            than annotated away. */}
        <Tooltip {...tooltip} formatter={(v) => [money(Number(v)), "NAV"] as [string, string]} />
        <Line
          type="monotone"
          dataKey="nav"
          stroke={theme.series[0]}
          strokeWidth={2}
          dot={false}
          name="NAV"
        />
      </LineChart>
    </ResponsiveContainer>
  );
}

export function Underwater({ points }: { points: NavPoint[] }) {
  let peak = 0;
  const drawdown = points.map((p) => {
    peak = Math.max(peak, p.nav);
    return { session: p.session, drawdown: peak > 0 ? (p.nav / peak - 1) * 100 : 0 };
  });
  return (
    <ResponsiveContainer width="100%" height={180}>
      <AreaChart data={drawdown} margin={{ top: 8, right: 16, bottom: 4, left: 8 }}>
        <CartesianGrid stroke={theme.grid} vertical={false} />
        <XAxis dataKey="session" {...axis} minTickGap={48} />
        <YAxis {...axis} tickFormatter={(v) => `${Number(v).toFixed(0)}%`} width={70} />
        <Tooltip
          {...tooltip}
          formatter={(v) => [`${Number(v).toFixed(2)}%`, "below peak"] as [string, string]}
        />
        <Area
          type="monotone"
          dataKey="drawdown"
          stroke={theme.series[0]}
          strokeWidth={2}
          fill={theme.series[0]}
          fillOpacity={0.12}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}

export function Allocation({ positions, cash }: { positions: Position[]; cash: number }) {
  const total = positions.reduce((sum, p) => sum + (p.value ?? 0), 0) + cash;
  const rows = [
    ...positions
      .filter((p) => p.value != null)
      .map((p) => ({ symbol: p.symbol, weight: ((p.value ?? 0) / total) * 100 })),
    { symbol: "cash", weight: (cash / total) * 100 },
  ].sort((a, b) => b.weight - a.weight);

  return (
    <>
      <ResponsiveContainer width="100%" height={Math.max(120, rows.length * 34)}>
        <BarChart data={rows} layout="vertical" margin={{ top: 4, right: 48, bottom: 4, left: 8 }}>
          <CartesianGrid stroke={theme.grid} horizontal={false} />
          <XAxis type="number" domain={[0, 100]} {...axis} tickFormatter={(v) => `${v}%`} />
          <YAxis type="category" dataKey="symbol" {...axis} width={56} />
          <Tooltip
            {...tooltip}
            formatter={(v) => [`${Number(v).toFixed(1)}%`, "of fund"] as [string, string]}
          />
          <Bar dataKey="weight" radius={[0, 4, 4, 0]} barSize={18}>
            {rows.map((row, index) => (
              <Cell
                key={row.symbol}
                fill={row.symbol === "cash" ? theme.inkMuted : theme.series[index % theme.series.length]}
              />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
      <table>
        <thead>
          <tr>
            <th>symbol</th>
            <th className="num">quantity</th>
            <th className="num">price</th>
            <th className="num">value</th>
            <th className="num">weight</th>
          </tr>
        </thead>
        <tbody>
          {positions.map((p) => (
            <tr key={p.symbol}>
              <th scope="row">{p.symbol}</th>
              <td className="num">{p.quantity.toLocaleString()}</td>
              <td className="num">{p.price == null ? "—" : money(p.price)}</td>
              <td className="num">{p.value == null ? "—" : money(p.value)}</td>
              <td className="num">{p.weight == null ? "—" : percent(p.weight)}</td>
            </tr>
          ))}
          <tr>
            <th scope="row">cash</th>
            <td className="num">—</td>
            <td className="num">—</td>
            <td className="num">{money(cash)}</td>
            <td className="num">{percent(cash / total)}</td>
          </tr>
        </tbody>
      </table>
    </>
  );
}

export function Blotter({ fills }: { fills: Fill[] }) {
  if (!fills.length) return <p className="empty">No fills yet.</p>;
  return (
    <table>
      <thead>
        <tr>
          <th>when</th>
          <th>symbol</th>
          <th>side</th>
          <th className="num">quantity</th>
          <th className="num">price</th>
          <th className="num">commission</th>
        </tr>
      </thead>
      <tbody>
        {fills.map((fill) => (
          <tr key={`${fill.order_id}-${fill.ts}-${fill.price}`}>
            <td>{fill.ts.slice(0, 10)}</td>
            <th scope="row">{fill.symbol}</th>
            {/* Side is a word as well as a colour: eight percent of men cannot rely on
                the colour, and this is the column that says whether you bought or sold. */}
            <td className={fill.quantity > 0 ? "up" : "down"}>
              {fill.quantity > 0 ? "buy" : "sell"}
            </td>
            <td className="num">{Math.abs(fill.quantity).toLocaleString()}</td>
            <td className="num">{money(fill.price)}</td>
            <td className="num">{money(fill.commission)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function Refusals({ rejections }: { rejections: Rejection[] }) {
  if (!rejections.length) return <p className="empty">Nothing has been refused.</p>;
  return (
    <table>
      <thead>
        <tr>
          <th>when</th>
          <th>symbol</th>
          <th className="num">quantity</th>
          <th>why</th>
        </tr>
      </thead>
      <tbody>
        {rejections.map((row, index) => (
          <tr key={`${row.symbol}-${index}`}>
            <td>{row.ts?.slice(0, 10) ?? "—"}</td>
            <th scope="row">{row.symbol}</th>
            <td className="num">{row.quantity.toLocaleString()}</td>
            <td className="reason">{row.reason}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export { signedPercent };
