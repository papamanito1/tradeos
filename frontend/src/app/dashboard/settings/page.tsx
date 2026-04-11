"use client";

import { useEffect, useState } from "react";
import { settingsApi } from "@/lib/api";
import { Brain, Shield, Server, Bot, Activity, Zap, TrendingUp, RefreshCw, ExternalLink } from "lucide-react";
import { useSharedServerAgent } from "@/context/ServerAgentContext";
import Link from "next/link";

export default function SettingsPage() {
  const [settings, setSettings] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading]   = useState(true);
  const serverAgent = useSharedServerAgent();
  const config      = serverAgent.config;
  const brain       = serverAgent.brain;
  const stats       = serverAgent.stats;

  useEffect(() => {
    settingsApi.get().then(setSettings).catch(() => {}).finally(() => setLoading(false));
  }, []);

  const StatCell = ({ label, value, color = "text-white" }: { label: string; value: string; color?: string }) => (
    <div className="bg-neutral-900 border border-neutral-800 rounded-xl p-3">
      <div className="text-[9px] text-neutral-600 mb-1 uppercase tracking-wide">{label}</div>
      <div className={`text-[13px] font-bold font-mono ${color}`}>{value}</div>
    </div>
  );

  return (
    <div className="space-y-4 max-w-3xl">

      {/* System overview */}
      <div className="card p-5">
        <div className="flex items-center gap-2 mb-4">
          <Server size={14} className="text-neutral-500" />
          <h3 className="text-sm font-semibold text-white">System Overview</h3>
          <span className="ml-auto text-[9px] text-neutral-700">TradeOS v1.0</span>
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
          <StatCell label="Frontend" value="Next.js · Vercel" />
          <StatCell label="Backend" value="FastAPI · Railway" />
          <StatCell label="Market Data" value="BingX WebSocket" />
          <StatCell label="Exchange" value="BingX Perpetuals" />
          <StatCell label="Agent Mode" value={config?.mode === "live" ? "Live BingX" : "Paper Training"} color={config?.mode === "live" ? "text-amber-400" : "text-violet-400"} />
          <StatCell label="Agent Status" value={serverAgent.running ? "Running 24/7" : "Stopped"} color={serverAgent.running ? "text-green-400" : "text-red-400"} />
        </div>
        {settings && (
          <div className="mt-3 p-2.5 bg-neutral-900 rounded-lg text-[10px] text-neutral-600 flex items-center gap-2">
            <span>Backend exchange: <span className="text-neutral-400">{String(settings.exchange_id ?? "bingx")}</span></span>
            <span>·</span>
            <span>{settings.exchange_testnet ? "⚠ Testnet" : "✓ Mainnet"}</span>
          </div>
        )}
      </div>

      {/* Live Agent status */}
      <div className="card p-5">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <Bot size={14} className="text-emerald-400" />
            <h3 className="text-sm font-semibold text-white">Live Agent</h3>
            {serverAgent.running && (
              <span className="flex items-center gap-1 text-[9px] text-emerald-400 font-bold px-2 py-0.5 rounded-full bg-emerald-500/10 border border-emerald-500/20">
                <span className="w-1 h-1 rounded-full bg-emerald-400 animate-pulse" />LIVE
              </span>
            )}
          </div>
          <Link href="/dashboard/agent" className="text-[9px] text-blue-400 hover:text-blue-300 transition-colors flex items-center gap-1">
            Configure <ExternalLink size={9} />
          </Link>
        </div>

        {serverAgent.error ? (
          <div className="py-4 text-center text-[11px] text-red-400">
            Cannot reach server: {serverAgent.error}
          </div>
        ) : config ? (
          <>
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-3 mb-3">
              <StatCell label="Enabled"        value={config.enabled ? "Yes" : "No"}            color={config.enabled ? "text-green-400" : "text-red-400"} />
              <StatCell label="Auto Execute"   value={config.auto_execute ? "Yes" : "No"}       color={config.auto_execute ? "text-green-400" : "text-neutral-400"} />
              <StatCell label="Min Confidence" value={`${(config.min_confidence * 100).toFixed(0)}%`} />
              <StatCell label="Min Conditions" value={`${config.min_conditions} met`} />
              <StatCell label="Position Size"  value={`$${config.size_usdc} USDC`} />
              <StatCell label="Leverage"       value={`${config.leverage}×`} color={config.leverage >= 10 ? "text-orange-400" : "text-white"} />
            </div>
            {config.mode === "live" && (
              <div className="grid grid-cols-2 gap-3">
                <StatCell label="Daily Loss Limit" value={`$${config.daily_loss_limit ?? 200}`} color="text-red-400" />
                <StatCell label="Max Position Cap"  value={`$${config.max_position_usdc ?? 500}`} color="text-orange-400" />
              </div>
            )}
          </>
        ) : (
          <div className="flex items-center justify-center gap-2 py-6 text-neutral-700 text-[12px]">
            <RefreshCw size={12} className="animate-spin" /> Loading…
          </div>
        )}
      </div>

      {/* Performance */}
      <div className="card p-5">
        <div className="flex items-center gap-2 mb-4">
          <TrendingUp size={14} className="text-blue-400" />
          <h3 className="text-sm font-semibold text-white">Performance Summary</h3>
          <Link href="/dashboard/agent" className="ml-auto text-[9px] text-neutral-600 hover:text-blue-400 transition-colors">Full history →</Link>
        </div>
        {stats && stats.total_trades > 0 ? (
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
            <StatCell label="Total Trades"  value={`${stats.total_trades}`} />
            <StatCell label="Wins / Losses" value={`${stats.wins}W / ${stats.losses}L`} />
            <StatCell label="Win Rate"      value={`${stats.win_rate.toFixed(1)}%`} color={stats.win_rate >= 50 ? "text-green-400" : "text-red-400"} />
            <StatCell label="All-Time P&L"  value={`${stats.total_pnl >= 0 ? "+" : ""}$${stats.total_pnl.toFixed(2)}`} color={stats.total_pnl >= 0 ? "text-green-400" : "text-red-400"} />
            <StatCell label="Best Trade"    value={`+$${stats.best_trade?.toFixed(2) ?? "—"}`} color="text-green-400" />
            <StatCell label="Worst Trade"   value={`$${stats.worst_trade?.toFixed(2) ?? "—"}`} color="text-red-400" />
          </div>
        ) : (
          <div className="text-center py-6 text-[11px] text-neutral-700">No closed trades yet — agent is scanning</div>
        )}
      </div>

      {/* Master Brain */}
      <div className="card p-5">
        <div className="flex items-center gap-2 mb-4">
          <Brain size={14} className="text-violet-400" />
          <h3 className="text-sm font-semibold text-white">Master Brain</h3>
          <span className="text-[9px] text-violet-400/60 ml-1">always learning</span>
        </div>
        {brain ? (
          <div className="space-y-3">
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
              <StatCell label="Market Regime"      value={brain.regime ?? "—"} />
              <StatCell label="Regime Confidence"  value={`${((brain.regime_confidence ?? 0) * 100).toFixed(0)}%`} />
              <StatCell label="Direction Bias"     value={brain.portfolio?.direction_bias ?? "—"} />
              <StatCell label="Daily Trades"       value={`${brain.portfolio?.daily_trades ?? 0}`} />
              <StatCell label="Daily P&L"          value={brain.portfolio ? `${brain.portfolio.daily_pnl >= 0 ? "+" : ""}$${brain.portfolio.daily_pnl.toFixed(2)}` : "—"} color={brain.portfolio && brain.portfolio.daily_pnl >= 0 ? "text-green-400" : "text-red-400"} />
              <StatCell label="Consec. Losses"     value={`${brain.portfolio?.consec_losses ?? 0}`} color={(brain.portfolio?.consec_losses ?? 0) >= 3 ? "text-red-400" : "text-white"} />
            </div>

            {brain.strategy_trust && Object.keys(brain.strategy_trust).length > 0 && (
              <div>
                <div className="text-[9px] text-neutral-600 font-bold uppercase tracking-wide mb-2">Strategy Trust Scores</div>
                <div className="space-y-1.5">
                  {Object.entries(brain.strategy_trust).map(([key, trust]) => {
                    const t = trust as number;
                    const pct = Math.min(Math.max((t / 2) * 100, 0), 100);
                    const col = t >= 1.3 ? "#22c55e" : t >= 0.9 ? "#60aaff" : t >= 0.6 ? "#f59e0b" : "#ef4444";
                    return (
                      <div key={key} className="flex items-center gap-3">
                        <span className="text-[9px] text-neutral-500 w-24 flex-shrink-0 capitalize">{key}</span>
                        <div className="flex-1 h-1.5 bg-neutral-800 rounded-full overflow-hidden">
                          <div className="h-full rounded-full transition-all" style={{ width: `${pct}%`, background: col }} />
                        </div>
                        <span className="text-[9px] font-mono w-8 text-right" style={{ color: col }}>{t.toFixed(2)}</span>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
          </div>
        ) : (
          <div className="text-center py-6 text-[11px] text-neutral-700">Master Brain not yet active</div>
        )}
      </div>

      {/* Strategies */}
      <div className="card p-5">
        <div className="flex items-center gap-2 mb-4">
          <Zap size={14} className="text-amber-400" />
          <h3 className="text-sm font-semibold text-white">Active Strategies</h3>
        </div>
        <div className="space-y-2">
          {[
            { name: "Momentum 15m",  desc: "EMA50 slope · RSI · VWAP · volume filter",      tf: "15m",   color: "#0a84ff" },
            { name: "HFT Scalper",   desc: "EMA9/21 · OBI · TFI · microprice · 100ms depth", tf: "1m",    color: "#a78bfa" },
            { name: "ORB-30",        desc: "Opening Range Breakout · first 30 bars · EMA20",  tf: "1m",    color: "#f59e0b" },
            { name: "OBI Scalper",   desc: "Order Book Imbalance · EMA9/21 · RSI momentum",  tf: "1m",    color: "#10b981" },
            { name: "Fusion",        desc: "Master Brain consensus — weighted vote across all 4 strategies", tf: "all", color: "#f472b6" },
          ].map(s => (
            <div key={s.name} className="flex items-center gap-3 py-2.5 px-3 rounded-xl border border-neutral-800 bg-neutral-900/50">
              <div className="w-2 h-2 rounded-full flex-shrink-0" style={{ background: s.color }} />
              <div className="flex-1 min-w-0">
                <div className="text-[11px] font-semibold text-white">{s.name}</div>
                <div className="text-[9px] text-neutral-600 truncate">{s.desc}</div>
              </div>
              <div className="text-[9px] font-mono px-2 py-0.5 rounded-lg border border-neutral-800 text-neutral-500">{s.tf}</div>
            </div>
          ))}
        </div>
      </div>

      {/* Risk */}
      <div className="card p-5">
        <div className="flex items-center gap-2 mb-4">
          <Shield size={14} className="text-blue-400" />
          <h3 className="text-sm font-semibold text-white">Risk Configuration</h3>
          <Link href="/dashboard/agent" className="ml-auto text-[9px] text-neutral-600 hover:text-blue-400 transition-colors">Configure →</Link>
        </div>
        <div className="grid grid-cols-2 gap-3">
          {[
            ["Agent-level SL",     "ATR-adaptive per strategy"],
            ["Brain circuit breaker", "Max consecutive losses cap"],
            ["Daily loss limit",   config?.daily_loss_limit ? `$${config.daily_loss_limit}` : "Configurable"],
            ["Max position cap",   config?.max_position_usdc ? `$${config.max_position_usdc}` : "Configurable"],
            ["Shadow training",    "Always-on paper positions"],
            ["Trust gates",        "Live trades require 45%+ WR on 10+ trades"],
          ].map(([label, val]) => (
            <div key={label} className="bg-neutral-900 rounded-lg p-3">
              <div className="text-[9px] text-neutral-600 mb-1">{label}</div>
              <div className="text-[11px] text-neutral-300">{val}</div>
            </div>
          ))}
        </div>
        <div className="mt-3 flex items-start gap-2 p-3 rounded-xl bg-emerald-500/5 border border-emerald-500/10">
          <Activity size={11} className="text-emerald-400 mt-0.5 flex-shrink-0" />
          <p className="text-[10px] text-neutral-600 leading-relaxed">
            The server agent runs 24/7 on Railway independent of this browser. All 4 strategies + Fusion run in parallel every 20s.
            Changes on the Live Agent page take effect within 20 seconds.
          </p>
        </div>
      </div>
    </div>
  );
}
