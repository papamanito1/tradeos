"use client";

import { useState, useEffect } from "react";
import { settingsApi } from "@/lib/api";
import {
  Brain, Shield, Server, Bot, Activity, Zap, TrendingUp, RefreshCw,
  Save, Loader2, ChevronRight, AlertTriangle, CheckCircle2, XCircle,
  Wifi, WifiOff,
} from "lucide-react";
import { useSharedServerAgent } from "@/context/ServerAgentContext";
import type { ServerAgentConfig, LiveExecutorStatus } from "@/hooks/useServerAgent";

// ─── Strategy metadata (mirrors AgentPageInner) ───────────────────────────────
const STRATEGIES = [
  { key: "momentum", label: "Momentum 15m", color: "#0a84ff", icon: "📈", tf: "15m" },
  { key: "hft",      label: "HFT Scalper",  color: "#a78bfa", icon: "⚡", tf: "1m"  },
  { key: "orb",      label: "ORB-30",       color: "#f59e0b", icon: "🔶", tf: "1m"  },
  { key: "obi",      label: "OBI Scalper",  color: "#10b981", icon: "📊", tf: "1m"  },
  { key: "fusion",   label: "Fusion",       color: "#f472b6", icon: "🧠", tf: "all"  },
] as const;

type StratOverride = { enabled: boolean; size_usdc: number; leverage: number; min_confidence: number; min_conditions: number };
const DEFAULT_STRAT: StratOverride = { enabled: true, size_usdc: 100, leverage: 1, min_confidence: 0.50, min_conditions: 2 };

