"use client";

import { useEffect, useState, useCallback } from "react";
import {
  DollarSign, TrendingUp, TrendingDown, Activity,
  BarChart2, RefreshCw, Target, Wallet, LineChart,
  ArrowUpRight, ArrowDownRight, Clock, Zap,
} from "lucide-react";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

interface PaperStats {
  total_trades: number;
  wins: number;
  losses: number;
  win_rate: number;
  total_pnl: number;
  best_trade: number;
  worst_trade: number;
  peak_balance: number;
  max_drawdown_pct: number;
}

interface PaperPosition {
  id: string;
  strategy_name: string;
  strat_key_ref: string;
  direction: string;
  entry: number;
  sl: number;
  tp: number;
  margin_usdc: number;
  leverage: number;
  btc_size: number;
  confidence: number;
  reasoning: string;
  rr: string;
  timestamp: string;
  current_price: number;
  unrealized_pnl: number;
  unrealized_pct: number;
}

interface PaperTrade {
  id: string;
  strategy_name: string;
  strat_key_ref: string;
  direction: string;
  entry: number;
  exit_price: number;
  exit_reason: string;
  pnl_usd: number;
  pnl_pct: number;
  margin_usdc: number;
  leverage: number;
  closed_at: string;
  balance_after: number;
}

interface EquityPoint {
  balance: number;
  pnl: number;
  strategy: string;
  reason: string;
  closed_at: string;
}

interface PaperTraderData {
  enabled: boolean;
  balance: number;
  starting_balance: number;
  return_pct: number;
  daily_pnl: number;
  open_positions: PaperPosition[];
  open_count: number;
  stats: PaperStats;
  recent_trades: PaperTrade[];
  equity_curve: EquityPoint[];
}

const fmt = (n: number) => n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const pnlColor = (n: number) => n > 0 ? "#22c55e" : n < 0 ? "#ef4444" : "#888";
const pnlSign = (n: number) => n > 0 ? "+" : "";

function EquityCurve({ data }: { data: EquityPoint[] }) {
  if (data.length < 2) {
    return (
      <div className="flex items-center justify-center h-40 text-sm" style={{ color: "#555" }}>
        Waiting for trades to build equity curve...
      </div>
    );
  }

  const W = 700, H = 160, PX = 0, PY = 10;
  const bals = data.map((d) => d.balance);
  const mn = Math.min(...bals) * 0.999;
  const mx = Math.max(...bals) * 1.001;
  const rng = mx - mn || 1;
  const xStep = (W - 2 * PX) / (bals.length - 1);

  const pts = bals.map((b, i) => `${PX + i * xStep},${PY + (1 - (b - mn) / rng) * (H - 2 * PY)}`);
  const isUp = bals[bals.length - 1] >= bals[0];
  const stroke = isUp ? "#22c55e" : "#ef4444";
  const fill = isUp ? "rgba(34,197,94,0.08)" : "rgba(239,68,68,0.08)";

  const areaPath = `M${pts[0]} ${pts.map((p) => `L${p}`).join(" ")} L${PX + (bals.length - 1) * xStep},${H - PY} L${PX},${H - PY} Z`;

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ maxHeight: 180 }}>
      <path d={areaPath} fill={fill} />
      <polyline points={pts.join(" ")} fill="none" stroke={stroke} strokeWidth="2" strokeLinejoin="round" />
      {bals.length > 0 && (
        <>
          <text x={W - 4} y={12} textAnchor="end" fill="#666" fontSize="10">
            ${fmt(mx)}
          </text>
          <text x={W - 4} y={H - 2} textAnchor="end" fill="#666" fontSize="10">
            ${fmt(mn)}
          </text>
        </>
      )}
    </svg>
  );
}

