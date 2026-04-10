"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useWallet } from "@solana/wallet-adapter-react";
import { WalletMultiButton } from "@solana/wallet-adapter-react-ui";
import {
  Bot, Power, RefreshCw, ExternalLink, Zap, Shield,
  CheckCircle2, XCircle, TrendingUp, TrendingDown,
  AlertTriangle, Activity, ChevronRight, Trash2, Copy,
  FileText, RotateCcw, X, DollarSign, Save, Loader2,
  Brain, MessageSquare, Send, ArrowUpRight, ArrowDownRight, Square,
} from "lucide-react";
import type { useMasterAgent } from "@/hooks/useMasterAgent";
import { SolanaProvider } from "@/providers/SolanaProvider";
import { usePhantomAgent, AgentState, AgentConfig, PaperPosition, PaperStats } from "@/hooks/usePhantomAgent";
import { useStrategyEngine, StrategyResult } from "@/hooks/useStrategyEngine";
import { useHFTScalper, useAggTradeBuffer, HFTResult } from "@/hooks/useHFTScalper";
import { useORBStrategy } from "@/hooks/useORBStrategy";
import { useOBIScalper, OBIResult } from "@/hooks/useOBIScalper";
import { useGridStrategy, GridResult } from "@/hooks/useGridStrategy";
import { useBinanceStream, BinanceCandle, BinanceOrderBook, BinanceAggTrade } from "@/hooks/useBinanceStream";
import { useServerAgent, type ServerAgentConfig } from "@/hooks/useServerAgent";
import { formatUSD } from "@/lib/utils";


// ─── State colours ────────────────────────────────────────────────────────────
const STATE_META: Record<AgentState, { label: string; color: string; pulse: boolean }> = {
  idle:             { label: "IDLE",             color: "#4b5563", pulse: false },
  scanning:         { label: "SCANNING",         color: "#0a84ff", pulse: true  },
  signal_detected:  { label: "SIGNAL DETECTED",  color: "#f59e0b", pulse: true  },
  executing:        { label: "EXECUTING",         color: "#8b5cf6", pulse: true  },
  position_open:    { label: "IN POSITION",      color: "#22c55e", pulse: true  },
  error:            { label: "ERROR",             color: "#ef4444", pulse: false },
};

