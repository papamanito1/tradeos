"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import {
  ExternalLink, Zap, TrendingUp, TrendingDown, AlertTriangle,
  CheckCircle2, XCircle, RefreshCw, Target, Shield, DollarSign,
  ArrowUpRight, ArrowDownRight, Wallet,
} from "lucide-react";
import { formatUSD } from "@/lib/utils";
import { useBinanceStream, BinanceTicker, BinanceCandle, BinanceOrderBook } from "@/hooks/useBinanceStream";

// ─── Constants ────────────────────────────────────────────────────────────────
const PHANTOM_PERPS_URL = "https://trade.phantom.com/perps/BTC";

// ─── Types ────────────────────────────────────────────────────────────────────
interface Signal {
  timestamp: string; direction: "long" | "short";
  entry: number; sl?: number | null; tp?: number | null;
  confidence: number; strategy_name: string; reasoning: string;
}
interface Condition {
  name: string; met: boolean; value: string;
}
interface Analysis {
  bias: string; met_count: number; total: number; all_met: boolean;
  conditions: Condition[];
  indicators: Record<string, number | null>;
  last_signal: Record<string, unknown> | null;
}

// ─── Indicator math ───────────────────────────────────────────────────────────
function calcEMA(vals: number[], p: number): number[] {
  if (vals.length < p) return vals.map(() => NaN);
  const k = 2 / (p + 1);
  const out: number[] = Array(p - 1).fill(NaN);
  let prev = vals.slice(0, p).reduce((a, b) => a + b, 0) / p;
  out.push(prev);
  for (let i = p; i < vals.length; i++) { prev = vals[i] * k + prev * (1 - k); out.push(prev); }
  return out;
}
function calcVWAP(candles: BinanceCandle[], w = 50): number[] {
  return candles.map((_, i) => {
    const seg = candles.slice(Math.max(0, i - w + 1), i + 1);
    const tv = seg.reduce((s, c) => s + c.volume, 0);
    return tv ? seg.reduce((s, c) => s + (c.high + c.low + c.close) / 3 * c.volume, 0) / tv
               : (candles[i].high + candles[i].low + candles[i].close) / 3;
  });
}

