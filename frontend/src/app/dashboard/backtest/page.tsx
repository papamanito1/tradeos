"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import {
  Play, ChevronDown, ChevronUp, TrendingUp, TrendingDown,
  BarChart2, Clock, Zap, Shield, DollarSign, Activity,
  RefreshCw, Info, Bot, Calendar,
} from "lucide-react";
import {
  AreaChart, Area, LineChart, Line,
  XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, ReferenceLine, ComposedChart, Bar,
} from "recharts";
import { strategiesApi, backtestApi } from "@/lib/api";
import {
  BacktestResult, BacktestMetrics, BacktestTrade, StrategyType,
} from "@/types";
import { formatUSD, formatPct, pnlColor, cn } from "@/lib/utils";

// ─── Helpers ─────────────────────────────────────────────────────────────────
function fmt(n: number, decimals = 2) {
  return n.toFixed(decimals);
}
function fmtDate(iso: string, timeframe: string): string {
  const d = new Date(iso);
  const tfSecs = {
    "1m": 60, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "4h": 14400, "1d": 86400,
  }[timeframe] ?? 3600;
  if (tfSecs < 3600) {
    return d.toLocaleTimeString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  }
  if (tfSecs < 86400) {
    return d.toLocaleDateString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  }
  return d.toLocaleDateString([], { month: "short", day: "numeric", year: "2-digit" });
}
function fmtDateShort(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleDateString([], { month: "short", day: "numeric", year: "2-digit" });
}
function fmtDateTime(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleString([], {
    month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
  });
}

// Metric color classification
function ratioColor(v: number, good = 1, great = 2): string {
  if (v >= great) return "text-green-400";
  if (v >= good) return "text-yellow-400";
  if (v > 0) return "text-orange-400";
  return "text-red-400";
}
function ddColor(v: number): string {
  if (v < 5) return "text-green-400";
  if (v < 15) return "text-yellow-400";
  if (v < 30) return "text-orange-400";
  return "text-red-400";
}
function winrateColor(v: number): string {
  if (v >= 60) return "text-green-400";
  if (v >= 45) return "text-yellow-400";
  return "text-red-400";
}

const SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "ADA/USDT", "DOGE/USDT"];
const TIMEFRAMES = ["1m", "5m", "15m", "1h", "4h", "1d"];

// ── Year → bar count presets ──────────────────────────────────────────────────
const BARS_PER_YEAR: Record<string, number> = {
  "1m": 525_600, "5m": 105_120, "15m": 35_040,
  "1h": 8_760,   "4h": 2_190,   "1d": 365,
};
const YEAR_PRESETS = [0.25, 0.5, 1, 2, 3] as const;

// ─── Stat Card ────────────────────────────────────────────────────────────────
function StatCard({
  label, value, sub, valueClass = "text-white", tooltip,
}: {
  label: string;
  value: string;
  sub?: string;
  valueClass?: string;
  tooltip?: string;
}) {
  return (
    <div className="bg-neutral-900 border border-neutral-800 rounded-lg p-3 relative group">
      <div className="flex items-center gap-1 mb-1.5">
        <span className="text-[11px] uppercase tracking-wider text-neutral-500 font-medium">{label}</span>
        {tooltip && (
          <div className="relative hidden group-hover:block">
            <span className="absolute bottom-full left-0 bg-neutral-800 text-neutral-300 text-[10px] px-2 py-1 rounded whitespace-nowrap z-10 border border-neutral-700">
              {tooltip}
            </span>
          </div>
        )}
      </div>
      <div className={`text-lg font-semibold tabular-nums leading-none ${valueClass}`}>{value}</div>
      {sub && <div className="text-[11px] text-neutral-500 mt-1">{sub}</div>}
    </div>
  );
}

