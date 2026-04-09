"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import {
  DollarSign, TrendingUp, TrendingDown, Activity,
  Layers, BarChart2, ShieldCheck, RefreshCw, WifiOff,
  Zap, ArrowUpRight, ArrowDownRight, Bell, Bot, BookOpen,
  CheckCircle2, XCircle, Brain, Cpu, RotateCcw, X as XIcon,
  Target, Shield, FileText,
} from "lucide-react";
import { overviewApi } from "@/lib/api";
import { Overview } from "@/types";
import { KPICard } from "@/components/dashboard/KPICard";
import { formatUSD, formatPct, pnlColor, cn } from "@/lib/utils";
import { useWebSocket } from "@/hooks/useWebSocket";
import {
  useBinanceStream, BinanceCandle, BinanceTicker, BinanceOrderBook,
} from "@/hooks/useBinanceStream";
import { useStrategyEngine, type StrategyResult } from "@/hooks/useStrategyEngine";
import { useORBStrategy, type ORBResult } from "@/hooks/useORBStrategy";
import { useMasterAgent, type MasterSignal, type ConvictionGrade } from "@/hooks/useMasterAgent";

// ─── Chart constants ──────────────────────────────────────────────────────────
const CW = 1000, CH = 220, PY = 14, BAR = 5;

// ─── Types ────────────────────────────────────────────────────────────────────
interface ActivityEvent {
  kind: "signal" | "trade"; timestamp: string; symbol: string;
  direction: "long" | "short"; entry?: number; fill_price?: number;
  amount?: number; sl?: number | null; tp?: number | null;
  confidence?: number; strategy_name?: string; timeframe?: string;
}
interface Toast {
  id: number; kind: "signal" | "trade"; direction?: "long" | "short";
  symbol: string; price: number; strategy?: string;
}

// ─── Indicator math ───────────────────────────────────────────────────────────
function calcEMA(vals: number[], period: number): number[] {
  if (vals.length < period) return vals.map(() => NaN);
  const k = 2 / (period + 1);
  const out: number[] = Array(period - 1).fill(NaN);
  let prev = vals.slice(0, period).reduce((a, b) => a + b, 0) / period;
  out.push(prev);
  for (let i = period; i < vals.length; i++) { prev = vals[i] * k + prev * (1 - k); out.push(prev); }
  return out;
}
function calcVWAP(candles: BinanceCandle[], w = 50): number[] {
  return candles.map((_, i) => {
    const seg = candles.slice(Math.max(0, i - w + 1), i + 1);
    const tv = seg.reduce((s, c) => s + c.volume, 0);
    if (!tv) return (candles[i].high + candles[i].low + candles[i].close) / 3;
    return seg.reduce((s, c) => s + (c.high + c.low + c.close) / 3 * c.volume, 0) / tv;
  });
}
function calcRSI(closes: number[], period = 14): number[] {
  const n = closes.length;
  if (n <= period) return Array(n).fill(NaN);
  const out: number[] = Array(period + 1).fill(NaN);
  const d = closes.slice(1).map((c, i) => c - closes[i]);
  const g = d.map(x => Math.max(x, 0)), l = d.map(x => Math.abs(Math.min(x, 0)));
  let ag = g.slice(0, period).reduce((a, b) => a + b, 0) / period;
  let al = l.slice(0, period).reduce((a, b) => a + b, 0) / period;
  for (let i = period; i < d.length; i++) {
    ag = (ag * (period - 1) + g[i]) / period;
    al = (al * (period - 1) + l[i]) / period;
    out.push(al === 0 ? 100 : 100 - 100 / (1 + ag / al));
  }
  return out;
}

// ─── Strategy Chart ───────────────────────────────────────────────────────────
function StrategyChart({
  candles, liveCandle, signals,
}: {
  candles: BinanceCandle[];
  liveCandle: BinanceCandle | null;
  signals: Array<{ timestamp: string; direction: string; sl?: number | null; tp?: number | null }>;
}) {
  const all = liveCandle && candles.length > 0
    ? (() => {
        const base = [...candles];
        const lastMs = new Date(base[base.length - 1].timestamp).getTime();
        const liveMs = new Date(liveCandle.timestamp).getTime();
        if (liveMs === lastMs) { base[base.length - 1] = { ...liveCandle, is_closed: false }; return base; }
        if (liveMs > lastMs) return [...base, { ...liveCandle, is_closed: false }];
        return base;
      })()
    : candles;

  const visible = all.slice(-Math.floor(CW / (BAR + 2)));
  if (visible.length < 5) return (
    <div className="flex items-center justify-center h-56 text-neutral-700 text-sm gap-2">
      <RefreshCw size={14} className="animate-spin" /> Loading BTC chart…
    </div>
  );

  const closes = visible.map(c => c.close);
  const prices = visible.flatMap(c => [c.high, c.low]);
  const minP = Math.min(...prices), maxP = Math.max(...prices);
  const range = maxP - minP || 1;
  const toY = (p: number) => PY + ((maxP - p) / range) * (CH - PY * 2);

  const allCloses = all.map(c => c.close);
  const offset = all.length - visible.length;
  const ema50  = calcEMA(allCloses, 50).slice(offset);
  const ema21  = calcEMA(allCloses, 21).slice(offset);
  const vwap   = calcVWAP(all, 50).slice(offset);
  const rsi    = calcRSI(allCloses, 14).slice(offset);

  const pts = (vals: number[]) =>
    vals.map((v, i) => isNaN(v) ? null : `${i * (BAR + 2) + BAR / 2},${toY(v)}`).filter(Boolean).join(" ");

  const signalMarkers = signals.flatMap(sig => {
    const sigMs = new Date(sig.timestamp).getTime();
    let best = -1, bestDiff = Infinity;
    visible.forEach((c, i) => {
      const diff = Math.abs(new Date(c.timestamp).getTime() - sigMs);
      if (diff < bestDiff) { bestDiff = diff; best = i; }
    });
    if (best < 0 || bestDiff > 2 * 60 * 60 * 1000) return [];
    const c = visible[best];
    const isLong = sig.direction === "long";
    return [{ x: best * (BAR + 2) + BAR / 2, y: isLong ? toY(c.low) + 12 : toY(c.high) - 12, dir: sig.direction }];
  });

  const lastSig = signals[signals.length - 1];
  const lastClose = visible[visible.length - 1]?.close;
  const chartUp = lastClose >= (visible[0]?.close || lastClose);

  const RSI_H = 36;
  const toRY = (v: number) => RSI_H - (v / 100) * RSI_H;
  const rsiPts = rsi.map((v, i) => isNaN(v) ? null : `${i * (BAR + 2) + BAR / 2},${toRY(v)}`).filter(Boolean).join(" ");
  const curRsi = rsi[rsi.length - 1];

  return (
    <div className="space-y-0.5">
      <div className="relative">
        {[maxP, (maxP + minP) / 2, minP].map((p, i) => (
          <div key={i} className="absolute right-1 text-[9px] text-neutral-700 font-mono -translate-y-1/2 z-10"
            style={{ top: `${[PY / CH, 0.5, (CH - PY) / CH][i] * 100}%` }}>
            {formatUSD(p)}
          </div>
        ))}
        <svg viewBox={`0 0 ${CW} ${CH}`} className="w-full" style={{ height: CH }} preserveAspectRatio="none">
          {[0.25, 0.5, 0.75].map(f => (
            <line key={f} x1={0} x2={CW} y1={PY + f * (CH - PY * 2)} y2={PY + f * (CH - PY * 2)} stroke="#18182a" strokeWidth={1} />
          ))}
          <polyline points={pts(vwap)}  fill="none" stroke="#a78bfa" strokeWidth={1}   strokeDasharray="4 3" opacity={0.65} />
          <polyline points={pts(ema50)} fill="none" stroke="#f97316" strokeWidth={1.5} opacity={0.9} />
          <polyline points={pts(ema21)} fill="none" stroke="#38bdf8" strokeWidth={1.2} opacity={0.9} />
          {lastSig?.sl && (lastSig.sl as number) > minP && (lastSig.sl as number) < maxP && (
            <>
              <line x1={0} x2={CW} y1={toY(lastSig.sl as number)} y2={toY(lastSig.sl as number)} stroke="#ef4444" strokeWidth={1} strokeDasharray="5 3" opacity={0.6} />
              <text x={CW - 4} y={toY(lastSig.sl as number) - 3} fontSize={9} fill="#ef4444" textAnchor="end">SL</text>
            </>
          )}
          {lastSig?.tp && (lastSig.tp as number) > minP && (lastSig.tp as number) < maxP && (
            <>
              <line x1={0} x2={CW} y1={toY(lastSig.tp as number)} y2={toY(lastSig.tp as number)} stroke="#22c55e" strokeWidth={1} strokeDasharray="5 3" opacity={0.6} />
              <text x={CW - 4} y={toY(lastSig.tp as number) - 3} fontSize={9} fill="#22c55e" textAnchor="end">TP</text>
            </>
          )}
          {visible.map((c, i) => {
            const x = i * (BAR + 2), up = c.close >= c.open, color = up ? "#22c55e" : "#ef4444";
            const bTop = toY(Math.max(c.open, c.close)), bH = Math.max(toY(Math.min(c.open, c.close)) - bTop, 1);
            return (
              <g key={i}>
                <line x1={x + BAR / 2} x2={x + BAR / 2} y1={toY(c.high)} y2={toY(c.low)} stroke={color} strokeWidth={1} opacity={0.5} />
                <rect x={x} y={bTop} width={BAR} height={bH} fill={color} fillOpacity={c.is_closed === false ? 0.45 : 0.85} />
              </g>
            );
          })}
          {lastClose && <line x1={0} x2={CW} y1={toY(lastClose)} y2={toY(lastClose)} stroke={chartUp ? "#22c55e" : "#ef4444"} strokeWidth={1} strokeDasharray="3 3" opacity={0.5} />}
          {signalMarkers.map((m, i) => {
            const isLong = m.dir === "long", color = isLong ? "#22c55e" : "#ef4444";
            const path = isLong
              ? `M ${m.x} ${m.y - 6} L ${m.x + 5} ${m.y + 3} L ${m.x - 5} ${m.y + 3} Z`
              : `M ${m.x} ${m.y + 6} L ${m.x + 5} ${m.y - 3} L ${m.x - 5} ${m.y - 3} Z`;
            return <g key={i}><circle cx={m.x} cy={m.y} r={9} fill={color} opacity={0.12} /><path d={path} fill={color} opacity={0.95} /></g>;
          })}
        </svg>
        <div className="absolute top-1 left-2 flex items-center gap-3 text-[9px] font-mono text-neutral-700">
          <span className="flex items-center gap-1"><span className="w-3 h-px bg-orange-400 inline-block" />EMA50</span>
          <span className="flex items-center gap-1"><span className="w-3 h-px bg-sky-400 inline-block" />EMA21</span>
          <span className="flex items-center gap-1"><span className="w-3 h-px bg-violet-400 inline-block opacity-60" />VWAP</span>
        </div>
      </div>
      {/* RSI panel */}
      <div className="relative" style={{ height: RSI_H }}>
        <svg viewBox={`0 0 ${CW} ${RSI_H}`} className="w-full" style={{ height: RSI_H }} preserveAspectRatio="none">
          <line x1={0} x2={CW} y1={toRY(70)} y2={toRY(70)} stroke="#ef444428" strokeWidth={1} />
          <line x1={0} x2={CW} y1={toRY(50)} y2={toRY(50)} stroke="#52525250" strokeWidth={1} />
          <line x1={0} x2={CW} y1={toRY(30)} y2={toRY(30)} stroke="#22c55e28" strokeWidth={1} />
          {rsiPts && <polyline points={rsiPts} fill="none" stroke="#a78bfa" strokeWidth={1.5} />}
          {!isNaN(curRsi) && <text x={CW - 4} y={toRY(curRsi)} fontSize={8} fill="#a78bfa" textAnchor="end">{curRsi.toFixed(1)}</text>}
        </svg>
        <div className="absolute left-1 top-0 text-[8px] text-neutral-700 font-mono">RSI</div>
      </div>
      {/* Volume */}
      <div className="flex items-end gap-px" style={{ height: 20 }}>
        {visible.slice(-Math.floor(CW / (BAR + 2))).map((c, i) => {
          const maxV = Math.max(...visible.map(x => x.volume)) || 1;
          return (
            <div key={i} className="flex-1 min-w-0 rounded-t"
              style={{ height: `${Math.max((c.volume / maxV) * 100, 4)}%`, background: c.close >= c.open ? "rgba(34,197,94,0.25)" : "rgba(239,68,68,0.25)" }} />
          );
        })}
      </div>
    </div>
  );
}