// ─── Config panel ─────────────────────────────────────────────────────────────
function ConfigPanel({
  serverConfig,
  onSave,
  executor,
}: {
  serverConfig: ServerAgentConfig | null;
  onSave: (patch: Partial<ServerAgentConfig>) => Promise<void>;
  executor: LiveExecutorStatus | null;
}) {
  const [draft, setDraft]   = useState<ServerAgentConfig | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved]   = useState(false);
  const [dirty, setDirty]   = useState(false);
  const [tab, setTab]       = useState<"global" | string>("global");

  useEffect(() => {
    if (serverConfig && !dirty) setDraft(serverConfig);
  }, [serverConfig, dirty]);

  const set = <K extends keyof ServerAgentConfig>(key: K, val: ServerAgentConfig[K]) => {
    setDraft(prev => prev ? { ...prev, [key]: val } : prev);
    setDirty(true); setSaved(false);
  };

  const setStrat = (stratKey: string, field: keyof StratOverride, val: number | boolean) => {
    setDraft(prev => {
      if (!prev) return prev;
      const overrides = { ...(prev.strategy_overrides ?? {}) };
      overrides[stratKey] = { ...DEFAULT_STRAT, ...(overrides[stratKey] ?? {}), [field]: val };
      return { ...prev, strategy_overrides: overrides };
    });
    setDirty(true); setSaved(false);
  };

  const handleSave = async () => {
    if (!draft) return;
    setSaving(true);
    try {
      await onSave(draft);
      setDirty(false); setSaved(true);
      setTimeout(() => setSaved(false), 2500);
    } finally { setSaving(false); }
  };

  const handleReset = () => {
    if (serverConfig) { setDraft(serverConfig); setDirty(false); setSaved(false); }
  };

  const d = draft ?? serverConfig;
  if (!d) {
    return (
      <div className="card p-5 flex items-center gap-3 text-neutral-600">
        <Loader2 size={14} className="animate-spin" />
        <span className="text-[12px]">Loading agent config…</span>
      </div>
    );
  }

  const activeStrat = tab !== "global" ? (d.strategy_overrides?.[tab] ?? DEFAULT_STRAT) : null;

  return (
    <div className="card p-5 space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Shield size={13} className="text-neutral-500" />
          <span className="text-[13px] font-semibold text-white">Agent Configuration</span>
        </div>
        {dirty && (
          <span className="text-[9px] px-2 py-0.5 rounded-full bg-yellow-500/10 border border-yellow-500/20 text-yellow-400">
            Unsaved changes
          </span>
        )}
      </div>

      {/* Tab bar */}
      <div className="flex gap-1 overflow-x-auto pb-1 -mx-1 px-1">
        <button onClick={() => setTab("global")}
          className="px-3 py-1.5 rounded-lg text-[10px] font-bold border transition-all whitespace-nowrap flex-shrink-0"
          style={tab === "global"
            ? { background: "rgba(10,132,255,0.15)", borderColor: "rgba(10,132,255,0.4)", color: "#60aaff" }
            : { background: "rgba(255,255,255,0.02)", borderColor: "rgba(255,255,255,0.06)", color: "#4b5563" }}>
          ⚙ Global
        </button>
        {STRATEGIES.map(s => {
          const isEnabled = d.strategy_overrides?.[s.key]?.enabled ?? true;
          return (
            <button key={s.key} onClick={() => setTab(s.key)}
              className="px-2.5 py-1.5 rounded-lg text-[10px] font-bold border transition-all whitespace-nowrap flex-shrink-0 flex items-center gap-1"
              style={tab === s.key
                ? { background: `${s.color}18`, borderColor: `${s.color}50`, color: s.color }
                : { background: "rgba(255,255,255,0.02)", borderColor: "rgba(255,255,255,0.06)", color: isEnabled ? "#6b7280" : "#2a2a3e" }}>
              <span className="text-[9px]">{s.icon}</span>
              {s.label}
              {!isEnabled && <span className="text-[7px] text-red-500">OFF</span>}
            </button>
          );
        })}
      </div>

      {/* ═══ GLOBAL TAB ═══ */}
      {tab === "global" && (
        <div className="space-y-4">
          {/* Mode */}
          <div>
            <label className="text-[10px] text-neutral-600 block mb-2">Execution Mode</label>
            <div className="grid grid-cols-2 gap-2">
              <button onClick={() => set("mode", "paper")}
                className="py-2.5 rounded-xl text-[10px] font-bold border transition-colors"
                style={d.mode === "paper"
                  ? { background: "rgba(139,92,246,0.15)", borderColor: "rgba(139,92,246,0.35)", color: "#a78bfa" }
                  : { background: "rgba(255,255,255,0.02)", borderColor: "rgba(255,255,255,0.06)", color: "#3d3d58" }}>
                📄 Paper
              </button>
              <button onClick={() => set("mode", "live")}
                className="py-2.5 rounded-xl text-[10px] font-bold border transition-colors"
                style={d.mode === "live"
                  ? { background: "rgba(239,68,68,0.15)", borderColor: "rgba(239,68,68,0.4)", color: "#ef4444" }
                  : { background: "rgba(255,255,255,0.02)", borderColor: "rgba(255,255,255,0.06)", color: "#3d3d58" }}>
                🔴 Live (BingX)
              </button>
            </div>
            <div className="text-[9px] mt-1.5" style={{ color: d.mode === "live" ? (executor?.keys_set ? "#f87171" : "#f59e0b") : "#4b5563" }}>
              {d.mode === "paper"
                ? "Simulated fills · live P&L tracking · no real money"
                : executor?.keys_set
                  ? "⚠ Real money · BingX perpetual futures · keys configured"
                  : "⚠ Real money · add BINGX_API_KEY + BINGX_API_SECRET in Railway → Redeploy"}
            </div>

            {d.mode === "live" && (
              <div className="mt-3 space-y-3 p-3 rounded-xl border border-red-500/20 bg-red-500/5">
                {!executor?.keys_set && (
                  <div className="p-2.5 rounded-lg border border-amber-500/30 bg-amber-500/10 space-y-1">
                    <div className="text-[9px] font-bold text-amber-400 flex items-center gap-1.5">
                      <AlertTriangle size={10} /> API Keys Missing
                    </div>
                    <p className="text-[8px] text-amber-300/80 leading-relaxed">
                      Add <code className="font-mono bg-black/40 px-1 rounded">BINGX_API_KEY</code> and <code className="font-mono bg-black/40 px-1 rounded">BINGX_API_SECRET</code> to your Railway project environment variables, then redeploy.
                    </p>
                    <p className="text-[8px] text-amber-300/60">Railway → Project → Variables → + New Variable</p>
                  </div>
                )}
                <div className="text-[9px] font-bold text-red-400 uppercase tracking-wide flex items-center gap-1.5">
                  <Shield size={10} /> Live Risk Controls
                </div>
                <div>
                  <div className="flex justify-between mb-1">
                    <label className="text-[9px] text-neutral-500">Daily Loss Limit (circuit breaker)</label>
                    <span className="text-[9px] font-mono text-red-400">${d.daily_loss_limit ?? 200}</span>
                  </div>
                  <input type="range" min={50} max={1000} step={50} value={d.daily_loss_limit ?? 200}
                    onChange={e => set("daily_loss_limit", Number(e.target.value))} className="w-full accent-red-500" />
                  <div className="flex justify-between text-[8px] text-neutral-700 mt-0.5"><span>$50</span><span>$1000</span></div>
                </div>
                <div>
                  <div className="flex justify-between mb-1">
                    <label className="text-[9px] text-neutral-500">Max Position Size Cap</label>
                    <span className="text-[9px] font-mono text-orange-400">${d.max_position_usdc ?? 500}</span>
                  </div>
                  <input type="range" min={50} max={2000} step={50} value={d.max_position_usdc ?? 500}
                    onChange={e => set("max_position_usdc", Number(e.target.value))} className="w-full accent-orange-500" />
                  <div className="flex justify-between text-[8px] text-neutral-700 mt-0.5"><span>$50</span><span>$2000</span></div>
                </div>
              </div>
            )}
          </div>

          {/* Default Size */}
          <div>
            <label className="text-[10px] text-neutral-600 block mb-1.5">Default Trade Size (USDC)</label>
            <div className="flex gap-2">
              <input type="number" value={d.size_usdc}
                onChange={e => set("size_usdc", Number(e.target.value))}
                className="flex-1 bg-neutral-800 border border-neutral-700 rounded-lg px-3 py-2 text-[12px] font-mono text-white outline-none focus:border-blue-500" />
              <div className="flex gap-1">
                {[50, 100, 250].map(s => (
                  <button key={s} onClick={() => set("size_usdc", s)}
                    className="px-2 py-2 rounded-lg text-[10px] font-bold border transition-colors"
                    style={d.size_usdc === s
                      ? { background: "rgba(10,132,255,0.12)", borderColor: "rgba(10,132,255,0.3)", color: "#60aaff" }
                      : { background: "rgba(255,255,255,0.02)", borderColor: "rgba(255,255,255,0.06)", color: "#3d3d58" }
                    }>${s}</button>
                ))}
              </div>
            </div>
          </div>

          {/* Min confidence */}
          <div>
            <div className="flex justify-between mb-1.5">
              <label className="text-[10px] text-neutral-600">Default Min Confidence</label>
              <span className="text-[10px] font-mono text-blue-400">{(d.min_confidence * 100).toFixed(0)}%</span>
            </div>
            <input type="range" min={0.4} max={0.95} step={0.05} value={d.min_confidence}
              onChange={e => set("min_confidence", Number(e.target.value))} className="w-full accent-blue-500" />
            <div className="flex justify-between text-[9px] text-neutral-700 mt-1"><span>Aggressive (40%)</span><span>Conservative (95%)</span></div>
          </div>

          {/* Min conditions */}
          <div>
            <div className="flex justify-between mb-1.5">
              <label className="text-[10px] text-neutral-600">Default Min Conditions</label>
              <span className="text-[10px] font-mono text-blue-400">{d.min_conditions}+ met</span>
            </div>
            <input type="range" min={2} max={7} step={1} value={d.min_conditions}
              onChange={e => set("min_conditions", Number(e.target.value))} className="w-full accent-blue-500" />
            <div className="flex justify-between text-[9px] text-neutral-700 mt-1"><span>Loose (2)</span><span>Strict (7)</span></div>
          </div>

          {/* Auto-execute */}
          <div className="flex items-center justify-between py-3 px-4 rounded-xl border border-neutral-800 bg-neutral-900">
            <div>
              <div className="text-[11px] font-semibold text-white">Auto-Execute</div>
              <div className="text-[9px] text-neutral-600 mt-0.5">Execute immediately on signal — no manual confirm</div>
            </div>
            <button onClick={() => set("auto_execute", !d.auto_execute)}
              className="relative w-10 h-5 rounded-full border transition-colors flex-shrink-0"
              style={d.auto_execute
                ? { background: "rgba(239,68,68,0.3)", borderColor: "rgba(239,68,68,0.4)" }
                : { background: "rgba(255,255,255,0.04)", borderColor: "rgba(255,255,255,0.1)" }}>
              <span className="absolute top-0.5 w-4 h-4 rounded-full transition-all duration-200"
                style={{ left: d.auto_execute ? "22px" : "2px", background: d.auto_execute ? "#ef4444" : "#3d3d58" }} />
            </button>
          </div>

          {/* Default Leverage */}
          <div>
            <div className="flex justify-between mb-2">
              <label className="text-[10px] text-neutral-600">Default Leverage</label>
              <span className="text-[10px] font-mono font-bold text-orange-400">{d.leverage ?? 1}×</span>
            </div>
            <div className="grid grid-cols-6 gap-1.5">
              {[1, 2, 3, 5, 10, 20].map(lev => (
                <button key={lev} onClick={() => set("leverage", lev)}
                  className="py-2 rounded-xl text-[11px] font-bold border transition-all"
                  style={(d.leverage ?? 1) === lev
                    ? lev >= 10 ? { background: "rgba(239,68,68,0.15)", borderColor: "rgba(239,68,68,0.4)", color: "#ef4444" }
                    : lev >= 5  ? { background: "rgba(245,158,11,0.15)", borderColor: "rgba(245,158,11,0.4)", color: "#f59e0b" }
                    :             { background: "rgba(34,197,94,0.12)", borderColor: "rgba(34,197,94,0.3)", color: "#22c55e" }
                    : { background: "rgba(255,255,255,0.02)", borderColor: "rgba(255,255,255,0.06)", color: "#3d3d58" }}>
                  {lev}×
                </button>
              ))}
            </div>
          </div>

          {/* Per-strategy overrides summary */}
          <div className="rounded-xl border border-neutral-800 bg-neutral-900 p-3">
            <div className="text-[9px] text-neutral-600 mb-2 font-bold uppercase tracking-wide">Per-Strategy Overrides</div>
            <div className="space-y-1">
              {STRATEGIES.map(s => {
                const so = d.strategy_overrides?.[s.key] ?? DEFAULT_STRAT;
                return (
                  <button key={s.key} onClick={() => setTab(s.key)}
                    className="w-full flex items-center gap-2 py-1.5 px-2 rounded-lg hover:bg-neutral-800 transition-colors text-left">
                    <span className="text-[10px]">{s.icon}</span>
                    <span className="text-[10px] font-semibold text-white flex-1">{s.label}</span>
                    <span className={`text-[9px] font-bold ${so.enabled ? "text-green-400" : "text-red-400"}`}>{so.enabled ? "ON" : "OFF"}</span>
                    <span className="text-[9px] text-neutral-600 font-mono">${so.size_usdc}</span>
                    <span className="text-[9px] text-orange-400 font-mono">{so.leverage}×</span>
                    <ChevronRight size={10} className="text-neutral-700" />
                  </button>
                );
              })}
            </div>
          </div>
        </div>
      )}

      {/* ═══ STRATEGY TAB ═══ */}
      {tab !== "global" && activeStrat && (() => {
        const meta = STRATEGIES.find(s => s.key === tab)!;
        if (!meta) return null;
        const so = activeStrat;
        return (
          <div className="space-y-4">
            <div className="flex items-center gap-2 p-3 rounded-xl border bg-neutral-900" style={{ borderColor: `${meta.color}30` }}>
              <span className="text-lg">{meta.icon}</span>
              <div className="flex-1">
                <div className="text-[12px] font-bold text-white">{meta.label}</div>
                <div className="text-[9px] text-neutral-600">{meta.tf} timeframe</div>
              </div>
              <button onClick={() => setStrat(tab, "enabled", !so.enabled)}
                className="relative w-10 h-5 rounded-full border transition-colors flex-shrink-0"
                style={so.enabled
                  ? { background: `${meta.color}30`, borderColor: `${meta.color}50` }
                  : { background: "rgba(255,255,255,0.04)", borderColor: "rgba(255,255,255,0.1)" }}>
                <span className="absolute top-0.5 w-4 h-4 rounded-full transition-all duration-200"
                  style={{ left: so.enabled ? "22px" : "2px", background: so.enabled ? meta.color : "#3d3d58" }} />
              </button>
            </div>

            {!so.enabled && (
              <div className="flex items-center gap-2 p-3 rounded-lg border border-red-500/20 bg-red-500/5">
                <XCircle size={12} className="text-red-400 flex-shrink-0" />
                <span className="text-[10px] text-red-400">Strategy disabled — no trades will be taken</span>
              </div>
            )}

            <div>
              <label className="text-[10px] text-neutral-600 block mb-1.5">Position Size (USDC)</label>
              <div className="flex gap-2">
                <input type="number" value={so.size_usdc}
                  onChange={e => setStrat(tab, "size_usdc", Number(e.target.value))}
                  className="flex-1 bg-neutral-800 border border-neutral-700 rounded-lg px-3 py-2 text-[12px] font-mono text-white outline-none focus:border-blue-500" />
                <div className="flex gap-1">
                  {[50, 100, 250, 500].map(s => (
                    <button key={s} onClick={() => setStrat(tab, "size_usdc", s)}
                      className="px-2 py-2 rounded-lg text-[10px] font-bold border transition-colors"
                      style={so.size_usdc === s
                        ? { background: `${meta.color}18`, borderColor: `${meta.color}40`, color: meta.color }
                        : { background: "rgba(255,255,255,0.02)", borderColor: "rgba(255,255,255,0.06)", color: "#3d3d58" }
                      }>${s}</button>
                  ))}
                </div>
              </div>
            </div>

            <div>
              <div className="flex justify-between mb-2">
                <label className="text-[10px] text-neutral-600">Leverage</label>
                <span className="text-[10px] font-mono font-bold text-orange-400">{so.leverage}×</span>
              </div>
              <div className="grid grid-cols-7 gap-1.5">
                {[1, 2, 3, 5, 10, 20, 30].map(lev => (
                  <button key={lev} onClick={() => setStrat(tab, "leverage", lev)}
                    className="py-2 rounded-xl text-[10px] font-bold border transition-all"
                    style={so.leverage === lev
                      ? lev >= 20 ? { background: "rgba(239,68,68,0.15)", borderColor: "rgba(239,68,68,0.4)", color: "#ef4444" }
                      : lev >= 5  ? { background: "rgba(245,158,11,0.15)", borderColor: "rgba(245,158,11,0.4)", color: "#f59e0b" }
                      :             { background: "rgba(34,197,94,0.12)", borderColor: "rgba(34,197,94,0.3)", color: "#22c55e" }
                      : { background: "rgba(255,255,255,0.02)", borderColor: "rgba(255,255,255,0.06)", color: "#3d3d58" }}>
                    {lev}×
                  </button>
                ))}
              </div>
            </div>

            <div>
              <div className="flex justify-between mb-1.5">
                <label className="text-[10px] text-neutral-600">Min Confidence</label>
                <span className="text-[10px] font-mono" style={{ color: meta.color }}>{(so.min_confidence * 100).toFixed(0)}%</span>
              </div>
              <input type="range" min={0.4} max={0.95} step={0.05} value={so.min_confidence}
                onChange={e => setStrat(tab, "min_confidence", Number(e.target.value))} className="w-full accent-blue-500" />
            </div>

            <div>
              <div className="flex justify-between mb-1.5">
                <label className="text-[10px] text-neutral-600">Min Conditions Met</label>
                <span className="text-[10px] font-mono" style={{ color: meta.color }}>{so.min_conditions}+</span>
              </div>
              <input type="range" min={2} max={7} step={1} value={so.min_conditions}
                onChange={e => setStrat(tab, "min_conditions", Number(e.target.value))} className="w-full accent-blue-500" />
            </div>

            <div className="rounded-xl border border-neutral-800 bg-neutral-900/50 p-3">
              <div className="text-[9px] text-neutral-600 font-bold uppercase tracking-wide mb-2">Effective Config</div>
              <div className="grid grid-cols-2 gap-2">
                {([
                  ["Size",       `$${so.size_usdc}`,                               meta.color],
                  ["Leverage",   `${so.leverage}×`,                                so.leverage >= 10 ? "#ef4444" : so.leverage >= 5 ? "#f59e0b" : "#22c55e"],
                  ["Confidence", `${(so.min_confidence * 100).toFixed(0)}%`,       meta.color],
                  ["Conditions", `${so.min_conditions}+ req`,                      meta.color],
                ] as const).map(([l, v, c]) => (
                  <div key={l} className="flex justify-between py-1">
                    <span className="text-[9px] text-neutral-600">{l}</span>
                    <span className="text-[10px] font-mono font-bold" style={{ color: c }}>{v}</span>
                  </div>
                ))}
              </div>
            </div>

            <button onClick={() => setTab("global")}
              className="w-full text-[10px] text-neutral-600 hover:text-neutral-400 transition-colors py-1">
              ← Back to Global Settings
            </button>
          </div>
        );
      })()}

      {/* Save / Reset */}
      <div className="flex items-center gap-2 pt-1">
        <button onClick={handleSave} disabled={saving || !dirty}
          className="flex-1 flex items-center justify-center gap-2 py-2.5 rounded-xl font-bold text-[12px] transition-all"
          style={dirty && !saving
            ? { background: "rgba(10,132,255,0.18)", border: "1px solid rgba(10,132,255,0.4)", color: "#60aaff" }
            : saved
            ? { background: "rgba(34,197,94,0.12)", border: "1px solid rgba(34,197,94,0.3)", color: "#22c55e" }
            : { background: "rgba(255,255,255,0.03)", border: "1px solid rgba(255,255,255,0.06)", color: "#3d3d58", cursor: "not-allowed" }}>
          {saving ? <><Loader2 size={12} className="animate-spin" /> Saving…</>
            : saved ? <><CheckCircle2 size={12} /> Applied to server</>
            : <><Save size={12} /> Make Changes</>}
        </button>
        {dirty && (
          <button onClick={handleReset}
            className="px-3 py-2.5 rounded-xl text-[11px] border transition-colors"
            style={{ background: "rgba(255,255,255,0.02)", border: "1px solid rgba(255,255,255,0.06)", color: "#4b5563" }}>
            Reset
          </button>
        )}
      </div>
    </div>
  );
}