// ─── Mini Chart ───────────────────────────────────────────────────────────────
function MiniChart({ candles, signal }: { candles: BinanceCandle[]; signal: Signal | null }) {
  const W = 800, H = 180, PY = 10, BAR = 5;
  const visible = candles.slice(-Math.floor(W / (BAR + 2)));
  if (visible.length < 5) return (
    <div className="flex items-center justify-center h-44 text-neutral-700 text-sm gap-2">
      <RefreshCw size={13} className="animate-spin" /> Loading chart…
    </div>
  );
  const prices = visible.flatMap(c => [c.high, c.low]);
  const minP = Math.min(...prices), maxP = Math.max(...prices), range = maxP - minP || 1;
  const toY = (p: number) => PY + ((maxP - p) / range) * (H - PY * 2);
  const allC = candles.map(c => c.close);
  const off = candles.length - visible.length;
  const ema50 = calcEMA(allC, 50).slice(off);
  const ema21 = calcEMA(allC, 21).slice(off);
  const vwap  = calcVWAP(candles, 50).slice(off);
  const pts = (vals: number[]) => vals.map((v, i) => isNaN(v) ? null : `${i * (BAR + 2) + BAR / 2},${toY(v)}`).filter(Boolean).join(" ");

  return (
    <div className="relative">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ height: H }} preserveAspectRatio="none">
        {[0.25, 0.5, 0.75].map(f => <line key={f} x1={0} x2={W} y1={PY + f * (H - PY * 2)} y2={PY + f * (H - PY * 2)} stroke="#18182a" strokeWidth={1} />)}
        <polyline points={pts(vwap)}  fill="none" stroke="#a78bfa" strokeWidth={1}   strokeDasharray="4 3" opacity={0.6} />
        <polyline points={pts(ema50)} fill="none" stroke="#f97316" strokeWidth={1.5} opacity={0.85} />
        <polyline points={pts(ema21)} fill="none" stroke="#38bdf8" strokeWidth={1.2} opacity={0.85} />
        {signal?.sl && signal.sl > minP && signal.sl < maxP && (
          <line x1={0} x2={W} y1={toY(signal.sl)} y2={toY(signal.sl)} stroke="#ef4444" strokeWidth={1} strokeDasharray="5 3" opacity={0.7} />
        )}
        {signal?.tp && signal.tp > minP && signal.tp < maxP && (
          <line x1={0} x2={W} y1={toY(signal.tp)} y2={toY(signal.tp)} stroke="#22c55e" strokeWidth={1} strokeDasharray="5 3" opacity={0.7} />
        )}
        {visible.map((c, i) => {
          const x = i * (BAR + 2), up = c.close >= c.open, color = up ? "#22c55e" : "#ef4444";
          const bTop = toY(Math.max(c.open, c.close)), bH = Math.max(toY(Math.min(c.open, c.close)) - bTop, 1);
          return (
            <g key={i}>
              <line x1={x + BAR / 2} x2={x + BAR / 2} y1={toY(c.high)} y2={toY(c.low)} stroke={color} strokeWidth={1} opacity={0.45} />
              <rect x={x} y={bTop} width={BAR} height={bH} fill={color} fillOpacity={c.is_closed === false ? 0.45 : 0.85} />
            </g>
          );
        })}
      </svg>
      <div className="absolute top-1 left-2 flex items-center gap-3 text-[8px] font-mono text-neutral-700">
        <span className="flex items-center gap-1"><span className="w-3 h-px bg-orange-400 inline-block" />EMA50</span>
        <span className="flex items-center gap-1"><span className="w-3 h-px bg-sky-400 inline-block" />EMA21</span>
        <span className="flex items-center gap-1"><span className="w-3 h-px bg-violet-400 inline-block opacity-60" />VWAP</span>
        {signal?.sl && <span className="flex items-center gap-1"><span className="w-3 h-px bg-red-400 inline-block" />SL</span>}
        {signal?.tp && <span className="flex items-center gap-1"><span className="w-3 h-px bg-green-400 inline-block" />TP</span>}
      </div>
      {[maxP, (maxP + minP) / 2, minP].map((p, i) => (
        <div key={i} className="absolute right-1 text-[8px] text-neutral-700 font-mono -translate-y-1/2"
          style={{ top: `${[PY / H, 0.5, (H - PY) / H][i] * 100}%` }}>
          {formatUSD(p)}
        </div>
      ))}
    </div>
  );
}

// ─── Order Book ───────────────────────────────────────────────────────────────
function OrderBook({ ob }: { ob: BinanceOrderBook | null }) {
  if (!ob) return <div className="text-center py-6 text-neutral-700 text-sm">Connecting…</div>;
  const asks = ob.asks.slice(0, 10), bids = ob.bids.slice(0, 10);
  const maxSz = Math.max(...asks.map(a => a.amount), ...bids.map(b => b.amount)) || 1;
  const spread = asks.length && bids.length ? asks[0].price - bids[0].price : 0;
  return (
    <div className="space-y-px">
      <div className="flex justify-between text-[9px] text-neutral-700 font-mono px-1 mb-1">
        <span>Price (USDT)</span><span>Size (BTC)</span>
      </div>
      {[...asks].reverse().map((a, i) => (
        <div key={i} className="relative flex justify-between text-[10px] font-mono h-5 items-center overflow-hidden rounded-sm">
          <div className="absolute right-0 top-0 bottom-0 bg-red-500/10" style={{ width: `${(a.amount / maxSz) * 100}%` }} />
          <span className="relative text-red-400 pl-1">{formatUSD(a.price)}</span>
          <span className="relative text-neutral-500 pr-1">{a.amount.toFixed(4)}</span>
        </div>
      ))}
      <div className="py-1 text-center text-[9px] text-neutral-600 border-y border-neutral-800 font-mono">
        Spread {formatUSD(spread)} · {bids.length ? ((spread / bids[0].price) * 100).toFixed(4) : "0"}%
      </div>
      {bids.map((b, i) => (
        <div key={i} className="relative flex justify-between text-[10px] font-mono h-5 items-center overflow-hidden rounded-sm">
          <div className="absolute left-0 top-0 bottom-0 bg-green-500/10" style={{ width: `${(b.amount / maxSz) * 100}%` }} />
          <span className="relative text-green-400 pl-1">{formatUSD(b.price)}</span>
          <span className="relative text-neutral-500 pr-1">{b.amount.toFixed(4)}</span>
        </div>
      ))}
    </div>
  );
}

