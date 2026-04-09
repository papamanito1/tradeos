"use client";

import { useEffect, useState } from "react";
import { Plus, Play, Pause, Trash2, ChevronDown, ChevronUp, Zap } from "lucide-react";
import { strategiesApi } from "@/lib/api";
import { Strategy, StrategyType } from "@/types";
import { Badge } from "@/components/ui/Badge";
import { cn, formatUSD } from "@/lib/utils";

const MODE_BADGE: Record<string, "profit" | "accent" | "neutral"> = {
  live: "profit", paper: "profit", off: "neutral",
};
const STATUS_BADGE: Record<string, "profit" | "warning" | "loss" | "neutral"> = {
  running: "profit", idle: "neutral", error: "loss", stopped: "neutral",
};

const TIMEFRAMES = ["1m", "5m", "15m", "30m", "1h", "4h", "1d"];

/* ── Inline modal shell (avoids the shared Modal component entirely) ── */
function Sheet({ open, onClose, children }: { open: boolean; onClose: () => void; children: React.ReactNode }) {
  useEffect(() => {
    const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", h);
    return () => document.removeEventListener("keydown", h);
  }, [onClose]);

  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      {/* Backdrop */}
      <div
        className="absolute inset-0"
        style={{ background: "rgba(0,0,0,0.65)", backdropFilter: "blur(6px)" }}
        onClick={onClose}
      />
      {children}
    </div>
  );
}

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="flex items-baseline justify-between mb-1.5">
        <label className="text-[12px] font-semibold" style={{ color: "#8a8aaa" }}>{label}</label>
        {hint && <span className="text-[11px]" style={{ color: "#3a3a52" }}>{hint}</span>}
      </div>
      {children}
    </div>
  );
}

