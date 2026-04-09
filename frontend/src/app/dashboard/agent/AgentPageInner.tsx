"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useWallet } from "@solana/wallet-adapter-react";
import { WalletMultiButton } from "@solana/wallet-adapter-react-ui";
import {
  Bot, Power, RefreshCw, ExternalLink, Zap, Shield,
  CheckCircle2, XCircle, TrendingUp, TrendingDown,
  AlertTriangle, Activity, ChevronRight, Trash2, Copy,
} from "lucide-react";
import { SolanaProvider } from "@/providers/SolanaProvider";
import { usePhantomAgent, AgentState, AgentConfig } from "@/hooks/usePhantomAgent";
import { useStrategyEngine, StrategyResult } from "@/hooks/useStrategyEngine";
import { useHFTScalper, useAggTradeBuffer, HFTResult } from "@/hooks/useHFTScalper";
import { useORBStrategy } from "@/hooks/useORBStrategy";
import { useBinanceStream, BinanceCandle, BinanceOrderBook, BinanceAggTrade } from "@/hooks/useBinanceStream";
import { formatUSD } from "@/lib/utils";

type StrategyMode = "momentum" | "hft" | "orb";

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
function PendingTradeModal({
  trade, onConfirm, onDismiss, mode
}: {
  trade: NonNullable<ReturnType<typeof usePhantomAgent>["pendingTrade"]>;
  onConfirm: () => void;
  onDismiss: () => void;
  mode: AgentConfig["mode"];
}) {
  const isLong = trade.direction === "long";
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm">
      <div className="w-[400px] rounded-2xl border border-neutral-700 bg-neutral-900 shadow-2xl p-6">
        <div className="flex items-center gap-3 mb-5">
          <div className={`w-10 h-10 rounded-xl flex items-center justify-center ${isLong ? "bg-green-500/15" : "bg-red-500/15"}`}>
            {isLong ? <TrendingUp size={20} className="text-green-400" /> : <TrendingDown size={20} className="text-red-400" />}
          </div>
          <div>
            <div className={`text-lg font-bold ${isLong ? "text-green-400" : "text-red-400"}`}>
              {isLong ? "LONG" : "SHORT"} BTC Signal
            </div>
            <div className="text-[11px] text-neutral-500">Agent requesting execution</div>
          </div>
          <div className="ml-auto text-right">
            <div className="text-[10px] text-neutral-600">Confidence</div>
            <div className="text-sm font-bold text-yellow-400">{(trade.confidence * 100).toFixed(0)}%</div>
          </div>
        </div>

        <div className="space-y-2 mb-5">
          {[
            ["Direction",  trade.direction.toUpperCase(), isLong ? "text-green-400" : "text-red-400"],
            ["Entry",      formatUSD(trade.entry),         "text-white"],
            ["Stop Loss",  trade.sl ? formatUSD(trade.sl) : "—", "text-red-400"],
            ["Take Profit",trade.tp ? formatUSD(trade.tp) : "—", "text-green-400"],
            ["Size",       formatUSD(trade.size_usdc) + " USDC",  "text-white"],
            ["Mode",       mode === "perps" ? "Phantom Perps" : "Jupiter Spot", "text-violet-400"],
          ].map(([label, val, cls]) => (
            <div key={label} className="flex justify-between items-center py-1.5 border-b border-neutral-800">
              <span className="text-[11px] text-neutral-500">{label}</span>
              <span className={`text-[12px] font-semibold font-mono ${cls}`}>{val}</span>
            </div>
          ))}
        </div>

        <div className="text-[10px] text-neutral-600 bg-neutral-800 rounded-lg p-3 mb-5 italic leading-relaxed">
          "{trade.reasoning}"
        </div>

        <div className="flex gap-3">
          <button onClick={onDismiss}
            className="flex-1 py-2.5 rounded-xl border border-neutral-700 text-[13px] font-semibold text-neutral-400 hover:text-white hover:border-neutral-600 transition-colors">
            Skip
          </button>
          <button onClick={onConfirm}
            className="flex-1 py-2.5 rounded-xl font-bold text-[13px] text-white flex items-center justify-center gap-2 transition-all"
            style={{
              background: isLong ? "linear-gradient(135deg, #16a34a, #15803d)" : "linear-gradient(135deg, #dc2626, #b91c1c)",
              boxShadow: isLong ? "0 4px 16px rgba(34,197,94,0.3)" : "0 4px 16px rgba(239,68,68,0.3)"
            }}>
            <Zap size={14} />
            {mode === "perps" ? "Open Phantom Perps" : "Execute Swap"}
            <ExternalLink size={12} className="opacity-70" />
          </button>
        </div>
      </div>
    </div>
  );
}

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
        <div className="text-[9px] font-bold" style={{ color: statusColor }}>{trade.status.toUpperCase()}</div>
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
  try {
    const r = await fetch(`https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=${interval}&limit=120`);
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
    const r = await fetch(`https://api.bybit.com/v5/market/kline?category=linear&symbol=BTCUSDT&interval=${bybitInterval}&limit=120`);
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

// ─── Main inner component (wrapped in SolanaProvider) ────────────────────────
function AgentContent() {
  const [strategyMode, setStrategyMode] = useState<StrategyMode>("hft");

  // ── 15m candles (Momentum strategy) ──────────────────────────────────
  const [candles15m, setCandles15m] = useState<BinanceCandle[]>([]);
  // ── 1m candles (HFT strategy) ────────────────────────────────────────
  const [candles1m,  setCandles1m]  = useState<BinanceCandle[]>([]);
  const [orderBook,  setOrderBook]  = useState<BinanceOrderBook | null>(null);
  const { push: pushTrade, get: getTrades } = useAggTradeBuffer();
  const [aggSnap, setAggSnap] = useState<BinanceAggTrade[]>([]);
  const aggSnapTimer = useRef<ReturnType<typeof setInterval> | null>(null);

  // Seed candles on mount and on strategy switch
  const [candles, setCandles] = useState<BinanceCandle[]>([]);
  // Seed both timeframes on mount
  useEffect(() => {
    seedCandles("15m").then(c => { setCandles15m(c); if (strategyMode === "momentum") setCandles(c); });
    seedCandles("1m").then(c  => { setCandles1m(c);  if (strategyMode === "hft" || strategyMode === "orb") setCandles(c); });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Sync active candles when mode changes
  useEffect(() => {
    if (strategyMode === "momentum" && candles15m.length) setCandles(candles15m);
    if ((strategyMode === "hft" || strategyMode === "orb") && candles1m.length) setCandles(candles1m);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [strategyMode]);

  // Snapshot aggTrades every 2s so HFT hook re-renders
  useEffect(() => {
    aggSnapTimer.current = setInterval(() => setAggSnap([...getTrades()]), 2000);
    return () => { if (aggSnapTimer.current) clearInterval(aggSnapTimer.current); };
  }, [getTrades]);

  // Subscribe to both 15m and 1m streams
  useBinanceStream({
    symbols: ["BTC/USDT"],
    timeframe: "15m",
    onCandle: useCallback((_sym: string, c: BinanceCandle) => {
      setCandles15m(prev => {
        const updated = !prev.length ? [c] : (() => {
          const lMs = new Date(prev[prev.length-1].timestamp).getTime();
          const cMs = new Date(c.timestamp).getTime();
          if (lMs === cMs) return [...prev.slice(0,-1), c];
          if (cMs > lMs)   return [...prev.slice(-119), c];
          return prev;
        })();
        if (strategyMode === "momentum") setCandles(updated);
        return updated;
      });
    }, [strategyMode]),
  });

  useBinanceStream({
    symbols: ["BTC/USDT"],
    timeframe: "1m",
    onCandle: useCallback((_sym: string, c: BinanceCandle) => {
      setCandles1m(prev => {
        const updated = !prev.length ? [c] : (() => {
          const lMs = new Date(prev[prev.length-1].timestamp).getTime();
          const cMs = new Date(c.timestamp).getTime();
          if (lMs === cMs) return [...prev.slice(0,-1), c];
          if (cMs > lMs)   return [...prev.slice(-119), c];
          return prev;
        })();
        if (strategyMode === "hft" || strategyMode === "orb") setCandles(updated);
        return updated;
      });
    }, [strategyMode]),
    onOrderBook: useCallback((ob: BinanceOrderBook) => setOrderBook(ob), []),
    onAggTrade:  useCallback((t: BinanceAggTrade) => pushTrade(t), [pushTrade]),
  });

  // ── Strategy engines ──────────────────────────────────────────────────
  const momentumResult = useStrategyEngine(candles15m);
  const hftResult      = useHFTScalper(candles1m, orderBook, aggSnap);
  const orbResult      = useORBStrategy(candles1m);

  // Active result fed to agent
  const activeResult: StrategyResult = useMemo(() => {
    if (strategyMode === "hft") {
      return {
        bias:       hftResult.bias,
        conditions: hftResult.conditions,
        met_count:  hftResult.met_count,
        total:      7,
        all_met:    hftResult.all_met,
        signal:     hftResult.signal ? {
          direction:  hftResult.signal.direction,
          entry:      hftResult.signal.entry,
          sl:         hftResult.signal.sl,
          tp:         hftResult.signal.tp2,
          confidence: hftResult.signal.confidence,
          reasoning:  hftResult.signal.reasoning,
          timestamp:  hftResult.signal.timestamp,
          rr:         `1 : ${(hftResult.signal.tp2 - hftResult.signal.entry) / (hftResult.signal.entry - hftResult.signal.sl) > 0 ? ((hftResult.signal.tp2 - hftResult.signal.entry) / Math.abs(hftResult.signal.entry - hftResult.signal.sl)).toFixed(1) : "1.2"}`,
        } : null,
        indicators: {
          rsi: null, ema50: null, ema21: null, vwap: null,
          atr: null, atr_pct: null, vol_ratio: null, ema50_slope: null,
        },
      };
    }
    if (strategyMode === "orb") {
      return {
        bias:       orbResult.bias,
        conditions: orbResult.conditions,
        met_count:  orbResult.met_count,
        total:      6,
        all_met:    orbResult.all_met,
        signal:     orbResult.signal ? {
          direction:  orbResult.signal.direction,
          entry:      orbResult.signal.entry,
          sl:         orbResult.signal.sl,
          tp:         orbResult.signal.tp,
          confidence: orbResult.signal.confidence,
          reasoning:  orbResult.signal.reasoning,
          timestamp:  orbResult.signal.timestamp,
          rr:         orbResult.signal.rr,
        } : null,
        indicators: {
          rsi: null, ema50: null, ema21: null, vwap: null,
          atr: null, atr_pct: null, vol_ratio: null, ema50_slope: null,
        },
      };
    }
    return momentumResult;
  }, [strategyMode, momentumResult, hftResult, orbResult]);

  const {
    config, updateConfig,
    agentState, analysis,
    trades, clearTrades,
    pendingTrade, confirmTrade, dismissPendingTrade,
    scanCount, lastScan,
    agentLog,
    walletConnected, walletAddress,
    forceScan,
  } = usePhantomAgent(activeResult);

  const { connected } = useWallet();
  const meta = STATE_META[agentState];

  const copyAddress = useCallback(() => {
    if (walletAddress) navigator.clipboard.writeText(walletAddress);
  }, [walletAddress]);

  return (
    <div className="p-4 space-y-5">
      {/* Pending trade modal */}
      {pendingTrade && !config.auto_execute && (
        <PendingTradeModal
          trade={pendingTrade}
          onConfirm={confirmTrade}
          onDismiss={dismissPendingTrade}
          mode={config.mode}
        />
      )}

      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-white flex items-center gap-2.5">
            <Bot size={22} className="text-blue-400" />
            Living Agent
          </h1>
          <p className="text-xs text-neutral-600 mt-0.5">
            {strategyMode === "hft" ? "HFT VWAP Scalper · 1m bars · OBI + TFI"
              : strategyMode === "orb" ? "ORB-30 Breakout · 1m bars · 5yr backtest: 94.3% return · 55.3% WR"
              : "BTC Momentum Velocity · 15m · EMA/RSI/VWAP"} · autonomous via Phantom
          </p>
        </div>
        <div className="flex items-center gap-3">
          {/* Strategy selector */}
          <div className="flex rounded-xl overflow-hidden border border-neutral-800 text-[11px] font-semibold">
            {([
              ["momentum", "Momentum 15m"],
              ["hft",      "HFT Scalper"],
              ["orb",      "ORB-30 ★"],
            ] as [StrategyMode, string][]).map(([m, label]) => (
              <button key={m} onClick={() => setStrategyMode(m)}
                className="px-4 py-2 transition-colors"
                style={strategyMode === m
                  ? { background: m === "orb" ? "rgba(245,158,11,0.2)" : "rgba(10,132,255,0.2)",
                      color:      m === "orb" ? "#f59e0b" : "#0a84ff" }
                  : { background: "transparent", color: "#4b5563" }}>
                {label}
              </button>
            ))}
          </div>
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
          ["Scans Run",    scanCount.toString()],
          ["Last Scan",    lastScan ?? "—"],
          ["Strategy",     strategyMode === "hft" ? "HFT 1m" : strategyMode === "orb" ? "ORB-30" : "MV 15m"],
          ["Conditions",   analysis ? `${analysis.met_count}/${activeResult.total ?? 7}` : "—"],
          ["Bias",         analysis?.bias?.toUpperCase() ?? "—"],
          ["Wallet",       connected ? "Connected" : "Disconnected"],
        ].map(([label, val]) => (
          <div key={label}>
            <div className="text-[9px] text-neutral-600 mb-0.5">{label}</div>
            <div className="text-[12px] font-semibold text-white">{val}</div>
          </div>
        ))}

        <div className="ml-auto flex items-center gap-2">
          <button onClick={forceScan}
            className="p-2 rounded-lg hover:bg-neutral-800 text-neutral-600 hover:text-white transition-colors"
            title="Force scan now">
            <RefreshCw size={14} className={agentState === "scanning" ? "animate-spin" : ""} />
          </button>

          {/* Master ON/OFF toggle */}
          <button
            onClick={() => updateConfig({ enabled: !config.enabled })}
            disabled={!connected && !config.enabled}
            className="flex items-center gap-2 px-5 py-2 rounded-xl font-bold text-[13px] transition-all disabled:opacity-40"
            style={config.enabled
              ? { background: "rgba(239,68,68,0.15)", border: "1px solid rgba(239,68,68,0.3)", color: "#ef4444" }
              : { background: "rgba(34,197,94,0.15)", border: "1px solid rgba(34,197,94,0.3)", color: "#22c55e" }
            }>
            <Power size={14} />
            {config.enabled ? "Stop Agent" : "Start Agent"}
          </button>
        </div>
      </div>

      {!connected && (
        <div className="flex items-center gap-3 p-4 rounded-xl border border-yellow-500/20 bg-yellow-500/5">
          <AlertTriangle size={16} className="text-yellow-400 flex-shrink-0" />
          <div className="text-[12px] text-yellow-300">
            Connect your <strong>Phantom wallet</strong> using the button above to enable trade execution.
            The agent will still monitor signals without a wallet connected.
          </div>
        </div>
      )}

      {/* Config + log grid */}
      <div className="grid grid-cols-12 gap-4">

        {/* Config panel */}
        <div className="col-span-12 lg:col-span-5 card p-5 space-y-5">
          <div className="flex items-center gap-2">
            <Shield size={13} className="text-neutral-500" />
            <span className="text-[13px] font-semibold text-white">Agent Configuration</span>
          </div>

          {/* Mode */}
          <div>
            <label className="text-[10px] text-neutral-600 block mb-2">Execution Mode</label>
            <div className="grid grid-cols-2 gap-2">
              {(["perps", "spot"] as const).map(m => (
                <button key={m} onClick={() => updateConfig({ mode: m })}
                  className="py-2.5 rounded-xl text-[11px] font-bold border transition-colors"
                  style={config.mode === m
                    ? { background: "rgba(10,132,255,0.12)", borderColor: "rgba(10,132,255,0.3)", color: "#60aaff" }
                    : { background: "rgba(255,255,255,0.02)", borderColor: "rgba(255,255,255,0.06)", color: "#3d3d58" }
                  }>
                  {m === "perps" ? "🚀 Phantom Perps" : "⚡ Jupiter Spot"}
                </button>
              ))}
            </div>
            <div className="text-[9px] text-neutral-700 mt-1.5">
              {config.mode === "perps"
                ? "Opens Phantom Perps — full leverage, confirm in wallet"
                : "Executes USDC↔wBTC swap directly on Solana mainnet"}
            </div>
          </div>

          {/* Size */}
          <div>
            <label className="text-[10px] text-neutral-600 block mb-1.5">Trade Size (USDC)</label>
            <div className="flex gap-2">
              <input type="number" value={config.size_usdc}
                onChange={e => updateConfig({ size_usdc: Number(e.target.value) })}
                className="flex-1 bg-neutral-800 border border-neutral-700 rounded-lg px-3 py-2 text-[12px] font-mono text-white outline-none focus:border-blue-500" />
              <div className="flex gap-1">
                {[50, 100, 250].map(s => (
                  <button key={s} onClick={() => updateConfig({ size_usdc: s })}
                    className="px-2 py-2 rounded-lg text-[10px] font-bold border transition-colors"
                    style={config.size_usdc === s
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
              <span className="text-[10px] font-mono text-blue-400">{(config.min_confidence * 100).toFixed(0)}%</span>
            </div>
            <input type="range" min={0.4} max={0.95} step={0.05}
              value={config.min_confidence}
              onChange={e => updateConfig({ min_confidence: Number(e.target.value) })}
              className="w-full accent-blue-500" />
            <div className="flex justify-between text-[9px] text-neutral-700 mt-1">
              <span>Aggressive (40%)</span><span>Conservative (95%)</span>
            </div>
          </div>

          {/* Min conditions */}
          <div>
            <div className="flex justify-between mb-1.5">
              <label className="text-[10px] text-neutral-600">Min Conditions Met</label>
              <span className="text-[10px] font-mono text-blue-400">{config.min_conditions}/7</span>
            </div>
            <input type="range" min={3} max={7} step={1}
              value={config.min_conditions}
              onChange={e => updateConfig({ min_conditions: Number(e.target.value) })}
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
            <button onClick={() => updateConfig({ auto_execute: !config.auto_execute })}
              className="relative w-10 h-5 rounded-full border transition-colors flex-shrink-0"
              style={config.auto_execute
                ? { background: "rgba(239,68,68,0.3)", borderColor: "rgba(239,68,68,0.4)" }
                : { background: "rgba(255,255,255,0.04)", borderColor: "rgba(255,255,255,0.1)" }
              }>
              <span className="absolute top-0.5 w-4 h-4 rounded-full transition-all duration-200"
                style={{ left: config.auto_execute ? "22px" : "2px", background: config.auto_execute ? "#ef4444" : "#3d3d58" }} />
            </button>
          </div>

          {config.auto_execute && (
            <div className="flex items-start gap-2 p-3 rounded-lg border border-red-500/20 bg-red-500/5">
              <AlertTriangle size={12} className="text-red-400 flex-shrink-0 mt-0.5" />
              <div className="text-[10px] text-red-400/80">Auto-execute is ON. The agent will submit transactions automatically when conditions are met. Each trade requires Phantom wallet approval.</div>
            </div>
          )}
        </div>

        {/* Right: conditions + HFT meters + log */}
        <div className="col-span-12 lg:col-span-7 space-y-4">

          {/* HFT-specific meters (only when HFT mode) */}
          {strategyMode === "hft" && (
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

          {/* ORB-30 session panel */}
          {strategyMode === "orb" && (
            <div className="card p-4">
              <div className="flex items-center gap-2 mb-3">
                <Activity size={13} className="text-amber-400" />
                <span className="text-[13px] font-semibold text-white">ORB-30 Session</span>
                <span className="text-[9px] text-amber-400 ml-auto">
                  {orbResult.indicators.session_label}
                </span>
              </div>

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
                    <span>Bars since ORB: {orbResult.indicators.bars_since_orb}/{90}</span>
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

          {/* Conditions checklist */}
          <div className="card p-4">
            <div className="flex items-center gap-2 mb-3">
              <Activity size={13} className="text-neutral-500" />
              <span className="text-[13px] font-semibold text-white">Live Strategy Conditions</span>
              <span className="text-[9px] text-green-400 ml-auto flex items-center gap-1">
                <span className="w-1 h-1 rounded-full bg-green-400 animate-pulse" />
                live · {strategyMode === "hft" ? "1m bar" : "15m bar"}
              </span>
            </div>
            {candles.length >= (strategyMode === "momentum" ? 60 : 35) ? (
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
                Loading live candles… ({candles.length}/{strategyMode === "momentum" ? 60 : 35})
              </div>
            )}
          </div>

          {/* Agent log */}
          <div className="card p-4">
            <div className="flex items-center gap-2 mb-3">
              <ChevronRight size={13} className="text-neutral-500" />
              <span className="text-[13px] font-semibold text-white">Agent Log</span>
              <span className="text-[9px] text-neutral-700 ml-auto">Live terminal</span>
            </div>
            <AgentLog logs={agentLog} />
          </div>
        </div>
      </div>

      {/* Trade history */}
      <div className="card p-4">
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2">
            <Zap size={13} className="text-neutral-500" />
            <span className="text-[13px] font-semibold text-white">Execution History</span>
            <span className="text-[9px] text-neutral-600">({trades.length} trades this session)</span>
          </div>
          {trades.length > 0 && (
            <button onClick={clearTrades}
              className="flex items-center gap-1.5 text-[10px] text-neutral-700 hover:text-red-400 transition-colors">
              <Trash2 size={11} /> Clear
            </button>
          )}
        </div>
        {trades.length === 0 ? (
          <div className="text-center py-8 text-neutral-700 text-sm">
            No trades executed this session. Start the agent to begin.
          </div>
        ) : (
          <div>{trades.map(t => <TradeRow key={t.id} trade={t} />)}</div>
        )}
      </div>

      {/* Info footer */}
      <div className="grid grid-cols-3 gap-3">
        {[
          {
            icon: Bot,
            title: "How the Agent Works",
            body: "Scans BTC Momentum Velocity strategy every 60s. When 5+ conditions are met and confidence exceeds your threshold, it fires a trade signal.",
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