// ─── Main settings page ───────────────────────────────────────────────────────
export default function SettingsPage() {
  const [settings, setSettings] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading]   = useState(true);
  const serverAgent = useSharedServerAgent();
  const config      = serverAgent.config;
  const brain       = serverAgent.brain;
  const stats       = serverAgent.stats;
  const executor    = serverAgent.liveExecutor;

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

      {/* ─── Agent Configuration (moved here from Live Agent page) ─── */}
      <ConfigPanel
        serverConfig={config}
        onSave={serverAgent.updateConfig}
        executor={executor}
      />

      {/* ─── System overview ───────────────────────────────────────── */}
      <div className="card p-5">
        <div className="flex items-center gap-2 mb-4">
          <Server size={14} className="text-neutral-500" />
          <h3 className="text-sm font-semibold text-white">System Overview</h3>
          <span className="ml-auto text-[9px] text-neutral-700">TradeOS v1.0</span>
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
          <StatCell label="Frontend"     value="Next.js · Vercel" />
          <StatCell label="Backend"      value="FastAPI · Railway" />
          <StatCell label="Market Data"  value="BingX WebSocket" />
          <StatCell label="Exchange"     value="BingX Perpetuals" />
          <StatCell label="Agent Mode"   value={config?.mode === "live" ? "Live BingX" : "Paper Training"} color={config?.mode === "live" ? "text-amber-400" : "text-violet-400"} />
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

      {/* ─── Performance ───────────────────────────────────────────── */}
      <div className="card p-5">
        <div className="flex items-center gap-2 mb-4">
          <TrendingUp size={14} className="text-blue-400" />
          <h3 className="text-sm font-semibold text-white">Performance Summary</h3>
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

      {/* ─── Master Brain ───────────────────────────────────────────── */}
      <div className="card p-5">
        <div className="flex items-center gap-2 mb-4">
          <Brain size={14} className="text-violet-400" />
          <h3 className="text-sm font-semibold text-white">Master Brain</h3>
          <span className="text-[9px] text-violet-400/60 ml-1">always learning</span>
        </div>
        {brain ? (
          <div className="space-y-3">
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
              <StatCell label="Market Regime"     value={brain.regime ?? "—"} />
              <StatCell label="Regime Confidence" value={`${((brain.regime_confidence ?? 0) * 100).toFixed(0)}%`} />
              <StatCell label="Direction Bias"    value={brain.portfolio?.direction_bias ?? "—"} />
              <StatCell label="Daily Trades"      value={`${brain.portfolio?.daily_trades ?? 0}`} />
              <StatCell label="Daily P&L"         value={brain.portfolio ? `${brain.portfolio.daily_pnl >= 0 ? "+" : ""}$${brain.portfolio.daily_pnl.toFixed(2)}` : "—"} color={brain.portfolio && brain.portfolio.daily_pnl >= 0 ? "text-green-400" : "text-red-400"} />
              <StatCell label="Consec. Losses"    value={`${brain.portfolio?.consec_losses ?? 0}`} color={(brain.portfolio?.consec_losses ?? 0) >= 3 ? "text-red-400" : "text-white"} />
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

      {/* ─── Active Strategies ─────────────────────────────────────── */}
      <div className="card p-5">
        <div className="flex items-center gap-2 mb-4">
          <Zap size={14} className="text-amber-400" />
          <h3 className="text-sm font-semibold text-white">Active Strategies</h3>
        </div>
        <div className="space-y-2">
          {[
            { name: "Momentum 15m", desc: "EMA50 slope · RSI · VWAP · volume filter",       tf: "15m", color: "#0a84ff" },
            { name: "HFT Scalper",  desc: "EMA9/21 · OBI · TFI · microprice · 100ms depth", tf: "1m",  color: "#a78bfa" },
            { name: "ORB-30",       desc: "Opening Range Breakout · first 30 bars · EMA20",  tf: "1m",  color: "#f59e0b" },
            { name: "OBI Scalper",  desc: "Order Book Imbalance · EMA9/21 · RSI momentum",  tf: "1m",  color: "#10b981" },
            { name: "Fusion",       desc: "Master Brain consensus — weighted vote across all 4 strategies", tf: "all", color: "#f472b6" },
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

      {/* ─── Risk overview ─────────────────────────────────────────── */}
      <div className="card p-5">
        <div className="flex items-center gap-2 mb-4">
          <Shield size={14} className="text-blue-400" />
          <h3 className="text-sm font-semibold text-white">Risk Overview</h3>
        </div>
        <div className="grid grid-cols-2 gap-3">
          {[
            ["Agent-level SL",        "ATR-adaptive per strategy"],
            ["Brain circuit breaker", "Max consecutive losses cap"],
            ["Daily loss limit",      config?.daily_loss_limit ? `$${config.daily_loss_limit}` : "Configurable above"],
            ["Max position cap",      config?.max_position_usdc ? `$${config.max_position_usdc}` : "Configurable above"],
            ["Shadow training",       "Always-on paper positions"],
            ["Trust gates",           "Live trades require 45%+ WR on 10+ trades"],
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
            Config changes take effect within 20 seconds.
          </p>
        </div>
      </div>

    </div>
  );
}