export default function StrategiesPage() {
  const [strategies, setStrategies] = useState<Strategy[]>([]);
  const [types, setTypes] = useState<StrategyType[]>([]);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [creating, setCreating] = useState(false);
  const [expanded, setExpanded] = useState<number | null>(null);

  const [form, setForm] = useState({
    name: "",
    strategy_type: "",
    symbols: "BTC/USDT",
    timeframe: "1h",
    capital_allocation: 1000,
  });

  async function load() {
    const [s, t] = await Promise.all([strategiesApi.list(), strategiesApi.types()]);
    setStrategies(s);
    setTypes(t);
    setLoading(false);
  }

  useEffect(() => { load(); }, []);

  function resetForm() {
    setForm({ name: "", strategy_type: "", symbols: "BTC/USDT", timeframe: "1h", capital_allocation: 1000 });
  }

  async function toggleMode(s: Strategy) {
    const next = s.mode === "off" ? "live" : "off";
    await strategiesApi.update(s.id, { mode: next });
    setStrategies((prev) => prev.map((x) => x.id === s.id ? { ...x, mode: next, is_enabled: next !== "off" } : x));
  }

  async function deleteStrategy(id: number) {
    if (!confirm("Delete this strategy?")) return;
    await strategiesApi.delete(id);
    setStrategies((prev) => prev.filter((s) => s.id !== id));
  }

  async function createStrategy() {
    if (!form.name.trim() || !form.strategy_type) return;
    setCreating(true);
    try {
      const s = await strategiesApi.create({
        name: form.name.trim(),
        strategy_type: form.strategy_type,
        symbols: form.symbols.split(",").map((s) => s.trim()).filter(Boolean),
        timeframe: form.timeframe,
        capital_allocation: form.capital_allocation,
      });
      setStrategies((prev) => [...prev, s]);
      setShowCreate(false);
      resetForm();
    } finally {
      setCreating(false);
    }
  }

  const selectedType = types.find((t) => t.type === form.strategy_type);
  const canCreate = form.name.trim().length > 0 && form.strategy_type !== "";

  if (loading) return (
    <div className="flex items-center justify-center h-64">
      <div
        className="w-5 h-5 border-2 border-t-transparent rounded-full animate-spin"
        style={{ borderColor: "rgba(10,132,255,0.2) rgba(10,132,255,0.2) rgba(10,132,255,0.2) #0a84ff" }}
      />
    </div>
  );

  return (
    <div className="space-y-4">

      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-[15px] font-semibold text-white" style={{ letterSpacing: "-0.01em" }}>
            {strategies.length} {strategies.length === 1 ? "Strategy" : "Strategies"}
          </h2>
          <p className="text-[12px] mt-0.5" style={{ color: "#3a3a52" }}>
            {strategies.filter((s) => s.is_enabled).length} active
          </p>
        </div>
        <button
          onClick={() => setShowCreate(true)}
          className="flex items-center gap-2 px-4 py-2 rounded-[10px] text-[13px] font-semibold text-white transition-all duration-150"
          style={{
            background: "linear-gradient(135deg, #1a8fff 0%, #0055e0 100%)",
            boxShadow: "0 2px 12px rgba(10,132,255,0.4), 0 1px 0 rgba(255,255,255,0.15) inset",
          }}
          onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.boxShadow = "0 4px 18px rgba(10,132,255,0.55), 0 1px 0 rgba(255,255,255,0.15) inset"; }}
          onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.boxShadow = "0 2px 12px rgba(10,132,255,0.4), 0 1px 0 rgba(255,255,255,0.15) inset"; }}
        >
          <Plus className="w-3.5 h-3.5" />
          New Strategy
        </button>
      </div>

      {/* Strategy list */}
      <div className="space-y-2">
        {strategies.length === 0 && (
          <div className="card p-12 text-center">
            <Zap className="w-8 h-8 mx-auto mb-3 opacity-20 text-white" />
            <p className="text-[14px] font-medium text-white opacity-40">No strategies yet</p>
            <p className="text-[12px] mt-1" style={{ color: "#3a3a52" }}>
              Create one to start trading
            </p>
          </div>
        )}
        {strategies.map((s) => (
          <div key={s.id} className="card overflow-hidden">
            <div className="flex items-center gap-4 px-4 py-3.5">
              {/* Toggle button */}
              <button
                onClick={() => toggleMode(s)}
                title={s.is_enabled ? "Disable strategy" : "Enable strategy"}
                className={cn(
                  "w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0 transition-all duration-150",
                  s.is_enabled
                    ? "text-profit hover:bg-profit/20"
                    : "text-neutral hover:bg-surface-700"
                )}
                style={s.is_enabled ? { background: "rgba(48,209,88,0.12)" } : { background: "rgba(255,255,255,0.05)" }}
              >
                {s.is_enabled ? <Play className="w-3.5 h-3.5 fill-current" /> : <Pause className="w-3.5 h-3.5" />}
              </button>

              {/* Info */}
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="text-[13px] font-semibold text-white">{s.name}</span>
                  <Badge variant={MODE_BADGE[s.mode] || "neutral"}>{s.mode}</Badge>
                  <Badge variant={STATUS_BADGE[s.status] || "neutral"}>{s.status}</Badge>
                  {s.last_signal && (
                    <Badge variant={s.last_signal === "long" ? "profit" : s.last_signal === "short" ? "loss" : "neutral"}>
                      {s.last_signal}
                    </Badge>
                  )}
                </div>
                <div className="flex items-center gap-2 mt-1 text-[12px]" style={{ color: "#3a3a52" }}>
                  <span>{s.strategy_type.replace(/_/g, " ")}</span>
                  <span>·</span>
                  <span>{s.symbols.join(", ")}</span>
                  <span>·</span>
                  <span>{s.timeframe}</span>
                  <span>·</span>
                  <span>{formatUSD(s.capital_allocation)}</span>
                </div>
                {s.last_action && (
                  <p className="text-[11px] mt-0.5 truncate" style={{ color: "#2a2a40" }}>{s.last_action}</p>
                )}
              </div>

              {/* Actions */}
              <div className="flex items-center gap-1 flex-shrink-0">
                <button
                  onClick={() => setExpanded(expanded === s.id ? null : s.id)}
                  className="p-1.5 rounded-lg text-neutral hover:text-white transition-colors"
                  style={{ background: "transparent" }}
                  onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.background = "rgba(255,255,255,0.06)"; }}
                  onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.background = "transparent"; }}
                >
                  {expanded === s.id ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
                </button>
                <button
                  onClick={() => deleteStrategy(s.id)}
                  className="p-1.5 rounded-lg text-neutral hover:text-loss transition-colors"
                  style={{ background: "transparent" }}
                  onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.background = "rgba(255,69,58,0.08)"; }}
                  onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.background = "transparent"; }}
                >
                  <Trash2 className="w-3.5 h-3.5" />
                </button>
              </div>
            </div>

            {/* Expanded parameters */}
            {expanded === s.id && (
              <div className="px-4 py-3" style={{ borderTop: "1px solid rgba(255,255,255,0.05)", background: "rgba(0,0,0,0.25)" }}>
                <p className="label mb-2.5">Parameters</p>
                <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
                  {Object.entries(s.parameters).map(([k, v]) => (
                    <div
                      key={k}
                      className="rounded-lg px-3 py-2"
                      style={{ background: "rgba(255,255,255,0.04)", border: "1px solid rgba(255,255,255,0.05)" }}
                    >
                      <div className="text-[10px] font-semibold uppercase tracking-wider mb-0.5" style={{ color: "#3a3a52" }}>
                        {k.replace(/_/g, " ")}
                      </div>
                      <div className="text-[13px] font-mono text-white">{String(v)}</div>
                    </div>
                  ))}
                </div>
                <div className="mt-2.5 text-[11px]" style={{ color: "#2a2a40" }}>
                  Failures: {s.consecutive_failures} · Updated {new Date(s.updated_at).toLocaleString()}
                </div>
              </div>
            )}
          </div>
        ))}
      </div>

      {/* ── Create Strategy Sheet ── */}
      <Sheet open={showCreate} onClose={() => { setShowCreate(false); resetForm(); }}>
        <div
          className="relative w-full max-w-md rounded-[18px] animate-fade-in overflow-hidden"
          style={{
            background: "linear-gradient(145deg, #0f1119 0%, #0c0e16 100%)",
            border: "1px solid rgba(255,255,255,0.08)",
            boxShadow: "0 32px 64px rgba(0,0,0,0.8), 0 1px 0 rgba(255,255,255,0.06) inset",
          }}
        >
          {/* Header */}
          <div
            className="flex items-center justify-between px-6 py-4"
            style={{ borderBottom: "1px solid rgba(255,255,255,0.06)" }}
          >
            <div className="flex items-center gap-2.5">
              <div
                className="w-6 h-6 rounded-md flex items-center justify-center"
                style={{ background: "rgba(10,132,255,0.15)" }}
              >
                <Zap className="w-3.5 h-3.5 text-accent" />
              </div>
              <span className="text-[14px] font-semibold text-white">New Strategy</span>
            </div>
            <button
              onClick={() => { setShowCreate(false); resetForm(); }}
              className="w-7 h-7 rounded-lg flex items-center justify-center text-neutral transition-colors"
              style={{ background: "rgba(255,255,255,0.04)" }}
              onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.background = "rgba(255,255,255,0.08)"; (e.currentTarget as HTMLElement).style.color = "#fff"; }}
              onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.background = "rgba(255,255,255,0.04)"; (e.currentTarget as HTMLElement).style.color = ""; }}
            >
              ✕
            </button>
          </div>

          {/* Body */}
          <div className="px-6 py-5 space-y-4">
            {/* Name */}
            <Field label="Strategy Name">
              <input
                className="input"
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                placeholder="e.g. BTC Scalper"
                autoFocus
              />
            </Field>

            {/* Type picker */}
            <Field label="Strategy Type">
              <div className="space-y-1.5">
                {types.map((t) => (
                  <button
                    key={t.type}
                    onClick={() => setForm({ ...form, strategy_type: t.type })}
                    className="w-full text-left px-3.5 py-3 rounded-xl transition-all duration-150"
                    style={
                      form.strategy_type === t.type
                        ? {
                            background: "rgba(10,132,255,0.12)",
                            border: "1px solid rgba(10,132,255,0.3)",
                          }
                        : {
                            background: "rgba(255,255,255,0.03)",
                            border: "1px solid rgba(255,255,255,0.06)",
                          }
                    }
                    onMouseEnter={(e) => {
                      if (form.strategy_type !== t.type) {
                        (e.currentTarget as HTMLElement).style.background = "rgba(255,255,255,0.06)";
                        (e.currentTarget as HTMLElement).style.borderColor = "rgba(255,255,255,0.1)";
                      }
                    }}
                    onMouseLeave={(e) => {
                      if (form.strategy_type !== t.type) {
                        (e.currentTarget as HTMLElement).style.background = "rgba(255,255,255,0.03)";
                        (e.currentTarget as HTMLElement).style.borderColor = "rgba(255,255,255,0.06)";
                      }
                    }}
                  >
                    <div className="flex items-center justify-between">
                      <span
                        className="text-[13px] font-semibold"
                        style={{ color: form.strategy_type === t.type ? "#60aaff" : "#c0c0d8" }}
                      >
                        {t.name}
                      </span>
                      {form.strategy_type === t.type && (
                        <span className="text-[10px] font-bold text-accent uppercase tracking-wide">Selected</span>
                      )}
                    </div>
                    <p className="text-[11px] mt-0.5 leading-snug" style={{ color: "#3a3a52" }}>
                      {t.description}
                    </p>
                  </button>
                ))}
              </div>
            </Field>

            {/* Symbols + Timeframe */}
            <div className="grid grid-cols-2 gap-3">
              <Field label="Symbols" hint="comma-separated">
                <input
                  className="input"
                  value={form.symbols}
                  onChange={(e) => setForm({ ...form, symbols: e.target.value })}
                  placeholder="BTC/USDT"
                />
              </Field>
              <Field label="Timeframe">
                <div className="grid grid-cols-3 gap-1">
                  {TIMEFRAMES.map((tf) => (
                    <button
                      key={tf}
                      onClick={() => setForm({ ...form, timeframe: tf })}
                      className="py-1.5 rounded-lg text-[12px] font-semibold transition-all duration-100"
                      style={
                        form.timeframe === tf
                          ? { background: "rgba(10,132,255,0.18)", color: "#60aaff", border: "1px solid rgba(10,132,255,0.3)" }
                          : { background: "rgba(255,255,255,0.04)", color: "#5a5a7a", border: "1px solid rgba(255,255,255,0.05)" }
                      }
                    >
                      {tf}
                    </button>
                  ))}
                </div>
              </Field>
            </div>

            {/* Capital */}
            <Field label="Capital Allocation" hint="USD">
              <div className="relative">
                <span
                  className="absolute left-3 top-1/2 -translate-y-1/2 text-[13px] font-semibold"
                  style={{ color: "#3a3a52" }}
                >
                  $
                </span>
                <input
                  className="input pl-6"
                  type="number"
                  min={1}
                  value={form.capital_allocation}
                  onChange={(e) => setForm({ ...form, capital_allocation: Number(e.target.value) })}
                />
              </div>
            </Field>
          </div>

          {/* Footer */}
          <div
            className="flex items-center gap-3 px-6 py-4"
            style={{ borderTop: "1px solid rgba(255,255,255,0.06)" }}
          >
            <button
              onClick={createStrategy}
              disabled={!canCreate || creating}
              className="flex-1 flex items-center justify-center gap-2 py-2.5 rounded-xl text-[14px] font-semibold text-white transition-all duration-150"
              style={{
                background: canCreate && !creating
                  ? "linear-gradient(135deg, #1a8fff 0%, #0055e0 100%)"
                  : "rgba(10,132,255,0.25)",
                boxShadow: canCreate && !creating ? "0 2px 12px rgba(10,132,255,0.4)" : "none",
                cursor: !canCreate || creating ? "not-allowed" : "pointer",
              }}
            >
              {creating ? (
                <div className="w-4 h-4 border-2 border-t-transparent border-white/40 rounded-full animate-spin border-r-white" />
              ) : (
                <><Plus className="w-4 h-4" /> Create Strategy</>
              )}
            </button>
            <button
              onClick={() => { setShowCreate(false); resetForm(); }}
              className="px-5 py-2.5 rounded-xl text-[14px] font-medium transition-all duration-150"
              style={{ background: "rgba(255,255,255,0.05)", color: "#5a5a7a", border: "1px solid rgba(255,255,255,0.07)" }}
              onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.background = "rgba(255,255,255,0.09)"; (e.currentTarget as HTMLElement).style.color = "#a0a0c0"; }}
              onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.background = "rgba(255,255,255,0.05)"; (e.currentTarget as HTMLElement).style.color = "#5a5a7a"; }}
            >
              Cancel
            </button>
          </div>
        </div>
      </Sheet>
    </div>
  );
}