// ─── Analysis Panel (pure frontend, no backend) ───────────────────────────────
function AnalysisPanel({ result, candleCount }: { result: StrategyResult; candleCount: number }) {
  const { conditions, indicators, bias, met_count, total, all_met } = result;
  const biasCls = bias === "long" ? "text-green-400" : bias === "short" ? "text-red-400" : "text-neutral-500";

  if (candleCount < 60) return (
    <div className="flex flex-col items-center justify-center h-48 gap-2 text-neutral-700">
      <RefreshCw size={14} className="animate-spin" />
      <span className="text-xs">Loading candles… ({candleCount}/60)</span>
    </div>
  );

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <div>
          <div className="text-[9px] text-neutral-600 uppercase tracking-wider mb-0.5">Bias</div>
          <div className={`text-sm font-bold uppercase ${biasCls}`}>
            {bias === "long" ? "▲ BULLISH" : bias === "short" ? "▼ BEARISH" : "— NEUTRAL"}
          </div>
        </div>
        <div className="text-right">
          <div className="text-[9px] text-neutral-600 mb-0.5">Conditions</div>
          <div className="flex items-center gap-1 justify-end">
            <span className={`text-xl font-bold ${all_met ? "text-green-400" : "text-neutral-300"}`}>{met_count}</span>
            <span className="text-neutral-600 text-sm">/ {total}</span>
          </div>
        </div>
      </div>

      <div className="flex gap-0.5 h-1">
        {Array.from({ length: 7 }).map((_, i) => (
          <div key={i} className="flex-1 rounded-full" style={{ background: i < met_count ? "#22c55e" : "#1e1e2e" }} />
        ))}
      </div>

      <div className="space-y-1">
        {conditions.map((c, i) => (
          <div key={i} className="flex items-center gap-2">
            {c.met
              ? <CheckCircle2 size={11} className="text-green-400 flex-shrink-0" />
              : <XCircle     size={11} className="text-neutral-700 flex-shrink-0" />}
            <span className={`text-[10px] flex-1 ${c.met ? "text-neutral-300" : "text-neutral-600"}`}>{c.name}</span>
            <span className={`text-[9px] font-mono ${c.met ? "text-green-400" : "text-neutral-700"}`}>{c.value}</span>
          </div>
        ))}
      </div>

      <div className="grid grid-cols-3 gap-x-3 gap-y-1 pt-2 border-t border-neutral-800">
        {[
          ["RSI",    indicators.rsi       != null ? indicators.rsi.toFixed(1)              : "—"],
          ["Vol×",   indicators.vol_ratio != null ? indicators.vol_ratio.toFixed(2) + "×"  : "—"],
          ["ATR%",   indicators.atr_pct   != null ? indicators.atr_pct.toFixed(3) + "%"    : "—"],
          ["EMA50",  indicators.ema50     != null ? formatUSD(indicators.ema50)            : "—"],
          ["EMA21",  indicators.ema21     != null ? formatUSD(indicators.ema21)            : "—"],
          ["VWAP",   indicators.vwap      != null ? formatUSD(indicators.vwap)             : "—"],
        ].map(([k, v]) => (
          <div key={k} className="flex justify-between items-center">
            <span className="text-[9px] text-neutral-700">{k}</span>
            <span className="text-[9px] font-mono text-neutral-500">{v}</span>
          </div>
        ))}
      </div>

      {all_met && (
        <div className="flex items-center gap-1.5 p-2 rounded border border-green-500/30 bg-green-500/5">
          <Zap size={10} className="text-green-400" />
          <span className="text-[10px] text-green-400 font-semibold">All conditions met — trade imminent</span>
        </div>
      )}
    </div>
  );
}

