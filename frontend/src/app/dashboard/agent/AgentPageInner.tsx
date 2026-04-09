"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useWallet } from "@solana/wallet-adapter-react";
import { WalletMultiButton } from "@solana/wallet-adapter-react-ui";
import {
  Bot, Power, RefreshCw, ExternalLink, Zap, Shield,
  CheckCircle2, XCircle, TrendingUp, TrendingDown,
  AlertTriangle, Activity, ChevronRight, Trash2, Copy,
  FileText, RotateCcw, X, DollarSign,
} from "lucide-react";
import { SolanaProvider } from "@/providers/SolanaProvider";
import { usePhantomAgent, AgentState, AgentConfig, PaperPosition, PaperStats } from "@/hooks/usePhantomAgent";
import { useStrategyEngine, StrategyResult } from "@/hooks/useStrategyEngine";
import { useHFTScalper, useAggTradeBuffer, HFTResult } from "@/hooks/useHFTScalper";
import { useORBStrategy } from "@/hooks/useORBStrategy";
import { useOBIScalper, OBIResult } from "@/hooks/useOBIScalper";
import { useBinanceStream, BinanceCandle, BinanceOrderBook, BinanceAggTrade } from "@/hooks/useBinanceStream";
import { useServerAgent } from "@/hooks/useServerAgent";
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
          <span className="text-[11px] text-neutral-700">No open paper positions · all 4 strategies scanning for signals…</span>
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

// ─── Main inner component (wrapped in SolanaProvider) ────────────────────────
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

  // ── All 4 strategy engines run in parallel always ─────────────────────
  const momentumResult = useStrategyEngine(candles15m);
  const hftResult      = useHFTScalper(candles1m, orderBook, aggSnap);
  const orbResult      = useORBStrategy(candles1m);
  const obiResult      = useOBIScalper(candles1m, orderBook);

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
            <span className="text-[10px] font-normal px-2 py-0.5 rounded-full bg-green-500/10 border border-green-500/20 text-green-400 ml-1">4 STRATEGIES LIVE</span>
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
          ["Open Pos",   server.openPositions.length > 0 ? `${server.openPositions.length} active` : anyPositionOpen ? `${openPositions.length} active` : "none"],
          ["Server P&L", server.stats ? `${server.stats.total_pnl >= 0 ? "+" : ""}$${server.stats.total_pnl.toFixed(2)}` : "—"],
          ["Mode",       "📄 PAPER"],
        ].map(([label, val]) => (
          <div key={label}>
            <div className="text-[9px] text-neutral-600 mb-0.5">{label}</div>
            <div className="text-[12px] font-semibold text-white">{val}</div>
          </div>
        ))}

        <div className="ml-auto flex items-center gap-2">
          <button onClick={() => { server.refresh(); forceScan(); }}
            className="p-2 rounded-lg hover:bg-neutral-800 text-neutral-600 hover:text-white transition-colors"
            title="Refresh now">
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
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
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

        {/* Config panel */}
        <div className="col-span-12 lg:col-span-5 card p-5 space-y-5">
          <div className="flex items-center gap-2">
            <Shield size={13} className="text-neutral-500" />
            <span className="text-[13px] font-semibold text-white">Agent Configuration</span>
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
                <button key={m} onClick={() => updateConfig({ mode: m })}
                  className="py-2.5 rounded-xl text-[10px] font-bold border transition-colors"
                  style={config.mode === m
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
              {config.mode === "paper"
                ? "Simulated fills · live P&L tracking · no wallet needed"
                : config.mode === "perps"
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
              <span className="text-[10px] font-mono text-blue-400">{config.min_conditions}+ met</span>
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

          {/* Paper trading panel — server data when available, browser fallback */}
          {config.mode === "paper" && (
            <PaperPanel
              openPositions={
                server.openPositions.length > 0
                  ? (server.openPositions as unknown as PaperPosition[])
                  : openPositions
              }
              stats={server.stats ?? paperStats}
              trades={
                server.trades.length > 0
                  ? (server.trades as unknown as ReturnType<typeof usePhantomAgent>["trades"])
                  : trades
              }
              onClose={key => server.running ? server.closePosition(key) : closePaperPosition(key)}
              onReset={() => server.running ? server.resetAccount() : resetPaperAccount()}
            />
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
            <AgentLog logs={server.log.length > 0 ? server.log : agentLog} />
          </div>
        </div>
      </div>

      {/* Trade history — server trades (persistent) + browser fallback */}
      <div className="card p-4">
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2">
            <Zap size={13} className="text-neutral-500" />
            <span className="text-[13px] font-semibold text-white">Execution History</span>
            {server.trades.length > 0
              ? <span className="text-[9px] text-emerald-400">({server.trades.length} server trades · persistent)</span>
              : <span className="text-[9px] text-neutral-600">({trades.length} trades this session)</span>}
          </div>
          {server.trades.length === 0 && trades.length > 0 && (
            <button onClick={clearTrades}
              className="flex items-center gap-1.5 text-[10px] text-neutral-700 hover:text-red-400 transition-colors">
              <Trash2 size={11} /> Clear
            </button>
          )}
          {server.trades.length > 0 && (
            <button onClick={server.resetAccount}
              className="flex items-center gap-1.5 text-[10px] text-neutral-700 hover:text-red-400 transition-colors">
              <RotateCcw size={11} /> Reset Account
            </button>
          )}
        </div>
        {server.trades.length > 0 ? (
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
                    <span className={`text-[11px] font-bold ${t.direction === "long" ? "text-green-400" : "text-red-400"}`}>{t.direction.toUpperCase()} BTC</span>
                    <span className="text-[9px] text-neutral-600">{t.strategy_name}</span>
                    <span className="text-[9px] text-neutral-700">{new Date(t.closed_at ?? t.timestamp).toLocaleTimeString()}</span>
                  </div>
                  <div className="text-[9px] text-neutral-600 truncate font-mono">${t.entry.toFixed(0)} → ${t.exit_price?.toFixed(0) ?? "open"}</div>
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
        ) : trades.length === 0 ? (
          <div className="text-center py-8 text-neutral-700 text-sm">
            No trades yet. Agent is scanning every 20s…
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
            body: "Runs 24/7 on the server — never stops when the browser closes. All 4 strategies scan independently every 20s. Each can hold its own position simultaneously — up to 4 concurrent trades.",
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
