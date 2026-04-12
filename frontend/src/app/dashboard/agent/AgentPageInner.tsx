"use client";

import { useState, useEffect, useCallback } from "react";
import {
  Bot, Power, RefreshCw, AlertTriangle, Activity, Brain,
  TrendingUp, TrendingDown, ArrowUpRight, ArrowDownRight,
  ChevronRight, RotateCcw, Zap, Shield, X, CheckCircle2, XCircle,
  DollarSign, FileText, Cpu, Wifi, WifiOff,
  LineChart,
} from "lucide-react";
import {
  useServerAgent,
  type MasterBrainStatus,
  type LiveExecutorStatus,
  type ServerPosition,
  type ServerTrade,
} from "@/hooks/useServerAgent";

// ─── Strategy metadata ────────────────────────────────────────────────────────
const STRATEGIES = [
  { key: "momentum", label: "Momentum 15m", color: "#0a84ff", icon: "📈", tf: "15m" },
  { key: "hft",      label: "HFT Scalper",  color: "#a78bfa", icon: "⚡", tf: "1m"  },
  { key: "orb",      label: "ORB-30",       color: "#f59e0b", icon: "🔶", tf: "1m"  },
  { key: "obi",      label: "OBI Scalper",  color: "#10b981", icon: "📊", tf: "1m"  },
  { key: "fusion",   label: "Fusion",       color: "#f472b6", icon: "🧠", tf: "all"  },
] as const;

// ─── Regime colours ───────────────────────────────────────────────────────────
const REGIME_META: Record<string, { color: string; bg: string; label: string }> = {
  trending_up:   { color: "#22c55e", bg: "rgba(34,197,94,0.12)",   label: "Trending Up"   },
  trending_down: { color: "#ef4444", bg: "rgba(239,68,68,0.12)",   label: "Trending Down" },
  ranging:       { color: "#f59e0b", bg: "rgba(245,158,11,0.12)",  label: "Ranging"       },
  volatile:      { color: "#f97316", bg: "rgba(249,115,22,0.12)",  label: "Volatile"      },
  unknown:       { color: "#6b7280", bg: "rgba(107,114,128,0.12)", label: "Unknown"       },
};

// ─── Helpers ──────────────────────────────────────────────────────────────────
const pnlCls = (v: number) => v > 0 ? "text-green-400" : v < 0 ? "text-red-400" : "text-neutral-400";
const fmtPnl = (v: number) => `${v >= 0 ? "+" : ""}$${Math.abs(v).toFixed(2)}`;

// ─── Agent log feed ───────────────────────────────────────────────────────────
function AgentLog({ logs }: { logs: string[] }) {
  return (
    <div className="h-64 overflow-y-auto font-mono text-[10px] space-y-0.5 bg-black/40 rounded-xl p-3 border border-neutral-800">
      {logs.length === 0 && (
        <div className="text-neutral-700 py-2">Server log will appear here…</div>
      )}
      {[...logs].reverse().map((line, i) => (
        <div key={i} className={`leading-relaxed ${
          line.includes("WIN") || line.includes("✓") || line.includes("OPEN") ? "text-green-400" :
          line.includes("LOSS") || line.includes("✗") || line.includes("ERROR") || line.includes("error") ? "text-red-400" :
          line.includes("FUSION") || line.includes("fuse") ? "text-pink-400" :
          line.includes("Brain") || line.includes("regime") ? "text-violet-400" :
          line.includes("LIVE") || line.includes("BingX") ? "text-amber-400" :
          line.includes("shadow") || line.includes("PAPER") ? "text-sky-400" :
          "text-neutral-500"
        }`}>
          {line}
        </div>
      ))}
    </div>
  );
}