// ─── Live Agent Signal Box ────────────────────────────────────────────────────
function LiveAgentSignal({ result }: { result: StrategyResult }) {
  const { signal, bias, met_count, total } = result;
  const isLong = signal?.direction === "long";

  if (!signal) {
    return (
      <div className="card p-4">
        <div className="flex items-center gap-2 mb-3">
          <div className="w-2 h-2 rounded-full bg-neutral-700 animate-pulse" />
          <span className="text-[12px] font-semibold text-white">Live Agent</span>
          <span className="text-[9px] text-neutral-700 ml-auto flex items-center gap-1">
            <RefreshCw size={9} /> live · every bar
          </span>
        </div>
        <div className="flex items-center gap-3 py-3">
          <div className={`w-10 h-10 rounded-xl flex items-center justify-center ${bias === "long" ? "bg-green-500/10" : bias === "short" ? "bg-red-500/10" : "bg-neutral-800"}`}>
            {bias === "long" ? <TrendingUp size={18} className="text-green-400" /> : bias === "short" ? <TrendingDown size={18} className="text-red-400" /> : <Activity size={18} className="text-neutral-600" />}
          </div>
          <div>
            <div className={`text-sm font-bold ${bias === "long" ? "text-green-400" : bias === "short" ? "text-red-400" : "text-neutral-500"}`}>
              {bias === "long" ? "▲ BULLISH BIAS" : bias === "short" ? "▼ BEARISH BIAS" : "SCANNING MARKET"}
            </div>
            <div className="text-[10px] text-neutral-600 mt-0.5">{met_count}/{total} conditions met · waiting for full setup</div>
          </div>
        </div>
        <div className="flex gap-0.5 h-1 mt-1">
          {Array.from({ length: total || 7 }).map((_, i) => (
            <div key={i} className="flex-1 rounded-full" style={{ background: i < met_count ? (bias === "long" ? "#22c55e" : "#ef4444") : "#1e1e2e" }} />
          ))}
        </div>
        <div className="text-[9px] text-neutral-700 mt-2 text-center">Signal fires when all 7 conditions align</div>
      </div>
    );
  }

  return (
    <div className={`card p-4 border ${isLong ? "border-green-500/20" : "border-red-500/20"}`}
      style={{ background: isLong ? "rgba(34,197,94,0.03)" : "rgba(239,68,68,0.03)" }}>
      <div className="flex items-center gap-2 mb-3">
        <div className="w-2 h-2 rounded-full animate-ping" style={{ background: isLong ? "#22c55e" : "#ef4444" }} />
        <span className="text-[12px] font-semibold text-white">Live Agent</span>
        <span className={`text-[9px] font-bold px-2 py-0.5 rounded border ml-auto ${isLong ? "border-green-500/30 bg-green-500/10 text-green-400" : "border-red-500/30 bg-red-500/10 text-red-400"}`}>
          SIGNAL ACTIVE
        </span>
      </div>

      {/* Direction */}
      <div className="flex items-center gap-3 mb-4">
        <div className={`w-12 h-12 rounded-xl flex items-center justify-center ${isLong ? "bg-green-500/15" : "bg-red-500/15"}`}>
          {isLong ? <ArrowUpRight size={22} className="text-green-400" /> : <ArrowDownRight size={22} className="text-red-400" />}
        </div>
        <div>
          <div className={`text-xl font-bold ${isLong ? "text-green-400" : "text-red-400"}`}>
            {isLong ? "LONG BTC" : "SHORT BTC"}
          </div>
          <div className="text-[10px] text-neutral-500">BTC/USDT · 15m · Momentum Velocity</div>
        </div>
        <div className="ml-auto text-right">
          <div className="text-[9px] text-neutral-600">Confidence</div>
          <div className="text-lg font-bold text-yellow-400">{(signal.confidence * 100).toFixed(0)}%</div>
        </div>
      </div>

      {/* Levels */}
      <div className="grid grid-cols-3 gap-2 mb-3">
        <div className="bg-blue-500/5 border border-blue-500/15 rounded-lg p-2.5 text-center">
          <div className="text-[9px] text-blue-400/70 mb-0.5">Entry</div>
          <div className="text-[12px] font-mono font-bold text-blue-300">{formatUSD(signal.entry)}</div>
        </div>
        <div className="bg-red-500/5 border border-red-500/15 rounded-lg p-2.5 text-center">
          <div className="text-[9px] text-red-400/70 mb-0.5">Stop Loss</div>
          <div className="text-[12px] font-mono font-bold text-red-400">{formatUSD(signal.sl)}</div>
          <div className="text-[8px] text-red-400/50">{isLong ? "-" : "+"}{formatUSD(Math.abs(signal.sl - signal.entry))}</div>
        </div>
        <div className="bg-green-500/5 border border-green-500/15 rounded-lg p-2.5 text-center">
          <div className="text-[9px] text-green-400/70 mb-0.5">Take Profit</div>
          <div className="text-[12px] font-mono font-bold text-green-400">{formatUSD(signal.tp)}</div>
          <div className="text-[8px] text-green-400/50">{isLong ? "+" : "-"}{formatUSD(Math.abs(signal.tp - signal.entry))}</div>
        </div>
      </div>

      {/* R:R */}
      <div className="flex items-center gap-2 mb-3">
        <div className="flex-1 h-1.5 bg-neutral-800 rounded-full overflow-hidden">
          <div className="h-full bg-gradient-to-r from-red-400 to-green-400" style={{ width: "67%" }} />
        </div>
        <span className="text-[9px] font-mono text-yellow-400 font-bold">R:R {signal.rr}</span>
      </div>

      {/* Reasoning */}
      <div className="text-[9px] text-neutral-600 leading-relaxed italic bg-neutral-900 rounded-lg p-2">
        {signal.reasoning}
      </div>
    </div>
  );
}

// ─── BTC Market Panel ─────────────────────────────────────────────────────────
function BTCMarketPanel({ ticker, orderBook }: { ticker: BinanceTicker | null; orderBook: BinanceOrderBook | null }) {
  const prevRef = useRef<number | null>(null);
  const [flash, setFlash] = useState<"up" | "down" | null>(null);
  useEffect(() => {
    if (!ticker) return;
    if (prevRef.current !== null && ticker.last !== prevRef.current)
      setFlash(ticker.last > prevRef.current ? "up" : "down");
    prevRef.current = ticker.last;
    const t = setTimeout(() => setFlash(null), 500);
    return () => clearTimeout(t);
  }, [ticker?.last]);

  return (
    <div className="space-y-3">
      {/* Price */}
      <div>
        <div className="flex items-end gap-3">
          <span className="text-2xl font-mono font-bold transition-colors duration-300"
            style={{ color: flash === "up" ? "#22c55e" : flash === "down" ? "#ef4444" : "#f0f0f8" }}>
            {ticker ? formatUSD(ticker.last) : "—"}
          </span>
          {ticker && (
            <span className={`text-sm font-semibold mb-0.5 ${ticker.change_pct >= 0 ? "text-green-400" : "text-red-400"}`}>
              {ticker.change_pct >= 0 ? "+" : ""}{ticker.change_pct.toFixed(2)}%
            </span>
          )}
        </div>
        <div className="text-[10px] text-neutral-600 mt-0.5">Bitcoin · USDT Pair</div>
      </div>

      {/* Stats grid */}
      <div className="grid grid-cols-2 gap-2">
        {[
          ["24h High",   ticker ? formatUSD(ticker.high_24h)    : "—", "text-green-400"],
          ["24h Low",    ticker ? formatUSD(ticker.low_24h)     : "—", "text-red-400"],
          ["Bid",        ticker ? formatUSD(ticker.bid)         : "—", "text-neutral-300"],
          ["Ask",        ticker ? formatUSD(ticker.ask)         : "—", "text-neutral-300"],
          ["Volume",     ticker ? `${(ticker.volume / 1000).toFixed(1)}K BTC` : "—", "text-neutral-400"],
          ["Quote Vol",  ticker ? `$${(ticker.quote_volume / 1_000_000).toFixed(0)}M` : "—", "text-neutral-400"],
        ].map(([label, val, cls]) => (
          <div key={label} className="bg-neutral-900/60 rounded-lg p-2">
            <div className="text-[9px] text-neutral-600 mb-0.5">{label}</div>
            <div className={`text-[11px] font-mono font-semibold ${cls}`}>{val}</div>
          </div>
        ))}
      </div>

      {/* Order Book */}
      <div>
        <div className="flex items-center justify-between mb-1.5">
          <div className="flex items-center gap-1.5">
            <BookOpen size={11} className="text-neutral-600" />
            <span className="text-[10px] text-neutral-500 uppercase tracking-wider font-medium">Order Book</span>
          </div>
          {orderBook && (
            <span className="text-[9px] text-green-400 flex items-center gap-1">
              <span className="w-1 h-1 rounded-full bg-green-400 animate-pulse" />100ms
            </span>
          )}
        </div>

        {!orderBook ? (
          <div className="text-center py-4 text-[11px] text-neutral-700">Connecting…</div>
        ) : (() => {
          const asks = orderBook.asks.slice(0, 8);
          const bids = orderBook.bids.slice(0, 8);
          const maxSz = Math.max(...asks.map(a => a.amount), ...bids.map(b => b.amount)) || 1;
          const spread = asks.length && bids.length ? asks[0].price - bids[0].price : 0;
          return (
            <div className="space-y-px">
              <div className="flex justify-between text-[9px] text-neutral-700 font-mono px-1 mb-1">
                <span>Price</span><span>Size</span>
              </div>
              {[...asks].reverse().map((a, i) => (
                <div key={i} className="relative flex justify-between text-[10px] font-mono h-5 items-center overflow-hidden rounded-sm">
                  <div className="absolute right-0 top-0 bottom-0 bg-red-500/10" style={{ width: `${(a.amount / maxSz) * 100}%` }} />
                  <span className="relative text-red-400 pl-1">{formatUSD(a.price)}</span>
                  <span className="relative text-neutral-500 pr-1">{a.amount.toFixed(3)}</span>
                </div>
              ))}
              <div className="py-0.5 text-center text-[9px] text-neutral-600 border-y border-neutral-800 font-mono my-0.5">
                Spread {formatUSD(spread)} · {bids.length ? ((spread / bids[0].price) * 100).toFixed(3) : "0"}%
              </div>
              {bids.map((b, i) => (
                <div key={i} className="relative flex justify-between text-[10px] font-mono h-5 items-center overflow-hidden rounded-sm">
                  <div className="absolute left-0 top-0 bottom-0 bg-green-500/10" style={{ width: `${(b.amount / maxSz) * 100}%` }} />
                  <span className="relative text-green-400 pl-1">{formatUSD(b.price)}</span>
                  <span className="relative text-neutral-500 pr-1">{b.amount.toFixed(3)}</span>
                </div>
              ))}
            </div>
          );
        })()}
      </div>
    </div>
  );
}