// ─── Trade Setup Card ─────────────────────────────────────────────────────────
function TradeSetup({
  signal, ticker, analysis,
}: {
  signal: Signal | null;
  ticker: BinanceTicker | null;
  analysis: Analysis | null;
}) {
  const [capital, setCapital] = useState(1000);
  const [leverage, setLeverage] = useState(5);

  const price      = ticker?.last || signal?.entry || 0;
  const isLong     = signal?.direction === "long";
  const sl         = signal?.sl || 0;
  const tp         = signal?.tp || 0;
  const slDist     = price && sl ? Math.abs(price - sl) : 0;
  const tpDist     = price && tp ? Math.abs(price - tp) : 0;
  const rr         = slDist > 0 ? (tpDist / slDist).toFixed(2) : "—";
  const posSize    = (capital * leverage) / (price || 1);
  const riskUsd    = capital * (slDist / (price || 1));
  const rewardUsd  = capital * (tpDist / (price || 1));

  const canTrade = analysis ? analysis.met_count >= 5 : false;
  const signalAge = signal ? Math.floor((Date.now() - new Date(signal.timestamp).getTime()) / 60000) : null;
  const signalFresh = signalAge !== null && signalAge < 15;

  const openPhantom = () => {
    window.open(PHANTOM_PERPS_URL, "_blank", "noopener,noreferrer");
  };

  return (
    <div className="space-y-4">
      {/* Signal status */}
      <div className={`p-4 rounded-xl border ${
        signal && signalFresh
          ? isLong ? "border-green-500/30 bg-green-500/5" : "border-red-500/30 bg-red-500/5"
          : "border-neutral-800 bg-neutral-900/40"
      }`}>
        <div className="flex items-center justify-between mb-2">
          <div className="text-[10px] text-neutral-600 uppercase tracking-wider">Strategy Signal</div>
          {signal && signalFresh && (
            <span className="text-[9px] text-neutral-500">{signalAge}m ago</span>
          )}
        </div>
        {signal && signalFresh ? (
          <div>
            <div className={`text-xl font-bold flex items-center gap-2 ${isLong ? "text-green-400" : "text-red-400"}`}>
              {isLong ? <ArrowUpRight size={20} /> : <ArrowDownRight size={20} />}
              {isLong ? "LONG BTC" : "SHORT BTC"}
            </div>
            <div className="text-[11px] text-neutral-500 mt-1 line-clamp-2">{signal.reasoning}</div>
            <div className="flex items-center gap-1 mt-2">
              <div className="flex-1 h-1 bg-neutral-800 rounded">
                <div className={`h-full rounded ${signal.confidence > 0.7 ? "bg-yellow-400" : isLong ? "bg-green-400" : "bg-red-400"}`}
                  style={{ width: `${signal.confidence * 100}%` }} />
              </div>
              <span className="text-[9px] text-neutral-600">{(signal.confidence * 100).toFixed(0)}% confidence</span>
            </div>
          </div>
        ) : (
          <div className="flex items-center gap-2 text-neutral-600">
            <RefreshCw size={14} />
            <span className="text-sm">
              {signal ? "Signal expired (>15m old)" : "Waiting for signal · runs every 60s"}
            </span>
          </div>
        )}
      </div>

      {/* Levels */}
      {signal && signalFresh && (
        <div className="grid grid-cols-3 gap-2">
          {[
            ["Entry", formatUSD(signal.entry || price), "text-blue-400"],
            ["Stop Loss", signal.sl ? formatUSD(signal.sl) : "—", "text-red-400"],
            ["Take Profit", signal.tp ? formatUSD(signal.tp) : "—", "text-green-400"],
          ].map(([label, val, cls]) => (
            <div key={label} className="bg-neutral-900 rounded-lg p-2.5 text-center">
              <div className="text-[9px] text-neutral-600 mb-1">{label}</div>
              <div className={`text-[11px] font-mono font-bold ${cls}`}>{val}</div>
            </div>
          ))}
        </div>
      )}

      {/* Position calculator */}
      <div className="bg-neutral-900 rounded-xl p-4 space-y-3">
        <div className="text-[11px] font-semibold text-neutral-400 flex items-center gap-1.5">
          <Target size={12} />Position Calculator
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="text-[9px] text-neutral-600 block mb-1">Capital (USDT)</label>
            <input
              type="number" value={capital}
              onChange={e => setCapital(Number(e.target.value))}
              className="w-full bg-neutral-800 border border-neutral-700 rounded-lg px-2.5 py-1.5 text-[12px] font-mono text-white outline-none focus:border-blue-500"
            />
          </div>
          <div>
            <label className="text-[9px] text-neutral-600 block mb-1">Leverage</label>
            <div className="flex gap-1">
              {[1, 2, 5, 10].map(l => (
                <button key={l} onClick={() => setLeverage(l)}
                  className="flex-1 py-1.5 rounded text-[10px] font-bold border transition-colors"
                  style={{ background: leverage === l ? "rgba(10,132,255,0.15)" : "rgba(255,255,255,0.03)", borderColor: leverage === l ? "rgba(10,132,255,0.4)" : "rgba(255,255,255,0.06)", color: leverage === l ? "#60aaff" : "#3d3d58" }}>
                  {l}×
                </button>
              ))}
            </div>
          </div>
        </div>
        <div className="grid grid-cols-3 gap-2 pt-1">
          {[
            ["Position", formatUSD(capital * leverage), "text-white"],
            ["BTC Size", posSize > 0 ? posSize.toFixed(5) : "—", "text-neutral-300"],
            ["R:R", signal ? `1 : ${rr}` : "—", "text-yellow-400"],
          ].map(([label, val, cls]) => (
            <div key={label} className="text-center">
              <div className="text-[8px] text-neutral-700 mb-0.5">{label}</div>
              <div className={`text-[11px] font-mono font-semibold ${cls}`}>{val}</div>
            </div>
          ))}
        </div>
        {signal && signalFresh && slDist > 0 && (
          <div className="flex gap-2 pt-1">
            <div className="flex-1 bg-red-500/5 border border-red-500/15 rounded p-2 text-center">
              <div className="text-[8px] text-red-400/60 mb-0.5">Risk (est.)</div>
              <div className="text-[11px] font-mono text-red-400 font-semibold">-{formatUSD(riskUsd)}</div>
            </div>
            <div className="flex-1 bg-green-500/5 border border-green-500/15 rounded p-2 text-center">
              <div className="text-[8px] text-green-400/60 mb-0.5">Reward (est.)</div>
              <div className="text-[11px] font-mono text-green-400 font-semibold">+{formatUSD(rewardUsd)}</div>
            </div>
          </div>
        )}
      </div>

      {/* Phantom trade button */}
      <button
        onClick={openPhantom}
        className="w-full py-3.5 rounded-xl font-bold text-[14px] flex items-center justify-center gap-2.5 transition-all active:scale-[0.98]"
        style={{
          background: canTrade && signal && signalFresh
            ? isLong
              ? "linear-gradient(135deg, #16a34a 0%, #15803d 100%)"
              : "linear-gradient(135deg, #dc2626 0%, #b91c1c 100%)"
            : "linear-gradient(135deg, #7c3aed 0%, #5b21b6 100%)",
          boxShadow: canTrade && signal && signalFresh
            ? isLong ? "0 4px 20px rgba(34,197,94,0.25)" : "0 4px 20px rgba(239,68,68,0.25)"
            : "0 4px 20px rgba(124,58,237,0.25)",
          color: "#fff",
        }}
      >
        <Wallet size={16} />
        {canTrade && signal && signalFresh
          ? `Execute ${isLong ? "LONG" : "SHORT"} on Phantom`
          : "Open Phantom Perps"}
        <ExternalLink size={13} className="opacity-70" />
      </button>

      {/* Phantom info */}
      <div className="flex items-start gap-2 p-3 rounded-lg border border-violet-500/15 bg-violet-500/5">
        <AlertTriangle size={12} className="text-violet-400 flex-shrink-0 mt-0.5" />
        <div className="text-[10px] text-neutral-500 leading-relaxed">
          <span className="text-violet-400 font-semibold">Phantom Perps</span> runs on Solana.
          Connect your Phantom wallet and fund your perps account before trading.
          Signals are generated by the Momentum Velocity strategy — always verify before execution.
        </div>
      </div>
    </div>
  );
}