export default function PaperTraderPage() {
  const [data, setData] = useState<PaperTraderData | null>(null);
  const [loading, setLoading] = useState(true);
  const [resetting, setResetting] = useState(false);

  const fetchData = useCallback(async () => {
    try {
      const res = await api.get("/api/agent/paper-trader");
      setData(res.data);
    } catch {
      /* swallow */
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
    const iv = setInterval(fetchData, 5000);
    return () => clearInterval(iv);
  }, [fetchData]);

  const handleReset = async () => {
    if (!confirm("Reset paper trader to $10,000? All paper trade history will be lost.")) return;
    setResetting(true);
    try {
      await api.post("/api/agent/paper-trader/reset");
      await fetchData();
    } catch { /* swallow */ }
    setResetting(false);
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="w-6 h-6 rounded-full border-2 border-t-transparent animate-spin" style={{ borderColor: "rgba(10,132,255,0.2) rgba(10,132,255,0.2) rgba(10,132,255,0.2) #0a84ff" }} />
      </div>
    );
  }

  if (!data) {
    return <div className="text-center py-20" style={{ color: "#666" }}>Paper trader data unavailable</div>;
  }

  const { stats, open_positions, recent_trades, equity_curve } = data;

  return (
    <div className="space-y-5 max-w-[1200px]">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold text-white flex items-center gap-2">
            <LineChart size={20} style={{ color: "#0a84ff" }} />
            Paper Trader
          </h1>
          <p className="text-xs mt-0.5" style={{ color: "#555" }}>
            Virtual $10K account running 24/7 — training the MasterBrain with every trade
          </p>
        </div>
        <button
          onClick={handleReset}
          disabled={resetting}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors"
          style={{
            background: "rgba(239,68,68,0.08)",
            color: "#ef4444",
            border: "1px solid rgba(239,68,68,0.15)",
          }}
        >
          <RefreshCw size={12} className={resetting ? "animate-spin" : ""} />
          Reset
        </button>
      </div>

      {/* KPI Cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <KpiCard
          label="Balance"
          value={`$${fmt(data.balance)}`}
          sub={`Started $${fmt(data.starting_balance)}`}
          icon={Wallet}
          color="#0a84ff"
          accent={data.balance >= data.starting_balance ? "#22c55e" : "#ef4444"}
        />
        <KpiCard
          label="Total Return"
          value={`${pnlSign(data.return_pct)}${data.return_pct.toFixed(2)}%`}
          sub={`${pnlSign(stats.total_pnl)}$${fmt(Math.abs(stats.total_pnl))}`}
          icon={data.return_pct >= 0 ? TrendingUp : TrendingDown}
          color={pnlColor(data.return_pct)}
          accent={pnlColor(data.return_pct)}
        />
        <KpiCard
          label="Win Rate"
          value={`${stats.win_rate.toFixed(1)}%`}
          sub={`${stats.wins}W / ${stats.losses}L (${stats.total_trades} total)`}
          icon={Target}
          color="#f59e0b"
          accent="#f59e0b"
        />
        <KpiCard
          label="Today"
          value={`${pnlSign(data.daily_pnl)}$${fmt(Math.abs(data.daily_pnl))}`}
          sub={`Peak $${fmt(stats.peak_balance)} · DD ${stats.max_drawdown_pct.toFixed(1)}%`}
          icon={Activity}
          color={pnlColor(data.daily_pnl)}
          accent={pnlColor(data.daily_pnl)}
        />
      </div>

      {/* Equity Curve */}
      <Card title="Equity Curve" icon={BarChart2}>
        <EquityCurve data={equity_curve} />
      </Card>

      {/* Best / Worst trade row */}
      <div className="grid grid-cols-2 gap-3">
        <MiniStat label="Best Trade" value={`+$${fmt(stats.best_trade)}`} color="#22c55e" />
        <MiniStat label="Worst Trade" value={`$${fmt(stats.worst_trade)}`} color="#ef4444" />
      </div>

      {/* Open Positions */}
      <Card title={`Open Positions (${open_positions.length})`} icon={Zap}>
        {open_positions.length === 0 ? (
          <div className="text-center py-8 text-sm" style={{ color: "#555" }}>
            No open paper positions — waiting for signals
          </div>
        ) : (
          <div className="space-y-2">
            {open_positions.map((p) => (
              <div
                key={p.id}
                className="flex items-center justify-between px-3 py-2 rounded-lg"
                style={{ background: "rgba(255,255,255,0.02)", border: "1px solid rgba(255,255,255,0.04)" }}
              >
                <div className="flex items-center gap-3">
                  <span
                    className="text-[10px] font-bold px-1.5 py-0.5 rounded"
                    style={{
                      background: p.direction === "long" ? "rgba(34,197,94,0.12)" : "rgba(239,68,68,0.12)",
                      color: p.direction === "long" ? "#22c55e" : "#ef4444",
                    }}
                  >
                    {p.direction.toUpperCase()}
                  </span>
                  <div>
                    <span className="text-xs font-medium text-white">{p.strategy_name.replace("[PAPER] ", "")}</span>
                    <div className="text-[10px]" style={{ color: "#555" }}>
                      Entry ${p.entry.toLocaleString()} · {p.leverage}x · {p.rr}
                    </div>
                  </div>
                </div>
                <div className="text-right">
                  <div className="text-xs font-mono font-semibold" style={{ color: pnlColor(p.unrealized_pnl) }}>
                    {pnlSign(p.unrealized_pnl)}${fmt(Math.abs(p.unrealized_pnl))}
                  </div>
                  <div className="text-[10px]" style={{ color: "#555" }}>
                    ${p.current_price.toLocaleString()}
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>

      {/* Recent Trades */}
      <Card title={`Recent Trades (${recent_trades.length})`} icon={Clock}>
        {recent_trades.length === 0 ? (
          <div className="text-center py-8 text-sm" style={{ color: "#555" }}>
            No trades yet — agent is scanning for signals
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr style={{ color: "#444" }}>
                  <th className="text-left pb-2 font-medium">Strategy</th>
                  <th className="text-left pb-2 font-medium">Dir</th>
                  <th className="text-right pb-2 font-medium">Entry</th>
                  <th className="text-right pb-2 font-medium">Exit</th>
                  <th className="text-right pb-2 font-medium">P&L</th>
                  <th className="text-right pb-2 font-medium">Balance</th>
                  <th className="text-right pb-2 font-medium">Reason</th>
                  <th className="text-right pb-2 font-medium">Time</th>
                </tr>
              </thead>
              <tbody>
                {recent_trades.map((t, i) => (
                  <tr key={i} className="border-t" style={{ borderColor: "rgba(255,255,255,0.03)" }}>
                    <td className="py-1.5 text-white font-medium">{t.strategy_name.replace("[PAPER] ", "")}</td>
                    <td className="py-1.5">
                      <span style={{ color: t.direction === "long" ? "#22c55e" : "#ef4444" }}>
                        {t.direction === "long" ? <ArrowUpRight size={12} className="inline" /> : <ArrowDownRight size={12} className="inline" />}
                        {t.direction.toUpperCase()}
                      </span>
                    </td>
                    <td className="py-1.5 text-right font-mono" style={{ color: "#888" }}>${t.entry.toLocaleString()}</td>
                    <td className="py-1.5 text-right font-mono" style={{ color: "#888" }}>${t.exit_price.toLocaleString()}</td>
                    <td className="py-1.5 text-right font-mono font-semibold" style={{ color: pnlColor(t.pnl_usd) }}>
                      {pnlSign(t.pnl_usd)}${fmt(Math.abs(t.pnl_usd))}
                    </td>
                    <td className="py-1.5 text-right font-mono" style={{ color: "#888" }}>${fmt(t.balance_after)}</td>
                    <td className="py-1.5 text-right">
                      <span
                        className="text-[10px] px-1.5 py-0.5 rounded font-medium"
                        style={{
                          background: t.exit_reason === "tp" ? "rgba(34,197,94,0.1)" : t.exit_reason === "sl" ? "rgba(239,68,68,0.1)" : "rgba(255,255,255,0.05)",
                          color: t.exit_reason === "tp" ? "#22c55e" : t.exit_reason === "sl" ? "#ef4444" : "#888",
                        }}
                      >
                        {t.exit_reason.toUpperCase()}
                      </span>
                    </td>
                    <td className="py-1.5 text-right" style={{ color: "#555" }}>
                      {t.closed_at ? new Date(t.closed_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}

/* ── Small components ─────────────────────────────────────────────────────── */

function KpiCard({
  label, value, sub, icon: Icon, color, accent,
}: {
  label: string; value: string; sub: string;
  icon: React.ElementType; color: string; accent: string;
}) {
  return (
    <div
      className="rounded-xl p-4"
      style={{
        background: "rgba(255,255,255,0.02)",
        border: "1px solid rgba(255,255,255,0.05)",
      }}
    >
      <div className="flex items-center gap-2 mb-2">
        <div
          className="w-7 h-7 rounded-lg flex items-center justify-center"
          style={{ background: `${color}15` }}
        >
          <Icon size={14} style={{ color }} />
        </div>
        <span className="text-[11px] font-medium" style={{ color: "#666" }}>{label}</span>
      </div>
      <div className="text-lg font-bold font-mono" style={{ color: accent }}>
        {value}
      </div>
      <div className="text-[10px] mt-0.5" style={{ color: "#555" }}>{sub}</div>
    </div>
  );
}

function Card({ title, icon: Icon, children }: { title: string; icon: React.ElementType; children: React.ReactNode }) {
  return (
    <div
      className="rounded-xl p-4"
      style={{
        background: "rgba(255,255,255,0.02)",
        border: "1px solid rgba(255,255,255,0.05)",
      }}
    >
      <div className="flex items-center gap-2 mb-3">
        <Icon size={14} style={{ color: "#0a84ff" }} />
        <span className="text-xs font-semibold text-white">{title}</span>
      </div>
      {children}
    </div>
  );
}

function MiniStat({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div
      className="rounded-xl px-4 py-3 flex items-center justify-between"
      style={{
        background: "rgba(255,255,255,0.02)",
        border: "1px solid rgba(255,255,255,0.05)",
      }}
    >
      <span className="text-xs" style={{ color: "#666" }}>{label}</span>
      <span className="text-sm font-bold font-mono" style={{ color }}>{value}</span>
    </div>
  );
}