// ─── Toasts ───────────────────────────────────────────────────────────────────
function ToastNotification({ toast, onDismiss }: { toast: Toast; onDismiss: () => void }) {
  useEffect(() => { const t = setTimeout(onDismiss, 7000); return () => clearTimeout(t); }, [onDismiss]);
  const isLong = toast.direction === "long", isTrade = toast.kind === "trade";
  return (
    <div className="flex items-start gap-3 p-3 rounded-xl border shadow-2xl animate-slide-in"
      style={{ background: isTrade ? (isLong ? "rgba(34,197,94,.10)" : "rgba(239,68,68,.10)") : "rgba(10,132,255,.10)", borderColor: isTrade ? (isLong ? "rgba(34,197,94,.25)" : "rgba(239,68,68,.25)") : "rgba(10,132,255,.25)", minWidth: 260 }}>
      {isTrade ? (isLong ? <ArrowUpRight size={15} className="text-green-400 mt-0.5" /> : <ArrowDownRight size={15} className="text-red-400 mt-0.5" />) : <Zap size={15} className="text-blue-400 mt-0.5" />}
      <div className="flex-1 min-w-0">
        <div className={`text-[10px] font-bold uppercase tracking-wide ${isTrade ? (isLong ? "text-green-400" : "text-red-400") : "text-blue-400"}`}>
          {isTrade ? (isLong ? "▲ LONG FILLED" : "▼ SHORT FILLED") : "⚡ SIGNAL"} · BTC
        </div>
        <div className="text-[13px] font-mono font-semibold text-white mt-0.5">{formatUSD(toast.price)}</div>
        {toast.strategy && <div className="text-[9px] text-neutral-600 truncate">{toast.strategy}</div>}
      </div>
      <button onClick={onDismiss} className="text-neutral-600 hover:text-neutral-400 text-xs">✕</button>
    </div>
  );
}
function ToastContainer({ toasts, onDismiss }: { toasts: Toast[]; onDismiss: (id: number) => void }) {
  return (
    <div className="fixed bottom-6 right-6 z-50 flex flex-col gap-2 pointer-events-none">
      {toasts.map(t => <div key={t.id} className="pointer-events-auto"><ToastNotification toast={t} onDismiss={() => onDismiss(t.id)} /></div>)}
    </div>
  );
}