// ─── BingX Live Executor panel ────────────────────────────────────────────────
function BingXPanel({
  executor,
  onResetCircuit,
}: {
  executor: LiveExecutorStatus | null;
  onResetCircuit: () => void;
}) {
  const [testing, setTesting]     = useState(false);
  const [testResult, setTestResult] = useState<{ok: boolean; message?: string; error?: string; hint?: string} | null>(null);

  const runTest = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      const token = typeof window !== "undefined" ? localStorage.getItem("tradeos_token") : null;
      const r = await fetch(`${process.env.NEXT_PUBLIC_API_URL || ""}/api/agent/live/test-connection`, {
        headers: { "Authorization": `Bearer ${token}` },
      });
      const data = await r.json();
      setTestResult(data);
    } catch (e) {
      setTestResult({ ok: false, error: "Network error — Railway may be down" });
    } finally {
      setTesting(false);
    }
  };

  if (!executor) {
    return (
      <div className="card p-5 flex flex-col gap-3">
        <div className="flex items-center gap-2">
          <Cpu size={13} className="text-neutral-500" />
          <span className="text-[13px] font-semibold text-white">BingX Live Executor</span>
        </div>
        <div className="flex items-center gap-2 py-4 text-neutral-600 text-[12px]">
          <WifiOff size={13} /> Not available — set mode to Live to activate
        </div>
        <button onClick={runTest} disabled={testing}
          className="w-full py-2 rounded-lg text-[10px] font-bold border border-neutral-700 bg-neutral-800 hover:bg-neutral-700 text-neutral-300 transition-colors flex items-center justify-center gap-1.5 disabled:opacity-50">
          {testing ? <><Loader2 size={10} className="animate-spin" /> Testing...</> : <><Wifi size={10} /> Test BingX Connection</>}
        </button>
        {testResult && (
          <div className={`p-2.5 rounded-lg border text-[10px] space-y-1 ${testResult.ok ? "border-green-500/30 bg-green-500/8 text-green-400" : "border-red-500/30 bg-red-500/8 text-red-400"}`}>
            <div className="font-bold flex items-center gap-1">
              {testResult.ok ? <CheckCircle2 size={10} /> : <XCircle size={10} />}
              {testResult.ok ? testResult.message : "Connection failed"}
            </div>
            {!testResult.ok && testResult.error && (
              <div className="text-[9px] opacity-80 font-mono break-all">{testResult.error}</div>
            )}
            {!testResult.ok && testResult.hint && (
              <div className="text-[9px] text-amber-400 mt-1">{testResult.hint}</div>
            )}
          </div>
        )}
      </div>
    );
  }

  const connected  = executor.connected ?? executor.keys_set ?? false;
  const halted     = executor.halted;
  const pctUsed    = executor.daily_loss_limit > 0
    ? Math.min(Math.abs(executor.daily_pnl) / executor.daily_loss_limit * 100, 100)
    : 0;
  const dailyPnlColor = executor.daily_pnl >= 0 ? "#22c55e" : "#ef4444";

  return (
    <div className="card p-5 space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Cpu size={13} className="text-amber-400" />
          <span className="text-[13px] font-semibold text-white">BingX Live Executor</span>
        </div>
        <div className={`flex items-center gap-1.5 text-[10px] font-bold px-2 py-1 rounded-lg border ${
          halted ? "bg-red-500/10 border-red-500/30 text-red-400" :
          connected ? "bg-green-500/10 border-green-500/30 text-green-400" :
          "bg-neutral-800 border-neutral-700 text-neutral-500"
        }`}>
          {halted ? <><AlertTriangle size={10} /> HALTED</> :
           connected ? <><span className="w-1.5 h-1.5 rounded-full bg-green-400 animate-pulse" /> LIVE</> :
           <><WifiOff size={10} /> DISCONNECTED</>}
        </div>
      </div>

      {/* Circuit breaker alert */}
      {halted && (
        <div className="p-3 rounded-xl border border-red-500/30 bg-red-500/8 flex items-center justify-between gap-3">
          <div>
            <div className="text-[11px] font-bold text-red-400">Circuit Breaker Triggered</div>
            {executor.message && <div className="text-[9px] text-red-400/70 mt-0.5">{executor.message}</div>}
          </div>
          <button onClick={onResetCircuit}
            className="px-3 py-1.5 rounded-lg text-[10px] font-bold border border-red-500/40 bg-red-500/10 text-red-400 hover:bg-red-500/20 transition-colors flex-shrink-0">
            Reset
          </button>
        </div>
      )}

      {executor.last_error && (
        <div className="p-2.5 rounded-lg border border-orange-500/20 bg-orange-500/5 text-[10px] text-orange-400/80">
          Last error: {executor.last_error}
        </div>
      )}

      {/* Connection test */}
      <div className="space-y-2">
        <button onClick={runTest} disabled={testing}
          className="w-full py-2 rounded-lg text-[10px] font-bold border border-neutral-700 bg-neutral-800 hover:bg-neutral-700 text-neutral-300 transition-colors flex items-center justify-center gap-1.5 disabled:opacity-50">
          {testing ? <><Loader2 size={10} className="animate-spin" /> Testing BingX connection...</> : <><Wifi size={10} /> Test BingX Connection</>}
        </button>
        {testResult && (
          <div className={`p-2.5 rounded-lg border text-[10px] space-y-1 ${testResult.ok ? "border-green-500/30 bg-green-500/8 text-green-400" : "border-red-500/30 bg-red-500/8 text-red-400"}`}>
            <div className="font-bold flex items-center gap-1">
              {testResult.ok ? <CheckCircle2 size={10} /> : <XCircle size={10} />}
              {testResult.ok ? testResult.message : "Connection failed"}
            </div>
            {!testResult.ok && testResult.error && (
              <div className="text-[9px] opacity-80 font-mono break-all">{testResult.error}</div>
            )}
            {!testResult.ok && testResult.hint && (
              <div className="text-[9px] text-amber-400 mt-1">{testResult.hint}</div>
            )}
          </div>
        )}
      </div>

      {/* Account */}
      <div className="grid grid-cols-2 gap-2">
        {[
          ["Account Balance",  executor.account_balance != null ? `$${executor.account_balance.toFixed(2)}` : "—", "text-white"],
          ["Free Balance",     executor.free_balance    != null ? `$${executor.free_balance.toFixed(2)}`    : "—", "text-blue-400"],
          ["Open Positions",   `${executor.open_count}`,                                                           "text-white"],
          ["Max Leverage",     executor.max_leverage ? `${executor.max_leverage}×` : "—",                         "text-orange-400"],
        ].map(([l, v, cls]) => (
          <div key={l} className="rounded-xl p-3 bg-neutral-900 border border-neutral-800">
            <div className="text-[8px] text-neutral-600 mb-0.5">{l}</div>
            <div className={`text-[13px] font-bold font-mono ${cls}`}>{v}</div>
          </div>
        ))}
      </div>

      {/* Daily P&L + circuit breaker progress */}
      <div className="space-y-2">
        <div className="flex items-center justify-between text-[10px]">
          <span className="text-neutral-600">Daily P&L</span>
          <span className="font-mono font-bold" style={{ color: dailyPnlColor }}>
            {fmtPnl(executor.daily_pnl)}
          </span>
        </div>
        <div className="flex items-center justify-between text-[10px]">
          <span className="text-neutral-600">Loss Limit</span>
          <span className="font-mono text-neutral-400">${executor.daily_loss_limit}</span>
        </div>
        <div className="h-2 bg-neutral-800 rounded-full overflow-hidden">
          <div
            className="h-full rounded-full transition-all duration-500"
            style={{
              width: `${pctUsed}%`,
              background: pctUsed > 80 ? "#ef4444" : pctUsed > 50 ? "#f59e0b" : "#22c55e",
            }}
          />
        </div>
        <div className="text-[8px] text-neutral-700 text-right">{pctUsed.toFixed(0)}% of daily limit used</div>
      </div>

      {/* Risk settings */}
      <div className="flex gap-3 pt-1 border-t border-neutral-800">
        {[
          ["Risk/Trade", executor.risk_per_trade_pct != null ? `${(executor.risk_per_trade_pct * 100).toFixed(0)}%` : "—"],
          ["Max Pos",    executor.max_position_usdc  != null ? `$${executor.max_position_usdc}` : "—"],
          ["Mode",       executor.mode ?? "—"],
        ].map(([l, v]) => (
          <div key={l} className="flex-1 text-center">
            <div className="text-[8px] text-neutral-700">{l}</div>
            <div className="text-[10px] font-mono text-neutral-300">{v}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ─── Master Brain panel ───────────────────────────────────────────────────────
function BrainPanel({ brain }: { brain: MasterBrainStatus | null }) {
  if (!brain) {
    return (
      <div className="card p-5 flex items-center gap-3 text-neutral-600">
        <Brain size={14} />
        <span className="text-[12px]">Master Brain not yet active…</span>
      </div>
    );
  }

  const regime    = brain.regime ?? "unknown";
  const regimeMeta = REGIME_META[regime] ?? REGIME_META.unknown;
  const pf        = brain.portfolio;

  return (
    <div className="card p-5 space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Brain size={13} className="text-violet-400" />
          <span className="text-[13px] font-semibold text-white">Master Brain</span>
          <span className="text-[9px] text-violet-400 ml-1">always learning · always on</span>
        </div>
        <div className="flex items-center gap-2 text-[9px] text-neutral-600">
          <span className="w-1.5 h-1.5 rounded-full bg-violet-400 animate-pulse" />
          adaptive
        </div>
      </div>

      {/* Regime + confidence */}
      <div className="flex items-center gap-4 p-4 rounded-xl" style={{ background: regimeMeta.bg, border: `1px solid ${regimeMeta.color}30` }}>
        <div>
          <div className="text-[9px] text-neutral-500 mb-0.5">Market Regime</div>
          <div className="text-[16px] font-bold" style={{ color: regimeMeta.color }}>{regimeMeta.label}</div>
        </div>
        <div className="flex-1">
          <div className="flex justify-between text-[9px] mb-1">
            <span className="text-neutral-600">Confidence</span>
            <span style={{ color: regimeMeta.color }}>{((brain.regime_confidence ?? 0) * 100).toFixed(0)}%</span>
          </div>
          <div className="h-1.5 bg-black/30 rounded-full overflow-hidden">
            <div className="h-full rounded-full transition-all duration-500"
              style={{ width: `${(brain.regime_confidence ?? 0) * 100}%`, background: regimeMeta.color }} />
          </div>
        </div>
        {brain.regime_stability && (
          <div className="text-[9px] px-2 py-1 rounded-lg bg-black/20 text-neutral-400">{brain.regime_stability}</div>
        )}
      </div>

      {/* Portfolio stats */}
      {pf && (
        <div>
          <div className="text-[9px] text-neutral-600 font-bold uppercase tracking-wide mb-2">Portfolio State</div>
          <div className="grid grid-cols-4 gap-2">
            {[
              ["Daily P&L",     fmtPnl(pf.daily_pnl),    pf.daily_pnl >= 0 ? "#22c55e" : "#ef4444"],
              ["Daily Trades",  `${pf.daily_trades}`,     "#60aaff"],
              ["Win/Loss",      `${pf.daily_wins}W / ${pf.daily_losses}L`, "#a78bfa"],
              ["Consec. Losses",`${pf.consec_losses}`,   pf.consec_losses >= 3 ? "#ef4444" : pf.consec_losses >= 2 ? "#f59e0b" : "#4b5563"],
              ["Long Exp.",     `$${pf.long_exposure.toFixed(0)}`,  "#22c55e"],
              ["Short Exp.",    `$${pf.short_exposure.toFixed(0)}`, "#ef4444"],
              ["Net Exp.",      `$${pf.net_exposure.toFixed(0)}`,   "#60aaff"],
              ["Unrealized",    fmtPnl(pf.total_unrealized), pf.total_unrealized >= 0 ? "#22c55e" : "#ef4444"],
            ].map(([l, v, c]) => (
              <div key={l} className="rounded-lg p-2.5 bg-neutral-900 border border-neutral-800">
                <div className="text-[8px] text-neutral-600 mb-0.5">{l}</div>
                <div className="text-[11px] font-bold font-mono" style={{ color: c as string }}>{v}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Strategy trust scores */}
      {brain.strategy_trust && (
        <div>
          <div className="text-[9px] text-neutral-600 font-bold uppercase tracking-wide mb-2">Strategy Trust Scores</div>
          <div className="space-y-2">
            {STRATEGIES.map(s => {
              const trust   = brain.strategy_trust[s.key] ?? 1.0;
              const stats   = brain.strategy_stats?.[s.key];
              const ready   = brain.live_readiness?.[s.key];
              const pct     = Math.min(Math.max((trust / 2) * 100, 0), 100);
              const tColor  = trust >= 1.3 ? "#22c55e" : trust >= 0.9 ? "#60aaff" : trust >= 0.6 ? "#f59e0b" : "#ef4444";
              return (
                <div key={s.key} className="flex items-center gap-3">
                  <div className="w-20 flex-shrink-0">
                    <span className="text-[9px] font-semibold text-neutral-400">{s.label}</span>
                  </div>
                  <div className="flex-1 h-1.5 bg-neutral-800 rounded-full overflow-hidden">
                    <div className="h-full rounded-full transition-all duration-500"
                      style={{ width: `${pct}%`, background: tColor }} />
                  </div>
                  <span className="text-[9px] font-mono w-8 text-right" style={{ color: tColor }}>
                    {trust.toFixed(2)}
                  </span>
                  {stats && (
                    <span className="text-[8px] text-neutral-700 w-20 text-right">
                      {stats.trades}t · {stats.win_rate != null ? (stats.win_rate * 100).toFixed(0) : "—"}%WR
                    </span>
                  )}
                  {ready && (
                    <span className={`text-[8px] px-1.5 py-0.5 rounded font-bold flex-shrink-0 ${ready.ready ? "bg-green-500/10 text-green-400" : "bg-neutral-800 text-neutral-600"}`}>
                      {ready.ready ? "LIVE ✓" : `${ready.trades}/${ready.trades_needed}t`}
                    </span>
                  )}
                </div>
              );
            })}
          </div>
          <div className="flex gap-4 mt-2 text-[8px] text-neutral-700">
            <span>0.0 = destroyed</span>
            <span>1.0 = neutral</span>
            <span>2.0 = excellent</span>
          </div>
        </div>
      )}

      {/* Recent decisions */}
      {brain.recent_decisions && brain.recent_decisions.length > 0 && (
        <div>
          <div className="text-[9px] text-neutral-600 font-bold uppercase tracking-wide mb-2">Recent Decisions</div>
          <div className="space-y-1.5 max-h-52 overflow-y-auto">
            {brain.recent_decisions.slice(0, 10).map((dec, i) => {
              const isApproved = dec.approved;
              const stratMeta  = STRATEGIES.find(s => s.label === dec.strategy_name || dec.strategy_name?.toLowerCase().includes(s.key));
              const color      = stratMeta?.color ?? "#6b7280";
              return (
                <div key={i} className="flex items-start gap-2 py-1.5 px-2 rounded-lg border"
                  style={{
                    background: isApproved ? "rgba(34,197,94,0.04)" : "rgba(255,255,255,0.02)",
                    borderColor: isApproved ? "rgba(34,197,94,0.15)" : "rgba(255,255,255,0.05)",
                  }}>
                  <div className={`w-4 h-4 rounded flex items-center justify-center flex-shrink-0 mt-0.5 ${isApproved ? "bg-green-500/20" : "bg-neutral-800"}`}>
                    {isApproved
                      ? <CheckCircle2 size={9} className="text-green-400" />
                      : <XCircle size={9} className="text-neutral-600" />}
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-1.5 flex-wrap">
                      <span className="text-[9px] font-bold" style={{ color }}>
                        {dec.strategy_name}
                      </span>
                      <span className={`text-[8px] font-bold ${dec.direction === "long" ? "text-green-400" : "text-red-400"}`}>
                        {dec.direction?.toUpperCase()}
                      </span>
                      <span className="text-[8px] text-neutral-600">{dec.regime}</span>
                      {dec.is_live && <span className="text-[7px] bg-amber-500/15 text-amber-400 px-1 rounded">LIVE</span>}
                      <span className="text-[8px] text-neutral-700 ml-auto">
                        {new Date(dec.timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                      </span>
                    </div>
                    <div className="text-[8px] text-neutral-600 mt-0.5 truncate">{dec.reasoning}</div>
                  </div>
                  <div className="text-[9px] font-mono text-neutral-500 flex-shrink-0">
                    {(dec.conviction * 100).toFixed(0)}%
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

// ─── Paper Trader panel ──────────────────────────────────────────────────────
function PaperTraderPanel({ data }: { data: Record<string, unknown> | null }) {
  if (!data) return null;

  const balance        = (data.balance as number)          ?? 10000;
  const startBal       = (data.starting_balance as number) ?? 10000;
  const returnPct      = (data.return_pct as number)       ?? 0;
  const dailyPnl       = (data.daily_pnl as number)        ?? 0;
  const openCount      = (data.open_count as number)       ?? 0;
  const stats          = (data.stats as Record<string, number>) ?? {};
  const totalTrades    = stats.total_trades   ?? 0;
  const wins           = stats.wins           ?? 0;
  const losses         = stats.losses         ?? 0;
  const winRate        = stats.win_rate       ?? 0;
  const totalPnl       = stats.total_pnl      ?? 0;
  const bestTrade      = stats.best_trade     ?? 0;
  const worstTrade     = stats.worst_trade    ?? 0;
  const peakBal        = stats.peak_balance   ?? startBal;
  const maxDD          = stats.max_drawdown_pct ?? 0;
  const openPositions  = (data.open_positions as Array<Record<string, unknown>>) ?? [];
  const recentTrades   = (data.recent_trades  as Array<Record<string, unknown>>) ?? [];
  const equityCurve    = (data.equity_curve   as Array<Record<string, unknown>>) ?? [];

  const pnlCol = (n: number) => n > 0 ? "#22c55e" : n < 0 ? "#ef4444" : "#888";
  const sign   = (n: number) => n > 0 ? "+" : "";
  const fmt2   = (n: number) => Math.abs(n).toFixed(2);

  // Mini equity sparkline
  const sparkW = 200, sparkH = 32;
  let sparkPath = "";
  if (equityCurve.length >= 2) {
    const bals = equityCurve.map(e => (e.balance as number) ?? startBal);
    const mn = Math.min(...bals) * 0.999, mx = Math.max(...bals) * 1.001, rng = mx - mn || 1;
    const xStep = sparkW / (bals.length - 1);
    sparkPath = bals.map((b, i) => `${i === 0 ? "M" : "L"}${i * xStep},${2 + (1 - (b - mn) / rng) * (sparkH - 4)}`).join(" ");
  }

  return (
    <div className="card p-5 space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <LineChart size={13} className="text-cyan-400" />
          <span className="text-[13px] font-semibold text-white">Paper Trader</span>
          <span className="text-[9px] text-cyan-400/60 ml-1">$10K virtual · training brain</span>
        </div>
        <div className="flex items-center gap-2">
          {equityCurve.length >= 2 && (
            <svg width={sparkW} height={sparkH} className="opacity-60">
              <polyline points={sparkPath} fill="none" stroke={returnPct >= 0 ? "#22c55e" : "#ef4444"} strokeWidth="1.5" strokeLinejoin="round" />
            </svg>
          )}
          <span className="text-[10px] font-bold font-mono px-2 py-1 rounded-lg border"
            style={{
              color: pnlCol(returnPct),
              background: returnPct >= 0 ? "rgba(34,197,94,0.08)" : "rgba(239,68,68,0.08)",
              borderColor: returnPct >= 0 ? "rgba(34,197,94,0.2)" : "rgba(239,68,68,0.2)",
            }}>
            {sign(returnPct)}{returnPct.toFixed(2)}%
          </span>
        </div>
      </div>

      {/* KPI grid */}
      <div className="grid grid-cols-4 gap-2">
        {([
          ["Balance",     `$${balance.toFixed(2)}`,                        pnlCol(balance - startBal)],
          ["Daily P&L",   `${sign(dailyPnl)}$${fmt2(dailyPnl)}`,         pnlCol(dailyPnl)],
          ["Win Rate",    totalTrades > 0 ? `${winRate.toFixed(1)}%` : "—", "#f59e0b"],
          ["Trades",      `${totalTrades} (${wins}W/${losses}L)`,         "#60aaff"],
        ] as const).map(([l, v, c]) => (
          <div key={l} className="rounded-xl p-2.5 bg-neutral-900 border border-neutral-800">
            <div className="text-[8px] text-neutral-600 mb-0.5">{l}</div>
            <div className="text-[11px] font-bold font-mono" style={{ color: c }}>{v}</div>
          </div>
        ))}
      </div>

      {/* Extra stats row */}
      <div className="flex gap-3 pt-1 border-t border-neutral-800">
        {([
          ["Peak",     `$${peakBal.toFixed(0)}`],
          ["Max DD",   `${maxDD.toFixed(1)}%`],
          ["Best",     `+$${bestTrade.toFixed(2)}`],
          ["Worst",    `$${worstTrade.toFixed(2)}`],
          ["Open",     `${openCount}`],
        ] as const).map(([l, v]) => (
          <div key={l} className="flex-1 text-center">
            <div className="text-[8px] text-neutral-700">{l}</div>
            <div className="text-[10px] font-mono text-neutral-300">{v}</div>
          </div>
        ))}
      </div>

      {/* Open paper positions — compact list */}
      {openPositions.length > 0 && (
        <div>
          <div className="text-[9px] text-cyan-400/60 font-bold uppercase tracking-wide mb-2">
            Open Paper Positions ({openPositions.length})
          </div>
          <div className="grid grid-cols-2 gap-2">
            {openPositions.slice(0, 10).map((p, i) => {
              const dir = (p.direction as string) ?? "";
              const upnl = (p.unrealized_pnl as number) ?? 0;
              const name = ((p.strategy_name as string) ?? "").replace("[PAPER] ", "");
              return (
                <div key={i} className="rounded-lg p-2.5 border border-cyan-500/10 bg-cyan-500/5">
                  <div className="flex items-center gap-1.5 mb-1">
                    <div className={`w-1.5 h-1.5 rounded-full ${dir === "long" ? "bg-green-400" : "bg-red-400"}`} />
                    <span className="text-[9px] font-bold text-cyan-400">{name}</span>
                    <span className={`text-[8px] ml-auto font-mono font-bold ${upnl >= 0 ? "text-green-400" : "text-red-400"}`}>
                      {sign(upnl)}${fmt2(upnl)}
                    </span>
                  </div>
                  <div className="text-[8px] text-neutral-700 font-mono">
                    ${((p.entry as number) ?? 0).toFixed(0)} · {dir} · {(p.leverage as number) ?? 1}x
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Recent trades — compact */}
      {recentTrades.length > 0 && (
        <div>
          <div className="text-[9px] text-neutral-600 font-bold uppercase tracking-wide mb-2">Recent Paper Trades</div>
          <div className="max-h-40 overflow-y-auto space-y-0">
            {recentTrades.slice(0, 10).map((t, i) => {
              const pnl = (t.pnl_usd as number) ?? 0;
              const dir = (t.direction as string) ?? "";
              const name = ((t.strategy_name as string) ?? "").replace("[PAPER] ", "");
              const reason = (t.exit_reason as string) ?? "";
              const bal = (t.balance_after as number) ?? balance;
              return (
                <div key={i} className="flex items-center gap-2 py-1.5 border-b border-neutral-800/30 last:border-0">
                  <div className={`w-5 h-5 rounded flex items-center justify-center flex-shrink-0 ${dir === "long" ? "bg-green-500/15" : "bg-red-500/15"}`}>
                    {dir === "long"
                      ? <ArrowUpRight size={10} className="text-green-400" />
                      : <ArrowDownRight size={10} className="text-red-400" />}
                  </div>
                  <div className="flex-1 min-w-0">
                    <span className="text-[9px] font-bold text-neutral-300">{name}</span>
                    <div className="text-[7px] text-neutral-700 font-mono">
                      ${((t.entry as number) ?? 0).toFixed(0)} → ${((t.exit_price as number) ?? 0).toFixed(0)} · bal ${bal.toFixed(0)}
                    </div>
                  </div>
                  <div className="text-right flex-shrink-0">
                    <div className={`text-[9px] font-bold font-mono ${pnl >= 0 ? "text-green-400" : "text-red-400"}`}>
                      {sign(pnl)}${fmt2(pnl)}
                    </div>
                    <div className={`text-[7px] font-bold ${reason === "tp" ? "text-green-400/70" : reason === "sl" ? "text-red-400/70" : "text-neutral-600"}`}>
                      {reason.toUpperCase()}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

// ─── Open positions panel ─────────────────────────────────────────────────────
function PositionsPanel({
  positions,
  livePositions,
  onClose,
  onCloseLive,
}: {
  positions:     ServerPosition[];
  livePositions: ServerPosition[];
  onClose:       (key: string) => void;
  onCloseLive:   (key: string) => void;
}) {
  const allPositions = [
    ...livePositions.map(p => ({ ...p, _isLive: true })),
    ...positions.map(p => ({ ...p, _isLive: false })),
  ];

  const fusion  = allPositions.filter(p => p.strategy_key === "fusion");
  const shadows = allPositions.filter(p => p.strategy_key?.startsWith("shadow_") || p.is_shadow);
  const regular = allPositions.filter(p => !p.strategy_key?.startsWith("shadow_") && !p.is_shadow && p.strategy_key !== "fusion");

  const renderPosition = (p: ServerPosition & { _isLive?: boolean }, key: string) => {
    const isLong   = p.direction === "long";
    const isFusion = p.strategy_key === "fusion";
    const isShadow = p.strategy_key?.startsWith("shadow_") || p.is_shadow;
    const colorStr = isFusion ? "#f472b6" : isLong ? "#22c55e" : "#ef4444";
    const unreal   = p.unrealized_pnl ?? 0;
    return (
      <div key={key}
        className="rounded-xl p-3.5 space-y-2.5"
        style={{
          background: isFusion ? "rgba(244,114,182,0.06)" : isShadow ? "rgba(14,165,233,0.05)" : isLong ? "rgba(34,197,94,0.05)" : "rgba(239,68,68,0.05)",
          border: `1px solid ${isFusion ? "rgba(244,114,182,0.25)" : isShadow ? "rgba(14,165,233,0.15)" : isLong ? "rgba(34,197,94,0.2)" : "rgba(239,68,68,0.2)"}`,
        }}>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <div className="w-2 h-2 rounded-full animate-pulse" style={{ background: colorStr }} />
            <span className="text-[11px] font-bold" style={{ color: colorStr }}>
              {p.direction.toUpperCase()} {isFusion ? "🧠 FUSION" : p.strategy_name}
            </span>
            {isShadow && <span className="text-[7px] bg-sky-500/10 border border-sky-500/20 text-sky-400 px-1.5 py-0.5 rounded">SHADOW</span>}
            {p._isLive && <span className="text-[7px] bg-amber-500/10 border border-amber-500/20 text-amber-400 px-1.5 py-0.5 rounded">LIVE</span>}
            {p.is_paper && !isShadow && <span className="text-[7px] bg-violet-500/10 border border-violet-500/20 text-violet-400 px-1.5 py-0.5 rounded">PAPER</span>}
          </div>
          <button
            onClick={() => p._isLive ? onCloseLive(p.strategy_key) : onClose(p.strategy_key)}
            className="flex items-center gap-1 text-[9px] text-neutral-600 hover:text-red-400 transition-colors border border-neutral-800 hover:border-red-500/30 rounded-lg px-2 py-1">
            <X size={9} /> Close
          </button>
        </div>

        <div className="grid grid-cols-3 gap-2 text-[10px] font-mono">
          <div><span className="text-neutral-600 block text-[8px]">Entry</span><span className="text-white">${p.entry.toFixed(0)}</span></div>
          <div><span className="text-neutral-600 block text-[8px]">Current</span><span className="text-white">${(p.current_price ?? p.entry).toFixed(0)}</span></div>
          <div>
            <span className="text-neutral-600 block text-[8px]">Unrealized P&L</span>
            <span className={pnlCls(unreal)}>{fmtPnl(unreal)}</span>
          </div>
        </div>

        {p.sl && p.tp && (() => {
          const isShort = p.direction === "short";
          // For longs:  progress goes SL (left) → TP (right), current between them
          // For shorts: SL is above entry and TP is below, so flip the geometry
          const lo      = isShort ? p.tp  : p.sl;
          const hi      = isShort ? p.sl  : p.tp;
          const range   = hi - lo;
          const pct     = range > 0 ? ((p.current_price - lo) / range * 100) : 50;
          const clamped = Math.min(Math.max(pct, 0), 100);
          return (
            <div className="space-y-1">
              <div className="flex justify-between text-[8px]">
                <span className="text-red-400">SL ${p.sl.toFixed(0)}</span>
                <span className="text-neutral-600 font-sans">${p.size_usdc} · {p.confidence ? `${(p.confidence * 100).toFixed(0)}% conf` : ""}</span>
                <span className="text-green-400">TP ${p.tp.toFixed(0)}</span>
              </div>
              <div className="h-1.5 bg-neutral-800 rounded-full overflow-hidden relative">
                <div className="absolute inset-0 flex">
                  <div className="h-full bg-red-900/40"   style={{ width: "33%" }} />
                  <div className="h-full bg-neutral-900"  style={{ width: "34%" }} />
                  <div className="h-full bg-green-900/40" style={{ width: "33%" }} />
                </div>
                <div className="absolute top-0 h-full w-0.5 bg-white rounded-full transition-all duration-200" style={{ left: `${clamped}%` }} />
              </div>
            </div>
          );
        })()}

        {p.reasoning && (
          <div className="text-[8px] text-neutral-600 truncate">{p.reasoning}</div>
        )}
      </div>
    );
  };

  if (allPositions.length === 0) {
    return (
      <div className="card p-4 flex items-center gap-3">
        <DollarSign size={14} className="text-neutral-700" />
        <span className="text-[12px] text-neutral-600">No open positions — agent scanning for signals…</span>
      </div>
    );
  }

  return (
    <div className="card p-4 space-y-4">
      <div className="flex items-center gap-2">
        <Activity size={13} className="text-neutral-500" />
        <span className="text-[13px] font-semibold text-white">Open Positions</span>
        <span className="text-[9px] text-neutral-600 ml-auto">{allPositions.length} total</span>
      </div>

      {fusion.length > 0 && (
        <div>
          <div className="text-[9px] text-pink-400/70 font-bold uppercase tracking-wide mb-2">🧠 Fusion (Master Brain)</div>
          <div className="space-y-2">
            {fusion.map((p, i) => renderPosition(p, `fusion-${i}`))}
          </div>
        </div>
      )}

      {regular.length > 0 && (
        <div>
          {fusion.length > 0 && <div className="text-[9px] text-neutral-600 font-bold uppercase tracking-wide mb-2">Individual Strategies</div>}
          <div className="space-y-2">
            {regular.map((p, i) => renderPosition(p, `reg-${i}`))}
          </div>
        </div>
      )}

      {shadows.length > 0 && (
        <div>
          <div className="text-[9px] text-sky-400/60 font-bold uppercase tracking-wide mb-2">Shadow Training ({shadows.length} active)</div>
          <div className="grid grid-cols-2 gap-2">
            {shadows.map((p, i) => (
              <div key={`sh-${i}`} className="rounded-lg p-2.5 border border-sky-500/10 bg-sky-500/5">
                <div className="flex items-center gap-1.5 mb-1">
                  <div className={`w-1.5 h-1.5 rounded-full ${p.direction === "long" ? "bg-green-400" : "bg-red-400"}`} />
                  <span className="text-[9px] font-bold text-sky-400">{p.strategy_name}</span>
                  <span className={`text-[8px] ml-auto ${pnlCls(p.unrealized_pnl ?? 0)}`}>{fmtPnl(p.unrealized_pnl ?? 0)}</span>
                </div>
                <div className="text-[8px] text-neutral-700 font-mono">
                  ${p.entry.toFixed(0)} · {p.direction}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// ─── Main page ────────────────────────────────────────────────────────────────
function AgentContent() {
  const server = useServerAgent();
  const brain  = server.brain;
  const exec   = server.liveExecutor;

  const stats       = server.stats;
  const positions   = server.openPositions;
  const livePos     = exec?.live_positions ?? [];
  const mode        = server.config?.mode ?? "paper";
  const isLive      = mode === "live";

  return (
    <div className="p-4 space-y-4">

      {/* ─── 1. Header ───────────────────────────────────────────────────── */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="flex items-center gap-3">
          <div className="relative">
            <Bot size={26} className={server.running ? "text-blue-400" : "text-neutral-600"} />
            {server.running && (
              <span className="absolute -top-0.5 -right-0.5 w-2.5 h-2.5 rounded-full bg-green-400 animate-ping" />
            )}
          </div>
          <div>
            <h1 className="text-lg font-bold text-white flex items-center gap-2">
              Trading Agent
              {server.running ? (
                <span className="flex items-center gap-1.5 text-[9px] font-bold px-2 py-0.5 rounded-full bg-emerald-500/10 border border-emerald-500/20 text-emerald-400">
                  <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
                  24/7 LIVE
                </span>
              ) : server.error ? (
                <span className="flex items-center gap-1.5 text-[9px] px-2 py-0.5 rounded-full bg-red-500/10 border border-red-500/20 text-red-400">
                  <AlertTriangle size={9} /> Server offline
                </span>
              ) : (
                <span className="text-[9px] text-neutral-600">connecting…</span>
              )}
              {isLive && (
                <span className="text-[9px] font-bold px-2 py-0.5 rounded-full bg-amber-500/10 border border-amber-500/20 text-amber-400">
                  🔴 LIVE BingX
                </span>
              )}
            </h1>
            <p className="text-[10px] text-neutral-600 mt-0.5">
              Fusion strategy · 5 sub-strategies · Master Brain adaptive learning · shadow training always on
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2">
          {server.livePrice > 0 && (
            <div className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-neutral-800 bg-neutral-900">
              <span className="text-[9px] text-neutral-600">BTC</span>
              <span className="text-[13px] font-bold font-mono text-white">${server.livePrice.toLocaleString()}</span>
            </div>
          )}
          <button onClick={() => server.running ? server.stopAgent() : server.startAgent()}
            className="flex items-center gap-2 px-4 py-2 rounded-xl font-bold text-[12px] transition-all"
            style={server.running
              ? { background: "rgba(239,68,68,0.15)", border: "1px solid rgba(239,68,68,0.3)", color: "#ef4444" }
              : { background: "rgba(34,197,94,0.15)", border: "1px solid rgba(34,197,94,0.3)", color: "#22c55e" }}>
            <Power size={13} />
            {server.running ? "Stop Agent" : "Start Agent"}
          </button>
          <button onClick={server.forceScan}
            className="p-2 rounded-xl border border-neutral-800 text-neutral-600 hover:text-white hover:border-neutral-600 transition-colors"
            title="Force scan">
            <RefreshCw size={13} className={server.loading ? "animate-spin" : ""} />
          </button>
        </div>
      </div>

      {/* ─── 2. Status strip ─────────────────────────────────────────────── */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2">
        {[
          { label: "Scans Run",    value: server.scanCount.toString(),                                                                        color: "#60aaff" },
          { label: "Last Scan",    value: server.lastScan ?? "—",                                                                             color: "#a78bfa" },
          { label: "Mode",         value: mode === "live" ? "🔴 LIVE" : "📄 PAPER",                                                           color: mode === "live" ? "#ef4444" : "#a78bfa" },
          { label: "Open Pos",     value: `${positions.length + livePos.length}`,                                                             color: "#22c55e" },
          { label: "Win Rate",     value: stats && stats.total_trades > 0 ? `${stats.win_rate.toFixed(1)}%` : "—",                           color: "#f59e0b" },
          { label: "All-Time P&L", value: stats ? fmtPnl(stats.total_pnl) : "—",                                                            color: stats && stats.total_pnl >= 0 ? "#22c55e" : "#ef4444" },
        ].map(({ label, value, color }) => (
          <div key={label} className="card px-3 py-2.5 flex flex-col gap-0.5">
            <div className="text-[8px] text-neutral-600 uppercase tracking-wide">{label}</div>
            <div className="text-[13px] font-bold font-mono" style={{ color }}>{value}</div>
          </div>
        ))}
      </div>

      {/* ─── 3. Mode banner ──────────────────────────────────────────────── */}
      {!isLive ? (
        <div className="flex items-center gap-3 p-3 rounded-xl border border-violet-500/20 bg-violet-500/5">
          <FileText size={13} className="text-violet-400 flex-shrink-0" />
          <div className="text-[11px] text-violet-300">
            <strong>Paper training active</strong> — all strategies are shadow-training · Master Brain learning from every trade ·
            strategies must prove &gt;40% win rate on 5+ trades before live BingX execution
          </div>
        </div>
      ) : (
        <div className="flex items-center gap-3 p-3 rounded-xl border border-amber-500/20 bg-amber-500/5">
          <AlertTriangle size={13} className="text-amber-400 flex-shrink-0" />
          <div className="text-[11px] text-amber-300">
            <strong>Dual mode active</strong> — live-qualified strategies execute on BingX · unqualified strategies shadow-train ·
            Fusion signal from Master Brain controls primary execution
          </div>
        </div>
      )}

      {/* ─── 4. BingX + Paper Trader ─────────────────────────────────────── */}
      <BingXPanel executor={exec} onResetCircuit={server.resetCircuitBreaker} />
      <PaperTraderPanel data={server.paperTrader} />

      {/* ─── 5. Master Brain ─────────────────────────────────────────────── */}
      <BrainPanel brain={brain} />

      {/* ─── 6. Open Positions ───────────────────────────────────────────── */}
      <PositionsPanel
        positions={positions}
        livePositions={livePos}
        onClose={server.closePosition}
        onCloseLive={server.closeLivePosition}
      />

      {/* ─── 7. Agent Log ────────────────────────────────────────────────── */}
      <div className="card p-4">
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2">
            <ChevronRight size={13} className="text-neutral-500" />
            <span className="text-[13px] font-semibold text-white">Master Brain Agent Log</span>
          </div>
          <div className="flex items-center gap-1.5">
            {server.log.length > 0
              ? <><span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" /><span className="text-[9px] text-emerald-400">live server log</span></>
              : <span className="text-[9px] text-neutral-700">waiting for logs…</span>}
          </div>
        </div>
        <AgentLog logs={server.log} />
      </div>

      {/* ─── 8. Execution History + Signal Timeline ───────────────────────── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">

        {/* Execution History */}
        <div className="card p-4">
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2">
              <Zap size={13} className="text-neutral-500" />
              <span className="text-[13px] font-semibold text-white">Execution History</span>
              {stats && stats.total_trades > 0 && (
                <span className={`text-[10px] font-bold font-mono ml-1 ${pnlCls(stats.total_pnl)}`}>
                  {fmtPnl(stats.total_pnl)}
                </span>
              )}
            </div>
            <div className="flex items-center gap-2">
              {stats && stats.total_trades > 0 && (
                <span className="text-[9px] text-neutral-600">
                  {stats.wins}W / {stats.losses}L · {stats.win_rate.toFixed(1)}% WR
                </span>
              )}
              {!server.loading && !server.error && (
                <button onClick={server.resetAccount}
                  className="flex items-center gap-1 text-[9px] text-neutral-700 hover:text-red-400 transition-colors"
                  title="Reset all trade history">
                  <RotateCcw size={10} /> Reset
                </button>
              )}
            </div>
          </div>

          {server.loading ? (
            <div className="flex items-center justify-center gap-2 py-8 text-neutral-700 text-[12px]">
              <RefreshCw size={12} className="animate-spin" /> Connecting…
            </div>
          ) : server.error ? (
            <div className="flex items-center gap-2 py-6 text-red-400 text-[12px] justify-center">
              <AlertTriangle size={12} /> Cannot reach server
            </div>
          ) : server.trades.length === 0 ? (
            <div className="text-center py-8 text-neutral-700 text-[12px]">
              No closed trades yet — agent scanning every 20s
            </div>
          ) : (
            <div className="max-h-72 overflow-y-auto space-y-0">
              {server.trades.slice(0, 30).map(t => {
                const isLong = t.direction === "long";
                const pnl    = t.pnl_usd ?? 0;
                const isFusion = t.strategy_key === "fusion" || t.strategy_name?.includes("Fusion");
                return (
                  <div key={t.id} className="flex items-center gap-3 py-2 border-b border-neutral-800/50 last:border-0">
                    <div className={`w-6 h-6 rounded-md flex items-center justify-center flex-shrink-0 ${isLong ? "bg-green-500/15" : "bg-red-500/15"}`}>
                      {isLong
                        ? <TrendingUp size={10} className="text-green-400" />
                        : <TrendingDown size={10} className="text-red-400" />}
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-1.5">
                        <span className={`text-[10px] font-bold ${isLong ? "text-green-400" : "text-red-400"}`}>
                          {t.direction.toUpperCase()} BTC
                        </span>
                        {isFusion && <span className="text-[7px] bg-pink-500/10 text-pink-400 px-1 rounded">🧠FUSION</span>}
                        <span className="text-[8px] text-neutral-600 truncate">{t.strategy_name}</span>
                      </div>
                      <div className="text-[8px] text-neutral-700 font-mono">
                        ${t.entry.toFixed(0)} → ${t.exit_price?.toFixed(0) ?? "—"} ·{" "}
                        {new Date(t.closed_at ?? t.timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                      </div>
                    </div>
                    <div className="text-right flex-shrink-0">
                      <div className={`text-[10px] font-bold font-mono ${pnlCls(pnl)}`}>{fmtPnl(pnl)}</div>
                      <div className={`text-[8px] font-bold ${
                        t.exit_reason === "tp" ? "text-green-400" : t.exit_reason === "sl" ? "text-red-400" : "text-neutral-600"
                      }`}>{(t.exit_reason ?? "—").toUpperCase()}</div>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* Signal Timeline */}
        <div className="card p-4">
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2">
              <Activity size={13} className="text-blue-400" />
              <span className="text-[13px] font-semibold text-white">Signal Timeline</span>
            </div>
            <span className="text-[9px] text-neutral-700">{server.trades.length} signals total</span>
          </div>

          <div className="max-h-72 overflow-y-auto space-y-1.5">
            {server.trades.length === 0 ? (
              <div className="text-center py-8 text-[11px] text-neutral-700">No signals yet</div>
            ) : (
              server.trades.slice(0, 25).map((t, i) => {
                const pnl    = t.pnl_usd ?? 0;
                const isWin  = pnl > 0;
                const time   = new Date(t.closed_at ?? t.timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
                const stratMeta = STRATEGIES.find(s =>
                  t.strategy_key === s.key || t.strategy_name?.toLowerCase().includes(s.key)
                );
                const dotColor = stratMeta?.color ?? "#6b7280";
                return (
                  <div key={i} className="flex items-center gap-2.5 py-1.5 border-b border-neutral-800/30 last:border-0">
                    <div className="w-1.5 h-1.5 rounded-full flex-shrink-0" style={{ background: dotColor }} />
                    <div className={`w-5 h-5 rounded flex items-center justify-center flex-shrink-0 ${t.direction === "long" ? "bg-green-500/15" : "bg-red-500/15"}`}>
                      {t.direction === "long"
                        ? <ArrowUpRight size={10} className="text-green-400" />
                        : <ArrowDownRight size={10} className="text-red-400" />}
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-1">
                        <span className={`text-[9px] font-bold ${t.direction === "long" ? "text-green-400" : "text-red-400"}`}>
                          {t.direction.toUpperCase()}
                        </span>
                        <span className="text-[8px] text-neutral-600 truncate">{t.strategy_name}</span>
                      </div>
                      <div className="text-[7px] text-neutral-700 font-mono">{time} · ${t.entry.toFixed(0)}</div>
                    </div>
                    <div className="text-right flex-shrink-0">
                      <div className={`text-[9px] font-bold font-mono ${pnlCls(pnl)}`}>{fmtPnl(pnl)}</div>
                      <div className={`text-[7px] font-bold ${isWin ? "text-green-400/70" : "text-red-400/70"}`}>
                        {(t.exit_reason ?? "—").toUpperCase()}
                      </div>
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

export default function AgentPageInner() {
  return <AgentContent />;
}