// ─── Conditions Panel ─────────────────────────────────────────────────────────
function ConditionsPanel({ analysis, loading }: { analysis: Analysis | null; loading: boolean }) {
  if (loading && !analysis) return (
    <div className="flex items-center justify-center py-8">
      <div className="w-4 h-4 border-2 rounded-full animate-spin border-t-transparent border-blue-500" />
    </div>
  );
  if (!analysis) return null;
  const { conditions, met_count, total, bias } = analysis;
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <span className={`text-sm font-bold ${bias === "long" ? "text-green-400" : bias === "short" ? "text-red-400" : "text-neutral-500"}`}>
          {bias === "long" ? "▲ BULLISH" : bias === "short" ? "▼ BEARISH" : "— NEUTRAL"}
        </span>
        <span className="text-[11px] text-neutral-500">{met_count}/{total} conditions</span>
      </div>
      <div className="flex gap-0.5 h-1 mb-3">
        {conditions.map((c, i) => <div key={i} className="flex-1 rounded-full" style={{ background: c.met ? "#22c55e" : "#1e1e2e" }} />)}
      </div>
      {conditions.map((c, i) => (
        <div key={i} className="flex items-center gap-2">
          {c.met ? <CheckCircle2 size={11} className="text-green-400 flex-shrink-0" /> : <XCircle size={11} className="text-neutral-700 flex-shrink-0" />}
          <span className={`text-[10px] flex-1 ${c.met ? "text-neutral-200" : "text-neutral-600"}`}>{c.name}</span>
          <span className={`text-[9px] font-mono ${c.met ? "text-green-400" : "text-neutral-700"}`}>{c.value}</span>
        </div>
      ))}
    </div>
  );
}