// ─── Equity Chart ─────────────────────────────────────────────────────────────
function EquityChart({
  data, initial, timeframe,
}: {
  data: { timestamp: string; equity: number }[];
  initial: number;
  timeframe: string;
}) {
  const final = data[data.length - 1]?.equity ?? initial;
  const up = final >= initial;
  const color = up ? "#22c55e" : "#ef4444";
  const minEq = Math.min(...data.map((d) => d.equity), initial) * 0.995;
  const maxEq = Math.max(...data.map((d) => d.equity), initial) * 1.005;

  // Sample for performance (max 600 points)
  const step = Math.max(1, Math.floor(data.length / 600));
  const sampled = data.filter((_, i) => i % step === 0 || i === data.length - 1);

  return (
    <ResponsiveContainer width="100%" height={220}>
      <AreaChart data={sampled} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
        <defs>
          <linearGradient id="eqGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor={color} stopOpacity={0.25} />
            <stop offset="95%" stopColor={color} stopOpacity={0.02} />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="3 3" stroke="#262626" vertical={false} />
        <XAxis
          dataKey="timestamp"
          tick={{ fill: "#525252", fontSize: 10 }}
          tickFormatter={(v) => fmtDate(v, timeframe)}
          interval="preserveStartEnd"
          tickLine={false}
          axisLine={false}
        />
        <YAxis
          tick={{ fill: "#525252", fontSize: 10 }}
          tickFormatter={(v) => `$${(v / 1000).toFixed(1)}k`}
          domain={[minEq, maxEq]}
          tickLine={false}
          axisLine={false}
          width={52}
        />
        <Tooltip
          contentStyle={{
            background: "#171717",
            border: "1px solid #404040",
            borderRadius: "6px",
            fontSize: 12,
          }}
          labelStyle={{ color: "#737373" }}
          labelFormatter={(v) => fmtDateTime(v)}
          formatter={(v: number) => [formatUSD(v), "Equity"]}
        />
        <ReferenceLine
          y={initial}
          stroke="#404040"
          strokeDasharray="6 3"
          label={{ value: "Start", fill: "#525252", fontSize: 10 }}
        />
        <Area
          type="monotone"
          dataKey="equity"
          stroke={color}
          strokeWidth={1.5}
          fill="url(#eqGrad)"
          dot={false}
          activeDot={{ r: 3, fill: color }}
          isAnimationActive={false}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}

// ─── Drawdown Chart ───────────────────────────────────────────────────────────
function DrawdownChart({
  ddSeries, equityCurve, timeframe,
}: {
  ddSeries: number[];
  equityCurve: { timestamp: string; equity: number }[];
  timeframe: string;
}) {
  const step = Math.max(1, Math.floor(ddSeries.length / 600));
  const data = ddSeries
    .filter((_, i) => i % step === 0 || i === ddSeries.length - 1)
    .map((dd, idx) => ({
      timestamp: equityCurve[idx * step]?.timestamp ?? "",
      drawdown: -dd,
    }));

  const minDD = Math.min(...data.map((d) => d.drawdown)) * 1.1;

  return (
    <ResponsiveContainer width="100%" height={110}>
      <AreaChart data={data} margin={{ top: 4, right: 12, left: 0, bottom: 0 }}>
        <defs>
          <linearGradient id="ddGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor="#ef4444" stopOpacity={0.3} />
            <stop offset="95%" stopColor="#ef4444" stopOpacity={0.05} />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="3 3" stroke="#262626" vertical={false} />
        <XAxis
          dataKey="timestamp"
          tick={{ fill: "#525252", fontSize: 9 }}
          tickFormatter={(v) => fmtDate(v, timeframe)}
          interval="preserveStartEnd"
          tickLine={false}
          axisLine={false}
        />
        <YAxis
          tick={{ fill: "#525252", fontSize: 9 }}
          tickFormatter={(v) => `${v.toFixed(1)}%`}
          domain={[minDD, 0]}
          tickLine={false}
          axisLine={false}
          width={42}
        />
        <Tooltip
          contentStyle={{
            background: "#171717",
            border: "1px solid #404040",
            borderRadius: "6px",
            fontSize: 11,
          }}
          labelFormatter={(v) => fmtDateTime(v)}
          formatter={(v: number) => [`${Math.abs(v).toFixed(2)}%`, "Drawdown"]}
        />
        <ReferenceLine y={0} stroke="#404040" />
        <Area
          type="monotone"
          dataKey="drawdown"
          stroke="#ef4444"
          strokeWidth={1}
          fill="url(#ddGrad)"
          dot={false}
          isAnimationActive={false}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}

// ─── Monthly Returns Heatmap ──────────────────────────────────────────────────
function MonthlyHeatmap({ monthly }: { monthly: Record<string, number> }) {
  if (!monthly || Object.keys(monthly).length < 2) return null;

  const months = Object.keys(monthly).sort();
  const years = [...new Set(months.map((m) => m.slice(0, 4)))];
  const MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

  function cellColor(v: number | undefined): string {
    if (v === undefined) return "bg-neutral-900";
    if (v > 10) return "bg-green-500";
    if (v > 5) return "bg-green-600";
    if (v > 2) return "bg-green-700";
    if (v > 0) return "bg-green-900";
    if (v > -2) return "bg-red-900";
    if (v > -5) return "bg-red-700";
    if (v > -10) return "bg-red-600";
    return "bg-red-500";
  }

  const maxAbs = Math.max(...Object.values(monthly).map(Math.abs), 1);

  return (
    <div className="bg-neutral-900 border border-neutral-800 rounded-lg p-4">
      <div className="flex items-center gap-2 mb-3">
        <BarChart2 size={13} className="text-neutral-500" />
        <span className="text-xs font-medium text-neutral-400 uppercase tracking-wider">
          Monthly Returns
        </span>
      </div>

      <div className="overflow-x-auto">
        <table className="text-[10px] font-mono border-separate border-spacing-0.5 min-w-full">
          <thead>
            <tr>
              <th className="text-neutral-600 text-right pr-2 font-normal">Year</th>
              {MONTH_NAMES.map((m) => (
                <th key={m} className="text-neutral-600 font-normal px-1 text-center w-9">{m}</th>
              ))}
              <th className="text-neutral-600 font-normal px-1 text-center">YTD</th>
            </tr>
          </thead>
          <tbody>
            {years.map((year) => {
              const ytd = Object.entries(monthly)
                .filter(([k]) => k.startsWith(year))
                .reduce((acc, [, v]) => acc * (1 + v / 100), 1) - 1;

              return (
                <tr key={year}>
                  <td className="text-neutral-500 text-right pr-2 py-0.5">{year}</td>
                  {Array.from({ length: 12 }, (_, mi) => {
                    const key = `${year}-${String(mi + 1).padStart(2, "0")}`;
                    const val = monthly[key];
                    return (
                      <td key={mi} className="text-center p-0">
                        <div
                          className={`rounded text-[9px] py-1 px-0.5 ${cellColor(val)} ${
                            val !== undefined ? "text-white" : "text-neutral-800"
                          }`}
                        >
                          {val !== undefined ? `${val > 0 ? "+" : ""}${val.toFixed(1)}` : "·"}
                        </div>
                      </td>
                    );
                  })}
                  <td className="text-center p-0 pl-1">
                    <div
                      className={`rounded text-[9px] py-1 px-1 ${cellColor(ytd * 100)} text-white`}
                    >
                      {ytd >= 0 ? "+" : ""}{(ytd * 100).toFixed(1)}
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ─── Trade Table ──────────────────────────────────────────────────────────────
function TradeTable({ trades }: { trades: BacktestTrade[] }) {
  const [sortKey, setSortKey] = useState<keyof BacktestTrade>("id");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");
  const [page, setPage] = useState(0);
  const PAGE_SIZE = 25;

  function toggleSort(key: keyof BacktestTrade) {
    if (sortKey === key) setDir((d) => d === "asc" ? "desc" : "asc");
    else { setSortKey(key); setSortDir("asc"); }
    setPage(0);
  }
  function setDir(fn: (d: "asc" | "desc") => "asc" | "desc") {
    setSortDir(fn);
  }

  const sorted = [...trades].sort((a, b) => {
    const av = a[sortKey], bv = b[sortKey];
    const cmp = typeof av === "number" && typeof bv === "number"
      ? av - bv
      : String(av).localeCompare(String(bv));
    return sortDir === "asc" ? cmp : -cmp;
  });

  const pageCount = Math.ceil(sorted.length / PAGE_SIZE);
  const rows = sorted.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);

  function Col({ k, label, right }: { k: keyof BacktestTrade; label: string; right?: boolean }) {
    return (
      <th
        className={`px-3 py-2 text-[11px] uppercase tracking-wider text-neutral-500 font-medium cursor-pointer select-none hover:text-neutral-300 ${right ? "text-right" : "text-left"}`}
        onClick={() => toggleSort(k)}
      >
        <span className="flex items-center gap-1 justify-end">
          {sortKey === k && (sortDir === "asc" ? <ChevronUp size={10} /> : <ChevronDown size={10} />)}
          {label}
        </span>
      </th>
    );
  }

  const reasonLabel: Record<string, string> = {
    stop_loss: "SL",
    take_profit: "TP",
    signal_reversal: "Rev",
    end_of_data: "EOD",
  };
  const reasonColor: Record<string, string> = {
    stop_loss: "text-red-400 bg-red-500/10",
    take_profit: "text-green-400 bg-green-500/10",
    signal_reversal: "text-blue-400 bg-blue-500/10",
    end_of_data: "text-neutral-400 bg-neutral-800",
  };

  return (
    <div className="bg-neutral-900 border border-neutral-800 rounded-lg overflow-hidden">
      <div className="flex items-center justify-between px-4 py-3 border-b border-neutral-800">
        <span className="text-sm font-semibold text-white">Trade Log</span>
        <span className="text-xs text-neutral-500">{trades.length} trades</span>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead className="border-b border-neutral-800">
            <tr>
              <Col k="id" label="#" />
              <Col k="side" label="Side" />
              <Col k="entry_time" label="Entry" />
              <Col k="exit_time" label="Exit" />
              <Col k="entry_price" label="Entry $" right />
              <Col k="exit_price" label="Exit $" right />
              <Col k="pnl" label="PnL" right />
              <Col k="pnl_pct" label="%" right />
              <Col k="rr_actual" label="R:R" right />
              <Col k="fees" label="Fees" right />
              <Col k="duration_human" label="Dur." right />
              <Col k="reason" label="Exit" />
            </tr>
          </thead>
          <tbody>
            {rows.map((t) => (
              <tr key={t.id} className="border-b border-neutral-800/60 hover:bg-neutral-800/30 transition-colors">
                <td className="px-3 py-2 text-neutral-600">{t.id}</td>
                <td className={`px-3 py-2 uppercase font-semibold text-[11px] ${t.side === "long" ? "text-green-400" : "text-red-400"}`}>
                  {t.side}
                </td>
                <td className="px-3 py-2 text-neutral-400 font-mono">{fmtDateTime(t.entry_time)}</td>
                <td className="px-3 py-2 text-neutral-400 font-mono">{fmtDateTime(t.exit_time)}</td>
                <td className="px-3 py-2 text-right font-mono text-neutral-300">
                  {t.entry_price > 100 ? t.entry_price.toFixed(2) : t.entry_price.toFixed(4)}
                </td>
                <td className="px-3 py-2 text-right font-mono text-neutral-300">
                  {t.exit_price > 100 ? t.exit_price.toFixed(2) : t.exit_price.toFixed(4)}
                </td>
                <td className={`px-3 py-2 text-right font-mono font-semibold ${pnlColor(t.pnl)}`}>
                  {formatUSD(t.pnl)}
                </td>
                <td className={`px-3 py-2 text-right font-mono ${pnlColor(t.pnl_pct)}`}>
                  {t.pnl_pct > 0 ? "+" : ""}{t.pnl_pct.toFixed(2)}%
                </td>
                <td className={`px-3 py-2 text-right font-mono font-semibold ${
                  (t as any).rr_actual == null ? "text-neutral-700" :
                  (t as any).rr_actual >= 1.2 ? "text-green-400" :
                  (t as any).rr_actual >= 0   ? "text-yellow-400" : "text-red-400"
                }`}>
                  {(t as any).rr_actual != null ? ((t as any).rr_actual as number).toFixed(2) : "—"}
                </td>
                <td className="px-3 py-2 text-right text-neutral-600 font-mono">
                  {formatUSD(t.fees)}
                </td>
                <td className="px-3 py-2 text-right text-neutral-400 font-mono">{t.duration_human}</td>
                <td className="px-3 py-2">
                  <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${reasonColor[t.reason] || "text-neutral-400"}`}>
                    {reasonLabel[t.reason] ?? t.reason}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {pageCount > 1 && (
        <div className="flex items-center justify-center gap-2 px-4 py-3 border-t border-neutral-800">
          <button
            onClick={() => setPage((p) => Math.max(0, p - 1))}
            disabled={page === 0}
            className="px-3 py-1 text-xs rounded bg-neutral-800 text-neutral-300 disabled:opacity-30 hover:bg-neutral-700"
          >
            ← Prev
          </button>
          <span className="text-xs text-neutral-500">
            Page {page + 1} / {pageCount}
          </span>
          <button
            onClick={() => setPage((p) => Math.min(pageCount - 1, p + 1))}
            disabled={page === pageCount - 1}
            className="px-3 py-1 text-xs rounded bg-neutral-800 text-neutral-300 disabled:opacity-30 hover:bg-neutral-700"
          >
            Next →
          </button>
        </div>
      )}
    </div>
  );
}

// ─── PnL Distribution histogram ───────────────────────────────────────────────
function PnLDistribution({ trades }: { trades: BacktestTrade[] }) {
  if (trades.length < 5) return null;

  const pnls = trades.map((t) => t.pnl);
  const min = Math.min(...pnls);
  const max = Math.max(...pnls);
  const BINS = 20;
  const binSize = (max - min) / BINS || 1;

  const bins = Array.from({ length: BINS }, (_, i) => ({
    start: min + i * binSize,
    end: min + (i + 1) * binSize,
    count: 0,
    isProfit: min + (i + 0.5) * binSize > 0,
  }));
  for (const pnl of pnls) {
    const idx = Math.min(Math.floor((pnl - min) / binSize), BINS - 1);
    bins[idx].count++;
  }

  const data = bins.map((b) => ({
    label: `$${b.start.toFixed(0)}`,
    count: b.count,
    fill: b.isProfit ? "#22c55e" : "#ef4444",
  }));

  return (
    <div className="bg-neutral-900 border border-neutral-800 rounded-lg p-4">
      <div className="flex items-center gap-2 mb-3">
        <Activity size={13} className="text-neutral-500" />
        <span className="text-xs font-medium text-neutral-400 uppercase tracking-wider">
          PnL Distribution
        </span>
      </div>
      <ResponsiveContainer width="100%" height={100}>
        <ComposedChart data={data} margin={{ top: 0, right: 0, left: 0, bottom: 0 }}>
          <XAxis
            dataKey="label"
            tick={{ fill: "#525252", fontSize: 9 }}
            interval={4}
            tickLine={false}
            axisLine={false}
          />
          <YAxis hide />
          <Tooltip
            contentStyle={{ background: "#171717", border: "1px solid #404040", borderRadius: "6px", fontSize: 11 }}
            formatter={(v) => [v, "trades"]}
          />
          <Bar dataKey="count" radius={[2, 2, 0, 0]} isAnimationActive={false}>
            {data.map((entry, idx) => (
              <rect key={idx} fill={entry.fill} fillOpacity={0.7} />
            ))}
          </Bar>
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}

// ─── Monthly R:R Table ────────────────────────────────────────────────────────
interface MonthStat {
  month: string; trades: number; wins: number; losses: number;
  win_rate: number; avg_rr: number | null; best_rr: number | null;
  worst_rr: number | null; pnl: number; profit_factor: number;
}

function MonthlyRRTable({ stats }: { stats: MonthStat[] }) {
  if (!stats || stats.length === 0) return null;
  const rrColor = (v: number | null) => {
    if (v === null) return "text-neutral-600";
    if (v >= 1.5) return "text-green-400"; if (v >= 0.5) return "text-yellow-400";
    if (v >= 0) return "text-orange-400"; return "text-red-400";
  };
  const avg = (arr: (number | null)[]) => {
    const valid = arr.filter(v => v !== null) as number[];
    return valid.length ? valid.reduce((a, b) => a + b, 0) / valid.length : null;
  };
  const avgRR = avg(stats.map(s => s.avg_rr));
  const avgWR = avg(stats.map(s => s.win_rate));

  return (
    <div className="bg-neutral-900 border border-neutral-800 rounded-lg overflow-hidden">
      <div className="flex items-center justify-between px-4 py-3 border-b border-neutral-800">
        <div className="flex items-center gap-2">
          <Calendar size={13} className="text-blue-400" />
          <span className="text-sm font-semibold text-white">Monthly Breakdown</span>
          <span className="text-xs text-neutral-600">· Avg R:R per month</span>
        </div>
        <div className="flex items-center gap-4 text-xs">
          <span className="text-neutral-500">Avg R:R: <span className={`font-mono font-semibold ${rrColor(avgRR)}`}>{avgRR != null ? avgRR.toFixed(2) : "—"}</span></span>
          <span className="text-neutral-500">Avg Win Rate: <span className={`font-mono font-semibold ${winrateColor(avgWR ?? 0)}`}>{avgWR != null ? `${avgWR.toFixed(1)}%` : "—"}</span></span>
        </div>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead className="border-b border-neutral-800 bg-neutral-900/50">
            <tr>
              {["Month","Trades","W","L","Win%","Avg R:R","Best R:R","Worst R:R","PnL","PF"].map((h, i) => (
                <th key={h} className={`px-3 py-2 text-[10px] uppercase tracking-wider text-neutral-500 font-medium ${i > 1 ? "text-right" : "text-left"}`}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {stats.map(s => (
              <tr key={s.month} className="border-b border-neutral-800/50 hover:bg-neutral-800/20 transition-colors">
                <td className="px-3 py-2 font-mono text-neutral-400">{s.month}</td>
                <td className="px-3 py-2 text-neutral-400 text-right">{s.trades}</td>
                <td className="px-3 py-2 text-green-400 text-right font-semibold">{s.wins}</td>
                <td className="px-3 py-2 text-red-400 text-right font-semibold">{s.losses}</td>
                <td className={`px-3 py-2 text-right font-semibold ${winrateColor(s.win_rate)}`}>{s.win_rate.toFixed(1)}%</td>
                <td className={`px-3 py-2 text-right font-mono font-bold ${rrColor(s.avg_rr)}`}>
                  {s.avg_rr != null ? s.avg_rr.toFixed(2) : "—"}
                </td>
                <td className={`px-3 py-2 text-right font-mono ${rrColor(s.best_rr)}`}>
                  {s.best_rr != null ? s.best_rr.toFixed(2) : "—"}
                </td>
                <td className={`px-3 py-2 text-right font-mono ${rrColor(s.worst_rr)}`}>
                  {s.worst_rr != null ? s.worst_rr.toFixed(2) : "—"}
                </td>
                <td className={`px-3 py-2 text-right font-mono font-semibold ${pnlColor(s.pnl)}`}>
                  {s.pnl >= 0 ? "+" : ""}{formatUSD(s.pnl)}
                </td>
                <td className={`px-3 py-2 text-right font-mono ${ratioColor(s.profit_factor, 1, 1.5)}`}>
                  {s.profit_factor >= 999 ? "∞" : s.profit_factor.toFixed(2)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ─── Live Backtest Agent ──────────────────────────────────────────────────────
interface AgentMessage { role: "agent" | "user"; text: string; ts: string; }

function LiveBacktestAgent({ result }: { result: BacktestResult }) {
  const [messages, setMessages] = useState<AgentMessage[]>([]);
  const [thinking, setThinking] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const hasAnalyzed = useRef(false);

  const analyze = useCallback((r: BacktestResult) => {
    const m  = r.metrics;
    const ms = (r as any).monthly_stats as MonthStat[] | undefined ?? [];
    const avgRR = ms.length
      ? ms.filter(s => s.avg_rr !== null).reduce((a, s) => a + (s.avg_rr ?? 0), 0) /
        Math.max(ms.filter(s => s.avg_rr !== null).length, 1)
      : null;
    const bestMonth  = ms.reduce((a, s) => s.pnl > (a?.pnl ?? -Infinity) ? s : a, ms[0] as MonthStat | undefined);
    const worstMonth = ms.reduce((a, s) => s.pnl < (a?.pnl ?? Infinity)  ? s : a, ms[0] as MonthStat | undefined);
    const years = (r as any).years_tested as number ?? 1;

    setThinking(true);
    setTimeout(() => {
      const lines: string[] = [];

      // Overview
      lines.push(`📊 **${r.strategy_name}** · ${r.symbol} ${r.timeframe} · ${years.toFixed(1)} years of real data (${r.candle_count.toLocaleString()} bars)`);
      lines.push("");

      // R:R analysis
      if (avgRR !== null) {
        if (avgRR >= 1.2) lines.push(`✅ **Avg R:R: ${avgRR.toFixed(2)}** — strong. The strategy consistently extracts more than 1R per trade on average.`);
        else if (avgRR >= 0.5) lines.push(`⚠️ **Avg R:R: ${avgRR.toFixed(2)}** — below 1. Win rate of ${m.win_rate.toFixed(1)}% is needed to offset this. ${m.win_rate >= 55 ? "Your win rate compensates." : "Win rate is too low — edge at risk."}`);
        else lines.push(`🔴 **Avg R:R: ${avgRR.toFixed(2)}** — very low. The strategy is cutting winners too early or letting losers run.`);
      }

      // Sharpe & drawdown
      if (m.sharpe_ratio >= 1.5) lines.push(`✅ **Sharpe ${m.sharpe_ratio.toFixed(2)}** — excellent risk-adjusted returns.`);
      else if (m.sharpe_ratio >= 0.7) lines.push(`⚠️ **Sharpe ${m.sharpe_ratio.toFixed(2)}** — acceptable. Tighten SL or increase TP multiplier to improve.`);
      else lines.push(`🔴 **Sharpe ${m.sharpe_ratio.toFixed(2)}** — poor. Strategy is taking too much risk relative to returns.`);

      if (m.max_drawdown_pct > 25) lines.push(`🔴 **Max DD: -${m.max_drawdown_pct.toFixed(1)}%** — dangerous drawdown. Reduce position size or tighten stops.`);
      else if (m.max_drawdown_pct > 12) lines.push(`⚠️ **Max DD: -${m.max_drawdown_pct.toFixed(1)}%** — manageable but watch for deeper extensions.`);
      else lines.push(`✅ **Max DD: -${m.max_drawdown_pct.toFixed(1)}%** — controlled drawdown.`);

      // Monthly patterns
      if (bestMonth)  lines.push(`📈 Best month: **${bestMonth.month}** (+${formatUSD(bestMonth.pnl)}, ${bestMonth.trades} trades)`);
      if (worstMonth) lines.push(`📉 Worst month: **${worstMonth.month}** (${formatUSD(worstMonth.pnl)}, ${worstMonth.trades} trades)`);

      // Profit factor
      if (m.profit_factor >= 1.5) lines.push(`✅ **Profit Factor: ${m.profit_factor.toFixed(2)}** — solid edge.`);
      else if (m.profit_factor >= 1.1) lines.push(`⚠️ **Profit Factor: ${m.profit_factor.toFixed(2)}** — thin edge. Susceptible to regime changes.`);
      else lines.push(`🔴 **Profit Factor: ${m.profit_factor.toFixed(2)}** — no edge. Strategy loses money before fees.`);

      lines.push("");

      // Suggestions
      lines.push("**💡 Agent Suggestions:**");
      if (m.win_rate < 45)     lines.push("• Win rate below 45% — tighten entry conditions (add volume filter or wait for higher-TF confirmation)");
      if (avgRR !== null && avgRR < 1) lines.push("• R:R below 1 — increase TP multiplier or use trailing stop to let winners run further");
      if (m.max_drawdown_pct > 20) lines.push("• Drawdown > 20% — reduce position size from " + r.metrics.exposure_pct.toFixed(0) + "% exposure or add a daily drawdown circuit breaker");
      if (m.total_trades < 30) lines.push("• Only " + m.total_trades + " trades in " + years.toFixed(1) + " years — low sample size. Results may not be statistically significant.");
      if (m.max_consecutive_losses >= 5) lines.push(`• ${m.max_consecutive_losses} consecutive losses detected — consider a cooldown rule after 3 consecutive losses`);
      if (m.profit_factor < 1.2 && m.win_rate >= 50) lines.push("• High win rate but low profit factor — winners are too small vs. losers. Widen TP or add partial take-profit at 1R.");
      if (lines[lines.length - 1] === "**💡 Agent Suggestions:**") lines.push("• Strategy looks solid. Consider running on additional symbols for diversification.");

      setMessages([{ role: "agent", text: lines.join("\n"), ts: new Date().toLocaleTimeString() }]);
      setThinking(false);
      hasAnalyzed.current = true;
    }, 800);
  }, []);

  useEffect(() => {
    if (!hasAnalyzed.current) analyze(result);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, thinking]);

  return (
    <div className="bg-neutral-900 border border-neutral-800 rounded-lg overflow-hidden">
      <div className="flex items-center justify-between px-4 py-3 border-b border-neutral-800">
        <div className="flex items-center gap-2">
          <Bot size={14} className="text-blue-400" />
          <span className="text-sm font-semibold text-white">Live Backtest Agent</span>
          <span className="w-1.5 h-1.5 rounded-full bg-green-400 animate-pulse ml-1" />
        </div>
        <button onClick={() => analyze(result)} className="text-[10px] text-neutral-600 hover:text-white transition-colors flex items-center gap-1">
          <RefreshCw size={10} /> Re-analyze
        </button>
      </div>

      <div ref={scrollRef} className="p-4 max-h-80 overflow-y-auto space-y-3 font-mono text-[11px]">
        {thinking && (
          <div className="flex items-center gap-2 text-blue-400">
            <RefreshCw size={11} className="animate-spin" />
            <span>Analyzing {result.metrics.total_trades} trades over {((result as any).years_tested ?? 1).toFixed(1)} years…</span>
          </div>
        )}
        {messages.map((msg, i) => (
          <div key={i} className="space-y-1">
            <div className="text-[9px] text-neutral-700">{msg.ts}</div>
            {msg.text.split("\n").map((line, j) => {
              const isBold = line.startsWith("**") || line.includes("**");
              const rendered = line
                .replace(/\*\*(.+?)\*\*/g, (_, t) => `<strong class="text-white">${t}</strong>`);
              return (
                <div key={j}
                  className={`leading-relaxed ${
                    line.startsWith("✅") ? "text-green-400" :
                    line.startsWith("⚠️") ? "text-yellow-400" :
                    line.startsWith("🔴") ? "text-red-400" :
                    line.startsWith("📊") || line.startsWith("💡") ? "text-blue-300" :
                    line.startsWith("📈") ? "text-green-300" :
                    line.startsWith("📉") ? "text-red-300" :
                    line.startsWith("•")  ? "text-neutral-400 pl-3" :
                    line === "" ? "h-2" : "text-neutral-300"
                  } ${isBold && !line.startsWith("•") ? "font-semibold" : ""}`}
                  dangerouslySetInnerHTML={{ __html: rendered }}
                />
              );
            })}
          </div>
        ))}
      </div>
    </div>
  );
}

// ─── Main Page ────────────────────────────────────────────────────────────────
export default function BacktestPage() {
  const [types, setTypes] = useState<StrategyType[]>([]);
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<BacktestResult | null>(null);
  const [error, setError] = useState("");
  const [elapsed, setElapsed] = useState(0);
  const [strategyParams, setStrategyParams] = useState<Record<string, number | string | boolean>>({});
  const [showParams, setShowParams] = useState(false);
  const [showConfig, setShowConfig] = useState(true);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const [yearsPreset, setYearsPreset] = useState<number>(1);
  const [form, setForm] = useState({
    strategy_type: "btc_momentum_velocity",
    symbol: "BTC/USDT",
    timeframe: "15m",
    years: 1,
    initial_capital: 10000,
    position_size_pct: 10,
    commission_pct: 0.1,
    slippage_pct: 0.05,
  });

  useEffect(() => {
    strategiesApi.types().then(setTypes).catch(() => {});
  }, []);

  // Load strategy default params when type changes
  useEffect(() => {
    backtestApi.getStrategyParams(form.strategy_type)
      .then((data) => setStrategyParams({ ...data.parameters }))
      .catch(() => {});
  }, [form.strategy_type]);

  function updateParam(key: string, raw: string) {
    const num = parseFloat(raw);
    setStrategyParams((prev) => ({
      ...prev,
      [key]: isNaN(num) ? raw : num,
    }));
  }

  async function runBacktest() {
    setRunning(true);
    setError("");
    setResult(null);
    setElapsed(0);
    setShowConfig(false);

    const startTs = Date.now();
    timerRef.current = setInterval(() => {
      setElapsed(Math.floor((Date.now() - startTs) / 1000));
    }, 1000);

    try {
      const res = await backtestApi.run({
        strategy_type:    form.strategy_type,
        symbol:           form.symbol,
        timeframe:        form.timeframe,
        years:            form.years,
        initial_capital:  form.initial_capital,
        position_size_pct:form.position_size_pct,
        commission_pct:   form.commission_pct,
        slippage_pct:     form.slippage_pct,
        parameters:       strategyParams,
      });
      setResult(res);
    } catch (e: any) {
      setError(e?.response?.data?.detail || "Backtest failed — check network / exchange connectivity");
      setShowConfig(true);
    } finally {
      setRunning(false);
      if (timerRef.current) clearInterval(timerRef.current);
    }
  }

  const m = result?.metrics;
  const totalPnl = result ? result.final_capital - result.initial_capital : 0;

  return (
    <div className="p-4 space-y-4 max-w-[1600px]">
      {/* ── Config Panel ──────────────────────────────────────────────── */}
      <div className="bg-neutral-900 border border-neutral-800 rounded-lg overflow-hidden">
        <button
          onClick={() => setShowConfig((v) => !v)}
          className="w-full flex items-center justify-between px-4 py-3 hover:bg-neutral-800/50 transition-colors"
        >
          <div className="flex items-center gap-2">
            <Zap size={14} className="text-blue-400" />
            <span className="text-sm font-semibold text-white">Backtest Configuration</span>
            {result && !showConfig && (
              <span className="text-xs text-neutral-500 ml-2">
                {result.symbol} · {result.timeframe} · {result.candle_count} bars
              </span>
            )}
          </div>
          {showConfig ? <ChevronUp size={14} className="text-neutral-500" /> : <ChevronDown size={14} className="text-neutral-500" />}
        </button>

        {showConfig && (
          <div className="px-4 pb-4 border-t border-neutral-800 pt-4 space-y-4">
            {/* Main config row */}
            <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-8 gap-3">
              <div className="col-span-2">
                <label className="text-[11px] uppercase tracking-wider text-neutral-500 block mb-1.5">Strategy</label>
                <select
                  className="w-full bg-neutral-800 border border-neutral-700 rounded text-sm text-white px-2 py-1.5 focus:outline-none focus:border-blue-500"
                  value={form.strategy_type}
                  onChange={(e) => setForm({ ...form, strategy_type: e.target.value })}
                >
                  {types.map((t) => (
                    <option key={t.type} value={t.type}>{t.name}</option>
                  ))}
                </select>
              </div>

              <div>
                <label className="text-[11px] uppercase tracking-wider text-neutral-500 block mb-1.5">Symbol</label>
                <select
                  className="w-full bg-neutral-800 border border-neutral-700 rounded text-sm text-white px-2 py-1.5 focus:outline-none focus:border-blue-500"
                  value={form.symbol}
                  onChange={(e) => setForm({ ...form, symbol: e.target.value })}
                >
                  {SYMBOLS.map((s) => <option key={s}>{s}</option>)}
                </select>
              </div>

              <div>
                <label className="text-[11px] uppercase tracking-wider text-neutral-500 block mb-1.5">Timeframe</label>
                <select
                  className="w-full bg-neutral-800 border border-neutral-700 rounded text-sm text-white px-2 py-1.5 focus:outline-none focus:border-blue-500"
                  value={form.timeframe}
                  onChange={(e) => setForm({ ...form, timeframe: e.target.value })}
                >
                  {TIMEFRAMES.map((tf) => <option key={tf}>{tf}</option>)}
                </select>
              </div>

              <div className="col-span-2">
                <label className="text-[11px] uppercase tracking-wider text-neutral-500 block mb-1.5">
                  Data Range · <span className="text-blue-400 font-mono">
                    {form.years}y ≈ {((BARS_PER_YEAR[form.timeframe] ?? 8760) * form.years).toLocaleString()} bars
                  </span>
                </label>
                <div className="flex gap-1.5">
                  {YEAR_PRESETS.map(y => (
                    <button key={y} onClick={() => { setYearsPreset(y); setForm(f => ({ ...f, years: y })); }}
                      className="flex-1 py-1.5 rounded text-[11px] font-bold border transition-colors"
                      style={form.years === y
                        ? { background: "rgba(10,132,255,0.18)", borderColor: "rgba(10,132,255,0.4)", color: "#60aaff" }
                        : { background: "rgba(255,255,255,0.03)", borderColor: "rgba(255,255,255,0.08)", color: "#4b5563" }
                      }>
                      {y < 1 ? `${y * 12}m` : `${y}y`}
                    </button>
                  ))}
                </div>
              </div>

              <div>
                <label className="text-[11px] uppercase tracking-wider text-neutral-500 block mb-1.5">Capital ($)</label>
                <input
                  type="number" min={100} step={1000}
                  className="w-full bg-neutral-800 border border-neutral-700 rounded text-sm text-white px-2 py-1.5 focus:outline-none focus:border-blue-500"
                  value={form.initial_capital}
                  onChange={(e) => setForm({ ...form, initial_capital: Number(e.target.value) })}
                />
              </div>

              <div>
                <label className="text-[11px] uppercase tracking-wider text-neutral-500 block mb-1.5">Pos. Size %</label>
                <input
                  type="number" min={1} max={100} step={1}
                  className="w-full bg-neutral-800 border border-neutral-700 rounded text-sm text-white px-2 py-1.5 focus:outline-none focus:border-blue-500"
                  value={form.position_size_pct}
                  onChange={(e) => setForm({ ...form, position_size_pct: Number(e.target.value) })}
                />
              </div>

              <div>
                <label className="text-[11px] uppercase tracking-wider text-neutral-500 block mb-1.5">Comm. %</label>
                <input
                  type="number" min={0} max={1} step={0.01}
                  className="w-full bg-neutral-800 border border-neutral-700 rounded text-sm text-white px-2 py-1.5 focus:outline-none focus:border-blue-500"
                  value={form.commission_pct}
                  onChange={(e) => setForm({ ...form, commission_pct: Number(e.target.value) })}
                />
              </div>
            </div>

            {/* Strategy parameters (expandable) */}
            {Object.keys(strategyParams).length > 0 && (
              <div>
                <button
                  onClick={() => setShowParams((v) => !v)}
                  className="flex items-center gap-2 text-[11px] text-neutral-400 hover:text-white transition-colors"
                >
                  {showParams ? <ChevronUp size={11} /> : <ChevronDown size={11} />}
                  Strategy Parameters
                  <span className="text-neutral-600">
                    ({Object.keys(strategyParams).length} params)
                  </span>
                </button>
                {showParams && (
                  <div className="mt-3 grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-3 p-3 bg-neutral-800/50 rounded-lg border border-neutral-700/50">
                    {Object.entries(strategyParams).map(([key, val]) => (
                      <div key={key}>
                        <label className="text-[10px] uppercase tracking-wider text-neutral-500 block mb-1">
                          {key.replace(/_/g, " ")}
                        </label>
                        <input
                          type={typeof val === "number" ? "number" : "text"}
                          step={typeof val === "number" && val < 1 ? 0.1 : 1}
                          className="w-full bg-neutral-800 border border-neutral-700 rounded text-xs text-white px-2 py-1.5 focus:outline-none focus:border-blue-500"
                          value={String(val)}
                          onChange={(e) => updateParam(key, e.target.value)}
                        />
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}

            {/* Run button */}
            <div className="flex items-center gap-3">
              <button
                onClick={runBacktest}
                disabled={running}
                className="flex items-center gap-2 px-5 py-2 bg-blue-600 hover:bg-blue-500 disabled:bg-blue-600/40 text-white text-sm font-medium rounded transition-all"
              >
                {running ? (
                  <RefreshCw size={14} className="animate-spin" />
                ) : (
                  <Play size={14} />
                )}
                {running ? `Running... ${elapsed}s` : "Run Backtest"}
              </button>
              {error && (
                <span className="text-red-400 text-xs bg-red-500/10 border border-red-500/20 px-3 py-1.5 rounded">
                  {error}
                </span>
              )}
            </div>
          </div>
        )}
      </div>

      {/* ── Loading ──────────────────────────────────────────────────────── */}
      {running && (
        <div className="bg-neutral-900 border border-neutral-800 rounded-lg p-10 flex flex-col items-center gap-4">
          <div className="relative">
            <div className="w-10 h-10 border-2 border-blue-600/30 rounded-full" />
            <div className="w-10 h-10 border-2 border-blue-500 border-t-transparent rounded-full animate-spin absolute inset-0" />
          </div>
          <div className="text-center">
            <p className="text-white text-sm font-medium">Fetching {form.years}y of real market data from Bybit/Binance…</p>
            <p className="text-neutral-500 text-xs mt-1">
              {form.timeframe} bars · ~{((BARS_PER_YEAR[form.timeframe] ?? 8760) * form.years).toLocaleString()} candles · {elapsed}s elapsed
            </p>
            <p className="text-neutral-700 text-xs mt-0.5">Large datasets may take 30–90s to paginate</p>
          </div>
        </div>
      )}

      {/* ── Results ──────────────────────────────────────────────────────── */}
      {result && m && (
        <div className="space-y-4">
          {/* Header */}
          <div className="flex items-center justify-between">
            <div>
              <div className="flex items-center gap-3">
                <h2 className="text-lg font-semibold text-white">
                  {result.symbol} · {result.timeframe}
                </h2>
                <span className={`text-sm font-semibold px-2 py-0.5 rounded ${totalPnl >= 0 ? "text-green-400 bg-green-500/10" : "text-red-400 bg-red-500/10"}`}>
                  {totalPnl >= 0 ? "+" : ""}{formatUSD(totalPnl)} ({m.total_return_pct >= 0 ? "+" : ""}{fmt(m.total_return_pct)}%)
                </span>
              </div>
              <p className="text-xs text-neutral-500 mt-1">
                {fmtDateShort(result.date_range.start)} → {fmtDateShort(result.date_range.end)}
                {" · "}{result.candle_count.toLocaleString()} bars · {m.total_trades} trades
                {" · "}{((result as any).years_tested ?? "?").toString()}y real data
                {(result as any).overall_avg_rr != null && (
                  <span className={`ml-2 font-semibold ${ratioColor((result as any).overall_avg_rr, 0.5, 1.2)}`}>
                    · Avg R:R {((result as any).overall_avg_rr as number).toFixed(2)}
                  </span>
                )}
              </p>
            </div>
            <button
              onClick={() => setShowConfig(true)}
              className="flex items-center gap-1.5 text-xs text-neutral-400 hover:text-white transition-colors px-3 py-1.5 rounded border border-neutral-700 hover:border-neutral-600"
            >
              <RefreshCw size={11} />
              Reconfigure
            </button>
          </div>

          {/* ── Metrics Grid ─────────────────────────────────────────────── */}
          <div className="space-y-2">
            {/* Returns section */}
            <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-2">
              <StatCard
                label="Total Return"
                value={`${m.total_return_pct >= 0 ? "+" : ""}${fmt(m.total_return_pct)}%`}
                sub={formatUSD(totalPnl)}
                valueClass={pnlColor(m.total_return_pct)}
              />
              <StatCard
                label="Ann. Return"
                value={`${m.annualized_return_pct >= 0 ? "+" : ""}${fmt(m.annualized_return_pct)}%`}
                sub="CAGR"
                valueClass={pnlColor(m.annualized_return_pct)}
                tooltip="Compound Annual Growth Rate"
              />
              <StatCard
                label="Sharpe"
                value={fmt(m.sharpe_ratio)}
                sub="Risk-adj. return"
                valueClass={ratioColor(m.sharpe_ratio, 0.5, 1.5)}
                tooltip="Sharpe ratio (rf=0). >1 = good, >2 = excellent"
              />
              <StatCard
                label="Sortino"
                value={fmt(m.sortino_ratio)}
                sub="Downside-adj."
                valueClass={ratioColor(m.sortino_ratio, 0.5, 2)}
                tooltip="Like Sharpe but only penalises downside volatility"
              />
              <StatCard
                label="Calmar"
                value={fmt(m.calmar_ratio)}
                sub="Return / MaxDD"
                valueClass={ratioColor(m.calmar_ratio, 0.5, 2)}
                tooltip="Annualised return / max drawdown. >1 = good"
              />
              <StatCard
                label="Profit Factor"
                value={m.profit_factor >= 999 ? "∞" : fmt(m.profit_factor)}
                sub={`${formatUSD(m.gross_profit)} / ${formatUSD(m.gross_loss)}`}
                valueClass={ratioColor(m.profit_factor, 1, 1.5)}
                tooltip="Gross profit / gross loss. >1.5 = good"
              />
            </div>

            {/* Risk section */}
            <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-2">
              <StatCard
                label="Max Drawdown"
                value={`-${fmt(m.max_drawdown_pct)}%`}
                sub={formatUSD(-m.max_drawdown_usd)}
                valueClass={ddColor(m.max_drawdown_pct)}
              />
              <StatCard
                label="DD Duration"
                value={String(m.max_drawdown_duration_bars)}
                sub={`${result.timeframe} bars`}
                valueClass="text-neutral-300"
              />
              <StatCard
                label="Win Rate"
                value={`${fmt(m.win_rate)}%`}
                sub={`${m.winning_trades}W / ${m.losing_trades}L`}
                valueClass={winrateColor(m.win_rate)}
              />
              <StatCard
                label="Avg Win"
                value={formatUSD(m.avg_win)}
                sub={`Best: ${formatUSD(m.largest_win)}`}
                valueClass="text-green-400"
              />
              <StatCard
                label="Avg Loss"
                value={formatUSD(m.avg_loss)}
                sub={`Worst: ${formatUSD(m.largest_loss)}`}
                valueClass="text-red-400"
              />
              <StatCard
                label="Expectancy"
                value={formatUSD(m.expectancy)}
                sub="Per trade"
                valueClass={pnlColor(m.expectancy)}
                tooltip="Average $ earned per trade (net of fees)"
              />
              {(result as any).overall_avg_rr != null && (
                <StatCard
                  label="Avg R:R"
                  value={((result as any).overall_avg_rr as number).toFixed(2)}
                  sub="All trades"
                  valueClass={ratioColor((result as any).overall_avg_rr, 0.5, 1.2)}
                  tooltip="Average realized risk/reward across all closed trades"
                />
              )}
            </div>

            {/* Detail section */}
            <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-2">
              <StatCard
                label="Total Trades"
                value={String(m.total_trades)}
                sub={`${fmt(m.exposure_pct)}% in market`}
              />
              <StatCard
                label="Avg Duration"
                value={fmt(m.avg_duration_bars, 1)}
                sub={`${result.timeframe} bars`}
              />
              <StatCard
                label="Max Consec. W"
                value={String(m.max_consecutive_wins)}
                valueClass="text-green-400"
              />
              <StatCard
                label="Max Consec. L"
                value={String(m.max_consecutive_losses)}
                valueClass="text-red-400"
              />
              <StatCard
                label="Total Fees"
                value={formatUSD(m.total_fees_paid)}
                sub={`${fmt((m.total_fees_paid / result.initial_capital) * 100)}% of capital`}
                valueClass="text-neutral-400"
              />
              <div className="bg-neutral-900 border border-neutral-800 rounded-lg p-3">
                <div className="text-[11px] uppercase tracking-wider text-neutral-500 mb-2">Capital</div>
                <div className="text-xs space-y-1 font-mono">
                  <div className="flex justify-between">
                    <span className="text-neutral-500">Start</span>
                    <span className="text-neutral-300">{formatUSD(result.initial_capital)}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-neutral-500">End</span>
                    <span className={pnlColor(totalPnl)}>{formatUSD(result.final_capital)}</span>
                  </div>
                </div>
              </div>
            </div>
          </div>

          {/* ── Charts ───────────────────────────────────────────────────── */}
          <div className="bg-neutral-900 border border-neutral-800 rounded-lg p-4">
            <div className="flex items-center justify-between mb-1">
              <div className="flex items-center gap-2">
                <TrendingUp size={13} className="text-neutral-500" />
                <span className="text-xs font-medium text-neutral-400 uppercase tracking-wider">
                  Equity Curve
                </span>
              </div>
              <span className="text-[10px] text-neutral-600">
                {result.candle_count} bars · real OHLCV · no lookahead bias
              </span>
            </div>
            <EquityChart
              data={result.equity_curve}
              initial={result.initial_capital}
              timeframe={result.timeframe}
            />

            <div className="mt-2 border-t border-neutral-800 pt-2">
              <div className="flex items-center gap-2 mb-1">
                <Shield size={11} className="text-red-400" />
                <span className="text-[10px] text-neutral-500 uppercase tracking-wider">
                  Drawdown
                </span>
                <span className="text-[10px] text-red-400 ml-auto">
                  Max: -{fmt(m.max_drawdown_pct)}%
                </span>
              </div>
              <DrawdownChart
                ddSeries={m.drawdown_series}
                equityCurve={result.equity_curve}
                timeframe={result.timeframe}
              />
            </div>
          </div>

          {/* ── Monthly Heatmap + Distribution ───────────────────────────── */}
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
            <div className="lg:col-span-2">
              <MonthlyHeatmap monthly={result.monthly_returns} />
            </div>
            <div>
              <PnLDistribution trades={result.trades} />
            </div>
          </div>

          {/* ── Monthly R:R Breakdown ─────────────────────────────────────── */}
          {(result as any).monthly_stats?.length > 0 && (
            <MonthlyRRTable stats={(result as any).monthly_stats} />
          )}

          {/* ── Live Backtest Agent ───────────────────────────────────────── */}
          <LiveBacktestAgent result={result} />

          {/* ── Trade Log ────────────────────────────────────────────────── */}
          <TradeTable trades={result.trades} />
        </div>
      )}
    </div>
  );
}