// ─── Wallet info ─────────────────────────────────────────────────────────────
function WalletInfo() {
  const { publicKey, connected } = useWallet();
  const [copied, setCopied] = useState(false);
  const addr = publicKey?.toBase58() ?? "";

  const copy = () => {
    navigator.clipboard.writeText(addr);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  if (!connected) return null;
  return (
    <div className="flex items-center gap-2 bg-green-500/10 border border-green-500/20 rounded-lg px-3 py-1.5">
      <div className="w-1.5 h-1.5 rounded-full bg-green-400 animate-pulse" />
      <span className="text-[10px] font-mono text-green-400">{addr.slice(0, 6)}…{addr.slice(-4)}</span>
      <button onClick={copy} className="text-neutral-600 hover:text-white transition-colors">
        {copied ? <CheckCircle2 size={11} className="text-green-400" /> : <Copy size={11} />}
      </button>
    </div>
  );
}

// ─── Pending trade modal ──────────────────────────────────────────────────────
// ─── Agent log feed ───────────────────────────────────────────────────────────
function AgentLog({ logs }: { logs: string[] }) {
  return (
    <div className="h-48 overflow-y-auto font-mono text-[10px] space-y-0.5 bg-black/30 rounded-xl p-3 border border-neutral-800">
      {logs.length === 0 && <div className="text-neutral-700">Agent log will appear here…</div>}
      {logs.map((line, i) => (
        <div key={i} className={`leading-relaxed ${line.includes("★") ? "text-yellow-400" : line.includes("error") || line.includes("failed") ? "text-red-400" : line.includes("Submitted") || line.includes("STARTED") || line.includes("position") ? "text-green-400" : "text-neutral-500"}`}>
          {line}
        </div>
      ))}
    </div>
  );
}

// ─── Trade row ────────────────────────────────────────────────────────────────
function TradeRow({ trade }: { trade: ReturnType<typeof usePhantomAgent>["trades"][0] }) {
  const isLong = trade.direction === "long";
  const statusColor = { pending: "#f59e0b", submitted: "#8b5cf6", confirmed: "#22c55e", failed: "#ef4444" }[trade.status];
  return (
    <div className="flex items-center gap-3 py-2 border-b border-neutral-800/60">
      <div className={`w-6 h-6 rounded-md flex items-center justify-center flex-shrink-0 ${isLong ? "bg-green-500/15" : "bg-red-500/15"}`}>
        {isLong ? <TrendingUp size={11} className="text-green-400" /> : <TrendingDown size={11} className="text-red-400" />}
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className={`text-[11px] font-bold ${isLong ? "text-green-400" : "text-red-400"}`}>{trade.direction.toUpperCase()} BTC</span>
          <span className="text-[9px] text-neutral-700">{new Date(trade.timestamp).toLocaleTimeString()}</span>
        </div>
        <div className="text-[9px] text-neutral-600 truncate">{trade.reasoning.slice(0, 60)}…</div>
      </div>
      <div className="text-right flex-shrink-0">
        <div className="text-[10px] font-mono text-white">{formatUSD(trade.entry)}</div>
        {trade.is_paper && trade.pnl_usd != null ? (
          <div className={`text-[9px] font-bold ${trade.pnl_usd >= 0 ? "text-green-400" : "text-red-400"}`}>
            {trade.pnl_usd >= 0 ? "+" : ""}${trade.pnl_usd.toFixed(2)} {trade.exit_reason?.toUpperCase()}
          </div>
        ) : (
          <div className="text-[9px] font-bold" style={{ color: statusColor }}>
            {trade.is_paper ? "📄 " : ""}{trade.status.toUpperCase()}
          </div>
        )}
      </div>
      {trade.tx_signature && (
        <a href={`https://solscan.io/tx/${trade.tx_signature}`} target="_blank" rel="noopener noreferrer"
          className="text-neutral-700 hover:text-blue-400 transition-colors flex-shrink-0">
          <ExternalLink size={11} />
        </a>
      )}
    </div>
  );
}

// ─── Candle seeder ────────────────────────────────────────────────────────────
async function seedCandles(tf: "1m" | "15m"): Promise<BinanceCandle[]> {
  const interval = tf === "1m" ? "1m" : "15m";
  const bybitInterval = tf === "1m" ? "1" : "15";
  // ORB-30 needs 30 bars for opening range + up to 90 bars after = 120 min minimum.
  // Use 300 bars so we always cover the full 4-hour session regardless of when we load.
  const limit = tf === "1m" ? 300 : 120;
  try {
    const r = await fetch(`https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=${interval}&limit=${limit}`);
    if (r.ok) {
      const raw: unknown[][] = await r.json();
      return raw.map(k => ({
        timestamp: new Date(k[0] as number).toISOString(),
        open:  parseFloat(k[1] as string), high: parseFloat(k[2] as string),
        low:   parseFloat(k[3] as string), close: parseFloat(k[4] as string),
        volume:parseFloat(k[5] as string), is_closed: true,
      }));
    }
  } catch { /* fall through */ }
  try {
    const r = await fetch(`https://api.bybit.com/v5/market/kline?category=linear&symbol=BTCUSDT&interval=${bybitInterval}&limit=${limit}`);
    if (r.ok) {
      const json = await r.json();
      const list: string[][] = json?.result?.list ?? [];
      return [...list].reverse().map(k => ({
        timestamp: new Date(parseInt(k[0])).toISOString(),
        open: parseFloat(k[1]), high: parseFloat(k[2]),
        low:  parseFloat(k[3]), close: parseFloat(k[4]),
        volume: parseFloat(k[5]), is_closed: true,
      }));
    }
  } catch { /* ignore */ }
  return [];
}

// ─── HFT Indicator bar ────────────────────────────────────────────────────────
function HFTBar({ label, value, min, max, goodHigh }: { label: string; value: number | null; min: number; max: number; goodHigh: boolean }) {
  if (value === null) return (
    <div><div className="text-[9px] text-neutral-700 mb-0.5">{label}</div><div className="h-1.5 bg-neutral-800 rounded-full" /></div>
  );
  const pct = Math.min(Math.max((value - min) / (max - min) * 100, 0), 100);
  const isGood = goodHigh ? value > 0 : value < 0;
  return (
    <div>
      <div className="flex justify-between mb-0.5">
        <span className="text-[9px] text-neutral-600">{label}</span>
        <span className={`text-[9px] font-mono ${isGood ? "text-green-400" : value === 0 ? "text-neutral-600" : "text-red-400"}`}>{value.toFixed(3)}</span>
      </div>
      <div className="h-1.5 bg-neutral-800 rounded-full overflow-hidden">
        <div className="h-full rounded-full transition-all duration-200"
          style={{ width: `${pct}%`, background: isGood ? "#22c55e" : "#ef4444" }} />
      </div>
    </div>
  );
}

// ─── Paper trading P&L panel ──────────────────────────────────────────────────
function PaperPanel({
  openPositions, stats, trades, onClose, onReset,
}: {
  openPositions: PaperPosition[];
  stats: PaperStats;
  trades: ReturnType<typeof usePhantomAgent>["trades"];
  onClose: (key: string) => void;
  onReset: () => void;
}) {
  const paperTrades = trades.filter(t => t.is_paper && t.exit_price != null);
  const pnlColor = (v: number) => v > 0 ? "text-green-400" : v < 0 ? "text-red-400" : "text-neutral-400";
  const totalUnrealized = openPositions.reduce((s, p) => s + p.unrealized_pnl, 0);

  return (
    <div className="card p-4 space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <FileText size={13} className="text-violet-400" />
          <span className="text-[13px] font-semibold text-white">Paper Trading</span>
          <span className="text-[9px] bg-violet-500/10 border border-violet-500/20 text-violet-400 px-1.5 py-0.5 rounded font-mono">
            {openPositions.length > 0 ? `${openPositions.length} OPEN` : "PAPER"}
          </span>
        </div>
        <button onClick={onReset} className="flex items-center gap-1.5 text-[10px] text-neutral-600 hover:text-red-400 transition-colors">
          <RotateCcw size={11} /> Reset
        </button>
      </div>

      {/* Stats row */}
      <div className="grid grid-cols-4 gap-2">
        {[
          ["Total P&L",    `${stats.total_pnl >= 0 ? "+" : ""}$${stats.total_pnl.toFixed(2)}`,   pnlColor(stats.total_pnl)],
          ["Unrealized",   openPositions.length > 0 ? `${totalUnrealized >= 0 ? "+" : ""}$${totalUnrealized.toFixed(2)}` : "—", pnlColor(totalUnrealized)],
          ["Win Rate",     stats.total_trades > 0 ? `${stats.win_rate.toFixed(1)}%` : "—",        "text-blue-400"],
          ["Closed",       `${stats.total_trades} (${stats.wins}W / ${stats.losses}L)`,            "text-neutral-400"],
        ].map(([l, v, cls]) => (
          <div key={l} className="rounded-xl p-3 text-center" style={{ background: "rgba(255,255,255,0.025)", border: "1px solid rgba(255,255,255,0.06)" }}>
            <div className="text-[9px] text-neutral-600 mb-0.5">{l}</div>
            <div className={`text-[11px] font-bold font-mono ${cls}`}>{v}</div>
          </div>
        ))}
      </div>

      {/* Open positions — one card per strategy */}
      {openPositions.length > 0 ? (
        <div className="space-y-2">
          {openPositions.map(position => (
            <div key={position.id} className="rounded-xl p-4 space-y-3"
              style={{
                background: position.direction === "long" ? "rgba(34,197,94,.06)" : "rgba(239,68,68,.06)",
                border: `1px solid ${position.direction === "long" ? "rgba(34,197,94,.2)" : "rgba(239,68,68,.2)"}`,
              }}>
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <div className={`w-2 h-2 rounded-full animate-pulse ${position.direction === "long" ? "bg-green-400" : "bg-red-400"}`} />
                  <span className={`text-[11px] font-bold ${position.direction === "long" ? "text-green-400" : "text-red-400"}`}>
                    {position.direction.toUpperCase()} — {position.strategy_name}
                  </span>
                </div>
                <button onClick={() => onClose(position.strategy_key)} className="flex items-center gap-1 text-[9px] text-neutral-600 hover:text-red-400 transition-colors border border-neutral-800 rounded-lg px-2 py-1">
                  <X size={9} /> Close
                </button>
              </div>

              <div className="grid grid-cols-3 gap-2 text-[10px] font-mono">
                <div><span className="text-neutral-600 block">Entry</span><span className="text-white">${position.entry.toFixed(0)}</span></div>
                <div><span className="text-neutral-600 block">Current</span><span className="text-white">${position.current_price.toFixed(0)}</span></div>
                <div>
                  <span className="text-neutral-600 block">Unrealized P&L</span>
                  <span className={pnlColor(position.unrealized_pnl)}>
                    {position.unrealized_pnl >= 0 ? "+" : ""}${position.unrealized_pnl.toFixed(2)}
                    <span className="text-[9px] ml-1">({position.unrealized_pct >= 0 ? "+" : ""}{position.unrealized_pct.toFixed(3)}%)</span>
                  </span>
                </div>
              </div>

              {/* SL/TP bar */}
              <div className="space-y-1">
                <div className="flex justify-between text-[9px]">
                  <span className="text-red-400">SL ${position.sl?.toFixed(0) ?? "—"}</span>
                  <span className="text-neutral-600">${ position.size_usdc} · {position.btc_size.toFixed(5)} BTC</span>
                  <span className="text-green-400">TP ${position.tp?.toFixed(0) ?? "—"}</span>
                </div>
                {position.sl && position.tp && (() => {
                  const range   = position.tp - position.sl;
                  const pct     = ((position.current_price - position.sl) / range * 100);
                  const clamped = Math.min(Math.max(pct, 0), 100);
                  return (
                    <div className="h-1.5 bg-neutral-800 rounded-full overflow-hidden relative">
                      <div className="absolute inset-0 flex">
                        <div className="h-full bg-red-900/50"  style={{ width: "33%" }} />
                        <div className="h-full bg-neutral-900/30" style={{ width: "34%" }} />
                        <div className="h-full bg-green-900/50" style={{ width: "33%" }} />
                      </div>
                      <div className="absolute top-0 h-full w-0.5 bg-white rounded-full transition-all duration-200"
                        style={{ left: `${clamped}%` }} />
                    </div>
                  );
                })()}
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="flex items-center gap-2 px-4 py-3 rounded-xl" style={{ background: "rgba(255,255,255,0.02)", border: "1px solid rgba(255,255,255,0.05)" }}>
          <DollarSign size={13} className="text-neutral-700" />
          <span className="text-[11px] text-neutral-700">No open paper positions · all 5 strategies scanning for signals…</span>
        </div>
      )}

      {/* Closed paper trades */}
      {paperTrades.length > 0 && (
        <div className="space-y-1">
          <div className="text-[10px] text-neutral-600 mb-1.5">Closed Trades</div>
          <div className="max-h-36 overflow-y-auto space-y-1">
            {paperTrades.slice(0, 30).map(t => (
              <div key={t.id} className="flex items-center gap-2 py-1.5 px-2 rounded-lg" style={{ background: "rgba(255,255,255,0.02)" }}>
                <span className={`text-[9px] font-bold w-8 ${t.direction === "long" ? "text-green-400" : "text-red-400"}`}>
                  {t.direction === "long" ? "▲" : "▼"} {t.direction.toUpperCase().slice(0,1)}
                </span>
                <span className="text-[9px] text-neutral-700 font-mono">{t.strategy_name ?? "—"}</span>
                <span className="text-[9px] text-neutral-600 font-mono flex-1">${t.entry.toFixed(0)} → ${t.exit_price?.toFixed(0) ?? "—"}</span>
                <span className={`text-[9px] font-mono font-bold ${pnlColor(t.pnl_usd ?? 0)}`}>
                  {(t.pnl_usd ?? 0) >= 0 ? "+" : ""}${t.pnl_usd?.toFixed(2) ?? "0"}
                </span>
                <span className={`text-[8px] px-1.5 py-0.5 rounded font-bold ${
                  t.exit_reason === "tp" ? "bg-green-500/10 text-green-400"
                  : t.exit_reason === "sl" ? "bg-red-500/10 text-red-400"
                  : "bg-neutral-800 text-neutral-500"
                }`}>{(t.exit_reason ?? "—").toUpperCase()}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// ─── Convert HFT result → StrategyResult ─────────────────────────────────────
function hftToStrategy(hft: HFTResult): StrategyResult {
  const sig = hft.signal;
  const rr = sig
    ? Math.abs(sig.tp2 - sig.entry) / Math.abs(sig.entry - sig.sl)
    : 0;
  return {
    bias:       hft.bias,
    conditions: hft.conditions,
    met_count:  hft.met_count,
    total:      7,
    all_met:    hft.all_met,
    signal:     sig ? {
      direction:  sig.direction,
      entry:      sig.entry,
      sl:         sig.sl,
      tp:         sig.tp2,
      confidence: sig.confidence,
      reasoning:  `[HFT] ${sig.reasoning}`,
      timestamp:  sig.timestamp,
      rr:         `1 : ${rr > 0 ? rr.toFixed(1) : "1.2"}`,
    } : null,
    indicators: { rsi: null, ema50: null, ema21: null, vwap: null, atr: null, atr_pct: null, vol_ratio: null, ema50_slope: null },
  };
}

function orbToStrategy(orb: ReturnType<typeof useORBStrategy>): StrategyResult {
  return {
    bias:       orb.bias,
    conditions: orb.conditions,
    met_count:  orb.met_count,
    total:      6,
    all_met:    orb.all_met,
    signal:     orb.signal ? {
      direction:  orb.signal.direction,
      entry:      orb.signal.entry,
      sl:         orb.signal.sl,
      tp:         orb.signal.tp,
      confidence: orb.signal.confidence,
      reasoning:  `[ORB-30] ${orb.signal.reasoning}`,
      timestamp:  orb.signal.timestamp,
      rr:         orb.signal.rr,
    } : null,
    indicators: { rsi: null, ema50: null, ema21: null, vwap: null, atr: null, atr_pct: null, vol_ratio: null, ema50_slope: null },
  };
}

// ─── Mini Master Agent Chat ───────────────────────────────────────────────────
function MiniMasterAgentChat({ masterAgent }: { masterAgent: ReturnType<typeof useMasterAgent> }) {
  const [input, setInput] = useState("");
  const { chat: messages, sendMessage } = masterAgent;
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const GRADE_COL: Record<string, string> = {
    "S+": "#fde68a", "S": "#fbbf24", "A+": "#34d399", "A": "#22c55e",
    "B+": "#60a5fa", "B": "#3b82f6", "C": "#a78bfa", "X": "#374151",
  };
  const gradCol = GRADE_COL[masterAgent.grade] ?? "#60aaff";

  const submit = () => {
    const q = input.trim();
    if (!q) return;
    sendMessage(q);
    setInput("");
  };

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.length]);

  return (
    <div className="card overflow-hidden flex flex-col" style={{ minHeight: 280 }}>
      <div className="flex items-center gap-2 px-4 py-3 flex-shrink-0" style={{ borderBottom: "1px solid rgba(255,255,255,0.06)" }}>
        <Brain size={12} style={{ color: gradCol }} />
        <span className="text-[12px] font-bold text-white">Master Agent · Chat</span>
        <span className="text-[8px] font-bold px-2 py-0.5 rounded-full" style={{ background: `${gradCol}18`, color: gradCol, border: `1px solid ${gradCol}30` }}>
          {masterAgent.grade} · {masterAgent.conviction}/100
        </span>
        <span className={`ml-auto text-[9px] font-bold ${masterAgent.direction === "LONG" ? "text-green-400" : masterAgent.direction === "SHORT" ? "text-red-400" : "text-neutral-600"}`}>
          {masterAgent.direction === "LONG" ? "▲ LONG" : masterAgent.direction === "SHORT" ? "▼ SHORT" : "FLAT"}
        </span>
      </div>
      <div className="flex-1 overflow-y-auto p-3 space-y-2" style={{ maxHeight: 220 }}>
        {messages.slice(-10).map((m: { role: string; content: string }, i: number) => (
          <div key={i} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
            <div className={`max-w-[85%] rounded-xl px-3 py-2 text-[10px] leading-relaxed ${m.role === "user" ? "bg-blue-500/10 border border-blue-500/20 text-blue-200" : "text-neutral-300"}`}
              style={m.role !== "user" ? { background: "rgba(255,255,255,0.04)", border: "1px solid rgba(255,255,255,0.07)" } : {}}>
              {m.content}
            </div>
          </div>
        ))}
        {messages.length === 0 && (
          <div className="text-center py-6 text-[10px] text-neutral-700">
            Ask the Master Agent anything — signals, regime, strategies…
          </div>
        )}
        <div ref={bottomRef} />
      </div>
      <div className="flex items-center gap-2 px-3 py-2.5 flex-shrink-0" style={{ borderTop: "1px solid rgba(255,255,255,0.06)" }}>
        <input
          className="flex-1 bg-transparent text-[10px] text-white placeholder-neutral-700 outline-none"
          placeholder="Ask signal, regime, strategy performance…"
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={e => e.key === "Enter" && submit()}
        />
        <button
          onClick={submit}
          disabled={!input.trim()}
          className="w-6 h-6 rounded-lg flex items-center justify-center disabled:opacity-30 transition-opacity"
          style={{ background: "rgba(10,132,255,0.2)", border: "1px solid rgba(10,132,255,0.3)" }}
        >
          <Send size={10} style={{ color: "#60aaff" }} />
        </button>
      </div>
    </div>
  );
}

// ─── Main inner component (wrapped in SolanaProvider) ────────────────────────
// ─── Config panel — draft state + Make Changes ────────────────────────────────
function ConfigPanel({
  serverConfig,
  onSave,
}: {
  serverConfig: ServerAgentConfig | null;
  onSave: (patch: Partial<ServerAgentConfig>) => Promise<void>;
}) {
  const [draft, setDraft]     = useState<ServerAgentConfig | null>(null);
  const [saving, setSaving]   = useState(false);
  const [saved, setSaved]     = useState(false);
  const [dirty, setDirty]     = useState(false);

  // Sync draft from server when config first loads (or resets)
  useEffect(() => {
    if (serverConfig && !dirty) {
      setDraft(serverConfig);
    }
  }, [serverConfig, dirty]);

  const set = <K extends keyof ServerAgentConfig>(key: K, val: ServerAgentConfig[K]) => {
    setDraft(prev => prev ? { ...prev, [key]: val } : prev);
    setDirty(true);
    setSaved(false);
  };

  const handleSave = async () => {
    if (!draft) return;
    setSaving(true);
    try {
      await onSave(draft);
      setDirty(false);
      setSaved(true);
      setTimeout(() => setSaved(false), 2500);
    } finally {
      setSaving(false);
    }
  };

  const handleReset = () => {
    if (serverConfig) { setDraft(serverConfig); setDirty(false); setSaved(false); }
  };

  const d = draft ?? serverConfig;
  if (!d) {
    return (
      <div className="col-span-12 lg:col-span-5 card p-5 flex items-center gap-3 text-neutral-600">
        <Loader2 size={14} className="animate-spin" />
        <span className="text-[12px]">Loading agent config…</span>
      </div>
    );
  }

  return (
    <div className="col-span-12 lg:col-span-5 card p-5 space-y-5">
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

      {/* Mode */}
      <div>
        <label className="text-[10px] text-neutral-600 block mb-2">Execution Mode</label>
        <div className="grid grid-cols-3 gap-2">
          {([
            ["paper", "📄 Paper"],
            ["perps", "🚀 Perps"],
            ["spot",  "⚡ Spot"],
          ] as const).map(([m, label]) => (
            <button key={m} onClick={() => set("mode", m)}
              className="py-2.5 rounded-xl text-[10px] font-bold border transition-colors"
              style={d.mode === m
                ? m === "paper"
                  ? { background: "rgba(139,92,246,0.15)", borderColor: "rgba(139,92,246,0.35)", color: "#a78bfa" }
                  : { background: "rgba(10,132,255,0.12)", borderColor: "rgba(10,132,255,0.3)", color: "#60aaff" }
                : { background: "rgba(255,255,255,0.02)", borderColor: "rgba(255,255,255,0.06)", color: "#3d3d58" }
              }>
              {label}
            </button>
          ))}
        </div>
        <div className="text-[9px] text-neutral-700 mt-1.5">
          {d.mode === "paper"
            ? "Simulated fills · live P&L tracking · no wallet needed"
            : d.mode === "perps"
            ? "Opens Phantom Perps — full leverage, confirm in wallet"
            : "Executes USDC↔wBTC swap directly on Solana mainnet"}
        </div>
      </div>

      {/* Size */}
      <div>
        <label className="text-[10px] text-neutral-600 block mb-1.5">Trade Size (USDC)</label>
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
          <label className="text-[10px] text-neutral-600">Min Confidence</label>
          <span className="text-[10px] font-mono text-blue-400">{(d.min_confidence * 100).toFixed(0)}%</span>
        </div>
        <input type="range" min={0.4} max={0.95} step={0.05}
          value={d.min_confidence}
          onChange={e => set("min_confidence", Number(e.target.value))}
          className="w-full accent-blue-500" />
        <div className="flex justify-between text-[9px] text-neutral-700 mt-1">
          <span>Aggressive (40%)</span><span>Conservative (95%)</span>
        </div>
      </div>

      {/* Min conditions */}
      <div>
        <div className="flex justify-between mb-1.5">
          <label className="text-[10px] text-neutral-600">Min Conditions Met</label>
          <span className="text-[10px] font-mono text-blue-400">{d.min_conditions}+ met</span>
        </div>
        <input type="range" min={3} max={7} step={1}
          value={d.min_conditions}
          onChange={e => set("min_conditions", Number(e.target.value))}
          className="w-full accent-blue-500" />
        <div className="flex justify-between text-[9px] text-neutral-700 mt-1">
          <span>Loose (3)</span><span>Strict (7)</span>
        </div>
      </div>

      {/* Auto-execute toggle */}
      <div className="flex items-center justify-between py-3 px-4 rounded-xl border border-neutral-800 bg-neutral-900">
        <div>
          <div className="text-[11px] font-semibold text-white">Auto-Execute</div>
          <div className="text-[9px] text-neutral-600 mt-0.5">Skip confirmation modal — execute immediately</div>
        </div>
        <button onClick={() => set("auto_execute", !d.auto_execute)}
          className="relative w-10 h-5 rounded-full border transition-colors flex-shrink-0"
          style={d.auto_execute
            ? { background: "rgba(239,68,68,0.3)", borderColor: "rgba(239,68,68,0.4)" }
            : { background: "rgba(255,255,255,0.04)", borderColor: "rgba(255,255,255,0.1)" }
          }>
          <span className="absolute top-0.5 w-4 h-4 rounded-full transition-all duration-200"
            style={{ left: d.auto_execute ? "22px" : "2px", background: d.auto_execute ? "#ef4444" : "#3d3d58" }} />
        </button>
      </div>

      {d.auto_execute && (
        <div className="flex items-start gap-2 p-3 rounded-lg border border-red-500/20 bg-red-500/5">
          <AlertTriangle size={12} className="text-red-400 flex-shrink-0 mt-0.5" />
          <div className="text-[10px] text-red-400/80">Auto-execute ON — agent trades automatically when signal conditions are met.</div>
        </div>
      )}

      {/* Leverage */}
      <div>
        <div className="flex justify-between mb-2">
          <label className="text-[10px] text-neutral-600">Leverage</label>
          <span className="text-[10px] font-mono font-bold text-orange-400">{d.leverage ?? 1}×</span>
        </div>
        <div className="grid grid-cols-6 gap-1.5 mb-2">
          {[1, 2, 3, 5, 10, 20].map(lev => (
            <button
              key={lev}
              onClick={() => set("leverage", lev)}
              className="py-2 rounded-xl text-[11px] font-bold border transition-all"
              style={(d.leverage ?? 1) === lev
                ? lev >= 10
                  ? { background: "rgba(239,68,68,0.15)", borderColor: "rgba(239,68,68,0.4)", color: "#ef4444" }
                  : lev >= 5
                  ? { background: "rgba(245,158,11,0.15)", borderColor: "rgba(245,158,11,0.4)", color: "#f59e0b" }
                  : { background: "rgba(34,197,94,0.12)", borderColor: "rgba(34,197,94,0.3)", color: "#22c55e" }
                : { background: "rgba(255,255,255,0.02)", borderColor: "rgba(255,255,255,0.06)", color: "#3d3d58" }
              }
            >
              {lev}×
            </button>
          ))}
        </div>
        <div className="flex items-start gap-2 p-2.5 rounded-lg"
          style={{
            background: (d.leverage ?? 1) >= 10 ? "rgba(239,68,68,0.05)" : (d.leverage ?? 1) >= 5 ? "rgba(245,158,11,0.05)" : "rgba(34,197,94,0.05)",
            border: `1px solid ${(d.leverage ?? 1) >= 10 ? "rgba(239,68,68,0.15)" : (d.leverage ?? 1) >= 5 ? "rgba(245,158,11,0.15)" : "rgba(34,197,94,0.15)"}`,
          }}>
          <span className="text-[9px] leading-relaxed" style={{ color: (d.leverage ?? 1) >= 10 ? "#ef4444" : (d.leverage ?? 1) >= 5 ? "#f59e0b" : "#22c55e" }}>
            {(d.leverage ?? 1) === 1
              ? "No leverage — safest mode, spot-equivalent sizing"
              : (d.leverage ?? 1) <= 3
              ? `${d.leverage}× — low risk · liquidation price far from entry`
              : (d.leverage ?? 1) <= 5
              ? `${d.leverage}× — moderate risk · use strict SL`
              : (d.leverage ?? 1) <= 10
              ? `${d.leverage}× — high risk · tight SL mandatory · small size recommended`
              : `${d.leverage}× — extreme risk · only for scalping with hard stops`}
          </span>
        </div>
        {(d.mode === "paper") && (d.leverage ?? 1) > 1 && (
          <div className="text-[9px] text-neutral-700 mt-1.5 text-center">
            Paper mode — leverage applied to P&L calculation only
          </div>
        )}
      </div>

      {/* ── Make Changes button ─────────────────────────────────────────── */}
      <div className="flex items-center gap-2 pt-1">
        <button
          onClick={handleSave}
          disabled={saving || !dirty}
          className="flex-1 flex items-center justify-center gap-2 py-2.5 rounded-xl font-bold text-[12px] transition-all"
          style={dirty && !saving
            ? { background: "rgba(10,132,255,0.18)", border: "1px solid rgba(10,132,255,0.4)", color: "#60aaff" }
            : saved
            ? { background: "rgba(34,197,94,0.12)", border: "1px solid rgba(34,197,94,0.3)", color: "#22c55e" }
            : { background: "rgba(255,255,255,0.03)", border: "1px solid rgba(255,255,255,0.06)", color: "#3d3d58", cursor: "not-allowed" }
          }>
          {saving
            ? <><Loader2 size={12} className="animate-spin" /> Saving…</>
            : saved
            ? <><CheckCircle2 size={12} /> Applied to server</>
            : <><Save size={12} /> Make Changes</>
          }
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

function AgentContent() {
  const [livePrice, setLivePrice] = useState<number | undefined>(undefined);

  // ── 24/7 backend agent — source of truth for positions/P&L/trades/log ──
  const server = useServerAgent();

  // ── 15m candles (Momentum) ────────────────────────────────────────────
  const [candles15m, setCandles15m] = useState<BinanceCandle[]>([]);
  // ── 1m candles (HFT + ORB — needs 300 bars for ORB session history) ──
  const [candles1m,  setCandles1m]  = useState<BinanceCandle[]>([]);
  const [orderBook,  setOrderBook]  = useState<BinanceOrderBook | null>(null);
  const { push: pushTrade, get: getTrades } = useAggTradeBuffer();
  const [aggSnap, setAggSnap] = useState<BinanceAggTrade[]>([]);
  const aggSnapTimer = useRef<ReturnType<typeof setInterval> | null>(null);

  // Chart candles (1m for display)
  const [candles, setCandles] = useState<BinanceCandle[]>([]);

  // Seed both timeframes on mount
  useEffect(() => {
    seedCandles("15m").then(c => setCandles15m(c));
    seedCandles("1m").then(c  => { setCandles1m(c); setCandles(c); });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Snapshot aggTrades every 2s so HFT hook re-renders
  useEffect(() => {
    aggSnapTimer.current = setInterval(() => setAggSnap([...getTrades()]), 2000);
    return () => { if (aggSnapTimer.current) clearInterval(aggSnapTimer.current); };
  }, [getTrades]);

  // ── 15m stream (Momentum) ─────────────────────────────────────────────
  useBinanceStream({
    symbols: ["BTC/USDT"],
    timeframe: "15m",
    onCandle: useCallback((_sym: string, c: BinanceCandle) => {
      setLivePrice(c.close);
      setCandles15m(prev => {
        if (!prev.length) return [c];
        const lMs = new Date(prev[prev.length-1].timestamp).getTime();
        const cMs = new Date(c.timestamp).getTime();
        if (lMs === cMs) return [...prev.slice(0,-1), c];
        if (cMs > lMs)   return [...prev.slice(-119), c];
        return prev;
      });
    }, []),
  });

  // ── 1m stream (HFT + ORB) — keep 299 bars ────────────────────────────
  useBinanceStream({
    symbols: ["BTC/USDT"],
    timeframe: "1m",
    onCandle: useCallback((_sym: string, c: BinanceCandle) => {
      setLivePrice(c.close);
      setCandles1m(prev => {
        const updated = !prev.length ? [c] : (() => {
          const lMs = new Date(prev[prev.length-1].timestamp).getTime();
          const cMs = new Date(c.timestamp).getTime();
          if (lMs === cMs) return [...prev.slice(0,-1), c];
          if (cMs > lMs)   return [...prev.slice(-299), c];
          return prev;
        })();
        setCandles(updated);
        return updated;
      });
    }, []),
    onOrderBook: useCallback((ob: BinanceOrderBook) => setOrderBook(ob), []),
    onAggTrade:  useCallback((t: BinanceAggTrade) => pushTrade(t), [pushTrade]),
  });

  // ── All 5 strategy engines run in parallel always ─────────────────────
  const momentumResult = useStrategyEngine(candles15m);
  const hftResult      = useHFTScalper(candles1m, orderBook, aggSnap);
  const orbResult      = useORBStrategy(candles1m);
  const obiResult      = useOBIScalper(candles1m, orderBook);
  const gridResult     = useGridStrategy(
    candles1m,
    candles1m.length > 0 ? candles1m[candles1m.length - 1].close : 0,
    server.gridState,
  );

  // ── Strategy slots for the multi-agent hook ──────────────────────────
  const strategies = useMemo(() => [
    { result: momentumResult,           name: "Momentum 15m", key: "momentum" },
    { result: hftToStrategy(hftResult), name: "HFT Scalper",  key: "hft"      },
    { result: orbToStrategy(orbResult), name: "ORB-30",        key: "orb"      },
    { result: obiResult,                name: "OBI Scalper",   key: "obi"      },
  ], [momentumResult, hftResult, orbResult, obiResult]);

  // ── For UI display only: best active result ───────────────────────────
  const { activeResult, firingStrategy } = useMemo(() => {
    const candidates = strategies;
    const withSignal = candidates.filter(c => c.result.signal !== null);
    if (withSignal.length > 0) {
      const best = withSignal.reduce((a, b) =>
        (b.result.signal!.confidence > a.result.signal!.confidence) ? b : a
      );
      return { activeResult: best.result, firingStrategy: best.name };
    }
    const best = candidates.reduce((a, b) => b.result.met_count > a.result.met_count ? b : a);
    return { activeResult: best.result, firingStrategy: best.name };
  }, [strategies]);

  const {
    config, updateConfig,
    agentState, analysis,
    trades, clearTrades,
    scanCount, lastScan,
    agentLog,
    walletConnected, walletAddress,
    forceScan,
    openPositions, anyPositionOpen, paperStats, closePaperPosition, resetPaperAccount,
  } = usePhantomAgent(strategies, livePrice);

  const { connected } = useWallet();
  const meta = STATE_META[agentState];

  const copyAddress = useCallback(() => {
    if (walletAddress) navigator.clipboard.writeText(walletAddress);
  }, [walletAddress]);

  return (
    <div className="p-4 space-y-5">

      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-white flex items-center gap-2.5">
            <Bot size={22} className="text-blue-400" />
            Living Agent
            <span className="text-[10px] font-normal px-2 py-0.5 rounded-full bg-green-500/10 border border-green-500/20 text-green-400 ml-1">5 STRATEGIES LIVE</span>
            {server.running ? (
              <span className="flex items-center gap-1.5 text-[10px] font-normal px-2 py-0.5 rounded-full bg-emerald-500/10 border border-emerald-500/20 text-emerald-400">
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
                RUNS 24/7 · BROWSER INDEPENDENT
              </span>
            ) : server.error ? (
              <span className="flex items-center gap-1.5 text-[10px] font-normal px-2 py-0.5 rounded-full bg-red-500/10 border border-red-500/20 text-red-400">
                Server offline — browser only
              </span>
            ) : (
              <span className="text-[10px] font-normal text-neutral-600">connecting to server…</span>
            )}
          </h1>
          <p className="text-xs text-neutral-600 mt-0.5">
            Momentum 15m · HFT Scalper 1m · ORB-30 1m · OBI Scalper 1m — all parallel · independent positions per strategy
          </p>
        </div>
        <div className="flex items-center gap-3">
          {/* Kill Switch */}
          {server.running && (
            <button
              onClick={() => server.updateConfig({ enabled: false })}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-[10px] font-bold transition-all hover:scale-105 active:scale-95"
              style={{ background: "rgba(239,68,68,0.08)", border: "1px solid rgba(239,68,68,0.25)", color: "#ef4444" }}
            >
              <Square size={10} />STOP ALL
            </button>
          )}
          <WalletInfo />
          <div className="phantom-btn-wrapper">
            <WalletMultiButton />
          </div>
        </div>
      </div>

      {/* Agent state bar */}
      <div className="card px-5 py-4 flex items-center gap-6">
        <div className="flex items-center gap-3">
          <div className="relative">
            <Bot size={32} style={{ color: meta.color }} />
            {meta.pulse && <span className="absolute -top-0.5 -right-0.5 w-2.5 h-2.5 rounded-full animate-ping" style={{ background: meta.color, opacity: 0.6 }} />}
            {meta.pulse && <span className="absolute -top-0.5 -right-0.5 w-2.5 h-2.5 rounded-full" style={{ background: meta.color }} />}
          </div>
          <div>
            <div className="text-[10px] text-neutral-600 mb-0.5">Agent State</div>
            <div className="text-sm font-bold" style={{ color: meta.color }}>{meta.label}</div>
          </div>
        </div>

        <div className="h-10 w-px bg-neutral-800" />

        {[
          ["Scans Run",  (server.scanCount > 0 ? server.scanCount : scanCount).toString()],
          ["Last Scan",  server.lastScan ?? lastScan ?? "—"],
          ["Firing",     firingStrategy],
          ["Open Pos",   server.loading ? "…" : `${server.openPositions.length} active`],
          ["Server P&L", server.stats ? `${server.stats.total_pnl >= 0 ? "+" : ""}$${server.stats.total_pnl.toFixed(2)}` : "—"],
          ["Mode",       "📄 PAPER"],
        ].map(([label, val]) => (
          <div key={label}>
            <div className="text-[9px] text-neutral-600 mb-0.5">{label}</div>
            <div className="text-[12px] font-semibold text-white">{val}</div>
          </div>
        ))}

        <div className="ml-auto flex items-center gap-2">
          <button onClick={() => { server.forceScan(); forceScan(); }}
            className="p-2 rounded-lg hover:bg-neutral-800 text-neutral-600 hover:text-white transition-colors"
            title="Force scan now">
            <RefreshCw size={14} className={agentState === "scanning" ? "animate-spin" : ""} />
          </button>

          {/* Server agent start/stop */}
          <button
            onClick={() => server.running ? server.stopAgent() : server.startAgent()}
            className="flex items-center gap-2 px-5 py-2 rounded-xl font-bold text-[13px] transition-all"
            style={server.running
              ? { background: "rgba(239,68,68,0.15)", border: "1px solid rgba(239,68,68,0.3)", color: "#ef4444" }
              : { background: "rgba(34,197,94,0.15)", border: "1px solid rgba(34,197,94,0.3)", color: "#22c55e" }
            }>
            <Power size={14} />
            {server.running ? "Stop Agent" : "Start Agent"}
          </button>
        </div>
      </div>

      {/* Server data status — shows if agent has live data */}
      {server.status && (
        <div className="flex items-center gap-3 px-4 py-2 rounded-xl border border-neutral-800 bg-neutral-900/50 text-[10px] flex-wrap">
          <div className={`flex items-center gap-1.5 ${server.livePrice > 0 ? "text-green-400" : "text-red-400"}`}>
            <div className={`w-1.5 h-1.5 rounded-full ${server.livePrice > 0 ? "bg-green-400 animate-pulse" : "bg-red-500"}`} />
            {server.livePrice > 0 ? `BTC $${server.livePrice.toLocaleString()}` : "No price feed"}
          </div>
          <div className="text-neutral-600">·</div>
          <div className="text-neutral-400">
            Enabled: <span className={server.config?.enabled ? "text-green-400" : "text-red-400"}>{server.config?.enabled ? "YES" : "NO"}</span>
          </div>
          <div className="text-neutral-600">·</div>
          <div className="text-neutral-400">
            Auto-Execute: <span className={server.config?.auto_execute ? "text-green-400" : "text-red-400"}>{server.config?.auto_execute ? "YES" : "NO"}</span>
          </div>
          <div className="text-neutral-600">·</div>
          <div className="text-neutral-400">Scans: <span className="text-white">{server.scanCount}</span></div>
          {(!server.config?.enabled || !server.config?.auto_execute) && (
            <button onClick={() => server.startAgent()}
              className="ml-auto px-3 py-1 rounded-lg bg-green-500/15 border border-green-500/30 text-green-400 text-[10px] font-bold hover:bg-green-500/25 transition-colors">
              Enable Trading
            </button>
          )}
        </div>
      )}

      {!connected && config.mode !== "paper" && (
        <div className="flex items-center gap-3 p-4 rounded-xl border border-yellow-500/20 bg-yellow-500/5">
          <AlertTriangle size={16} className="text-yellow-400 flex-shrink-0" />
          <div className="text-[12px] text-yellow-300">
            Connect your <strong>Phantom wallet</strong> using the button above to enable live execution.
            Switch to <strong>Paper mode</strong> to trade without a wallet.
          </div>
        </div>
      )}
      {config.mode === "paper" && (
        <div className="flex items-center gap-3 p-3 rounded-xl border border-violet-500/20 bg-violet-500/5">
          <FileText size={14} className="text-violet-400 flex-shrink-0" />
          <div className="text-[12px] text-violet-300">
            <strong>Paper mode active</strong> — no wallet required. Trades are simulated at signal price with live P&L tracking.
          </div>
        </div>
      )}

      {/* ── All-strategy live status strip ───────────────────────────────── */}
      <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
        {[
          {
            name: "Momentum 15m",
            bias: momentumResult.bias,
            met: momentumResult.met_count,
            total: momentumResult.total ?? 7,
            hasSignal: !!momentumResult.signal,
            conf: momentumResult.signal?.confidence,
            color: "#0a84ff",
          },
          {
            name: "HFT Scalper",
            bias: hftResult.bias,
            met: hftResult.met_count,
            total: 7,
            hasSignal: !!hftResult.signal,
            conf: hftResult.signal?.confidence,
            color: "#a78bfa",
          },
          {
            name: orbResult.status === "building_orb" ? `ORB-30 ★ (${orbResult.indicators.bars_in_orb}/30)`
                : orbResult.status === "past_window"  ? `ORB-30 ★ · next ${orbResult.indicators.next_session}`
                : "ORB-30 ★",
            bias: orbResult.bias,
            met: orbResult.met_count,
            total: orbResult.total ?? 6,
            hasSignal: !!orbResult.signal,
            conf: orbResult.signal?.confidence,
            color: "#f59e0b",
          },
          {
            name: "OBI Scalper 1m",
            bias: obiResult.bias,
            met: obiResult.met_count,
            total: 3,
            hasSignal: !!obiResult.signal,
            conf: obiResult.signal?.confidence,
            color: "#10b981",
          },
          {
            name: "Grid $50",
            bias: gridResult.bias === "stopped" || gridResult.bias === "paused" ? "neutral" : gridResult.bias,
            met: gridResult.metCount,
            total: gridResult.total,
            hasSignal: !!gridResult.signal,
            conf: gridResult.signal?.confidence,
            color: "#06b6d4",
          },
        ].map(s => {
          const biasColor = s.bias === "long" ? "#22c55e" : s.bias === "short" ? "#ef4444" : "#4b5563";
          const pct = Math.round(s.met / s.total * 100);
          return (
            <div key={s.name} className="card p-4 relative overflow-hidden"
              style={s.hasSignal ? { border: `1px solid ${s.color}40`, boxShadow: `0 0 16px ${s.color}15` } : {}}>
              <div className="flex items-center justify-between mb-2">
                <span className="text-[11px] font-bold text-white">{s.name}</span>
                {s.hasSignal ? (
                  <span className="text-[8px] font-bold px-2 py-0.5 rounded-full animate-pulse"
                    style={{ background: `${s.color}20`, color: s.color, border: `1px solid ${s.color}40` }}>
                    ⚡ SIGNAL
                  </span>
                ) : (
                  <span className="text-[8px] text-neutral-700 px-2 py-0.5 rounded-full border border-neutral-800">SCANNING</span>
                )}
              </div>
              <div className="flex items-center gap-2 mb-2">
                <span className="text-[10px] font-bold" style={{ color: biasColor }}>{s.bias.toUpperCase()}</span>
                <span className="text-[9px] text-neutral-600">{s.met}/{s.total} conds</span>
                {s.conf != null && <span className="text-[9px] ml-auto" style={{ color: s.color }}>{(s.conf * 100).toFixed(0)}% conf</span>}
              </div>
              <div className="h-1 rounded-full bg-neutral-800 overflow-hidden">
                <div className="h-full rounded-full transition-all duration-500"
                  style={{ width: `${pct}%`, background: s.hasSignal ? s.color : `${s.color}60` }} />
              </div>
            </div>
          );
        })}
      </div>

      {/* Config + log grid */}
      <div className="grid grid-cols-12 gap-4">

        {/* Config panel — wired to server.config with draft + Make Changes */}
        <ConfigPanel serverConfig={server.config} onSave={server.updateConfig} />

        {/* Right: conditions + HFT meters + log */}
        <div className="col-span-12 lg:col-span-7 space-y-4">

          {/* HFT microstructure meters — always visible */}
          {(
            <div className="card p-4">
              <div className="flex items-center gap-2 mb-3">
                <Zap size={13} className="text-yellow-400" />
                <span className="text-[13px] font-semibold text-white">Microstructure Meters</span>
                <span className="text-[9px] text-yellow-400 ml-auto flex items-center gap-1">
                  <span className="w-1 h-1 rounded-full bg-yellow-400 animate-pulse" />100 ms depth
                </span>
              </div>
              <div className="grid grid-cols-2 gap-x-6 gap-y-3">
                <HFTBar label="Order Book Imbalance (OBI)" value={hftResult.indicators.obi} min={-1} max={1} goodHigh />
                <HFTBar label="Trade Flow Imbalance (TFI)" value={hftResult.indicators.tfi} min={-1} max={1} goodHigh />
              </div>
              <div className="grid grid-cols-4 gap-3 mt-4">
                {[
                  ["Bid",         hftResult.indicators.bid?.toFixed(1) ?? "—"],
                  ["Ask",         hftResult.indicators.ask?.toFixed(1) ?? "—"],
                  ["Spread",      hftResult.indicators.spread_ticks != null ? `${hftResult.indicators.spread_ticks.toFixed(1)} tks` : "—"],
                  ["Microprice",  hftResult.indicators.microprice?.toFixed(1) ?? "—"],
                  ["VWAP 1m",     hftResult.indicators.vwap_1m?.toFixed(1) ?? "—"],
                  ["ATR 1m",      hftResult.indicators.atr_1m?.toFixed(1) ?? "—"],
                  ["EMA9 5m",     hftResult.indicators.ema9_5m?.toFixed(1) ?? "—"],
                  ["EMA21 5m",    hftResult.indicators.ema21_5m?.toFixed(1) ?? "—"],
                ].map(([l, v]) => (
                  <div key={l}>
                    <div className="text-[9px] text-neutral-700">{l}</div>
                    <div className="text-[11px] font-mono text-white">{v}</div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* OBI Scalper panel — always visible */}
          {(
            <div className="card p-4">
              <div className="flex items-center gap-2 mb-3">
                <Activity size={13} className="text-emerald-400" />
                <span className="text-[13px] font-semibold text-white">OBI Scalper 1m</span>
                <span className="text-[9px] text-neutral-600 ml-1">Order Book Imbalance</span>
                {obiResult.signal ? (
                  <span className="text-[8px] px-2 py-0.5 rounded-full bg-emerald-500/15 border border-emerald-500/25 text-emerald-400 ml-auto animate-pulse font-bold">
                    ⚡ {obiResult.signal.direction.toUpperCase()} SIGNAL
                  </span>
                ) : (
                  <span className="text-[8px] text-neutral-700 ml-auto">scanning every 1m</span>
                )}
              </div>

              {/* OBI Gauge */}
              {(() => {
                const obi = (obiResult as OBIResult).obi_indicators.obi ?? 0;
                const pct = ((obi + 1) / 2 * 100);
                const clampedPct = Math.min(Math.max(pct, 0), 100);
                const obiColor = obi > 0.20 ? "#10b981" : obi < -0.20 ? "#ef4444" : "#6b7280";
                return (
                  <div className="mb-4">
                    <div className="flex justify-between text-[9px] mb-1">
                      <span className="text-red-400">ASK PRESSURE</span>
                      <span style={{ color: obiColor }} className="font-bold font-mono">
                        OBI {obi >= 0 ? "+" : ""}{obi.toFixed(3)}
                      </span>
                      <span className="text-green-400">BID PRESSURE</span>
                    </div>
                    <div className="h-2.5 bg-neutral-800 rounded-full overflow-hidden relative">
                      <div className="absolute inset-0 flex">
                        <div className="h-full bg-red-900/40"   style={{ width: "38%" }} />
                        <div className="h-full bg-neutral-900"  style={{ width: "24%" }} />
                        <div className="h-full bg-green-900/40" style={{ width: "38%" }} />
                      </div>
                      {/* Center line */}
                      <div className="absolute top-0 bottom-0 w-px bg-neutral-600" style={{ left: "50%" }} />
                      {/* OBI indicator */}
                      <div className="absolute top-0.5 bottom-0.5 w-1.5 rounded-full transition-all duration-300"
                        style={{ left: `calc(${clampedPct}% - 3px)`, background: obiColor }} />
                    </div>
                    <div className="flex justify-between text-[8px] text-neutral-700 mt-0.5">
                      <span>−1.0</span>
                      <span className="text-neutral-600">threshold ±{(0.20).toFixed(2)}</span>
                      <span>+1.0</span>
                    </div>
                  </div>
                );
              })()}

              {/* Indicators grid */}
              <div className="grid grid-cols-3 gap-3 mb-3">
                {[
                  ["Bid Vol",  (obiResult as OBIResult).obi_indicators.bid_vol?.toFixed(2) ?? "—",  "text-green-400"],
                  ["Ask Vol",  (obiResult as OBIResult).obi_indicators.ask_vol?.toFixed(2) ?? "—",  "text-red-400"],
                  ["RSI(14)",  (obiResult as OBIResult).obi_indicators.rsi?.toFixed(1)    ?? "—",  (() => { const r = (obiResult as OBIResult).obi_indicators.rsi ?? 50; return r > 55 ? "text-green-400" : r < 45 ? "text-red-400" : "text-neutral-400"; })()],
                  ["EMA9",     (obiResult as OBIResult).obi_indicators.ema9?.toFixed(1)   ?? "—",  "text-blue-400"],
                  ["EMA21",    (obiResult as OBIResult).obi_indicators.ema21?.toFixed(1)  ?? "—",  "text-neutral-400"],
                  ["Bias",     obiResult.bias.toUpperCase(), obiResult.bias === "long" ? "text-green-400" : obiResult.bias === "short" ? "text-red-400" : "text-neutral-500"],
                ].map(([l, v, cls]) => (
                  <div key={l} className="bg-neutral-900 rounded-lg p-2">
                    <div className="text-[8px] text-neutral-600">{l}</div>
                    <div className={`text-[11px] font-mono font-semibold ${cls}`}>{v}</div>
                  </div>
                ))}
              </div>

              {/* Conditions checklist */}
              <div className="space-y-1.5">
                {obiResult.conditions.map((c, i) => (
                  <div key={i} className="flex items-center gap-2">
                    {c.met
                      ? <CheckCircle2 size={10} className="text-emerald-400 flex-shrink-0" />
                      : <XCircle     size={10} className="text-neutral-700 flex-shrink-0" />}
                    <span className={`text-[9px] flex-1 ${c.met ? "text-neutral-300" : "text-neutral-600"}`}>{c.name}</span>
                    <span className={`text-[9px] font-mono ${c.met ? "text-emerald-400" : "text-neutral-700"}`}>{c.value}</span>
                  </div>
                ))}
              </div>

              {/* Strategy spec */}
              <div className="flex gap-4 mt-3 pt-3 border-t border-neutral-800">
                {[["SL", "0.4%", "text-red-400"], ["TP", "0.8%", "text-green-400"],
                  ["R:R", "1:2", "text-white"], ["Timeframe", "1m", "text-blue-400"],
                  ["Max Hold", "10 min", "text-neutral-400"], ["Signals", "3/3 req", "text-emerald-400"],
                ].map(([l, v, cls]) => (
                  <div key={l} className="flex-1 text-center">
                    <div className="text-[7px] text-neutral-700">{l}</div>
                    <div className={`text-[10px] font-bold ${cls}`}>{v}</div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* ORB-30 session panel — always visible */}
          {(
            <div className="card p-4">
              <div className="flex items-center gap-2 mb-3">
                <Activity size={13} className="text-amber-400" />
                <span className="text-[13px] font-semibold text-white">ORB-30 Session</span>
                {/* Status badge */}
                {orbResult.status === "building_orb" && (
                  <span className="text-[8px] px-2 py-0.5 rounded-full bg-amber-500/10 border border-amber-500/20 text-amber-400 ml-auto animate-pulse">
                    BUILDING {orbResult.indicators.bars_in_orb}/30
                  </span>
                )}
                {orbResult.status === "watching" && (
                  <span className="text-[8px] px-2 py-0.5 rounded-full bg-green-500/10 border border-green-500/20 text-green-400 ml-auto">
                    WATCHING · bar {orbResult.indicators.bars_since_orb}/210
                  </span>
                )}
                {orbResult.status === "past_window" && (
                  <span className="text-[8px] px-2 py-0.5 rounded-full bg-neutral-800 border border-neutral-700 text-neutral-500 ml-auto">
                    NEXT SIGNAL @ {orbResult.indicators.next_session}
                  </span>
                )}
                {orbResult.status === "no_data" && (
                  <span className="text-[8px] text-neutral-600 ml-auto">Loading…</span>
                )}
                <span className="text-[9px] text-amber-400/60 ml-2">{orbResult.indicators.session_label}</span>
              </div>

              {/* Past window notice */}
              {orbResult.status === "past_window" && (
                <div className="mb-3 p-2.5 rounded-lg bg-neutral-900 border border-neutral-800 text-[10px] text-neutral-500 text-center">
                  Trade window expired ({orbResult.indicators.bars_since_orb}/210 bars) · Next ORB closes at {orbResult.indicators.next_session}
                </div>
              )}

              {/* OR levels */}
              <div className="grid grid-cols-3 gap-3 mb-4">
                {[
                  ["OR High",    orbResult.indicators.or_high != null   ? `$${orbResult.indicators.or_high.toFixed(0)}`   : "—", "text-green-400"],
                  ["OR Low",     orbResult.indicators.or_low != null    ? `$${orbResult.indicators.or_low.toFixed(0)}`    : "—", "text-red-400"],
                  ["OR Range",   orbResult.indicators.or_range_pct != null ? `${orbResult.indicators.or_range_pct.toFixed(2)}%` : "—", "text-neutral-300"],
                  ["15m EMA20",  orbResult.indicators.ema20_15m != null ? `$${orbResult.indicators.ema20_15m.toFixed(0)}` : "—", "text-blue-400"],
                  ["Last Price", orbResult.indicators.last_price != null? `$${orbResult.indicators.last_price.toFixed(0)}`: "—", "text-white"],
                  ["Vol Avg",    orbResult.indicators.session_vol_avg != null ? orbResult.indicators.session_vol_avg.toFixed(1) : "—", "text-neutral-400"],
                ].map(([l, v, cls]) => (
                  <div key={l} className="bg-neutral-900 rounded-lg p-2.5">
                    <div className="text-[9px] text-neutral-600 mb-0.5">{l}</div>
                    <div className={`text-[12px] font-mono font-semibold ${cls}`}>{v}</div>
                  </div>
                ))}
              </div>

              {/* ORB progress */}
              <div className="space-y-1.5">
                <div className="flex justify-between text-[9px] text-neutral-600">
                  <span>ORB window ({orbResult.indicators.bars_in_orb}/30 bars)</span>
                  <span>{orbResult.current_session?.or_established ? "✓ Established" : "Building…"}</span>
                </div>
                <div className="h-1.5 bg-neutral-800 rounded-full overflow-hidden">
                  <div className="h-full rounded-full bg-amber-400 transition-all duration-300"
                    style={{ width: `${Math.min(orbResult.indicators.bars_in_orb / 30 * 100, 100)}%` }} />
                </div>
                {orbResult.indicators.bars_since_orb > 0 && (
                  <div className="text-[9px] text-neutral-600 flex justify-between mt-1">
                    <span>Trade window: {orbResult.indicators.bars_since_orb}/210 bars</span>
                    <span className={orbResult.bias === "long" ? "text-green-400" : orbResult.bias === "short" ? "text-red-400" : "text-neutral-500"}>
                      Bias: {orbResult.bias.toUpperCase()}
                    </span>
                  </div>
                )}
              </div>

              {/* Backtest stats strip */}
              <div className="flex gap-4 mt-4 pt-3 border-t border-neutral-800">
                {[["5yr Return", "+94.3%", "text-green-400"], ["Win Rate", "55.3%", "text-blue-400"],
                  ["R:R", "2.25:1", "text-white"], ["Profit Factor", "1.81", "text-amber-400"],
                  ["Sharpe", "1.74", "text-violet-400"], ["Max DD", "−13.8%", "text-red-400"],
                ].map(([l, v, cls]) => (
                  <div key={l} className="flex-1 text-center">
                    <div className="text-[8px] text-neutral-700">{l}</div>
                    <div className={`text-[10px] font-bold ${cls}`}>{v}</div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Grid $50 Strategy panel */}
          {(
            <div className="card p-4">
              <div className="flex items-center gap-2 mb-3">
                <svg width="13" height="13" viewBox="0 0 13 13" fill="none" className="text-cyan-400 flex-shrink-0">
                  <rect x="0.5" y="0.5" width="12" height="12" rx="1.5" stroke="currentColor" strokeWidth="1"/>
                  <line x1="0.5" y1="4.5"  x2="12.5" y2="4.5"  stroke="currentColor" strokeWidth="0.8"/>
                  <line x1="0.5" y1="8.5"  x2="12.5" y2="8.5"  stroke="currentColor" strokeWidth="0.8"/>
                  <line x1="4.5" y1="0.5"  x2="4.5"  y2="12.5" stroke="currentColor" strokeWidth="0.8"/>
                  <line x1="8.5" y1="0.5"  x2="8.5"  y2="12.5" stroke="currentColor" strokeWidth="0.8"/>
                </svg>
                <span className="text-[13px] font-semibold text-white">Grid $50</span>
                <span className="text-[9px] text-neutral-600 ml-1">Arithmetic Perp Grid</span>
                {gridResult.signal ? (
                  <span className="text-[8px] px-2 py-0.5 rounded-full bg-cyan-500/15 border border-cyan-500/25 text-cyan-400 ml-auto animate-pulse font-bold">
                    ⚡ BUY LEVEL HIT
                  </span>
                ) : (
                  <span className={`text-[8px] ml-auto ${gridResult.bias === "stopped" ? "text-red-500" : gridResult.bias === "paused" ? "text-yellow-500" : "text-neutral-600"}`}>
                    {gridResult.bias === "stopped" ? "⛔ OUT OF RANGE" : gridResult.bias === "paused" ? "⏸ PAUSED" : "scanning every 20s"}
                  </span>
                )}
              </div>

              {/* Grid visualizer — mini ladder */}
              <div className="mb-4 bg-neutral-900 rounded-xl p-3 border border-neutral-800">
                <div className="flex justify-between text-[8px] text-neutral-600 mb-2">
                  <span>Grid Centre <span className="text-cyan-400 font-mono">${gridResult.gridCenter.toLocaleString()}</span></span>
                  <span>Range <span className="font-mono">${gridResult.gridMin.toLocaleString()}–${gridResult.gridMax.toLocaleString()}</span></span>
                </div>
                {/* Levels ladder (closest 7) */}
                <div className="space-y-0.5">
                  {gridResult.levels.slice(-7).reverse().map((lvl) => {
                    const isCurrent = lvl.price === gridResult.currentLevel;
                    const isAbove   = lvl.price > gridResult.currentPrice;
                    return (
                      <div key={lvl.price}
                        className={`flex items-center gap-2 px-2 py-1 rounded text-[9px] font-mono transition-colors ${
                          isCurrent ? "bg-cyan-500/20 border border-cyan-500/40 text-cyan-300" :
                          isAbove   ? "bg-green-500/5  border border-green-500/10  text-green-500" :
                                      "bg-neutral-800/60 border border-neutral-700/40 text-neutral-500"
                        }`}>
                        <span className="w-3">{isAbove ? "↑" : "↓"}</span>
                        <span className="flex-1">${lvl.price.toLocaleString()}</span>
                        <span className={`text-[8px] ${isAbove ? "text-green-600" : "text-neutral-600"}`}>
                          {isAbove ? "SELL" : "BUY"}
                        </span>
                        {isCurrent && <span className="text-[8px] text-cyan-400 font-bold">← now</span>}
                      </div>
                    );
                  })}
                </div>
                <div className="flex justify-between text-[8px] text-neutral-700 mt-2">
                  <span>Next buy in <span className="text-cyan-400 font-mono">${gridResult.distToNextBuy.toFixed(0)}</span></span>
                  <span>Next sell in <span className="text-green-400 font-mono">${gridResult.distToNextSell.toFixed(0)}</span></span>
                </div>
              </div>

              {/* Stats row */}
              <div className="grid grid-cols-4 gap-2 mb-3">
                {[
                  ["Grid Size",  "$50",                         "text-cyan-400"],
                  ["TP / SL",    "+$50 / −$50",                "text-white"],
                  ["Max Slots",  "5 concurrent",               "text-violet-400"],
                  ["Daily P&L",  `${gridResult.dailyPnl >= 0 ? "+" : ""}$${gridResult.dailyPnl.toFixed(0)}`, gridResult.dailyPnl >= 0 ? "text-green-400" : "text-red-400"],
                ].map(([l, v, cls]) => (
                  <div key={l} className="bg-neutral-900 rounded-lg p-2">
                    <div className="text-[8px] text-neutral-600">{l}</div>
                    <div className={`text-[10px] font-mono font-semibold ${cls}`}>{v}</div>
                  </div>
                ))}
              </div>

              {/* Conditions checklist */}
              <div className="space-y-1.5">
                {gridResult.conditions.map((c, i) => (
                  <div key={i} className="flex items-center gap-2">
                    {c.met
                      ? <CheckCircle2 size={10} className="text-cyan-400 flex-shrink-0" />
                      : <XCircle     size={10} className="text-neutral-700 flex-shrink-0" />}
                    <span className={`text-[9px] flex-1 ${c.met ? "text-neutral-300" : "text-neutral-600"}`}>{c.label}</span>
                    <span className={`text-[9px] font-mono ${c.met ? "text-cyan-400" : "text-neutral-700"}`}>{c.value}</span>
                  </div>
                ))}
              </div>

              {/* Strategy spec footer */}
              <div className="flex gap-3 mt-3 pt-3 border-t border-neutral-800">
                {[["Spacing", "$50", "text-cyan-400"], ["Leverage", "30×", "text-orange-400"],
                  ["R:R", "1:1", "text-neutral-300"], ["Timeframe", "continuous", "text-blue-400"],
                  ["Max Hold", "2 hr", "text-neutral-400"], ["Bias", "Long", "text-green-400"],
                ].map(([l, v, cls]) => (
                  <div key={l} className="flex-1 text-center">
                    <div className="text-[7px] text-neutral-700">{l}</div>
                    <div className={`text-[10px] font-bold ${cls}`}>{v}</div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Paper trading panel — server is the ONLY source of truth */}
          {config.mode === "paper" && (
            server.loading ? (
              <div className="card p-6 flex items-center justify-center gap-3 text-neutral-600">
                <RefreshCw size={14} className="animate-spin" />
                <span className="text-[12px]">Connecting to server agent…</span>
              </div>
            ) : server.error ? (
              <div className="card p-4 flex items-center gap-3 text-red-400 text-[12px]">
                <AlertTriangle size={14} />
                Cannot reach server — check Railway deployment. ({server.error})
              </div>
            ) : (
              <PaperPanel
                openPositions={server.openPositions as unknown as PaperPosition[]}
                stats={server.stats
                  ? { ...server.stats, avg_rr: 0, best_trade: server.stats.best_trade ?? 0, worst_trade: server.stats.worst_trade ?? 0 }
                  : { total_pnl: 0, win_rate: 0, total_trades: 0, wins: 0, losses: 0, avg_rr: 0, best_trade: 0, worst_trade: 0 }
                }
                trades={server.trades as unknown as ReturnType<typeof usePhantomAgent>["trades"]}
                onClose={key => server.closePosition(key)}
                onReset={() => server.resetAccount()}
              />
            )
          )}

          {/* Conditions checklist */}
          <div className="card p-4">
            <div className="flex items-center gap-2 mb-3">
              <Activity size={13} className="text-neutral-500" />
              <span className="text-[13px] font-semibold text-white">Live Strategy Conditions</span>
              <span className="text-[9px] text-green-400 ml-auto flex items-center gap-1">
                <span className="w-1 h-1 rounded-full bg-green-400 animate-pulse" />
                live · best signal · multi-strategy
              </span>
            </div>
            {candles.length >= 35 ? (
              <div className="space-y-2">
                <div className="flex items-center justify-between mb-2">
                  <span className={`text-sm font-bold ${activeResult.bias === "long" ? "text-green-400" : activeResult.bias === "short" ? "text-red-400" : "text-neutral-500"}`}>
                    {activeResult.bias === "long" ? "▲ BULLISH" : activeResult.bias === "short" ? "▼ BEARISH" : "— NEUTRAL"}
                  </span>
                  <span className="text-[10px] text-neutral-500">{activeResult.met_count}/{activeResult.total ?? 7} conditions</span>
                </div>
                <div className="flex gap-0.5 h-1 mb-3">
                  {Array.from({ length: activeResult.total ?? 7 }).map((_, i) => (
                    <div key={i} className="flex-1 rounded-full"
                      style={{ background: i < activeResult.met_count ? "#22c55e" : "#1e1e2e" }} />
                  ))}
                </div>
                {activeResult.conditions.map((c, i) => (
                  <div key={i} className="flex items-center gap-2">
                    {c.met ? <CheckCircle2 size={11} className="text-green-400 flex-shrink-0" /> : <XCircle size={11} className="text-neutral-700 flex-shrink-0" />}
                    <span className={`text-[10px] flex-1 ${c.met ? "text-neutral-200" : "text-neutral-600"}`}>{c.name}</span>
                    <span className={`text-[9px] font-mono ${c.met ? "text-green-400" : "text-neutral-700"}`}>{c.value}</span>
                  </div>
                ))}
              </div>
            ) : (
              <div className="text-center py-6 text-neutral-700 text-sm flex items-center justify-center gap-2">
                <RefreshCw size={13} className="animate-spin" />
                Loading live candles… ({candles.length}/35)
              </div>
            )}
          </div>

          {/* Agent log — prefer server log (survives page close) */}
          <div className="card p-4">
            <div className="flex items-center gap-2 mb-3">
              <ChevronRight size={13} className="text-neutral-500" />
              <span className="text-[13px] font-semibold text-white">Agent Log</span>
              {server.log.length > 0
                ? <span className="text-[9px] text-emerald-400 ml-auto flex items-center gap-1"><span className="w-1 h-1 rounded-full bg-emerald-400 animate-pulse" />server log</span>
                : <span className="text-[9px] text-neutral-700 ml-auto">browser log</span>}
            </div>
            <AgentLog logs={server.log} />
          </div>
        </div>
      </div>

      {/* Trade history — server ONLY (persistent, cross-device) */}
      <div className="card p-4">
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2 flex-wrap">
            <Zap size={13} className="text-neutral-500" />
            <span className="text-[13px] font-semibold text-white">Execution History</span>
            <span className="text-[9px] px-2 py-0.5 rounded-full bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 flex items-center gap-1">
              <span className="w-1 h-1 rounded-full bg-emerald-400" />
              Persistent · survives browser close
            </span>
            {server.stats && server.stats.total_trades > 0 && (
              <div className="flex items-center gap-3 ml-2">
                <span className={`text-[11px] font-bold font-mono ${server.stats.total_pnl >= 0 ? "text-green-400" : "text-red-400"}`}>
                  {server.stats.total_pnl >= 0 ? "+" : ""}${server.stats.total_pnl.toFixed(2)} all-time
                </span>
                <span className="text-[9px] text-neutral-600">
                  {server.stats.wins}W / {server.stats.losses}L · {server.stats.win_rate.toFixed(1)}% WR
                </span>
              </div>
            )}
          </div>
          {!server.loading && !server.error && (
            <button onClick={server.resetAccount}
              className="flex items-center gap-1.5 text-[10px] text-neutral-700 hover:text-red-400 transition-colors ml-2"
              title="Clears all trade history and resets P&L to zero">
              <RotateCcw size={11} /> Reset
            </button>
          )}
        </div>

        {server.loading ? (
          <div className="text-center py-8 text-neutral-700 text-sm flex items-center justify-center gap-2">
            <RefreshCw size={13} className="animate-spin" /> Connecting to server…
          </div>
        ) : server.error ? (
          <div className="flex items-center gap-2 py-6 text-red-400 text-[12px] justify-center">
            <AlertTriangle size={13} /> Cannot reach server ({server.error})
          </div>
        ) : server.trades.length === 0 ? (
          <div className="text-center py-8 text-neutral-700 text-sm">
            No trades yet. Server agent is scanning every 20s…
          </div>
        ) : (
          <div>
            {server.trades.map(t => (
              <div key={t.id} className="flex items-center gap-3 py-2 border-b border-neutral-800/60">
                <div className={`w-6 h-6 rounded-md flex items-center justify-center flex-shrink-0 ${t.direction === "long" ? "bg-green-500/15" : "bg-red-500/15"}`}>
                  {t.direction === "long"
                    ? <TrendingUp size={11} className="text-green-400" />
                    : <TrendingDown size={11} className="text-red-400" />}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className={`text-[11px] font-bold ${t.direction === "long" ? "text-green-400" : "text-red-400"}`}>
                      {t.direction.toUpperCase()} BTC
                    </span>
                    <span className="text-[9px] text-neutral-600">{t.strategy_name}</span>
                    <span className="text-[9px] text-neutral-700">
                      {new Date(t.closed_at ?? t.timestamp).toLocaleTimeString()}
                    </span>
                  </div>
                  <div className="text-[9px] text-neutral-600 truncate font-mono">
                    ${t.entry.toFixed(0)} → ${t.exit_price?.toFixed(0) ?? "open"}
                  </div>
                </div>
                <div className="text-right flex-shrink-0">
                  {t.pnl_usd != null && (
                    <div className={`text-[10px] font-bold font-mono ${t.pnl_usd >= 0 ? "text-green-400" : "text-red-400"}`}>
                      {t.pnl_usd >= 0 ? "+" : ""}${t.pnl_usd.toFixed(2)}
                    </div>
                  )}
                  <div className={`text-[9px] font-bold ${
                    t.exit_reason === "tp" ? "text-green-400" : t.exit_reason === "sl" ? "text-red-400" : "text-neutral-500"
                  }`}>{(t.exit_reason ?? "—").toUpperCase()}</div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* ── Signal Timeline ── */}
      <div className="card overflow-hidden">
        <div className="flex items-center justify-between px-4 py-3" style={{ borderBottom: "1px solid rgba(255,255,255,0.06)" }}>
          <div className="flex items-center gap-2">
            <Activity size={12} className="text-blue-400" />
            <span className="text-[12px] font-bold text-white">Signal Timeline</span>
            <span className="text-[8px] text-neutral-600">Today</span>
          </div>
          <span className="text-[8px] text-neutral-700">{server.trades.length} trades</span>
        </div>
        <div className="p-3 space-y-2 max-h-64 overflow-y-auto">
          {server.trades.length === 0 ? (
            <div className="text-center py-8 text-[10px] text-neutral-700">No signals yet — scanning every 20s</div>
          ) : (
            server.trades.slice(0, 20).map((t, i) => {
              const pnl = t.pnl_usd ?? 0;
              const isWin = pnl > 0;
              const time = new Date(t.closed_at ?? t.timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
              return (
                <div key={i} className="flex items-center gap-2.5 py-1.5 border-b border-neutral-800/40 last:border-0">
                  <div className={`w-5 h-5 rounded flex items-center justify-center flex-shrink-0 ${t.direction === "long" ? "bg-green-500/15" : "bg-red-500/15"}`}>
                    {t.direction === "long"
                      ? <ArrowUpRight size={10} className="text-green-400" />
                      : <ArrowDownRight size={10} className="text-red-400" />}
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-1.5">
                      <span className={`text-[9px] font-bold ${t.direction === "long" ? "text-green-400" : "text-red-400"}`}>{t.direction.toUpperCase()}</span>
                      <span className="text-[8px] text-neutral-700 truncate">{t.strategy_name}</span>
                    </div>
                    <div className="text-[8px] text-neutral-700 font-mono">{time} · ${t.entry.toFixed(0)}</div>
                  </div>
                  <div className="text-right flex-shrink-0">
                    <div className={`text-[9px] font-bold font-mono ${isWin ? "text-green-400" : "text-red-400"}`}>
                      {pnl >= 0 ? "+" : ""}${pnl.toFixed(2)}
                    </div>
                    <div className={`text-[8px] font-bold ${t.exit_reason === "tp" ? "text-green-400/70" : t.exit_reason === "sl" ? "text-red-400/70" : "text-neutral-600"}`}>
                      {(t.exit_reason ?? "—").toUpperCase()}
                    </div>
                  </div>
                </div>
              );
            })
          )}
        </div>
      </div>

      {/* Info footer */}
      <div className="grid grid-cols-3 gap-3">
        {[
          {
            icon: Bot,
            title: "How the Agent Works",
            body: "Runs 24/7 on the server — never stops when the browser closes. All 5 strategies scan independently every 20s. Each can hold its own position simultaneously — Grid $50 supports up to 5 concurrent positions.",
          },
          {
            icon: Shield,
            title: "Perps Mode",
            body: "Opens Phantom Perps (trade.phantom.com) instantly. You set leverage and confirm the position in the Phantom interface — one click.",
          },
          {
            icon: Zap,
            title: "Spot Mode",
            body: "Builds a real Solana transaction via Jupiter v6. USDC → wBTC for LONG, wBTC → USDC for SHORT. Sent to Phantom for signing.",
          },
        ].map(({ icon: Icon, title, body }) => (
          <div key={title} className="card p-4">
            <div className="flex items-center gap-2 mb-2">
              <Icon size={13} className="text-neutral-500" />
              <span className="text-[11px] font-semibold text-white">{title}</span>
            </div>
            <p className="text-[10px] text-neutral-600 leading-relaxed">{body}</p>
          </div>
        ))}
      </div>
    </div>
  );
}

// ─── Export with SolanaProvider ───────────────────────────────────────────────
export default function AgentPageInner() {
  return (
    <SolanaProvider>
      <AgentContent />
    </SolanaProvider>
  );
}