// ─── Page ─────────────────────────────────────────────────────────────────────
export default function TradePage() {
  const [ticker, setTicker]           = useState<BinanceTicker | null>(null);
  const [orderBook, setOrderBook]     = useState<BinanceOrderBook | null>(null);
  const [candles, setCandles]         = useState<BinanceCandle[]>([]);
  const [liveCandle, setLiveCandle]   = useState<BinanceCandle | null>(null);
  const [latestSignal, setLatestSignal] = useState<Signal | null>(null);
  const [analysis, setAnalysis]       = useState<Analysis | null>(null);
  const [analysisLoading, setAnalysisLoading] = useState(true);
  const prevRef = useRef<number | null>(null);
  const [priceFlash, setPriceFlash]   = useState<"up" | "down" | null>(null);

  const apiBase = process.env.NEXT_PUBLIC_API_URL || "";
  const token   = typeof window !== "undefined" ? localStorage.getItem("token") : "";

  const authFetch = useCallback(async (url: string) => {
    try {
      const r = await fetch(`${apiBase}${url}`, { headers: { Authorization: `Bearer ${token}` } });
      return r.ok ? r.json() : null;
    } catch { return null; }
  }, [apiBase, token]);

  const fetchAll = useCallback(async () => {
    setAnalysisLoading(true);
    const [sigs, an, candles15m] = await Promise.all([
      authFetch("/api/market/signals?symbol=BTC%2FUSDT&limit=10"),
      authFetch("/api/paper/analysis"),
      authFetch("/api/market/candles/BTC%2FUSDT?timeframe=15m&limit=120"),
    ]);
    if (sigs?.length) setLatestSignal(sigs[sigs.length - 1]);
    if (an) setAnalysis(an);
    if (Array.isArray(candles15m) && candles15m.length > 0)
      setCandles(candles15m.map((c: BinanceCandle) => ({ ...c, is_closed: true })));
    setAnalysisLoading(false);
  }, [authFetch]);

  useEffect(() => {
    fetchAll();
    const i = setInterval(fetchAll, 60_000);
    return () => clearInterval(i);
  }, [fetchAll]);

  // ── Live Binance stream (BTC/USDT 15m) ───────────────────────────────────
  useBinanceStream({
    symbols: ["BTC/USDT"],
    timeframe: "15m",
    onTicker: useCallback((t: BinanceTicker) => {
      if (t.symbol !== "BTC/USDT") return;
      setTicker(t);
      if (prevRef.current !== null && t.last !== prevRef.current)
        setPriceFlash(t.last > prevRef.current ? "up" : "down");
      prevRef.current = t.last;
      setTimeout(() => setPriceFlash(null), 500);
    }, []),
    onCandle: useCallback((sym: string, c: BinanceCandle) => {
      if (sym !== "BTC/USDT") return;
      setLiveCandle(c);
      setCandles(prev => {
        if (!prev.length) return prev;
        const lastMs = new Date(prev[prev.length - 1].timestamp).getTime();
        const curMs  = new Date(c.timestamp).getTime();
        if (lastMs === curMs) return [...prev.slice(0, -1), { ...c }];
        if (curMs > lastMs)   return [...prev.slice(-119), { ...c }];
        return prev;
      });
    }, []),
    onOrderBook: useCallback((ob: BinanceOrderBook) => {
      if (ob.symbol === "BTC/USDT") setOrderBook(ob);
    }, []),
  });

  // Merge live candle for chart display
  const allCandles = liveCandle && candles.length > 0 ? (() => {
    const base = [...candles];
    const lastMs = new Date(base[base.length - 1].timestamp).getTime();
    const liveMs = new Date(liveCandle.timestamp).getTime();
    if (liveMs === lastMs) { base[base.length - 1] = { ...liveCandle, is_closed: false }; return base; }
    if (liveMs > lastMs) return [...base, { ...liveCandle, is_closed: false }];
    return base;
  })() : candles;

  return (
    <div className="p-4 space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-white flex items-center gap-2">
            BTC Perpetuals
            <span className="text-[10px] bg-violet-500/15 border border-violet-500/25 text-violet-400 px-2 py-0.5 rounded-full font-medium">via Phantom</span>
          </h1>
          <p className="text-xs text-neutral-600 mt-0.5">
            Momentum Velocity strategy · 15m · Paper signals → Phantom execution
          </p>
        </div>

        <div className="flex items-center gap-3">
          <button onClick={fetchAll} className="p-2 rounded-lg hover:bg-neutral-800 text-neutral-600 hover:text-white transition-colors">
            <RefreshCw size={14} className={analysisLoading ? "animate-spin" : ""} />
          </button>
          <button
            onClick={() => window.open(PHANTOM_PERPS_URL, "_blank", "noopener,noreferrer")}
            className="flex items-center gap-2 px-4 py-2 rounded-xl font-semibold text-[13px] transition-all"
            style={{ background: "linear-gradient(135deg, #7c3aed 0%, #5b21b6 100%)", color: "#fff", boxShadow: "0 4px 16px rgba(124,58,237,0.3)" }}>
            <Wallet size={14} />
            Open Phantom
            <ExternalLink size={12} className="opacity-70" />
          </button>
        </div>
      </div>

      {/* BTC live price bar */}
      <div className="card px-5 py-3 flex items-center gap-8">
        <div>
          <div className="text-[10px] text-neutral-600 mb-0.5">BTC/USDT</div>
          <div className="text-2xl font-mono font-bold transition-colors duration-300"
            style={{ color: priceFlash === "up" ? "#22c55e" : priceFlash === "down" ? "#ef4444" : "#f0f0f8" }}>
            {ticker ? formatUSD(ticker.last) : "—"}
          </div>
        </div>
        {ticker && (
          <>
            <div className={`flex items-center gap-1 text-sm font-semibold ${ticker.change_pct >= 0 ? "text-green-400" : "text-red-400"}`}>
              {ticker.change_pct >= 0 ? <TrendingUp size={14} /> : <TrendingDown size={14} />}
              {ticker.change_pct >= 0 ? "+" : ""}{ticker.change_pct.toFixed(2)}%
            </div>
            {[
              ["24h High", formatUSD(ticker.high_24h), "text-green-400"],
              ["24h Low",  formatUSD(ticker.low_24h),  "text-red-400"],
              ["Bid",      formatUSD(ticker.bid),       "text-neutral-300"],
              ["Ask",      formatUSD(ticker.ask),       "text-neutral-300"],
              ["Volume",   `${(ticker.quote_volume / 1_000_000).toFixed(0)}M USDT`, "text-neutral-400"],
            ].map(([label, val, cls]) => (
              <div key={label}>
                <div className="text-[9px] text-neutral-600 mb-0.5">{label}</div>
                <div className={`text-[12px] font-mono font-semibold ${cls}`}>{val}</div>
              </div>
            ))}
          </>
        )}
        {analysis && (
          <div className="ml-auto">
            <div className={`flex items-center gap-2 px-3 py-1.5 rounded-lg border text-[11px] font-bold ${
              analysis.all_met
                ? "border-green-500/30 bg-green-500/10 text-green-400"
                : "border-neutral-700 bg-neutral-800/50 text-neutral-500"}`}>
              {analysis.all_met ? <Zap size={12} className="text-green-400" /> : <Shield size={12} />}
              {analysis.met_count}/{analysis.total} conditions · {analysis.bias.toUpperCase()}
            </div>
          </div>
        )}
      </div>

      {/* Main grid */}
      <div className="grid grid-cols-12 gap-4">

        {/* Chart */}
        <div className="col-span-12 lg:col-span-7 card p-4">
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2">
              <span className="text-[13px] font-semibold text-white">BTC · 15m</span>
              <span className="text-[9px] text-neutral-700">Momentum Velocity</span>
            </div>
            <div className="flex items-center gap-1 text-[9px] text-green-400">
              <span className="w-1 h-1 rounded-full bg-green-400 animate-pulse" /> Live Binance
            </div>
          </div>
          <MiniChart candles={allCandles} signal={latestSignal} />
        </div>

        {/* Right column */}
        <div className="col-span-12 lg:col-span-5 space-y-4">

          {/* Trade setup */}
          <div className="card p-4">
            <div className="flex items-center gap-2 mb-4">
              <DollarSign size={13} className="text-neutral-500" />
              <span className="text-[13px] font-semibold text-white">Trade Setup</span>
            </div>
            <TradeSetup signal={latestSignal} ticker={ticker} analysis={analysis} />
          </div>
        </div>
      </div>

      {/* Bottom row: conditions + order book */}
      <div className="grid grid-cols-12 gap-4">
        <div className="col-span-12 lg:col-span-5 card p-4">
          <div className="flex items-center gap-2 mb-3">
            <Zap size={12} className="text-neutral-500" />
            <span className="text-[12px] font-semibold text-white">Strategy Conditions</span>
            <span className="text-[9px] text-neutral-700 ml-auto flex items-center gap-1">
              <RefreshCw size={9} className={analysisLoading ? "animate-spin" : ""} />60s cycle
            </span>
          </div>
          <ConditionsPanel analysis={analysis} loading={analysisLoading} />
        </div>

        <div className="col-span-12 lg:col-span-4 card p-4">
          <div className="flex items-center justify-between mb-3">
            <span className="text-[12px] font-semibold text-white">Order Book</span>
            {orderBook && <span className="text-[9px] text-green-400 flex items-center gap-1"><span className="w-1 h-1 rounded-full bg-green-400 animate-pulse" />100ms</span>}
          </div>
          <OrderBook ob={orderBook} />
        </div>

        <div className="col-span-12 lg:col-span-3 card p-4 flex flex-col gap-3">
          <span className="text-[12px] font-semibold text-white">Quick Links</span>
          {[
            { label: "Phantom Perps — BTC",  url: PHANTOM_PERPS_URL, color: "#7c3aed", desc: "Trade BTC perpetuals" },
            { label: "Phantom App",           url: "https://phantom.com",               color: "#5b21b6", desc: "Wallet & portfolio" },
          ].map(({ label, url, color, desc }) => (
            <a key={url} href={url} target="_blank" rel="noopener noreferrer"
              className="flex items-center gap-3 p-3 rounded-xl border border-neutral-800 hover:border-neutral-700 transition-colors group">
              <div className="w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0"
                style={{ background: `${color}20`, border: `1px solid ${color}30` }}>
                <Wallet size={14} style={{ color }} />
              </div>
              <div className="flex-1 min-w-0">
                <div className="text-[11px] font-semibold text-white group-hover:text-neutral-200">{label}</div>
                <div className="text-[9px] text-neutral-600">{desc}</div>
              </div>
              <ExternalLink size={11} className="text-neutral-700 group-hover:text-neutral-400" />
            </a>
          ))}
          <div className="mt-auto pt-2 border-t border-neutral-800">
            <div className="text-[9px] text-neutral-700 leading-relaxed">
              Signals are AI-generated from the Momentum Velocity strategy. Always use your own judgment. Not financial advice.
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