// ─── Activity Row ─────────────────────────────────────────────────────────────
function ActivityRow({ ev }: { ev: ActivityEvent }) {
  const isLong = ev.direction === "long", isTrade = ev.kind === "trade";
  const ts = new Date(ev.timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  return (
    <div className="flex items-center gap-2 px-4 py-2 border-b last:border-0 hover:bg-white/[0.02]" style={{ borderColor: "rgba(255,255,255,0.04)" }}>
      <div className={`w-4 h-4 rounded-full flex items-center justify-center flex-shrink-0 ${isTrade ? (isLong ? "bg-green-500/15" : "bg-red-500/15") : "bg-blue-500/15"}`}>
        {isTrade ? (isLong ? <ArrowUpRight size={9} className="text-green-400" /> : <ArrowDownRight size={9} className="text-red-400" />) : <Zap size={9} className="text-blue-400" />}
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-1.5">
          <span className={`text-[10px] font-bold ${isLong ? "text-green-400" : "text-red-400"}`}>{isLong ? "▲" : "▼"}</span>
          <span className="text-[11px] font-semibold text-white">BTC</span>
          {isTrade && <span className="text-[8px] bg-green-500/10 border border-green-500/20 text-green-400 px-1 rounded">LIVE</span>}
        </div>
        <div className="flex gap-2 text-[9px] font-mono">
          <span className="text-neutral-500">{formatUSD(isTrade ? (ev.fill_price || 0) : (ev.entry || 0))}</span>
          {ev.sl && ev.sl > 0 && <span className="text-red-400">SL {formatUSD(ev.sl)}</span>}
          {ev.tp && ev.tp > 0 && <span className="text-green-400">TP {formatUSD(ev.tp)}</span>}
        </div>
      </div>
      <span className="text-[9px] font-mono text-neutral-700 flex-shrink-0">{ts}</span>
    </div>
  );
}


// ─── All Live Agents Panel ────────────────────────────────────────────────────
function AgentCard({
  name, timeframe, strategy, result, candleCount, ticker,
}: {
  name: string; timeframe: string; strategy: string;
  result: StrategyResult; candleCount: number;
  ticker?: BinanceTicker | null;
}) {
  const sig = result.signal;
  const isReady = candleCount >= 60;
  const isLong  = result.bias === "long";
  const isShort = result.bias === "short";

  const biasColor = isLong ? "#22c55e" : isShort ? "#ef4444" : "#4b5563";
  const biasBg    = isLong ? "rgba(34,197,94,.07)" : isShort ? "rgba(239,68,68,.07)" : "rgba(255,255,255,.02)";
  const biasBdr   = isLong ? "rgba(34,197,94,.18)" : isShort ? "rgba(239,68,68,.18)" : "rgba(255,255,255,.06)";

  return (
    <div className="card p-4 flex flex-col gap-3" style={{ minHeight: 210 }}>
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <div className="w-7 h-7 rounded-xl flex items-center justify-center" style={{ background: "rgba(10,132,255,.12)", border: "1px solid rgba(10,132,255,.2)" }}>
            <Bot size={13} style={{ color: "#60aaff" }} />
          </div>
          <div>
            <div className="text-[12px] font-semibold text-white leading-none">{name}</div>
            <div className="text-[9px] text-neutral-700 mt-0.5">{strategy} · {timeframe}</div>
          </div>
        </div>
        <div className="flex items-center gap-1.5">
          <span className="w-1.5 h-1.5 rounded-full animate-pulse" style={{ background: isReady ? "#22c55e" : "#f59e0b" }} />
          <span className="text-[9px]" style={{ color: isReady ? "#22c55e" : "#f59e0b" }}>
            {isReady ? "SCANNING" : `${candleCount}/60`}
          </span>
        </div>
      </div>

      {/* Bias pill */}
      <div className="flex items-center justify-between rounded-xl px-3 py-2"
        style={{ background: biasBg, border: `1px solid ${biasBdr}` }}>
        <span className="text-[11px] font-bold" style={{ color: biasColor }}>
          {isLong ? "▲ BULLISH" : isShort ? "▼ BEARISH" : "— NEUTRAL"}
        </span>
        <span className="text-[10px] text-neutral-600">{result.met_count}/{result.total ?? 7} conditions</span>
      </div>

      {/* Conditions mini bar */}
      <div className="flex gap-0.5 h-1">
        {Array.from({ length: result.total ?? 7 }).map((_, i) => (
          <div key={i} className="flex-1 rounded-full transition-all duration-300"
            style={{ background: i < result.met_count ? biasColor : "#1e1e2e" }} />
        ))}
      </div>

      {/* Signal card or waiting */}
      {sig ? (
        <div className="rounded-xl p-3 space-y-1.5"
          style={{ background: sig.direction === "long" ? "rgba(34,197,94,.06)" : "rgba(239,68,68,.06)", border: `1px solid ${sig.direction === "long" ? "rgba(34,197,94,.2)" : "rgba(239,68,68,.2)"}` }}>
          <div className="flex items-center justify-between">
            <span className={`text-[10px] font-bold ${sig.direction === "long" ? "text-green-400" : "text-red-400"}`}>
              {sig.direction === "long" ? "▲ LONG" : "▼ SHORT"} SIGNAL
            </span>
            <span className="text-[9px] font-mono text-neutral-500">{(sig.confidence * 100).toFixed(0)}% conf</span>
          </div>
          <div className="grid grid-cols-3 gap-1 text-[9px] font-mono">
            <div><span className="text-neutral-700">Entry</span><br /><span className="text-white">{formatUSD(sig.entry)}</span></div>
            <div><span className="text-red-400">SL</span><br /><span className="text-red-300">{formatUSD(sig.sl)}</span></div>
            <div><span className="text-green-400">TP</span><br /><span className="text-green-300">{formatUSD(sig.tp)}</span></div>
          </div>
          {sig.rr && <div className="text-[9px] text-neutral-600">R:R {sig.rr}</div>}
        </div>
      ) : (
        <div className="flex-1 flex items-center justify-center text-center">
          <div>
            <div className="text-[10px] text-neutral-700">Monitoring market…</div>
            {ticker && <div className="text-[11px] font-mono text-neutral-500 mt-0.5">{formatUSD(ticker.last)}</div>}
          </div>
        </div>
      )}
    </div>
  );
}

// ─── Master Agent Panel ───────────────────────────────────────────────────────
const GRADE_META: Record<ConvictionGrade, { color: string; bg: string; border: string; label: string }> = {
  "A+": { color: "#22c55e", bg: "rgba(34,197,94,0.08)",  border: "rgba(34,197,94,0.25)",  label: "ULTRA HIGH" },
  "A":  { color: "#0a84ff", bg: "rgba(10,132,255,0.08)", border: "rgba(10,132,255,0.25)", label: "HIGH" },
  "B":  { color: "#f59e0b", bg: "rgba(245,158,11,0.08)", border: "rgba(245,158,11,0.25)", label: "MODERATE" },
  "C":  { color: "#8b5cf6", bg: "rgba(139,92,246,0.08)", border: "rgba(139,92,246,0.25)", label: "LOW" },
  "X":  { color: "#4b5563", bg: "rgba(75,85,99,0.06)",   border: "rgba(75,85,99,0.15)",   label: "NO TRADE" },
};

function MasterAgentPanel({ agent, strategyResult, orbResult }: {
  agent: ReturnType<typeof useMasterAgent>;
  strategyResult: StrategyResult;
  orbResult: ORBResult;
}) {
  const { direction, conviction, grade, signal, votes, consensus_count, regime, thoughts,
          paper_position, paper_stats, last_price, price_24h } = agent;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const { openPaperTrade, closePaperTrade, resetPaper } = agent as any;

  const gm = GRADE_META[grade];
  const isLong  = direction === "LONG";
  const isShort = direction === "SHORT";
  const dirColor = isLong ? "#22c55e" : isShort ? "#ef4444" : "#4b5563";
  const pnlColor = (v: number) => v > 0 ? "text-green-400" : v < 0 ? "text-red-400" : "text-neutral-500";

  return (
    <div className="rounded-2xl overflow-hidden" style={{ background: "#080810", border: "1px solid rgba(255,255,255,0.07)" }}>
      {/* Header bar */}
      <div className="flex items-center justify-between px-5 py-3" style={{ borderBottom: "1px solid rgba(255,255,255,0.06)", background: "rgba(0,0,0,0.4)" }}>
        <div className="flex items-center gap-3">
          <div className="relative">
            <Brain size={20} style={{ color: gm.color }} />
            {grade !== "X" && <span className="absolute -top-0.5 -right-0.5 w-2 h-2 rounded-full animate-ping" style={{ background: gm.color, opacity: 0.5 }} />}
            {grade !== "X" && <span className="absolute -top-0.5 -right-0.5 w-2 h-2 rounded-full" style={{ background: gm.color }} />}
          </div>
          <div>
            <div className="text-[13px] font-bold text-white tracking-wide flex items-center gap-2">
              MASTER AGENT
              <span className="text-[9px] px-2 py-0.5 rounded font-mono" style={{ background: gm.bg, border: `1px solid ${gm.border}`, color: gm.color }}>
                {gm.label}
              </span>
            </div>
            <div className="text-[9px] text-neutral-600">Multi-strategy confluence · Hedge fund analytics · Live paper trading</div>
          </div>
        </div>

        <div className="flex items-center gap-6">
          {/* Regime */}
          <div className="text-center">
            <div className="text-[8px] text-neutral-700">REGIME</div>
            <div className={`text-[10px] font-bold ${regime === "TRENDING" ? "text-blue-400" : regime === "VOLATILE" ? "text-red-400" : regime === "RANGING" ? "text-yellow-400" : "text-neutral-600"}`}>
              {regime}
            </div>
          </div>
          {/* Live price */}
          {last_price && (
            <div className="text-center">
              <div className="text-[8px] text-neutral-700">BTC PRICE</div>
              <div className="text-[11px] font-mono font-bold text-white">${last_price.toLocaleString()}</div>
              {price_24h != null && (
                <div className={`text-[8px] ${price_24h >= 0 ? "text-green-400" : "text-red-400"}`}>
                  {price_24h >= 0 ? "+" : ""}{price_24h.toFixed(2)}%
                </div>
              )}
            </div>
          )}
          {/* Consensus */}
          <div className="text-center">
            <div className="text-[8px] text-neutral-700">CONSENSUS</div>
            <div className="flex gap-1 mt-1 justify-center">
              {votes.map((v, i) => {
                const aligned = direction !== "FLAT" && v.bias === direction.toLowerCase();
                return (
                  <div key={i} className="w-2 h-2 rounded-full" title={v.name}
                    style={{ background: aligned ? gm.color : v.bias !== "neutral" ? "#4b5563" : "#1e1e2e" }} />
                );
              })}
            </div>
            <div className="text-[8px] text-neutral-600 mt-0.5">{consensus_count}/3 agree</div>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-12 gap-0">

        {/* Left: Conviction + Signal + Paper P&L */}
        <div className="col-span-12 lg:col-span-4 p-4 space-y-3" style={{ borderRight: "1px solid rgba(255,255,255,0.05)" }}>

          {/* Direction + Conviction gauge */}
          <div className="rounded-xl p-4 text-center space-y-2" style={{ background: gm.bg, border: `1px solid ${gm.border}` }}>
            <div className="text-[11px] font-semibold" style={{ color: gm.color }}>
              {direction === "LONG" ? "▲ " : direction === "SHORT" ? "▼ " : "— "}{direction} · GRADE {grade}
            </div>
            {/* Conviction bar */}
            <div className="space-y-1">
              <div className="flex justify-between text-[9px]">
                <span className="text-neutral-700">Conviction</span>
                <span className="font-mono" style={{ color: gm.color }}>{conviction}/100</span>
              </div>
              <div className="h-2 bg-neutral-900 rounded-full overflow-hidden">
                <div className="h-full rounded-full transition-all duration-700"
                  style={{ width: `${conviction}%`, background: `linear-gradient(90deg, ${gm.color}88, ${gm.color})` }} />
              </div>
            </div>
            {/* Grade scale */}
            <div className="flex justify-between text-[8px] text-neutral-800">
              <span>X</span><span>C</span><span>B</span><span>A</span><span>A+</span>
            </div>
          </div>

          {/* Strategy votes */}
          <div className="space-y-1.5">
            {votes.map(v => {
              const aligned = direction !== "FLAT" && v.bias === direction.toLowerCase();
              return (
                <div key={v.name} className="flex items-center gap-2 px-2.5 py-2 rounded-lg" style={{ background: "rgba(255,255,255,0.02)", border: "1px solid rgba(255,255,255,0.04)" }}>
                  <div className="w-1.5 h-1.5 rounded-full flex-shrink-0" style={{ background: aligned ? gm.color : v.bias !== "neutral" ? "#4b5563" : "#1e1e2e" }} />
                  <span className="text-[9px] text-neutral-500 flex-1">{v.name}</span>
                  <span className={`text-[9px] font-bold ${v.bias === "long" ? "text-green-400" : v.bias === "short" ? "text-red-400" : "text-neutral-700"}`}>
                    {v.bias === "long" ? "▲ LONG" : v.bias === "short" ? "▼ SHORT" : "NEUTRAL"}
                  </span>
                  <span className="text-[8px] font-mono text-neutral-700">{(v.met_pct * 100).toFixed(0)}%</span>
                </div>
              );
            })}
          </div>

          {/* Master signal card */}
          {signal ? (
            <div className="rounded-xl p-3 space-y-2" style={{ background: isLong ? "rgba(34,197,94,0.06)" : "rgba(239,68,68,0.06)", border: `1px solid ${isLong ? "rgba(34,197,94,0.2)" : "rgba(239,68,68,0.2)"}` }}>
              <div className="flex items-center justify-between">
                <span className={`text-[10px] font-bold ${isLong ? "text-green-400" : "text-red-400"}`}>
                  {isLong ? "▲ LONG" : "▼ SHORT"} SIGNAL · {grade}
                </span>
                <span className="text-[8px] text-neutral-600">{signal.size_pct}% size</span>
              </div>
              <div className="grid grid-cols-3 gap-1 text-[9px] font-mono">
                <div><span className="text-neutral-700 block">Entry</span><span className="text-white">${signal.entry.toFixed(0)}</span></div>
                <div><span className="text-red-400 block">SL</span><span className="text-red-300">${signal.sl.toFixed(0)}</span></div>
                <div><span className="text-green-400 block">TP</span><span className="text-green-300">${signal.tp.toFixed(0)}</span></div>
              </div>
              <div className="text-[8px] text-neutral-600">R:R {signal.rr}</div>
              {!paper_position?.open && (
                <button onClick={() => openPaperTrade(signal)}
                  className="w-full py-1.5 rounded-lg text-[9px] font-bold text-white transition-colors"
                  style={{ background: isLong ? "rgba(34,197,94,0.2)" : "rgba(239,68,68,0.2)", border: `1px solid ${isLong ? "rgba(34,197,94,0.3)" : "rgba(239,68,68,0.3)"}` }}>
                  <FileText size={9} className="inline mr-1" />Paper Trade
                </button>
              )}
            </div>
          ) : (
            <div className="flex items-center gap-2 px-3 py-2.5 rounded-xl" style={{ background: "rgba(255,255,255,0.02)", border: "1px solid rgba(255,255,255,0.04)" }}>
              <Cpu size={11} className="text-neutral-700" />
              <span className="text-[10px] text-neutral-700">Scanning for high-conviction setup…</span>
            </div>
          )}

          {/* Paper stats */}
          <div className="rounded-xl p-3 space-y-2" style={{ background: "rgba(139,92,246,0.04)", border: "1px solid rgba(139,92,246,0.12)" }}>
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-1.5">
                <FileText size={10} className="text-violet-400" />
                <span className="text-[10px] font-semibold text-white">Paper P&L</span>
              </div>
              <button onClick={resetPaper} className="text-[8px] text-neutral-700 hover:text-red-400 flex items-center gap-1 transition-colors">
                <RotateCcw size={8} />Reset
              </button>
            </div>
            <div className="grid grid-cols-2 gap-2">
              {[
                ["Total P&L", `${paper_stats.total_pnl >= 0 ? "+" : ""}$${paper_stats.total_pnl.toFixed(2)}`, pnlColor(paper_stats.total_pnl)],
                ["Win Rate",  paper_stats.total_trades > 0 ? `${paper_stats.win_rate.toFixed(1)}%` : "—", "text-blue-400"],
                ["Wins/Loss", `${paper_stats.wins}W / ${paper_stats.losses}L`, "text-neutral-400"],
                ["Trades",    `${paper_stats.total_trades}`, "text-neutral-500"],
              ].map(([l, v, cls]) => (
                <div key={l} className="text-center rounded-lg py-1.5" style={{ background: "rgba(255,255,255,0.02)" }}>
                  <div className="text-[8px] text-neutral-700">{l}</div>
                  <div className={`text-[10px] font-bold font-mono ${cls}`}>{v}</div>
                </div>
              ))}
            </div>

            {/* Open position */}
            {paper_position?.open && (
              <div className="rounded-lg p-2.5 space-y-1.5" style={{ background: paper_position.direction === "LONG" ? "rgba(34,197,94,0.06)" : "rgba(239,68,68,0.06)", border: `1px solid ${paper_position.direction === "LONG" ? "rgba(34,197,94,0.18)" : "rgba(239,68,68,0.18)"}` }}>
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-1.5">
                    <div className={`w-1.5 h-1.5 rounded-full animate-pulse ${paper_position.direction === "LONG" ? "bg-green-400" : "bg-red-400"}`} />
                    <span className={`text-[9px] font-bold ${paper_position.direction === "LONG" ? "text-green-400" : "text-red-400"}`}>
                      OPEN {paper_position.direction}
                    </span>
                  </div>
                  <button onClick={closePaperTrade} className="text-[8px] text-neutral-700 hover:text-red-400 flex items-center gap-0.5 transition-colors">
                    <XIcon size={8} />Close
                  </button>
                </div>
                <div className="grid grid-cols-2 gap-1 text-[9px] font-mono">
                  <div><span className="text-neutral-700">Entry</span><br /><span className="text-white">${paper_position.entry?.toFixed(0)}</span></div>
                  <div>
                    <span className="text-neutral-700">P&L</span><br />
                    <span className={pnlColor(paper_position.pnl_usd ?? 0)}>
                      {(paper_position.pnl_usd ?? 0) >= 0 ? "+" : ""}${(paper_position.pnl_usd ?? 0).toFixed(2)}
                    </span>
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>

        {/* Right: Commentary feed */}
        <div className="col-span-12 lg:col-span-8 p-4 flex flex-col">
          <div className="flex items-center gap-2 mb-3">
            <Cpu size={11} className="text-neutral-600" />
            <span className="text-[11px] font-semibold text-white">Live Analyst Feed</span>
            <span className="w-1.5 h-1.5 rounded-full bg-green-400 animate-pulse ml-1" />
            <span className="text-[9px] text-green-400">thinking · updates every 12s</span>
            <div className="ml-auto flex items-center gap-3 text-[9px] text-neutral-700">
              <span>Momentum 15m</span>
              <span className="w-px h-3 bg-neutral-800" />
              <span>ORB-30</span>
              <span className="w-px h-3 bg-neutral-800" />
              <span>HFT Flow</span>
              <span className="w-px h-3 bg-neutral-800" />
              <span>Risk Engine</span>
            </div>
          </div>

          {/* Terminal feed */}
          <div className="flex-1 overflow-y-auto space-y-0.5 font-mono text-[10px]"
            style={{ maxHeight: 340, background: "rgba(0,0,0,0.3)", borderRadius: 10, padding: "10px 12px", border: "1px solid rgba(255,255,255,0.04)" }}>
            {thoughts.length === 0 && (
              <div className="text-neutral-800 flex items-center gap-2">
                <RefreshCw size={10} className="animate-spin" />
                Initialising brain… loading market data
              </div>
            )}
            {thoughts.map((t, i) => {
              const isSignal  = t.includes("★") || t.includes("CONSENSUS") || t.includes("A+");
              const isTP      = t.includes("✅") || t.includes("TP HIT");
              const isSL      = t.includes("⛔") || t.includes("SL HIT");
              const isPaper   = t.includes("📄") || t.includes("Paper");
              const isWarning = t.includes("VOLATILE") || t.includes("RANGING") || t.includes("risk") || t.includes("Risk");
              const cls = isSignal  ? "text-yellow-400"
                        : isTP      ? "text-green-400"
                        : isSL      ? "text-red-400"
                        : isPaper   ? "text-violet-400"
                        : isWarning ? "text-orange-400"
                        : i === 0   ? "text-neutral-300"
                        : "text-neutral-600";
              return (
                <div key={i} className={`leading-relaxed py-0.5 ${cls} ${i === 0 ? "font-semibold" : ""}`}>
                  {t}
                </div>
              );
            })}
          </div>

          {/* Bottom stat strip */}
          <div className="flex items-center gap-6 mt-3 pt-3" style={{ borderTop: "1px solid rgba(255,255,255,0.05)" }}>
            {[
              ["MV15",     strategyResult.bias !== "neutral" ? strategyResult.bias.toUpperCase() : "NEUTRAL",
               strategyResult.bias === "long" ? "text-green-400" : strategyResult.bias === "short" ? "text-red-400" : "text-neutral-600"],
              ["ORB-30",   orbResult.bias !== "neutral" ? orbResult.bias.toUpperCase() : "NEUTRAL",
               orbResult.bias === "long" ? "text-green-400" : orbResult.bias === "short" ? "text-red-400" : "text-neutral-600"],
              ["MV conds", `${strategyResult.met_count}/${strategyResult.total ?? 7}`, "text-neutral-400"],
              ["ORB conds",`${orbResult.met_count}/${orbResult.total ?? 6}`, "text-neutral-400"],
              ["OR High",  orbResult.indicators.or_high ? `$${orbResult.indicators.or_high.toFixed(0)}` : "—", "text-green-400"],
              ["OR Low",   orbResult.indicators.or_low  ? `$${orbResult.indicators.or_low.toFixed(0)}`  : "—", "text-red-400"],
              ["Session",  orbResult.indicators.session_label ?? "—", "text-neutral-600"],
            ].map(([l, v, cls]) => (
              <div key={l} className="text-center min-w-0">
                <div className="text-[8px] text-neutral-800 truncate">{l}</div>
                <div className={`text-[10px] font-mono font-semibold ${cls} truncate`}>{v}</div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

function AllAgentsPanel({
  strategyResult, chartCandles, activity, btcTicker,
}: {
  strategyResult: StrategyResult; chartCandles: BinanceCandle[];
  activity: ActivityEvent[]; btcTicker: BinanceTicker | null;
}) {
  return (
    <div className="space-y-3">
      {/* Section header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Bot size={14} className="text-blue-400" />
          <span className="text-[13px] font-semibold text-white">All Live Agents</span>
          <span className="w-1.5 h-1.5 rounded-full bg-green-400 animate-pulse" />
          <span className="text-[9px] text-green-400">3 agents running</span>
        </div>
        {btcTicker && (
          <div className="text-[11px] font-mono text-neutral-500">
            BTC <span className="text-white">{formatUSD(btcTicker.last)}</span>
            <span className={`ml-1.5 ${(btcTicker.change_pct ?? 0) >= 0 ? "text-green-400" : "text-red-400"}`}>
              {(btcTicker.change_pct ?? 0) >= 0 ? "+" : ""}{(btcTicker.change_pct ?? 0).toFixed(2)}%
            </span>
          </div>
        )}
      </div>

      {/* Agent cards + activity */}
      <div className="grid grid-cols-1 lg:grid-cols-4 gap-4">

        {/* Momentum Velocity 15m agent */}
        <AgentCard
          name="Momentum Velocity"
          timeframe="15m"
          strategy="EMA50 · RSI · VWAP"
          result={strategyResult}
          candleCount={chartCandles.length}
          ticker={btcTicker}
        />

        {/* HFT VWAP Scalper agent — shows live conditions using same result but labelled separately */}
        <div className="card p-4 flex flex-col gap-3" style={{ minHeight: 210 }}>
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <div className="w-7 h-7 rounded-xl flex items-center justify-center" style={{ background: "rgba(245,158,11,.10)", border: "1px solid rgba(245,158,11,.2)" }}>
                <Zap size={13} style={{ color: "#f59e0b" }} />
              </div>
              <div>
                <div className="text-[12px] font-semibold text-white leading-none">HFT VWAP Scalper</div>
                <div className="text-[9px] text-neutral-700 mt-0.5">OBI · TFI · Microprice · 1m</div>
              </div>
            </div>
            <div className="flex items-center gap-1.5">
              <span className="w-1.5 h-1.5 rounded-full bg-yellow-400 animate-pulse" />
              <span className="text-[9px] text-yellow-400">LIVE AGENT</span>
            </div>
          </div>
          <div className="flex-1 flex flex-col justify-center gap-2">
            <div className="rounded-xl p-3 space-y-2" style={{ background: "rgba(245,158,11,.04)", border: "1px solid rgba(245,158,11,.12)" }}>
              <div className="text-[10px] text-yellow-400 font-semibold">Full config on Live Agent page</div>
              <div className="text-[9px] text-neutral-600 leading-relaxed">
                OBI/TFI/microprice computed from live Binance depth stream.
                Strategy runs on 1m bars with 5m EMA bias filter.
              </div>
            </div>
            <div className="grid grid-cols-3 gap-2 text-[9px]">
              {[["OBI threshold","0.18"],["TFI threshold","0.12"],["Spread max","2 ticks"]].map(([l,v]) => (
                <div key={l} className="rounded-lg p-2" style={{ background: "rgba(255,255,255,.02)", border: "1px solid rgba(255,255,255,.04)" }}>
                  <div className="text-neutral-700 mb-0.5">{l}</div>
                  <div className="font-mono text-neutral-300">{v}</div>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* ORB-30 Breakout agent */}
        <div className="card p-4 flex flex-col gap-3" style={{ minHeight: 210 }}>
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <div className="w-7 h-7 rounded-xl flex items-center justify-center" style={{ background: "rgba(245,158,11,.10)", border: "1px solid rgba(245,158,11,.25)" }}>
                <BarChart2 size={13} style={{ color: "#f59e0b" }} />
              </div>
              <div>
                <div className="text-[12px] font-semibold text-white leading-none">ORB-30 Breakout ★</div>
                <div className="text-[9px] text-neutral-700 mt-0.5">15m EMA20 bias · 4h sessions · 1m</div>
              </div>
            </div>
            <div className="flex items-center gap-1.5">
              <span className="w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse" />
              <span className="text-[9px] text-amber-400">LIVE</span>
            </div>
          </div>

          {/* 5-year backtest strip */}
          <div className="rounded-xl px-3 py-2.5 grid grid-cols-3 gap-2"
            style={{ background: "rgba(245,158,11,.04)", border: "1px solid rgba(245,158,11,.12)" }}>
            {[["5yr Return","+94.3%","text-green-400"],["Win Rate","55.3%","text-blue-400"],["R:R","2.25:1","text-white"]].map(([l,v,cls]) => (
              <div key={l} className="text-center">
                <div className="text-[8px] text-neutral-700">{l}</div>
                <div className={`text-[11px] font-bold ${cls}`}>{v}</div>
              </div>
            ))}
          </div>

          <div className="flex-1 flex flex-col justify-between gap-2">
            <div className="grid grid-cols-2 gap-2 text-[9px]">
              {[
                ["Sharpe Ratio","1.74"],["Profit Factor","1.81"],
                ["Max DD","−13.8%"],["Trades/mo","18.7"],
              ].map(([l,v]) => (
                <div key={l} className="rounded-lg p-2" style={{ background: "rgba(255,255,255,.02)", border: "1px solid rgba(255,255,255,.04)" }}>
                  <div className="text-neutral-700 mb-0.5">{l}</div>
                  <div className="font-mono text-neutral-300">{v}</div>
                </div>
              ))}
            </div>
            <div className="text-[9px] text-neutral-700 text-center">
              Full analysis on{" "}
              <a href="/dashboard/agent" className="text-amber-400 hover:underline">Live Agent → ORB-30</a>
            </div>
          </div>
        </div>

        {/* Live activity feed */}
        <div className="card overflow-hidden flex flex-col" style={{ minHeight: 210 }}>
          <div className="flex items-center justify-between px-4 py-3 flex-shrink-0" style={{ borderBottom: "1px solid rgba(255,255,255,0.05)" }}>
            <div className="flex items-center gap-2">
              <Bell size={11} className="text-neutral-600" />
              <span className="text-[12px] font-semibold text-white">Live Activity</span>
              {activity.length > 0 && <span className="text-[8px] bg-green-500/10 border border-green-500/20 text-green-400 px-1.5 py-0.5 rounded font-mono">{activity.length}</span>}
            </div>
          </div>
          <div className="overflow-y-auto flex-1 max-h-52">
            {activity.length === 0
              ? <div className="px-5 py-8 text-center">
                  <Zap size={16} className="text-neutral-800 mx-auto mb-2" />
                  <p className="text-[11px] text-neutral-700">Waiting for signals…</p>
                  <p className="text-[9px] mt-0.5 text-neutral-800">Runs on every bar close</p>
                </div>
              : activity.map((ev, i) => <ActivityRow key={i} ev={ev} />)}
          </div>
        </div>
      </div>
    </div>
  );
}

// ─── Page ─────────────────────────────────────────────────────────────────────
export default function OverviewPage() {
  const [data, setData]                 = useState<Overview | null>(null);
  const [loading, setLoading]           = useState(true);
  const [lastUpdate, setLastUpdate]     = useState<Date | null>(null);
  const [streamConnected, setStreamConnected] = useState(false);
  const [activity, setActivity]         = useState<ActivityEvent[]>([]);
  const [toasts, setToasts]             = useState<Toast[]>([]);
  const [chartCandles, setChartCandles] = useState<BinanceCandle[]>([]);
  const [liveCandle, setLiveCandle]     = useState<BinanceCandle | null>(null);
  const [btcTicker, setBtcTicker]       = useState<BinanceTicker | null>(null);
  const [btcOrderBook, setBtcOrderBook] = useState<BinanceOrderBook | null>(null);
  const [signals, setSignals]           = useState<Array<{ timestamp: string; direction: string; sl?: number | null; tp?: number | null }>>([]);
  const [candles1m, setCandles1m]       = useState<BinanceCandle[]>([]);
  const toastId = useRef(0);

  // ── Frontend strategy engines ─────────────────────────────────────────────
  const strategyResult = useStrategyEngine(chartCandles);
  const orbResult      = useORBStrategy(candles1m);

  const masterAgent = useMasterAgent(strategyResult, orbResult, chartCandles, candles1m, btcTicker, btcOrderBook);

  const addToast = useCallback((t: Omit<Toast, "id">) =>
    setToasts(p => [...p.slice(-4), { ...t, id: ++toastId.current }]), []);

  // Toast when the engine fires a live signal
  const prevSignalRef = useRef<string | null>(null);
  useEffect(() => {
    if (!strategyResult.signal) return;
    const key = `${strategyResult.signal.direction}-${strategyResult.signal.entry}`;
    if (key !== prevSignalRef.current) {
      prevSignalRef.current = key;
      addToast({ kind: "signal", direction: strategyResult.signal.direction, symbol: "BTC/USDT", price: strategyResult.signal.entry, strategy: "Momentum Velocity 15m" });
    }
  }, [strategyResult.signal, addToast]);

  const authFetch = useCallback(async (url: string) => {
    try {
      const r = await fetch(`${process.env.NEXT_PUBLIC_API_URL || ""}${url}`, {
        headers: { Authorization: `Bearer ${localStorage.getItem("token")}` },
      });
      return r.ok ? r.json() : null;
    } catch { return null; }
  }, []);

  const fetchData     = useCallback(async () => {
    try { const r = await overviewApi.get(); setData(r); setLastUpdate(new Date()); }
    catch { } finally { setLoading(false); }
  }, []);

  const fetchActivity = useCallback(async () => {
    const d = await authFetch("/api/paper/activity?limit=40");
    if (d) setActivity(d);
  }, [authFetch]);

  const fetchChartCandles = useCallback(async () => {
    // 1) Try backend (Bybit-backed)
    const d = await authFetch("/api/market/candles/BTC%2FUSDT?timeframe=15m&limit=120");
    if (Array.isArray(d) && d.length > 0) {
      setChartCandles(d.map((c: { timestamp: string; open: number; high: number; low: number; close: number; volume: number }) => ({ ...c, is_closed: true })));
      return;
    }
    // 2) Fallback: Binance public REST (no auth, no key required)
    try {
      const r = await fetch("https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=15m&limit=120");
      if (r.ok) {
        const raw: unknown[][] = await r.json();
        setChartCandles(raw.map(k => ({
          timestamp:  new Date(k[0] as number).toISOString(),
          open:       parseFloat(k[1] as string),
          high:       parseFloat(k[2] as string),
          low:        parseFloat(k[3] as string),
          close:      parseFloat(k[4] as string),
          volume:     parseFloat(k[5] as string),
          is_closed:  true,
        })));
        return;
      }
    } catch { /* ignore */ }
    // 3) Last resort: try Bybit directly
    try {
      const r = await fetch("https://api.bybit.com/v5/market/kline?category=linear&symbol=BTCUSDT&interval=15&limit=120");
      if (r.ok) {
        const json = await r.json();
        const list: string[][] = json?.result?.list ?? [];
        setChartCandles([...list].reverse().map(k => ({
          timestamp: new Date(parseInt(k[0])).toISOString(),
          open:      parseFloat(k[1]),
          high:      parseFloat(k[2]),
          low:       parseFloat(k[3]),
          close:     parseFloat(k[4]),
          volume:    parseFloat(k[5]),
          is_closed: true,
        })));
      }
    } catch { /* ignore */ }
  }, [authFetch]);

  const fetchSignals = useCallback(async () => {
    const d = await authFetch("/api/market/signals?symbol=BTC%2FUSDT&limit=50");
    if (d) setSignals(d);
  }, [authFetch]);

  // Seed 1m candles for ORB + Master Agent
  const seed1mCandles = useCallback(async () => {
    try {
      const r = await fetch("https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1m&limit=120");
      if (r.ok) {
        const raw: unknown[][] = await r.json();
        setCandles1m(raw.map(k => ({
          timestamp: new Date(k[0] as number).toISOString(),
          open:  parseFloat(k[1] as string), high:  parseFloat(k[2] as string),
          low:   parseFloat(k[3] as string), close: parseFloat(k[4] as string),
          volume:parseFloat(k[5] as string), is_closed: true,
        })));
        return;
      }
    } catch { /* fall through */ }
    try {
      const r = await fetch("https://api.bybit.com/v5/market/kline?category=linear&symbol=BTCUSDT&interval=1&limit=120");
      if (r.ok) {
        const json = await r.json();
        const list: string[][] = json?.result?.list ?? [];
        setCandles1m([...list].reverse().map(k => ({
          timestamp: new Date(parseInt(k[0])).toISOString(),
          open: parseFloat(k[1]), high: parseFloat(k[2]),
          low:  parseFloat(k[3]), close: parseFloat(k[4]),
          volume: parseFloat(k[5]), is_closed: true,
        })));
      }
    } catch { /* ignore */ }
  }, []);

  useEffect(() => {
    fetchData(); fetchActivity(); fetchChartCandles(); fetchSignals(); seed1mCandles();
    const i1 = setInterval(fetchData,         15_000);
    const i2 = setInterval(fetchActivity,     20_000);
    const i3 = setInterval(fetchChartCandles, 60_000);
    const i5 = setInterval(fetchSignals,      30_000);
    return () => [i1, i2, i3, i5].forEach(clearInterval);
  }, [fetchData, fetchActivity, fetchChartCandles, fetchSignals, seed1mCandles]);

  // ── Direct Binance stream (BTC only) ─────────────────────────────────────
  useBinanceStream({
    symbols: ["BTC/USDT"],
    timeframe: "15m",
    onTicker: useCallback((t: BinanceTicker) => {
      if (t.symbol === "BTC/USDT") setBtcTicker(t);
    }, []),
    onCandle: useCallback((sym: string, candle: BinanceCandle) => {
      if (sym !== "BTC/USDT") return;
      setLiveCandle(candle);
      setChartCandles(prev => {
        // Seed the chart from WebSocket if REST fetch produced nothing
        if (!prev.length) return [{ ...candle, is_closed: false }];
        const lastMs = new Date(prev[prev.length - 1].timestamp).getTime();
        const curMs  = new Date(candle.timestamp).getTime();
        if (lastMs === curMs) return [...prev.slice(0, -1), { ...candle }];
        if (curMs > lastMs)   return [...prev.slice(-119), { ...candle }];
        return prev;
      });
    }, []),
    onOrderBook: useCallback((ob: BinanceOrderBook) => {
      if (ob.symbol === "BTC/USDT") setBtcOrderBook(ob);
    }, []),
  });

  // ── 1m stream for ORB + Master Agent ─────────────────────────────────────
  useBinanceStream({
    symbols: ["BTC/USDT"],
    timeframe: "1m",
    onCandle: useCallback((_sym: string, c: BinanceCandle) => {
      setCandles1m(prev => {
        if (!prev.length) return [c];
        const lMs = new Date(prev[prev.length - 1].timestamp).getTime();
        const cMs = new Date(c.timestamp).getTime();
        if (lMs === cMs) return [...prev.slice(0, -1), c];
        if (cMs > lMs)   return [...prev.slice(-119), c];
        return prev;
      });
    }, []),
  });

  // ── Backend WebSocket ─────────────────────────────────────────────────────
  const { lastMessage, connected } = useWebSocket();
  useEffect(() => { setStreamConnected(connected); }, [connected]);
  useEffect(() => {
    if (!lastMessage) return;
    const { type, data: d } = lastMessage as { type?: string; data?: Record<string, unknown> };
    if (type === "signal:new" && d && (d.direction === "long" || d.direction === "short")) {
      addToast({ kind: "signal", direction: d.direction as "long"|"short", symbol: "BTC/USDT", price: (d.entry as number) || 0, strategy: d.strategy_name as string });
      fetchActivity(); fetchSignals();
    }
    if (type === "execution:order_placed" && d && d.event !== "paper_reset") {
      const side = d.side as string;
      addToast({ kind: "trade", direction: side === "buy" ? "long" : "short", symbol: "BTC/USDT", price: (d.fill_price as number) || 0 });
      fetchData(); fetchActivity();
    }
    if (type && ["risk:position_closed", "snapshot"].includes(type)) { fetchData(); fetchActivity(); }
  }, [lastMessage, fetchData, fetchActivity, fetchSignals, addToast]);

  if (loading) return (
    <div className="flex items-center justify-center h-64">
      <div className="w-5 h-5 border-2 border-t-transparent rounded-full animate-spin"
        style={{ borderColor: "rgba(10,132,255,0.2) rgba(10,132,255,0.2) rgba(10,132,255,0.2) #0a84ff" }} />
    </div>
  );

  const d = data!;

  return (
    <>
      <ToastContainer toasts={toasts} onDismiss={id => setToasts(p => p.filter(t => t.id !== id))} />

      <div className="space-y-4">
        {d?.kill_switch_active && (
          <div className="flex items-center gap-3 px-5 py-3 rounded-apple"
            style={{ background: "rgba(255,69,58,0.08)", border: "1px solid rgba(255,69,58,0.2)" }}>
            <ShieldCheck size={15} style={{ color: "#ff453a" }} />
            <span className="text-[13px] font-semibold" style={{ color: "#ff453a" }}>Emergency Stop Active</span>
          </div>
        )}

        {/* ── Master Agent ── */}
        <MasterAgentPanel agent={masterAgent} strategyResult={strategyResult} orbResult={orbResult} />

        {/* KPI row */}
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
          <KPICard label="Total Equity"       value={formatUSD(d?.equity || 0)}             subValue={`Available ${formatUSD(d?.available_balance || 0)}`} icon={DollarSign} trend="neutral" />
          <KPICard label="Daily PnL"           value={formatUSD(d?.daily_pnl || 0)}          subValue={formatPct(d?.daily_pnl_pct || 0)} subColor={pnlColor(d?.daily_pnl || 0)} icon={d?.daily_pnl >= 0 ? TrendingUp : TrendingDown} trend={d?.daily_pnl >= 0 ? "up" : "down"} />
          <KPICard label="Win Rate"            value={`${(d?.win_rate || 0).toFixed(1)}%`}   subValue={`${d?.total_trades || 0} trades`} icon={BarChart2} />
          <KPICard label="Active Strategies"  value={String(d?.active_strategies || 0)}     icon={Layers} />
        </div>

        {/* ── Live Agent Signal ── */}
        <LiveAgentSignal result={strategyResult} />

        {/* ── Main row: Chart · Analysis · Market ── */}
        <div className="grid grid-cols-12 gap-4">

          {/* Chart — 7 cols */}
          <div className="col-span-12 lg:col-span-7 card p-4">
            <div className="flex items-center justify-between mb-3">
              <div className="flex items-center gap-2">
                <span className="text-[13px] font-semibold text-white">BTC/USDT · 15m</span>
                <span className="text-[10px] text-neutral-600">Momentum Velocity</span>
                {streamConnected && <span className="flex items-center gap-1 text-[9px] text-green-400"><span className="w-1 h-1 rounded-full bg-green-400 animate-pulse" />live</span>}
              </div>
              <div className="flex items-center gap-2">
                {btcTicker && <span className="text-[12px] font-mono text-neutral-300">{formatUSD(btcTicker.last)}</span>}
                <span className={`text-[9px] font-bold px-1.5 py-0.5 rounded border ${strategyResult.bias === "long" ? "border-green-500/30 bg-green-500/10 text-green-400" : strategyResult.bias === "short" ? "border-red-500/30 bg-red-500/10 text-red-400" : "border-neutral-700 text-neutral-600"}`}>
                  {strategyResult.bias === "long" ? "▲ BULL" : strategyResult.bias === "short" ? "▼ BEAR" : "NEUTRAL"}
                </span>
              </div>
            </div>
            <StrategyChart candles={chartCandles} liveCandle={liveCandle} signals={signals} />
          </div>

          {/* Analysis — 2 cols */}
          <div className="col-span-12 lg:col-span-2 card p-4">
            <div className="flex items-center justify-between mb-3">
              <div className="flex items-center gap-1.5">
                <Activity size={12} className="text-neutral-600" />
                <span className="text-[12px] font-semibold text-white">Analysis</span>
              </div>
              <span className="text-[8px] text-green-400 flex items-center gap-1">
                <span className="w-1 h-1 rounded-full bg-green-400 animate-pulse" />live
              </span>
            </div>
            <AnalysisPanel result={strategyResult} candleCount={chartCandles.length} />
          </div>

          {/* BTC Market — 3 cols */}
          <div className="col-span-12 lg:col-span-3 card p-4">
            <div className="flex items-center justify-between mb-3">
              <div className="flex items-center gap-1.5">
                <span className="text-[12px] font-semibold text-white">BTC Market</span>
              </div>
              {streamConnected
                ? <span className="flex items-center gap-1 text-[9px] text-green-400"><span className="w-1 h-1 rounded-full bg-green-400 animate-pulse" />{connected ? "Binance Direct" : ""}</span>
                : <span className="flex items-center gap-1 text-[9px] text-neutral-600"><WifiOff size={9} />Connecting</span>}
            </div>
            <BTCMarketPanel ticker={btcTicker} orderBook={btcOrderBook} />
          </div>
        </div>

        {/* ── All Live Agents ── */}
        <AllAgentsPanel
          strategyResult={strategyResult}
          chartCandles={chartCandles}
          activity={activity}
          btcTicker={btcTicker}
        />

        {lastUpdate && (
          <p className="text-[9px] text-right text-neutral-800">Updated {lastUpdate.toLocaleTimeString()}</p>
        )}
      </div>
    </>
  );
}
