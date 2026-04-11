"use client";

import { useEffect, useState, useCallback, useRef, useMemo } from "react";
import {
  TrendingUp, TrendingDown, Activity,
  BarChart2, ShieldCheck, RefreshCw, WifiOff,
  Zap, ArrowUpRight, ArrowDownRight, Bell, Bot, BookOpen,
  CheckCircle2, XCircle, Brain, Cpu, RotateCcw, X as XIcon,
  Target, Shield, FileText, Send, MessageSquare, Square,
} from "lucide-react";
import { overviewApi } from "@/lib/api";
import { Overview } from "@/types";
import { formatUSD, formatPct, pnlColor, cn } from "@/lib/utils";
import { useWebSocket } from "@/hooks/useWebSocket";
import {
  useBingXStream, seedBingXCandles, BinanceCandle, BinanceTicker, BinanceOrderBook,
} from "@/hooks/useBingXStream";
import { useStrategyEngine, type StrategyResult } from "@/hooks/useStrategyEngine";
import { useORBStrategy, type ORBResult } from "@/hooks/useORBStrategy";
import { useHFTScalper, type HFTResult } from "@/hooks/useHFTScalper";
import { useOBIScalper, type OBIResult } from "@/hooks/useOBIScalper";
import { useSharedServerAgent } from "@/context/ServerAgentContext";
import { useServerAgent } from "@/hooks/useServerAgent";
import { useMasterAgent, type MasterSignal, type ConvictionGrade, type ChatMessage } from "@/hooks/useMasterAgent";

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
  const isLong  = bias === "long";
  const isShort = bias === "short";
  const biasColor = isLong ? "#22c55e" : isShort ? "#ef4444" : "#4b5563";

  if (candleCount < 60) return (
    <div className="flex flex-col items-center justify-center h-48 gap-3 text-neutral-700">
      <div className="w-8 h-8 rounded-full border border-neutral-800 flex items-center justify-center">
        <RefreshCw size={13} className="animate-spin text-neutral-600" />
      </div>
      <div className="text-center">
        <div className="text-[11px] text-neutral-600">Loading candles</div>
        <div className="text-[9px] text-neutral-800 mt-0.5">{candleCount}/60 bars</div>
      </div>
    </div>
  );

  return (
    <div className="space-y-3">
      {/* Bias header */}
      <div className="rounded-xl px-3 py-2.5" style={{ background: `${biasColor}0d`, border: `1px solid ${biasColor}28` }}>
        <div className="flex items-center justify-between">
          <span className="text-[12px] font-bold" style={{ color: biasColor }}>
            {isLong ? "▲ BULLISH" : isShort ? "▼ BEARISH" : "— NEUTRAL"}
          </span>
          <span className="text-[10px] font-mono font-bold" style={{ color: biasColor }}>{met_count}<span className="text-neutral-700 font-normal">/{total}</span></span>
        </div>
        <div className="flex gap-0.5 h-1 mt-2">
          {Array.from({ length: total ?? 7 }).map((_, i) => (
            <div key={i} className="flex-1 rounded-full transition-all duration-500"
              style={{ background: i < met_count ? biasColor : "#1a1a2e" }} />
          ))}
        </div>
      </div>

      {/* Conditions */}
      <div className="space-y-1">
        {conditions.map((c, i) => (
          <div key={i} className="flex items-center gap-2 py-0.5">
            <div className={`w-3.5 h-3.5 rounded-full flex items-center justify-center flex-shrink-0 ${c.met ? "bg-green-500/15" : "bg-neutral-800"}`}>
              {c.met ? <CheckCircle2 size={9} className="text-green-400" /> : <XCircle size={9} className="text-neutral-700" />}
            </div>
            <span className={`text-[9px] flex-1 leading-tight ${c.met ? "text-neutral-300" : "text-neutral-700"}`}>{c.name}</span>
            <span className={`text-[8px] font-mono ${c.met ? "text-green-400" : "text-neutral-800"}`}>{c.value}</span>
          </div>
        ))}
      </div>

      {/* Indicator grid */}
      <div className="rounded-xl p-2.5 grid grid-cols-2 gap-1.5" style={{ background: "rgba(255,255,255,0.02)", border: "1px solid rgba(255,255,255,0.04)" }}>
        {[
          ["RSI",   indicators.rsi       != null ? indicators.rsi.toFixed(1)             : "—"],
          ["Vol×",  indicators.vol_ratio != null ? indicators.vol_ratio.toFixed(2) + "×" : "—"],
          ["EMA50", indicators.ema50     != null ? formatUSD(indicators.ema50)           : "—"],
          ["VWAP",  indicators.vwap      != null ? formatUSD(indicators.vwap)            : "—"],
        ].map(([k, v]) => (
          <div key={k} className="flex items-center justify-between">
            <span className="text-[8px] text-neutral-700">{k}</span>
            <span className="text-[9px] font-mono text-neutral-500">{v}</span>
          </div>
        ))}
      </div>

      {all_met && (
        <div className="flex items-center gap-2 px-2.5 py-2 rounded-xl" style={{ background: "rgba(34,197,94,0.08)", border: "1px solid rgba(34,197,94,0.2)" }}>
          <Zap size={10} className="text-green-400" />
          <span className="text-[9px] text-green-400 font-semibold">All conditions met</span>
        </div>
      )}
    </div>
  );
}

// ─── Live Agent Signal Box ────────────────────────────────────────────────────
function LiveAgentSignal({ result }: { result: StrategyResult }) {
  const { signal, bias, met_count, total } = result;
  const isLong = signal?.direction === "long";
  const isShort = signal?.direction === "short";
  const biasColor = bias === "long" ? "#22c55e" : bias === "short" ? "#ef4444" : "#374151";

  if (!signal) {
    return (
      <div className="rounded-2xl p-4 flex items-center gap-4" style={{ background: "rgba(255,255,255,0.025)", border: "1px solid rgba(255,255,255,0.06)" }}>
        <div className="w-10 h-10 rounded-2xl flex items-center justify-center flex-shrink-0"
          style={{ background: `${biasColor}15`, border: `1px solid ${biasColor}30` }}>
          {bias === "long" ? <TrendingUp size={18} style={{ color: biasColor }} />
           : bias === "short" ? <TrendingDown size={18} style={{ color: biasColor }} />
           : <Activity size={18} className="text-neutral-600" />}
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1">
            <span className="text-[11px] font-bold" style={{ color: biasColor }}>
              {bias === "long" ? "▲ BULLISH BIAS" : bias === "short" ? "▼ BEARISH BIAS" : "SCANNING MARKET"}
            </span>
            <span className="flex items-center gap-1 text-[8px] text-neutral-700 ml-auto">
              <span className="w-1 h-1 rounded-full bg-green-500 animate-pulse" />live · 15m bar
            </span>
          </div>
          <div className="flex gap-0.5 h-1 mb-1">
            {Array.from({ length: total || 7 }).map((_, i) => (
              <div key={i} className="flex-1 rounded-full transition-all duration-300"
                style={{ background: i < met_count ? biasColor : "#1a1a2e" }} />
            ))}
          </div>
          <div className="text-[9px] text-neutral-700">{met_count}/{total} conditions met · waiting for signal</div>
        </div>
        <div className="text-center flex-shrink-0 hidden lg:block">
          <div className="text-[8px] text-neutral-700">MOMENTUM 15m</div>
          <div className="text-[20px] font-bold font-mono" style={{ color: biasColor }}>{met_count}</div>
          <div className="text-[8px] text-neutral-700">/{total}</div>
        </div>
      </div>
    );
  }

  const glowColor = isLong ? "34,197,94" : "239,68,68";
  return (
    <div className="rounded-2xl p-4" style={{
      background: isLong ? "rgba(34,197,94,0.04)" : "rgba(239,68,68,0.04)",
      border: `1px solid rgba(${glowColor},0.2)`,
      boxShadow: `0 0 30px rgba(${glowColor},0.08)`,
    }}>
      <div className="flex items-center gap-4">
        {/* Big direction badge */}
        <div className="flex flex-col items-center flex-shrink-0">
          <div className="w-14 h-14 rounded-2xl flex items-center justify-center"
            style={{ background: `rgba(${glowColor},0.12)`, border: `1px solid rgba(${glowColor},0.25)` }}>
            {isLong ? <ArrowUpRight size={26} style={{ color: `rgb(${glowColor})` }} /> : <ArrowDownRight size={26} style={{ color: `rgb(${glowColor})` }} />}
          </div>
          <div className="text-[8px] mt-1 font-mono" style={{ color: `rgba(${glowColor},0.7)` }}>MV 15m</div>
        </div>

        {/* Direction + details */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-3 mb-2">
            <span className="text-[16px] font-bold" style={{ color: `rgb(${glowColor})` }}>
              {isLong ? "LONG" : "SHORT"} BTC
            </span>
            <span className="text-[9px] font-bold px-2 py-0.5 rounded-full animate-pulse"
              style={{ background: `rgba(${glowColor},0.12)`, border: `1px solid rgba(${glowColor},0.3)`, color: `rgb(${glowColor})` }}>
              SIGNAL ACTIVE
            </span>
            <span className="text-[9px] text-neutral-500 ml-auto font-mono">{(signal.confidence * 100).toFixed(0)}% conf</span>
          </div>
          <div className="grid grid-cols-4 gap-2">
            {[
              ["Entry",       formatUSD(signal.entry),                      "text-white"],
              ["Stop Loss",   formatUSD(signal.sl),                         "text-red-400"],
              ["Take Profit", formatUSD(signal.tp),                         "text-green-400"],
              ["R:R",         signal.rr,                                    "text-yellow-400"],
            ].map(([l, v, cls]) => (
              <div key={l} className="rounded-xl p-2 text-center" style={{ background: "rgba(0,0,0,0.2)", border: "1px solid rgba(255,255,255,0.06)" }}>
                <div className="text-[8px] text-neutral-600 mb-0.5">{l}</div>
                <div className={`text-[10px] font-mono font-bold ${cls}`}>{v}</div>
              </div>
            ))}
          </div>
        </div>
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
    const t = setTimeout(() => setFlash(null), 400);
    return () => clearTimeout(t);
  }, [ticker?.last]);

  const isUp = (ticker?.change_pct ?? 0) >= 0;

  return (
    <div className="space-y-3">
      {/* Price header */}
      <div className="rounded-xl p-3" style={{ background: "rgba(255,255,255,0.02)", border: "1px solid rgba(255,255,255,0.05)" }}>
        <div className="flex items-center justify-between mb-1">
          <span className="text-[9px] text-neutral-600 font-medium uppercase tracking-wider">BTC · USDT</span>
          <span className="flex items-center gap-1 text-[8px] text-green-400">
            <span className="w-1 h-1 rounded-full bg-green-400 animate-pulse" />LIVE
          </span>
        </div>
        <div className="flex items-end gap-2">
          <span className="text-[22px] font-mono font-bold transition-colors duration-200"
            style={{ color: flash === "up" ? "#22c55e" : flash === "down" ? "#ef4444" : "#f5f5fa" }}>
            {ticker ? formatUSD(ticker.last) : "—"}
          </span>
          {ticker && (
            <span className={`text-[12px] font-bold mb-0.5 px-1.5 py-0.5 rounded ${isUp ? "bg-green-500/10 text-green-400" : "bg-red-500/10 text-red-400"}`}>
              {isUp ? "+" : ""}{ticker.change_pct.toFixed(2)}%
            </span>
          )}
        </div>
        <div className="flex gap-3 mt-1.5 text-[9px] font-mono">
          <span className="text-green-400">H {ticker ? formatUSD(ticker.high_24h) : "—"}</span>
          <span className="text-red-400">L {ticker ? formatUSD(ticker.low_24h) : "—"}</span>
          <span className="text-neutral-600 ml-auto">Vol {ticker ? `${(ticker.volume / 1000).toFixed(1)}K` : "—"}</span>
        </div>
      </div>

      {/* Bid / Ask */}
      {ticker && (
        <div className="grid grid-cols-2 gap-1.5">
          <div className="rounded-xl p-2.5 text-center" style={{ background: "rgba(34,197,94,0.05)", border: "1px solid rgba(34,197,94,0.12)" }}>
            <div className="text-[8px] text-green-400/70 mb-0.5">BID</div>
            <div className="text-[11px] font-mono font-bold text-green-400">{formatUSD(ticker.bid)}</div>
          </div>
          <div className="rounded-xl p-2.5 text-center" style={{ background: "rgba(239,68,68,0.05)", border: "1px solid rgba(239,68,68,0.12)" }}>
            <div className="text-[8px] text-red-400/70 mb-0.5">ASK</div>
            <div className="text-[11px] font-mono font-bold text-red-400">{formatUSD(ticker.ask)}</div>
          </div>
        </div>
      )}

      {/* Order Book */}
      <div>
        <div className="flex items-center justify-between mb-1.5">
          <div className="flex items-center gap-1.5">
            <BookOpen size={10} className="text-neutral-600" />
            <span className="text-[10px] text-neutral-500 font-semibold">Order Book</span>
          </div>
          {orderBook && (
            <span className="text-[8px] text-neutral-700 flex items-center gap-1">
              <span className="w-1 h-1 rounded-full bg-green-500 animate-pulse" />100ms depth
            </span>
          )}
        </div>

        {!orderBook ? (
          <div className="text-center py-4 text-[10px] text-neutral-700 flex items-center justify-center gap-2">
            <RefreshCw size={10} className="animate-spin" />Connecting…
          </div>
        ) : (() => {
          const asks = orderBook.asks.slice(0, 7);
          const bids = orderBook.bids.slice(0, 7);
          const maxSz = Math.max(...asks.map(a => a.amount), ...bids.map(b => b.amount)) || 1;
          const spread = asks.length && bids.length ? asks[0].price - bids[0].price : 0;
          return (
            <div className="space-y-px">
              {[...asks].reverse().map((a, i) => (
                <div key={i} className="relative flex justify-between text-[9px] font-mono h-5 items-center overflow-hidden rounded">
                  <div className="absolute right-0 top-0 bottom-0 rounded" style={{ width: `${(a.amount / maxSz) * 100}%`, background: "rgba(239,68,68,0.1)" }} />
                  <span className="relative text-red-400 pl-1">{formatUSD(a.price)}</span>
                  <span className="relative text-neutral-600 pr-1">{a.amount.toFixed(3)}</span>
                </div>
              ))}
              <div className="py-1 text-center text-[8px] text-neutral-700 font-mono border-y border-neutral-800/80">
                spread {formatUSD(spread)}
              </div>
              {bids.map((b, i) => (
                <div key={i} className="relative flex justify-between text-[9px] font-mono h-5 items-center overflow-hidden rounded">
                  <div className="absolute left-0 top-0 bottom-0 rounded" style={{ width: `${(b.amount / maxSz) * 100}%`, background: "rgba(34,197,94,0.1)" }} />
                  <span className="relative text-green-400 pl-1">{formatUSD(b.price)}</span>
                  <span className="relative text-neutral-600 pr-1">{b.amount.toFixed(3)}</span>
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

  const biasRgb   = isLong ? "34,197,94" : isShort ? "239,68,68" : "55,65,81";
  const biasColor = isLong ? "#22c55e"   : isShort ? "#ef4444"   : "#4b5563";

  return (
    <div className="rounded-2xl p-4 flex flex-col gap-3"
      style={{ background: "rgba(255,255,255,0.025)", border: `1px solid rgba(${biasRgb},0.12)`, boxShadow: `0 0 20px rgba(${biasRgb},0.04)` }}>
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <div className="w-8 h-8 rounded-xl flex items-center justify-center" style={{ background: "rgba(10,132,255,0.1)", border: "1px solid rgba(10,132,255,0.18)" }}>
            <Bot size={14} style={{ color: "#60aaff" }} />
          </div>
          <div>
            <div className="text-[12px] font-bold text-white leading-none">{name}</div>
            <div className="text-[8px] text-neutral-600 mt-0.5">{strategy} · {timeframe}</div>
          </div>
        </div>
        <div className="flex items-center gap-1.5 px-2 py-1 rounded-full" style={{ background: isReady ? "rgba(34,197,94,0.08)" : "rgba(245,158,11,0.08)", border: isReady ? "1px solid rgba(34,197,94,0.2)" : "1px solid rgba(245,158,11,0.2)" }}>
          <span className="w-1.5 h-1.5 rounded-full animate-pulse" style={{ background: isReady ? "#22c55e" : "#f59e0b" }} />
          <span className="text-[8px] font-bold" style={{ color: isReady ? "#22c55e" : "#f59e0b" }}>
            {isReady ? "SCANNING" : `${candleCount}/60`}
          </span>
        </div>
      </div>

      {/* Bias */}
      <div className="rounded-xl px-3 py-2.5" style={{ background: `rgba(${biasRgb},0.06)`, border: `1px solid rgba(${biasRgb},0.15)` }}>
        <div className="flex items-center justify-between mb-1.5">
          <span className="text-[11px] font-bold" style={{ color: biasColor }}>
            {isLong ? "▲ BULLISH" : isShort ? "▼ BEARISH" : "— NEUTRAL"}
          </span>
          <span className="text-[9px] font-mono text-neutral-600">{result.met_count}/{result.total ?? 7}</span>
        </div>
        <div className="flex gap-0.5 h-1">
          {Array.from({ length: result.total ?? 7 }).map((_, i) => (
            <div key={i} className="flex-1 rounded-full transition-all duration-500"
              style={{ background: i < result.met_count ? biasColor : "#111120" }} />
          ))}
        </div>
      </div>

      {/* Signal card or waiting */}
      {sig ? (
        <div className="rounded-xl p-3 space-y-2"
          style={{ background: sig.direction === "long" ? "rgba(34,197,94,0.06)" : "rgba(239,68,68,0.06)", border: `1px solid ${sig.direction === "long" ? "rgba(34,197,94,0.2)" : "rgba(239,68,68,0.2)"}` }}>
          <div className="flex items-center justify-between">
            <span className={`text-[10px] font-bold ${sig.direction === "long" ? "text-green-400" : "text-red-400"}`}>
              {sig.direction === "long" ? "▲ LONG" : "▼ SHORT"} SIGNAL
            </span>
            <span className="text-[8px] font-mono text-neutral-600">{(sig.confidence * 100).toFixed(0)}%</span>
          </div>
          <div className="grid grid-cols-3 gap-1">
            {[["Entry","text-white",formatUSD(sig.entry)],["SL","text-red-400",formatUSD(sig.sl)],["TP","text-green-400",formatUSD(sig.tp)]].map(([l,c,v]) => (
              <div key={l} className="text-center rounded-lg py-1" style={{ background: "rgba(0,0,0,0.2)" }}>
                <div className="text-[7px] text-neutral-700">{l}</div>
                <div className={`text-[9px] font-mono font-bold ${c}`}>{v}</div>
              </div>
            ))}
          </div>
          {sig.rr && <div className="text-[8px] text-neutral-600">R:R {sig.rr}</div>}
        </div>
      ) : (
        <div className="flex-1 flex items-center justify-center text-center py-2">
          <div>
            <Activity size={14} className="text-neutral-800 mx-auto mb-1" />
            <div className="text-[9px] text-neutral-700">Monitoring market…</div>
            {ticker && <div className="text-[10px] font-mono text-neutral-600 mt-0.5">{formatUSD(ticker.last)}</div>}
          </div>
        </div>
      )}
    </div>
  );
}

// ─── Mini Agent Card (for HFT/ORB/OBI in AllAgentsPanel) ─────────────────────
function MiniAgentCard({
  name, timeframe, strategy, bias, metCount, total, hasSignal, ticker,
}: {
  name: string; timeframe: string; strategy: string;
  bias: "long" | "short" | "neutral";
  metCount: number; total: number; hasSignal: boolean;
  ticker?: BinanceTicker | null;
}) {
  const isLong = bias === "long", isShort = bias === "short";
  const biasRgb   = isLong ? "34,197,94" : isShort ? "239,68,68" : "55,65,81";
  const biasColor = isLong ? "#22c55e"   : isShort ? "#ef4444"   : "#4b5563";
  const pct = total > 0 ? (metCount / total) * 100 : 0;
  return (
    <div className="rounded-2xl p-4 flex flex-col gap-3"
      style={{ background: "rgba(255,255,255,0.025)", border: `1px solid rgba(${biasRgb},0.12)` }}>
      <div className="flex items-center justify-between">
        <div>
          <div className="text-[11px] font-bold text-white">{name}</div>
          <div className="text-[8px] text-neutral-600">{strategy} · {timeframe}</div>
        </div>
        <div className="flex items-center gap-1 px-1.5 py-0.5 rounded-full" style={{ background: hasSignal ? `rgba(${biasRgb},0.12)` : "rgba(34,197,94,0.06)", border: hasSignal ? `1px solid rgba(${biasRgb},0.3)` : "1px solid rgba(34,197,94,0.15)" }}>
          <span className="w-1 h-1 rounded-full animate-pulse" style={{ background: hasSignal ? biasColor : "#22c55e" }} />
          <span className="text-[7px] font-bold" style={{ color: hasSignal ? biasColor : "#22c55e" }}>{hasSignal ? "SIGNAL" : "LIVE"}</span>
        </div>
      </div>
      <div className="space-y-1.5">
        <div className="flex justify-between text-[9px]">
          <span className="text-neutral-700">Conditions met</span>
          <span className="font-bold" style={{ color: biasColor }}>{metCount}/{total}</span>
        </div>
        <div className="h-1.5 bg-neutral-900 rounded-full overflow-hidden">
          <div className="h-full rounded-full transition-all duration-500" style={{ width: `${pct}%`, background: biasColor }} />
        </div>
        <div className="flex justify-between text-[9px]">
          <span className="font-bold capitalize" style={{ color: biasColor }}>
            {isLong ? "▲ LONG" : isShort ? "▼ SHORT" : "FLAT"}
          </span>
          {ticker && <span className="text-neutral-600 font-mono">{formatUSD(ticker.last)}</span>}
        </div>
      </div>
    </div>
  );
}

// ─── Master Agent Chat Interface ─────────────────────────────────────────────
function ChatBubble({ msg }: { msg: ChatMessage }) {
  const isUser = msg.role === "user";
  const isAuto = msg.type === "auto";
  const isSys  = msg.type === "system";

  if (isUser) {
    return (
      <div className="flex items-end gap-2 justify-end">
        <div className="max-w-[80%]">
          <div className="rounded-2xl rounded-br-sm px-3.5 py-2.5"
            style={{ background: "rgba(10,132,255,0.2)", border: "1px solid rgba(10,132,255,0.3)" }}>
            <p className="text-[11px] text-white leading-relaxed">{msg.content}</p>
          </div>
          <div className="text-[8px] text-neutral-700 text-right mt-0.5 pr-1">{msg.timestamp}</div>
        </div>
      </div>
    );
  }

  // Agent message — auto signals use same bright style as responses
  const isSignal = isAuto && (msg.content.includes("▲ LONG") || msg.content.includes("▼ SHORT"));
  const isNoSig  = isAuto && msg.content.startsWith("NO SIGNAL");
  const textCls  = isSys
    ? (msg.content.includes("✅") ? "text-green-400" : msg.content.includes("⛔") ? "text-red-400" : "text-violet-400")
    : isSignal ? (msg.content.includes("▲ LONG") ? "text-green-300" : "text-red-300")
    : isNoSig  ? "text-neutral-500"
    : isAuto   ? "text-neutral-400"
    : "text-neutral-200";

  const bgStyle = isSys ? "rgba(139,92,246,0.06)"
    : isSignal && msg.content.includes("▲ LONG") ? "rgba(34,197,94,0.06)"
    : isSignal && msg.content.includes("▼ SHORT") ? "rgba(239,68,68,0.06)"
    : isAuto ? "rgba(255,255,255,0.025)"
    : "rgba(255,255,255,0.05)";

  const borderStyle = isSys ? "1px solid rgba(139,92,246,0.15)"
    : isSignal && msg.content.includes("▲ LONG") ? "1px solid rgba(34,197,94,0.2)"
    : isSignal && msg.content.includes("▼ SHORT") ? "1px solid rgba(239,68,68,0.2)"
    : "1px solid rgba(255,255,255,0.06)";

  return (
    <div className="flex items-start gap-2">
      <div className="w-6 h-6 rounded-lg flex items-center justify-center flex-shrink-0 mt-0.5"
        style={{ background: isSys ? "rgba(139,92,246,0.12)" : isSignal && msg.content.includes("▲") ? "rgba(34,197,94,0.12)" : isSignal ? "rgba(239,68,68,0.12)" : "rgba(255,255,255,0.04)", border: isSys ? "1px solid rgba(139,92,246,0.2)" : "1px solid rgba(255,255,255,0.08)" }}>
        <Brain size={10} style={{ color: isSys ? "#a78bfa" : isSignal && msg.content.includes("▲") ? "#4ade80" : isSignal ? "#f87171" : "#4b5563" }} />
      </div>
      <div className="max-w-[90%]">
        <div className="rounded-2xl rounded-tl-sm px-3.5 py-2.5"
          style={{ background: bgStyle, border: borderStyle }}>
          <p className={`text-[11px] leading-relaxed whitespace-pre-line font-mono ${textCls}`}>{msg.content}</p>
        </div>
        <div className="text-[8px] text-neutral-700 mt-0.5 pl-1 flex items-center gap-1">
          {isAuto && <span className="text-[7px] text-neutral-800 italic">live scan</span>}
          {isSys  && <span className="text-[7px] text-violet-800 italic">system</span>}
          <span className="ml-auto">{msg.timestamp}</span>
        </div>
      </div>
    </div>
  );
}

function ChatInterface({
  agent, gm, strategyResult, orbResult,
}: {
  agent: ReturnType<typeof useMasterAgent>;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  gm: { color: string; rgb: string; bg: string; border: string; label: string; desc: string };
  strategyResult: StrategyResult;
  orbResult: ORBResult;
}) {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const { chat, direction, conviction, regime } = agent as any;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const sendMessage: (msg: string) => void = (agent as any).sendMessage;

  const [input, setInput] = useState("");
  const [isTyping, setIsTyping] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef  = useRef<HTMLInputElement>(null);

  // Auto-scroll to bottom on new messages
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [chat.length]);

  // Show typing indicator briefly then send
  const handleSend = useCallback(() => {
    if (!input.trim()) return;
    const msg = input.trim();
    setInput("");
    setIsTyping(true);
    sendMessage(msg);
    setTimeout(() => setIsTyping(false), 900);
    inputRef.current?.focus();
  }, [input, sendMessage]);

  const handleKey = useCallback((e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleSend(); }
  }, [handleSend]);

  const quickPrompts = useMemo(() => {
    if (direction === "LONG"  && conviction >= 55) return ["Show long signal", "Entry SL TP", "Should I trade now?", "Paper stats"];
    if (direction === "SHORT" && conviction >= 55) return ["Show short signal", "Entry SL TP", "Should I trade now?", "Paper stats"];
    return ["Current signal?", "Entry SL TP", "Paper stats", "When will signal fire?"];
  }, [direction, conviction]);

  return (
    <div className="col-span-12 lg:col-span-6 flex flex-col" style={{ borderRight: `1px solid rgba(${gm.rgb},0.08)` }}>
      {/* Header */}
      <div className="flex items-center gap-2 px-4 py-3" style={{ borderBottom: `1px solid rgba(${gm.rgb},0.08)` }}>
        <MessageSquare size={11} style={{ color: gm.color }} />
        <span className="text-[10px] font-bold text-white">Agent Chat</span>
        <span className="w-1.5 h-1.5 rounded-full animate-pulse ml-0.5" style={{ background: gm.color }} />
        <span className="text-[8px]" style={{ color: gm.color }}>always listening</span>
        <span className="ml-auto text-[8px] text-neutral-700">{regime} · {direction} · {conviction}/100</span>
      </div>

      {/* Chat messages */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto p-3 space-y-2.5" style={{ minHeight: 220, maxHeight: 320 }}>
        {chat.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full gap-3 py-8">
            <div className="w-10 h-10 rounded-2xl flex items-center justify-center" style={{ background: `rgba(${gm.rgb},0.1)`, border: `1px solid rgba(${gm.rgb},0.2)` }}>
              <Brain size={18} style={{ color: gm.color }} />
            </div>
            <div className="text-center">
              <div className="text-[11px] text-neutral-400">Signal feed active</div>
              <div className="text-[9px] text-neutral-700 mt-0.5">Entry · SL · TP · R:R · Grade</div>
            </div>
          </div>
        ) : (
          <>
            {chat.map((msg: ChatMessage) => <ChatBubble key={msg.id} msg={msg} />)}
            {isTyping && (
              <div className="flex items-center gap-2">
                <div className="w-6 h-6 rounded-lg flex items-center justify-center" style={{ background: "rgba(10,132,255,0.12)", border: "1px solid rgba(255,255,255,0.08)" }}>
                  <Brain size={10} className="text-blue-400" />
                </div>
                <div className="flex gap-1 px-3 py-2 rounded-2xl" style={{ background: "rgba(255,255,255,0.04)", border: "1px solid rgba(255,255,255,0.06)" }}>
                  {[0, 1, 2].map(i => (
                    <div key={i} className="w-1.5 h-1.5 rounded-full bg-neutral-600 animate-bounce" style={{ animationDelay: `${i * 0.15}s` }} />
                  ))}
                </div>
              </div>
            )}
          </>
        )}
      </div>

      {/* Quick prompts */}
      <div className="flex flex-wrap gap-1 px-3 pb-2">
        {quickPrompts.map(q => (
          <button key={q} onClick={() => { sendMessage(q); setInput(""); setIsTyping(true); setTimeout(() => setIsTyping(false), 900); }}
            className="text-[8px] px-2 py-1 rounded-full transition-all hover:scale-105"
            style={{ background: "rgba(255,255,255,0.04)", border: "1px solid rgba(255,255,255,0.08)", color: "#6b7280" }}>
            {q}
          </button>
        ))}
      </div>

      {/* Input */}
      <div className="flex items-center gap-2 px-3 pb-3">
        <input
          ref={inputRef}
          type="text"
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={handleKey}
          placeholder="Ask about market, signals, risk…"
          className="flex-1 bg-transparent rounded-xl px-3 py-2 text-[11px] text-white placeholder-neutral-700 outline-none"
          style={{ background: "rgba(255,255,255,0.04)", border: "1px solid rgba(255,255,255,0.08)" }}
        />
        <button
          onClick={handleSend}
          disabled={!input.trim()}
          className="w-8 h-8 rounded-xl flex items-center justify-center transition-all hover:scale-110 disabled:opacity-30 disabled:cursor-not-allowed"
          style={{ background: `rgba(${gm.rgb},0.2)`, border: `1px solid rgba(${gm.rgb},0.3)` }}>
          <Send size={12} style={{ color: gm.color }} />
        </button>
      </div>

      {/* Strategy indicator strip */}
      <div className="grid grid-cols-6 gap-1 px-3 pb-3" style={{ borderTop: `1px solid rgba(${gm.rgb},0.06)`, paddingTop: 8 }}>
        {[
          ["MV15",   strategyResult.bias !== "neutral" ? strategyResult.bias.toUpperCase() : "—",   strategyResult.bias === "long" ? "#22c55e" : strategyResult.bias === "short" ? "#ef4444" : "#374151"],
          ["ORB",    orbResult.bias !== "neutral" ? orbResult.bias.toUpperCase() : "—",              orbResult.bias === "long" ? "#22c55e" : orbResult.bias === "short" ? "#ef4444" : "#374151"],
          ["MV",     `${strategyResult.met_count}/${strategyResult.total ?? 7}`, "#6b7280"],
          ["ORB",    `${orbResult.met_count}/${orbResult.total ?? 6}`, "#6b7280"],
          ["OR Hi",  orbResult.indicators.or_high ? `$${orbResult.indicators.or_high.toFixed(0)}` : "—", "#22c55e"],
          ["OR Lo",  orbResult.indicators.or_low  ? `$${orbResult.indicators.or_low.toFixed(0)}`  : "—", "#ef4444"],
        ].map(([l, v, c]) => (
          <div key={l} className="text-center rounded-lg py-1" style={{ background: "rgba(255,255,255,0.02)", border: "1px solid rgba(255,255,255,0.04)" }}>
            <div className="text-[7px] text-neutral-800">{l}</div>
            <div className="text-[8px] font-mono font-bold" style={{ color: c }}>{v}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ─── Master Agent Panel ───────────────────────────────────────────────────────
const GRADE_META: Record<ConvictionGrade, { color: string; rgb: string; bg: string; border: string; label: string; desc: string }> = {
  "A+": { color: "#22c55e", rgb: "34,197,94",   bg: "rgba(34,197,94,0.08)",  border: "rgba(34,197,94,0.25)",  label: "A+ ULTRA HIGH", desc: "Execute at full size" },
  "A":  { color: "#0a84ff", rgb: "10,132,255",  bg: "rgba(10,132,255,0.08)", border: "rgba(10,132,255,0.25)", label: "A  HIGH",        desc: "Execute at 75% size" },
  "B":  { color: "#f59e0b", rgb: "245,158,11",  bg: "rgba(245,158,11,0.08)", border: "rgba(245,158,11,0.25)", label: "B  MODERATE",    desc: "Scale in at 50%" },
  "C":  { color: "#8b5cf6", rgb: "139,92,246",  bg: "rgba(139,92,246,0.08)", border: "rgba(139,92,246,0.25)", label: "C  LOW",         desc: "Monitor only" },
  "X":  { color: "#374151", rgb: "55,65,81",    bg: "rgba(55,65,81,0.06)",   border: "rgba(55,65,81,0.15)",   label: "X  NO TRADE",   desc: "Insufficient edge" },
};

function MasterAgentPanel({ agent, strategyResult, orbResult }: {
  agent: ReturnType<typeof useMasterAgent>;
  strategyResult: StrategyResult;
  orbResult: ORBResult;
}) {
  const { direction, conviction, grade, signal, votes, consensus_count, regime, thoughts,
          paper_position, paper_stats, last_price, price_24h,
          strategyPerf, sessionInfo } = agent as ReturnType<typeof useMasterAgent>;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const { openPaperTrade, closePaperTrade, resetPaper } = agent as any;

  const gm = GRADE_META[grade];
  const isLong  = direction === "LONG";
  const isShort = direction === "SHORT";
  const pnlColor = (v: number) => v > 0 ? "text-green-400" : v < 0 ? "text-red-400" : "text-neutral-500";

  return (
    <div className="rounded-2xl overflow-hidden"
      style={{ background: "linear-gradient(135deg, #06060f 0%, #080812 100%)", border: `1px solid rgba(${gm.rgb},0.15)`, boxShadow: `0 0 40px rgba(${gm.rgb},0.06)` }}>

      {/* ── TOP HEADER ───────────────────────────────────────────────────── */}
      <div className="flex items-center justify-between px-5 py-3.5"
        style={{ borderBottom: `1px solid rgba(${gm.rgb},0.1)`, background: `rgba(${gm.rgb},0.03)` }}>
        <div className="flex items-center gap-3">
          {/* Animated brain icon */}
          <div className="relative w-9 h-9 rounded-xl flex items-center justify-center flex-shrink-0"
            style={{ background: `rgba(${gm.rgb},0.12)`, border: `1px solid rgba(${gm.rgb},0.25)` }}>
            <Brain size={18} style={{ color: gm.color }} />
            {grade !== "X" && (
              <>
                <span className="absolute -top-1 -right-1 w-2.5 h-2.5 rounded-full animate-ping" style={{ background: gm.color, opacity: 0.4 }} />
                <span className="absolute -top-1 -right-1 w-2.5 h-2.5 rounded-full" style={{ background: gm.color }} />
              </>
            )}
          </div>
          <div>
            <div className="flex items-center gap-2.5">
              <span className="text-[14px] font-bold text-white tracking-wider">MASTER AGENT</span>
              <span className="text-[9px] px-2.5 py-0.5 rounded-full font-bold tracking-wide"
                style={{ background: `rgba(${gm.rgb},0.15)`, border: `1px solid rgba(${gm.rgb},0.3)`, color: gm.color }}>
                {gm.label}
              </span>
            </div>
            <div className="text-[9px] text-neutral-600 mt-0.5">{gm.desc} · Self-learning · 5 strategies · {sessionInfo?.label?.split("—")[0]?.trim() ?? "Scanning"}</div>
          </div>
        </div>

        {/* Right: quick stats */}
        <div className="hidden lg:flex items-center gap-5">
          {[
            ["DIRECTION", direction === "LONG" ? "▲ LONG" : direction === "SHORT" ? "▼ SHORT" : "— FLAT",
              isLong ? "text-green-400" : isShort ? "text-red-400" : "text-neutral-600"],
            ["CONVICTION", `${conviction}/100`, "text-white"],
            ["CONSENSUS",  `${consensus_count}/5`, "text-white"],
            ["REGIME",     regime, regime === "TRENDING" ? "text-blue-400" : regime === "VOLATILE" ? "text-red-400" : regime === "RANGING" ? "text-yellow-400" : "text-neutral-600"],
            ["SESSION",    sessionInfo?.peak ? "PEAK" : "OFF-PEAK", sessionInfo?.peak ? "text-green-400" : "text-yellow-500"],
          ].map(([l, v, cls]) => (
            <div key={l} className="text-center">
              <div className="text-[7px] font-bold text-neutral-700 tracking-widest mb-0.5">{l}</div>
              <div className={`text-[11px] font-bold ${cls}`}>{v}</div>
            </div>
          ))}
          {last_price && (
            <div className="text-center pl-4 border-l border-neutral-800">
              <div className="text-[7px] text-neutral-700 tracking-widest mb-0.5">BTC</div>
              <div className="text-[12px] font-mono font-bold text-white">${last_price.toLocaleString()}</div>
              {price_24h != null && <div className={`text-[8px] ${price_24h >= 0 ? "text-green-400" : "text-red-400"}`}>{price_24h >= 0 ? "+" : ""}{price_24h.toFixed(2)}%</div>}
            </div>
          )}
        </div>
      </div>

      {/* ── MAIN BODY ─────────────────────────────────────────────────────── */}
      <div className="grid grid-cols-12 gap-0 min-h-[320px]">

        {/* LEFT: Conviction + Strategy Votes + Signal */}
        <div className="col-span-12 lg:col-span-3 p-4 space-y-3 flex flex-col" style={{ borderRight: `1px solid rgba(${gm.rgb},0.08)` }}>

          {/* Conviction arc card */}
          <div className="rounded-2xl p-4" style={{ background: `rgba(${gm.rgb},0.06)`, border: `1px solid rgba(${gm.rgb},0.15)` }}>
            <div className="text-center mb-3">
              <div className="text-[9px] text-neutral-600 mb-1 tracking-widest uppercase">Conviction Score</div>
              <div className="text-[40px] font-black font-mono leading-none" style={{ color: gm.color }}>{conviction}</div>
              <div className="text-[9px] text-neutral-700 mt-0.5">out of 100</div>
            </div>
            <div className="h-2 bg-black/40 rounded-full overflow-hidden">
              <div className="h-full rounded-full transition-all duration-700"
                style={{ width: `${conviction}%`, background: `linear-gradient(90deg, rgba(${gm.rgb},0.5), ${gm.color})` }} />
            </div>
            <div className="flex justify-between mt-1 text-[7px] text-neutral-800 font-mono">
              <span>0</span><span>25</span><span>50</span><span>75</span><span>100</span>
            </div>
          </div>

          {/* Session label */}
          {sessionInfo && (
            <div className="rounded-xl px-2.5 py-1.5 flex items-center gap-2"
              style={{ background: sessionInfo.peak ? "rgba(34,197,94,0.05)" : "rgba(251,191,36,0.05)", border: `1px solid ${sessionInfo.peak ? "rgba(34,197,94,0.15)" : "rgba(251,191,36,0.15)"}` }}>
              <span className={`text-[8px] ${sessionInfo.peak ? "text-green-400" : "text-yellow-400"}`}>
                {sessionInfo.peak ? "⚡" : "⏰"}
              </span>
              <span className="text-[8px] text-neutral-500 truncate flex-1">{sessionInfo.label}</span>
              <span className={`text-[8px] font-mono font-bold ${sessionInfo.multiplier >= 1 ? "text-green-400" : "text-yellow-500"}`}>
                {sessionInfo.multiplier >= 1 ? "+" : ""}{((sessionInfo.multiplier - 1) * 100).toFixed(0)}%
              </span>
            </div>
          )}

          {/* Strategy votes with trust scores */}
          <div className="space-y-1.5 flex-1">
            <div className="text-[8px] text-neutral-700 uppercase tracking-widest">Strategy Votes · Trust</div>
            {votes.map(v => {
              const aligned = direction !== "FLAT" && v.bias === direction.toLowerCase();
              const vc   = v.bias === "long" ? "#22c55e" : v.bias === "short" ? "#ef4444" : "#374151";
              const perf = strategyPerf?.[v.name];
              const lbl  = perf?.label === "HOT" ? "🔥" : perf?.label === "COLD" ? "❄" : "";
              const trust = v.weight ?? 1.0;
              const cooldown = perf && perf.msSinceLastSL < 45 * 60 * 1000;
              return (
                <div key={v.name} className="rounded-xl px-3 py-2 flex items-center gap-2"
                  style={{ background: aligned ? `rgba(${gm.rgb},0.08)` : "rgba(255,255,255,0.025)", border: `1px solid ${aligned ? `rgba(${gm.rgb},0.2)` : "rgba(255,255,255,0.05)"}` }}>
                  <div className="w-2 h-2 rounded-full flex-shrink-0" style={{ background: vc }} />
                  <span className="text-[8px] text-neutral-500 flex-1 truncate">{v.name}{lbl}</span>
                  {cooldown && <span className="text-[7px] text-yellow-600">~cool</span>}
                  <span className="text-[7px] font-mono text-neutral-600">{(trust * 100).toFixed(0)}%</span>
                  <span className="text-[9px] font-bold" style={{ color: vc }}>
                    {v.bias === "long" ? "▲" : v.bias === "short" ? "▼" : "—"}
                  </span>
                  <div className="h-1 w-8 bg-neutral-900 rounded-full overflow-hidden ml-1">
                    <div className="h-full rounded-full" style={{ width: `${Math.min(trust * 50, 100)}%`, background: trust >= 1.2 ? "#22c55e" : trust <= 0.8 ? "#ef4444" : vc }} />
                  </div>
                </div>
              );
            })}
          </div>

          {/* Signal or waiting */}
          {signal ? (
            <div className="rounded-2xl p-3 space-y-2"
              style={{ background: isLong ? "rgba(34,197,94,0.07)" : "rgba(239,68,68,0.07)", border: `1px solid ${isLong ? "rgba(34,197,94,0.22)" : "rgba(239,68,68,0.22)"}`, boxShadow: `0 0 20px rgba(${isLong ? "34,197,94" : "239,68,68"},0.05)` }}>
              <div className="flex items-center justify-between">
                <span className={`text-[10px] font-bold ${isLong ? "text-green-400" : "text-red-400"}`}>
                  {isLong ? "▲ LONG" : "▼ SHORT"} · {grade} SIGNAL
                </span>
                <span className="text-[8px] text-neutral-600">{signal.size_pct}% size</span>
              </div>
              <div className="grid grid-cols-3 gap-1 text-[9px] font-mono">
                {[["Entry","text-white",`$${signal.entry.toFixed(0)}`],["SL","text-red-400",`$${signal.sl.toFixed(0)}`],["TP","text-green-400",`$${signal.tp.toFixed(0)}`]].map(([l,c,v]) => (
                  <div key={l} className="text-center rounded-lg py-1" style={{ background: "rgba(0,0,0,0.3)" }}>
                    <div className="text-[7px] text-neutral-700">{l}</div>
                    <div className={`text-[9px] font-bold ${c}`}>{v}</div>
                  </div>
                ))}
              </div>
              <div className="flex items-center justify-between">
                <span className="text-[8px] text-neutral-600">R:R {signal.rr}</span>
                {!paper_position?.open && (
                  <button onClick={() => openPaperTrade(signal)}
                    className="flex items-center gap-1 px-2 py-1 rounded-lg text-[8px] font-bold text-white transition-all hover:scale-105"
                    style={{ background: isLong ? "rgba(34,197,94,0.25)" : "rgba(239,68,68,0.25)", border: `1px solid ${isLong ? "rgba(34,197,94,0.4)" : "rgba(239,68,68,0.4)"}` }}>
                    <FileText size={8} />Paper Trade
                  </button>
                )}
              </div>
            </div>
          ) : (
            <div className="rounded-xl px-3 py-2.5 flex items-center gap-2" style={{ background: "rgba(255,255,255,0.02)", border: "1px solid rgba(255,255,255,0.04)" }}>
              <div className="w-1.5 h-1.5 rounded-full bg-neutral-700 animate-pulse" />
              <span className="text-[9px] text-neutral-700">Scanning for setup — grade {grade}</span>
            </div>
          )}
        </div>

        {/* CENTER: AI Chat Interface */}
        <ChatInterface
          agent={agent}
          gm={gm}
          strategyResult={strategyResult}
          orbResult={orbResult}
        />

        {/* RIGHT: Paper P&L */}
        <div className="col-span-12 lg:col-span-3 p-4 flex flex-col space-y-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-1.5">
              <FileText size={11} className="text-violet-400" />
              <span className="text-[10px] font-bold text-white">Paper Trading</span>
            </div>
            <button onClick={resetPaper} className="flex items-center gap-1 text-[8px] text-neutral-700 hover:text-red-400 transition-colors">
              <RotateCcw size={9} />Reset
            </button>
          </div>

          {/* Stats */}
          <div className="grid grid-cols-2 gap-1.5">
            {[
              ["Total P&L", `${paper_stats.total_pnl >= 0 ? "+" : ""}$${paper_stats.total_pnl.toFixed(2)}`, pnlColor(paper_stats.total_pnl)],
              ["Win Rate",  paper_stats.total_trades > 0 ? `${paper_stats.win_rate.toFixed(1)}%` : "—", "text-blue-400"],
              ["W / L",     `${paper_stats.wins} / ${paper_stats.losses}`, "text-neutral-400"],
              ["Trades",    `${paper_stats.total_trades}`, "text-neutral-500"],
            ].map(([l, v, cls]) => (
              <div key={l} className="rounded-xl p-2.5 text-center" style={{ background: "rgba(139,92,246,0.04)", border: "1px solid rgba(139,92,246,0.1)" }}>
                <div className="text-[8px] text-neutral-700">{l}</div>
                <div className={`text-[11px] font-bold font-mono ${cls}`}>{v}</div>
              </div>
            ))}
          </div>

          {/* Open position */}
          {paper_position?.open ? (
            <div className="rounded-2xl p-3.5 flex-1" style={{
              background: paper_position.direction === "LONG" ? "rgba(34,197,94,0.06)" : "rgba(239,68,68,0.06)",
              border: `1px solid ${paper_position.direction === "LONG" ? "rgba(34,197,94,0.2)" : "rgba(239,68,68,0.2)"}`,
            }}>
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-1.5">
                  <div className={`w-2 h-2 rounded-full animate-pulse ${paper_position.direction === "LONG" ? "bg-green-400" : "bg-red-400"}`} />
                  <span className={`text-[10px] font-bold ${paper_position.direction === "LONG" ? "text-green-400" : "text-red-400"}`}>
                    OPEN {paper_position.direction}
                  </span>
                </div>
                <button onClick={closePaperTrade} className="text-[8px] text-neutral-600 hover:text-red-400 flex items-center gap-0.5 transition-colors border border-neutral-800 rounded-lg px-1.5 py-0.5">
                  <XIcon size={8} />Close
                </button>
              </div>
              <div className="grid grid-cols-2 gap-2 mb-2">
                {[
                  ["Entry",   `$${paper_position.entry?.toFixed(0) ?? "—"}`, "text-white"],
                  ["Current", `$${(paper_position.current ?? 0).toFixed(0)}`, "text-neutral-300"],
                ].map(([l, v, c]) => (
                  <div key={l} className="rounded-lg p-1.5 text-center" style={{ background: "rgba(0,0,0,0.2)" }}>
                    <div className="text-[7px] text-neutral-700">{l}</div>
                    <div className={`text-[10px] font-mono font-bold ${c}`}>{v}</div>
                  </div>
                ))}
              </div>
              <div className="text-center">
                <div className="text-[8px] text-neutral-700 mb-0.5">Unrealized P&L</div>
                <div className={`text-[16px] font-bold font-mono ${pnlColor(paper_position.pnl_usd ?? 0)}`}>
                  {(paper_position.pnl_usd ?? 0) >= 0 ? "+" : ""}${(paper_position.pnl_usd ?? 0).toFixed(2)}
                </div>
              </div>
            </div>
          ) : (
            <div className="flex-1 rounded-2xl flex flex-col items-center justify-center gap-2 text-center"
              style={{ background: "rgba(255,255,255,0.015)", border: "1px dashed rgba(255,255,255,0.08)", minHeight: 120 }}>
              <Target size={20} className="text-neutral-800" />
              <div className="text-[9px] text-neutral-700">No open position</div>
              <div className="text-[8px] text-neutral-800">Paper trades execute<br />on signal + grade ≥ B</div>
            </div>
          )}

          <div className="text-[8px] text-neutral-800 text-center">$500 per paper trade · auto TP/SL</div>
        </div>
      </div>
    </div>
  );
}

function AllAgentsPanel({
  strategyResult, hftResult, obiResult, orbResult, chartCandles, activity, btcTicker, serverAgent, masterAgent,
}: {
  strategyResult: StrategyResult; hftResult: HFTResult; obiResult: OBIResult; orbResult: ORBResult;
  chartCandles: BinanceCandle[];
  activity: ActivityEvent[]; btcTicker: BinanceTicker | null;
  serverAgent: ReturnType<typeof useServerAgent>;
  masterAgent: ReturnType<typeof useMasterAgent>;
}) {
  const activeCount = [strategyResult, hftResult, obiResult, orbResult].filter(r => r.bias !== "neutral" || r.met_count >= 2).length;
  return (
    <div className="space-y-3">
      {/* Section header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2.5">
          <div className="w-7 h-7 rounded-xl flex items-center justify-center" style={{ background: "rgba(10,132,255,0.1)", border: "1px solid rgba(10,132,255,0.2)" }}>
            <Bot size={13} style={{ color: "#60aaff" }} />
          </div>
          <span className="text-[13px] font-bold text-white">Individual Agents</span>
          <div className="flex items-center gap-1.5 px-2 py-0.5 rounded-full" style={{ background: "rgba(34,197,94,0.08)", border: "1px solid rgba(34,197,94,0.18)" }}>
            <span className="w-1.5 h-1.5 rounded-full bg-green-400 animate-pulse" />
            <span className="text-[8px] text-green-400 font-bold">4 RUNNING</span>
          </div>
          {serverAgent.running && (
            <div className="flex items-center gap-1 px-2 py-0.5 rounded-full" style={{ background: "rgba(16,185,129,0.08)", border: "1px solid rgba(16,185,129,0.18)" }}>
              <span className="w-1 h-1 rounded-full bg-emerald-400 animate-pulse" />
              <span className="text-[8px] text-emerald-400 font-bold">Server 24/7</span>
            </div>
          )}
        </div>
        <a href="/dashboard/agent" className="text-[9px] text-neutral-600 hover:text-blue-400 transition-colors">Full control →</a>
      </div>

      {/* Agent cards — all 4 live */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">

        {/* Momentum Velocity 15m agent */}
        <AgentCard
          name="Momentum 15m"
          timeframe="15m"
          strategy="EMA50 · RSI · VWAP"
          result={strategyResult}
          candleCount={chartCandles.length}
          ticker={btcTicker}
        />

        {/* HFT VWAP Scalper — live data */}
        <MiniAgentCard
          name="HFT Scalper"
          timeframe="1m"
          strategy="VWAP · OBI · Microprice"
          bias={hftResult.bias}
          metCount={hftResult.met_count}
          total={hftResult.total ?? 5}
          hasSignal={hftResult.signal !== null}
          ticker={btcTicker}
        />

        {/* ORB-30 — live data */}
        <MiniAgentCard
          name="ORB-30"
          timeframe="1m"
          strategy="Opening Range · EMA20"
          bias={orbResult.bias}
          metCount={orbResult.met_count}
          total={orbResult.total ?? 4}
          hasSignal={orbResult.signal !== null}
          ticker={btcTicker}
        />

        {/* OBI Scalper — live data */}
        <MiniAgentCard
          name="OBI Scalper"
          timeframe="1m"
          strategy="Order Book Imbalance · RSI"
          bias={obiResult.bias}
          metCount={obiResult.met_count}
          total={obiResult.total ?? 3}
          hasSignal={obiResult.signal !== null}
          ticker={btcTicker}
        />

      </div>

      {/* Last 5 trades from server */}
      {serverAgent.trades.length > 0 && (
        <div className="rounded-2xl overflow-hidden" style={{ background: "rgba(255,255,255,0.02)", border: "1px solid rgba(255,255,255,0.06)" }}>
          <div className="flex items-center justify-between px-4 py-2.5" style={{ borderBottom: "1px solid rgba(255,255,255,0.05)" }}>
            <div className="flex items-center gap-2">
              <FileText size={11} className="text-neutral-600" />
              <span className="text-[11px] font-bold text-white">Recent Executions</span>
              <span className="text-[8px] text-emerald-400">persistent · server</span>
            </div>
            <a href="/dashboard/agent" className="text-[8px] text-neutral-700 hover:text-blue-400 transition-colors">Full history →</a>
          </div>
          <div className="divide-y divide-neutral-800/50">
            {serverAgent.trades.slice(0, 5).map((t, i) => {
              const pnl = t.pnl_usd ?? 0;
              return (
                <div key={i} className="flex items-center gap-3 px-4 py-2">
                  <div className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${pnl >= 0 ? "bg-green-400" : "bg-red-400"}`} />
                  <span className={`text-[9px] font-bold w-10 ${t.direction === "long" ? "text-green-400" : "text-red-400"}`}>{t.direction.toUpperCase()}</span>
                  <span className="text-[9px] text-neutral-600 flex-1 truncate">{t.strategy_name}</span>
                  <span className="text-[8px] text-neutral-700 font-mono">${t.entry.toFixed(0)}→${(t.exit_price ?? 0).toFixed(0)}</span>
                  <span className={`text-[9px] font-bold font-mono w-16 text-right ${pnl >= 0 ? "text-green-400" : "text-red-400"}`}>
                    {pnl >= 0 ? "+" : ""}${pnl.toFixed(2)}
                  </span>
                  <span className={`text-[8px] font-bold w-8 text-right ${t.exit_reason === "tp" ? "text-green-400" : t.exit_reason === "sl" ? "text-red-400" : "text-neutral-500"}`}>
                    {(t.exit_reason ?? "—").toUpperCase()}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

// ─── Command Bar ──────────────────────────────────────────────────────────────
function CommandBar({
  ticker, agent, strategyResult, orbResult, hftResult, obiResult, streamConnected, serverAgent,
}: {
  ticker: BinanceTicker | null;
  agent: ReturnType<typeof useMasterAgent>;
  strategyResult: StrategyResult;
  orbResult: ORBResult;
  hftResult: HFTResult;
  obiResult: OBIResult;
  streamConnected: boolean;
  serverAgent: ReturnType<typeof useServerAgent>;
}) {
  const { direction, conviction, grade, regime, sessionInfo } = agent as ReturnType<typeof useMasterAgent>;
  const gm = GRADE_META[grade];
  const isUp = (ticker?.change_pct ?? 0) >= 0;

  const strategies: { label: string; bias: string; met: number; total: number }[] = [
    { label: "MV 15m", bias: strategyResult.bias, met: strategyResult.met_count,  total: strategyResult.total ?? 7 },
    { label: "ORB",    bias: orbResult.bias,       met: orbResult.met_count,       total: orbResult.total ?? 6 },
    { label: "HFT",    bias: hftResult.bias,       met: hftResult.met_count,       total: hftResult.total ?? 5 },
    { label: "OBI",    bias: obiResult.bias,       met: obiResult.met_count,       total: obiResult.total ?? 3 },
  ];

  return (
    <div className="flex flex-wrap items-center gap-3 px-4 py-3 rounded-2xl"
      style={{ background: "rgba(255,255,255,0.025)", border: "1px solid rgba(255,255,255,0.07)" }}>

      {/* BTC Price */}
      <div className="flex items-center gap-3 flex-shrink-0">
        <div>
          <div className="text-[22px] font-mono font-black text-white leading-none">
            {ticker ? formatUSD(ticker.last) : "—"}
          </div>
          <div className={`text-[10px] font-bold mt-0.5 ${isUp ? "text-green-400" : "text-red-400"}`}>
            {isUp ? "+" : ""}{ticker?.change_pct.toFixed(2) ?? "0.00"}% 24h
          </div>
        </div>
        <div className="h-8 w-px bg-neutral-800 hidden sm:block" />
      </div>

      {/* Strategy bias pills */}
      <div className="flex items-center gap-2 flex-wrap">
        {strategies.map(s => {
          const isLongB = s.bias === "long", isShortB = s.bias === "short";
          const rgb = isLongB ? "34,197,94" : isShortB ? "239,68,68" : "55,65,81";
          const col = isLongB ? "#22c55e"   : isShortB ? "#ef4444"   : "#4b5563";
          const pct = s.total > 0 ? (s.met / s.total) * 100 : 0;
          return (
            <div key={s.label} className="flex flex-col items-center gap-0.5 px-3 py-1.5 rounded-xl min-w-[52px]"
              style={{ background: `rgba(${rgb},0.06)`, border: `1px solid rgba(${rgb},0.18)` }}>
              <span className="text-[7px] text-neutral-600 font-bold tracking-wider">{s.label}</span>
              <span className="text-[11px] font-bold leading-none" style={{ color: col }}>
                {isLongB ? "▲" : isShortB ? "▼" : "—"}
              </span>
              <div className="w-8 h-0.5 bg-neutral-800 rounded-full overflow-hidden mt-0.5">
                <div className="h-full rounded-full transition-all" style={{ width: `${pct}%`, background: col }} />
              </div>
            </div>
          );
        })}
      </div>

      <div className="h-8 w-px bg-neutral-800 hidden sm:block" />

      {/* Master Agent status */}
      <div className="flex items-center gap-4">
        <div>
          <div className="text-[7px] text-neutral-700 tracking-widest mb-1 font-bold">MASTER AGENT</div>
          <div className="flex items-center gap-2">
            <span className="text-[10px] font-bold px-2.5 py-0.5 rounded-full"
              style={{ background: `rgba(${gm.rgb},0.12)`, color: gm.color, border: `1px solid rgba(${gm.rgb},0.25)` }}>
              {grade}
            </span>
            <span className="text-[12px] font-bold font-mono" style={{ color: gm.color }}>{conviction}/100</span>
            <span className={`text-[11px] font-bold ${direction === "LONG" ? "text-green-400" : direction === "SHORT" ? "text-red-400" : "text-neutral-600"}`}>
              {direction === "LONG" ? "▲ LONG" : direction === "SHORT" ? "▼ SHORT" : "FLAT"}
            </span>
          </div>
        </div>
        <div>
          <div className="text-[7px] text-neutral-700 tracking-widest mb-1 font-bold">REGIME</div>
          <span className={`text-[10px] font-bold ${regime === "TRENDING" ? "text-blue-400" : regime === "VOLATILE" ? "text-red-400" : "text-yellow-400"}`}>
            {regime}
          </span>
        </div>
      </div>

      {/* Session + server running indicator + Kill Switch */}
      <div className="ml-auto flex items-center gap-3">
        {sessionInfo && (
          <span className={`text-[8px] font-semibold hidden lg:flex ${sessionInfo.peak ? "text-green-400" : "text-yellow-500"}`}>
            {sessionInfo.peak ? "⚡" : "⏰"} {sessionInfo.label.split("—")[0].trim()}
          </span>
        )}
        <div className="h-5 w-px bg-neutral-800 hidden sm:block" />
        {serverAgent.running
          ? <span className="flex items-center gap-1.5 text-[8px] text-emerald-400 font-semibold"><span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />Agent 24/7</span>
          : <span className="flex items-center gap-1.5 text-[8px] text-neutral-600">Agent offline</span>}
        <div className="h-5 w-px bg-neutral-800 hidden sm:block" />
        {streamConnected
          ? <span className="flex items-center gap-1.5 text-[8px] text-green-400 font-semibold"><span className="w-1.5 h-1.5 rounded-full bg-green-400 animate-pulse" />BingX LIVE</span>
          : <span className="flex items-center gap-1.5 text-[8px] text-neutral-600"><WifiOff size={9} />Connecting</span>}
        <div className="h-5 w-px bg-neutral-800 hidden sm:block" />
        <button
          onClick={() => serverAgent.updateConfig({ enabled: false })}
          className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-xl text-[9px] font-bold transition-all hover:scale-105 active:scale-95"
          style={{ background: "rgba(239,68,68,0.08)", border: "1px solid rgba(239,68,68,0.2)", color: "#ef4444" }}
          title="Stop all trading"
        >
          <Square size={9} />STOP ALL
        </button>
      </div>
    </div>
  );
}

// ─── Signal Command Center ─────────────────────────────────────────────────────
function SignalCommandCenter({
  agent, strategyResult, orbResult, serverAgent,
}: {
  agent: ReturnType<typeof useMasterAgent>;
  strategyResult: StrategyResult;
  orbResult: ORBResult;
  serverAgent: ReturnType<typeof useServerAgent>;
}) {
  const { direction, conviction, grade, signal, votes, consensus_count, regime,
          paper_stats, last_price, strategyPerf } = agent as ReturnType<typeof useMasterAgent>;
  const gm = GRADE_META[grade];
  const isLong  = direction === "LONG";
  const isShort = direction === "SHORT";
  const pnlColor2 = (v: number) => v > 0 ? "text-green-400" : v < 0 ? "text-red-400" : "text-neutral-500";
  const dirRgb = isLong ? "34,197,94" : isShort ? "239,68,68" : gm.rgb;
  const dirCol = isLong ? "#22c55e"   : isShort ? "#ef4444"   : "#4b5563";

  return (
    <div className="rounded-2xl overflow-hidden"
      style={{ background: "linear-gradient(135deg,#06060f 0%,#080812 100%)", border: `1px solid rgba(${dirRgb},0.2)`, boxShadow: `0 0 40px rgba(${dirRgb},0.05)` }}>

      {/* Header bar */}
      <div className="flex items-center gap-3 px-5 py-3" style={{ borderBottom: `1px solid rgba(${dirRgb},0.1)`, background: `rgba(${dirRgb},0.03)` }}>
        <div className="relative w-8 h-8 rounded-xl flex items-center justify-center flex-shrink-0"
          style={{ background: `rgba(${gm.rgb},0.12)`, border: `1px solid rgba(${gm.rgb},0.25)` }}>
          <Brain size={16} style={{ color: gm.color }} />
          {grade !== "X" && (
            <>
              <span className="absolute -top-1 -right-1 w-2 h-2 rounded-full animate-ping" style={{ background: gm.color, opacity: 0.4 }} />
              <span className="absolute -top-1 -right-1 w-2 h-2 rounded-full" style={{ background: gm.color }} />
            </>
          )}
        </div>
        <div>
          <div className="flex items-center gap-2.5">
            <span className="text-[13px] font-bold text-white tracking-wider">MASTER AGENT · SIGNAL COMMAND</span>
            <span className="text-[9px] px-2 py-0.5 rounded-full font-bold" style={{ background: `rgba(${gm.rgb},0.15)`, border: `1px solid rgba(${gm.rgb},0.3)`, color: gm.color }}>
              {gm.label}
            </span>
          </div>
          <div className="text-[8px] text-neutral-600 mt-0.5">{gm.desc} · Momentum × ORB-30 × HFT Flow</div>
        </div>
        <div className="ml-auto text-[8px] text-neutral-700">{regime} · consensus {consensus_count}/3</div>
      </div>

      {/* Body: 3 columns */}
      <div className="grid grid-cols-12 gap-0">

        {/* LEFT: Direction + conviction */}
        <div className="col-span-12 lg:col-span-3 flex flex-col items-center justify-center gap-4 p-5 text-center"
          style={{ borderRight: `1px solid rgba(${dirRgb},0.08)` }}>
          <div className="w-20 h-20 rounded-3xl flex items-center justify-center"
            style={{ background: `rgba(${dirRgb},0.1)`, border: `2px solid rgba(${dirRgb},0.25)` }}>
            {isLong
              ? <ArrowUpRight size={38} style={{ color: dirCol }} />
              : isShort
                ? <ArrowDownRight size={38} style={{ color: dirCol }} />
                : <Activity size={30} className="text-neutral-600" />}
          </div>
          <div>
            <div className="text-[30px] font-black leading-none" style={{ color: dirCol }}>
              {isLong ? "LONG" : isShort ? "SHORT" : "FLAT"}
            </div>
            <div className="text-[10px] text-neutral-600 mt-1">BTC/USDT · {regime}</div>
          </div>
          {/* Conviction bar */}
          <div className="w-full">
            <div className="flex justify-between text-[7px] mb-1">
              <span className="text-neutral-700 font-bold tracking-widest">CONVICTION</span>
              <span className="font-bold font-mono" style={{ color: gm.color }}>{conviction}/100</span>
            </div>
            <div className="h-2 bg-black/40 rounded-full overflow-hidden">
              <div className="h-full rounded-full transition-all duration-700"
                style={{ width: `${conviction}%`, background: `linear-gradient(90deg,rgba(${gm.rgb},0.4),${gm.color})` }} />
            </div>
          </div>
        </div>

        {/* CENTER: Signal or waiting */}
        <div className="col-span-12 lg:col-span-5 p-5 flex flex-col justify-center gap-4"
          style={{ borderRight: `1px solid rgba(${dirRgb},0.08)` }}>

          {signal ? (
            <>
              <div className="text-[10px] text-neutral-500 font-bold tracking-widest uppercase">Active Signal</div>
              <div className="grid grid-cols-2 gap-3">
                {([
                  ["Entry",       `$${signal.entry.toFixed(0)}`,  "text-white",     "rgba(255,255,255,0.04)","rgba(255,255,255,0.06)"],
                  ["Stop Loss",   `$${signal.sl.toFixed(0)}`,     "text-red-400",   "rgba(239,68,68,0.06)","rgba(239,68,68,0.15)"],
                  ["Take Profit", `$${signal.tp.toFixed(0)}`,     "text-green-400", "rgba(34,197,94,0.06)","rgba(34,197,94,0.15)"],
                  ["R:R",         signal.rr,                       "text-yellow-400","rgba(245,158,11,0.06)","rgba(245,158,11,0.15)"],
                ] as [string, string, string, string, string][]).map(([l, v, cls, bg, border]) => (
                  <div key={l} className="rounded-xl p-3.5 text-center" style={{ background: bg, border: `1px solid ${border}` }}>
                    <div className="text-[8px] text-neutral-600 mb-1 font-semibold">{l}</div>
                    <div className={`text-[16px] font-mono font-bold ${cls}`}>{v}</div>
                  </div>
                ))}
              </div>
              {/* Price position bar */}
              {signal.sl && signal.tp && last_price && (() => {
                const rng = signal.tp - signal.sl;
                const pct = Math.min(Math.max(((last_price - signal.sl) / rng) * 100, 0), 100);
                return (
                  <div>
                    <div className="flex justify-between text-[8px] text-neutral-600 mb-1">
                      <span className="text-red-400/70">SL ${signal.sl.toFixed(0)}</span>
                      <span className="text-neutral-500">Current ${last_price.toFixed(0)}</span>
                      <span className="text-green-400/70">TP ${signal.tp.toFixed(0)}</span>
                    </div>
                    <div className="h-2 bg-neutral-800 rounded-full relative overflow-hidden">
                      <div className="absolute inset-0 flex">
                        <div className="h-full bg-red-900/40" style={{ width: "33%" }} />
                        <div className="h-full bg-neutral-900" style={{ width: "34%" }} />
                        <div className="h-full bg-green-900/40" style={{ width: "33%" }} />
                      </div>
                      <div className="absolute top-0 h-full w-1 bg-white rounded-full shadow-lg" style={{ left: `${pct}%` }} />
                    </div>
                  </div>
                );
              })()}
              <div className="text-[9px] text-neutral-600 italic">
                {signal.size_pct}% position size · auto SL/TP
              </div>
            </>
          ) : (
            <div className="flex flex-col items-center justify-center gap-4 py-4">
              <div className="w-14 h-14 rounded-2xl flex items-center justify-center"
                style={{ background: `rgba(${gm.rgb},0.08)`, border: `1px solid rgba(${gm.rgb},0.15)` }}>
                <Brain size={26} style={{ color: gm.color }} />
              </div>
              <div className="text-center">
                <div className="text-[14px] font-bold text-neutral-400">Scanning Markets</div>
                <div className="text-[10px] text-neutral-600 mt-1">
                  {conviction < 55 ? `${55 - conviction} more conviction pts needed` : "Awaiting entry confluence"}
                </div>
              </div>
              <div className="w-full rounded-xl p-3 text-center" style={{ background: "rgba(255,255,255,0.02)", border: "1px solid rgba(255,255,255,0.04)" }}>
                <div className="text-[9px] text-neutral-600">
                  {direction !== "FLAT"
                    ? `${direction} bias · ${consensus_count}/5 strategies aligned · conviction ${conviction}`
                    : "Strategies diverging · no edge detected"}
                </div>
              </div>
            </div>
          )}
        </div>

        {/* RIGHT: Strategy votes + live server positions */}
        <div className="col-span-12 lg:col-span-4 p-5 flex flex-col gap-4">

          {/* Strategy votes with trust scores */}
          <div>
            <div className="text-[8px] text-neutral-700 uppercase tracking-widest mb-2 font-bold">
              Strategy Alignment ({consensus_count}/5) · Trust
            </div>
            <div className="space-y-1.5">
              {votes.map(v => {
                const aligned = direction !== "FLAT" && v.bias === direction.toLowerCase();
                const vc = v.bias === "long" ? "#22c55e" : v.bias === "short" ? "#ef4444" : "#374151";
                const perf = strategyPerf?.[v.name];
                const lbl = perf?.label === "HOT" ? "🔥" : perf?.label === "COLD" ? "❄" : "";
                return (
                  <div key={v.name} className="flex items-center gap-2 rounded-xl px-3 py-2"
                    style={{ background: aligned ? `rgba(${dirRgb},0.06)` : "rgba(255,255,255,0.025)", border: `1px solid rgba(${dirRgb},${aligned ? "0.15" : "0.04"})` }}>
                    <div className="w-2 h-2 rounded-full flex-shrink-0" style={{ background: vc }} />
                    <span className="text-[8px] text-neutral-500 flex-1 truncate">{v.name}{lbl}</span>
                    <span className="text-[7px] font-mono text-neutral-700">{((v.weight ?? 1) * 100).toFixed(0)}%</span>
                    <span className="text-[9px] font-bold" style={{ color: vc }}>{v.bias === "long" ? "▲" : v.bias === "short" ? "▼" : "—"}</span>
                    <div className="h-1 w-8 bg-neutral-900 rounded-full overflow-hidden">
                      <div className="h-full rounded-full" style={{ width: `${v.met_pct * 100}%`, background: vc }} />
                    </div>
                  </div>
                );
              })}
            </div>
          </div>

          {/* Server live positions + all-time P&L */}
          <div className="flex-1">
            <div className="flex items-center justify-between mb-2">
              <div className="flex items-center gap-1.5">
                <Bot size={10} className="text-emerald-400" />
                <span className="text-[10px] font-bold text-white">Live Positions</span>
                <span className="text-[8px] text-emerald-400">24/7 · server</span>
              </div>
              <a href="/dashboard/agent" className="text-[8px] text-neutral-700 hover:text-blue-400 transition-colors">Full control →</a>
            </div>
            {/* Stats row */}
            <div className="grid grid-cols-3 gap-1.5 mb-2">
              {([
                ["All-time P&L", paper_stats.total_pnl >= 0 ? `+$${paper_stats.total_pnl.toFixed(2)}` : `$${paper_stats.total_pnl.toFixed(2)}`, paper_stats.total_pnl >= 0 ? "text-green-400" : "text-red-400"],
                ["Win%", paper_stats.total_trades > 0 ? `${paper_stats.win_rate.toFixed(1)}%` : "—", "text-blue-400"],
                ["Trades", `${paper_stats.total_trades}`, "text-neutral-300"],
              ] as [string, string, string][]).map(([l, v, cls]) => (
                <div key={l} className="rounded-xl p-2 text-center" style={{ background: "rgba(255,255,255,0.02)", border: "1px solid rgba(255,255,255,0.04)" }}>
                  <div className="text-[7px] text-neutral-700 mb-0.5">{l}</div>
                  <div className={`text-[11px] font-bold font-mono ${cls}`}>{v}</div>
                </div>
              ))}
            </div>

            {/* Open server positions */}
            {serverAgent.openPositions.length > 0 ? (
              <div className="space-y-1.5">
                {serverAgent.openPositions.slice(0, 3).map((pos, i) => {
                  const pnl = pos.unrealized_pnl ?? 0;
                  return (
                    <div key={i} className="rounded-xl px-3 py-2 flex items-center gap-2"
                      style={{ background: pos.direction === "long" ? "rgba(34,197,94,0.05)" : "rgba(239,68,68,0.05)", border: `1px solid ${pos.direction === "long" ? "rgba(34,197,94,0.18)" : "rgba(239,68,68,0.18)"}` }}>
                      <div className={`w-1.5 h-1.5 rounded-full flex-shrink-0 animate-pulse ${pos.direction === "long" ? "bg-green-400" : "bg-red-400"}`} />
                      <span className="text-[8px] text-neutral-500 flex-1 truncate">{pos.strategy_name}</span>
                      <span className="text-[8px] font-mono text-neutral-400">${pos.entry.toFixed(0)}</span>
                      <span className={`text-[9px] font-bold font-mono ${pnl >= 0 ? "text-green-400" : "text-red-400"}`}>
                        {pnl >= 0 ? "+" : ""}${pnl.toFixed(2)}
                      </span>
                    </div>
                  );
                })}
                {serverAgent.openPositions.length > 3 && (
                  <div className="text-[8px] text-neutral-700 text-center">+{serverAgent.openPositions.length - 3} more on agent page</div>
                )}
              </div>
            ) : (
              <div className="rounded-xl px-3 py-2.5 flex items-center gap-2" style={{ background: "rgba(255,255,255,0.02)", border: "1px solid rgba(255,255,255,0.04)" }}>
                <div className="w-1.5 h-1.5 rounded-full bg-neutral-700 animate-pulse" />
                <span className="text-[9px] text-neutral-700">
                  {serverAgent.running ? "No open positions — scanning every 20s" : "Agent offline"}
                </span>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

// ─── Market Pulse Strip ────────────────────────────────────────────────────────
function MarketPulseStrip({
  ticker, orderBook, chartCandles,
}: {
  ticker: BinanceTicker | null;
  orderBook: BinanceOrderBook | null;
  chartCandles: BinanceCandle[];
}) {
  const atr = useMemo(() => {
    if (chartCandles.length < 15) return null;
    const slice = chartCandles.slice(-15);
    const trs = slice.slice(1).map((c, i) =>
      Math.max(c.high - c.low, Math.abs(c.high - slice[i].close), Math.abs(c.low - slice[i].close))
    );
    return trs.reduce((s, v) => s + v, 0) / trs.length;
  }, [chartCandles]);

  const obi = useMemo(() => {
    if (!orderBook) return 0;
    const bv = orderBook.bids.slice(0, 10).reduce((s, b) => s + b.amount, 0);
    const av = orderBook.asks.slice(0, 10).reduce((s, a) => s + a.amount, 0);
    const total = bv + av;
    return total > 0 ? (bv - av) / total : 0;
  }, [orderBook]);

  const { session, elapsedH } = useMemo(() => {
    const h = new Date().getUTCHours();
    if (h >= 0  && h < 8)  return { session: "Asia",     elapsedH: h };
    if (h >= 8  && h < 16) return { session: "Europe",   elapsedH: h - 8 };
    return { session: "New York", elapsedH: h - 16 };
  }, []);

  const rangePos = useMemo(() => {
    if (!ticker) return 50;
    const range = ticker.high_24h - ticker.low_24h;
    return range > 0 ? ((ticker.last - ticker.low_24h) / range) * 100 : 50;
  }, [ticker]);

  const cardBase = { background: "rgba(255,255,255,0.025)", border: "1px solid rgba(255,255,255,0.07)" };

  return (
    <div className="grid grid-cols-2 lg:grid-cols-5 gap-2">
      {/* 24h Range */}
      <div className="rounded-2xl px-4 py-3" style={cardBase}>
        <div className="text-[8px] text-neutral-600 uppercase tracking-widest mb-2 font-semibold">24h Range</div>
        <div className="flex justify-between text-[8px] font-mono mb-1.5">
          <span className="text-red-400">{ticker ? formatUSD(ticker.low_24h) : "—"}</span>
          <span className="text-green-400">{ticker ? formatUSD(ticker.high_24h) : "—"}</span>
        </div>
        <div className="h-2 bg-neutral-800 rounded-full relative overflow-hidden">
          <div className="absolute inset-0 rounded-full" style={{ background: "linear-gradient(90deg,rgba(239,68,68,0.3),rgba(34,197,94,0.3))" }} />
          {ticker && <div className="absolute top-0 h-full w-1.5 rounded-full bg-white" style={{ left: `${Math.min(Math.max(rangePos, 2), 98)}%` }} />}
        </div>
        <div className="text-[9px] font-mono font-bold text-white text-center mt-1.5">{rangePos.toFixed(0)}% of range</div>
      </div>

      {/* ATR Volatility */}
      <div className="rounded-2xl px-4 py-3" style={cardBase}>
        <div className="text-[8px] text-neutral-600 uppercase tracking-widest mb-2 font-semibold">Volatility (ATR 15)</div>
        {atr ? (
          <>
            <div className="text-[20px] font-mono font-black text-yellow-400 leading-none">{formatUSD(atr)}</div>
            <div className="text-[8px] text-neutral-600 mt-1">{ticker ? `${(atr / ticker.last * 100).toFixed(2)}% of price` : "15m ATR"}</div>
            <div className="h-1.5 bg-neutral-800 rounded-full overflow-hidden mt-2">
              <div className="h-full rounded-full bg-yellow-400 transition-all" style={{ width: `${Math.min((atr / (ticker?.last ?? 1)) / 0.004 * 100, 100)}%` }} />
            </div>
          </>
        ) : <div className="text-neutral-700 text-[10px] mt-2">Loading…</div>}
      </div>

      {/* OBI */}
      <div className="rounded-2xl px-4 py-3" style={cardBase}>
        <div className="text-[8px] text-neutral-600 uppercase tracking-widest mb-2 font-semibold">Order Flow (OBI)</div>
        <div className={`text-[20px] font-mono font-black leading-none ${obi > 0.1 ? "text-green-400" : obi < -0.1 ? "text-red-400" : "text-neutral-500"}`}>
          {obi >= 0 ? "+" : ""}{obi.toFixed(3)}
        </div>
        <div className="text-[8px] text-neutral-600 mt-1">
          {obi > 0.2 ? "Strong bid pressure" : obi < -0.2 ? "Strong ask pressure" : "Balanced flow"}
        </div>
        <div className="h-1.5 bg-neutral-800 rounded-full overflow-hidden mt-2 relative">
          <div className="absolute inset-0" style={{ background: "linear-gradient(90deg,rgba(239,68,68,0.25) 0%,transparent 50%,rgba(34,197,94,0.25) 100%)" }} />
          <div className="absolute top-0 h-full w-1.5 rounded-full bg-white" style={{ left: `${Math.min(Math.max((obi + 1) / 2 * 100, 2), 98)}%` }} />
        </div>
      </div>

      {/* Volume */}
      <div className="rounded-2xl px-4 py-3" style={cardBase}>
        <div className="text-[8px] text-neutral-600 uppercase tracking-widest mb-2 font-semibold">24h Volume</div>
        {ticker ? (
          <>
            <div className="text-[20px] font-mono font-black text-blue-400 leading-none">
              {(ticker.quote_volume / 1e9).toFixed(1)}<span className="text-[11px] text-neutral-600 ml-1">B USDT</span>
            </div>
            <div className="text-[8px] text-neutral-600 mt-1">{(ticker.volume / 1000).toFixed(1)}K BTC traded</div>
          </>
        ) : <div className="text-neutral-700 text-[10px] mt-2">Loading…</div>}
      </div>

      {/* Session */}
      <div className="rounded-2xl px-4 py-3" style={cardBase}>
        <div className="text-[8px] text-neutral-600 uppercase tracking-widest mb-2 font-semibold">Trading Session</div>
        <div className="text-[14px] font-bold text-white">{session}</div>
        <div className="text-[8px] text-neutral-600 mt-0.5">{elapsedH}h elapsed · {8 - elapsedH}h remaining</div>
        <div className="h-1.5 bg-neutral-800 rounded-full overflow-hidden mt-2">
          <div className="h-full rounded-full bg-blue-400 transition-all" style={{ width: `${(elapsedH / 8) * 100}%` }} />
        </div>
      </div>
    </div>
  );
}

// ─── Floating Chat ─────────────────────────────────────────────────────────────
function FloatingChat({
  agent, strategyResult, orbResult,
}: {
  agent: ReturnType<typeof useMasterAgent>;
  strategyResult: StrategyResult;
  orbResult: ORBResult;
}) {
  const [open, setOpen] = useState(false);
  const { grade } = agent;
  const gm = GRADE_META[grade];

  return (
    <>
      {/* Floating button */}
      <button
        onClick={() => setOpen(v => !v)}
        className="fixed bottom-6 left-6 z-50 w-12 h-12 rounded-2xl flex items-center justify-center shadow-2xl transition-all hover:scale-110 active:scale-95"
        style={{ background: `rgba(${gm.rgb},0.15)`, border: `1px solid rgba(${gm.rgb},0.3)` }}
        title="Ask Master Agent">
        {open
          ? <XIcon size={16} style={{ color: gm.color }} />
          : <MessageSquare size={16} style={{ color: gm.color }} />}
      </button>

      {/* Slide-up panel */}
      {open && (
        <div className="fixed bottom-20 left-6 z-50 w-80 rounded-2xl shadow-2xl overflow-hidden transition-all"
          style={{ background: "#08080f", border: `1px solid rgba(${gm.rgb},0.2)`, boxShadow: `0 0 40px rgba(${gm.rgb},0.1)` }}>
          <div className="flex items-center justify-between px-4 py-3" style={{ borderBottom: `1px solid rgba(${gm.rgb},0.1)`, background: `rgba(${gm.rgb},0.04)` }}>
            <div className="flex items-center gap-2">
              <Brain size={13} style={{ color: gm.color }} />
              <span className="text-[11px] font-bold text-white">Master Agent · Chat</span>
              <span className="w-1.5 h-1.5 rounded-full animate-pulse ml-0.5" style={{ background: gm.color }} />
            </div>
            <button onClick={() => setOpen(false)} className="text-neutral-600 hover:text-white transition-colors">
              <XIcon size={13} />
            </button>
          </div>
          <ChatInterface agent={agent} gm={gm} strategyResult={strategyResult} orbResult={orbResult} />
        </div>
      )}
    </>
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
  const strategyResult  = useStrategyEngine(chartCandles);
  const orbResult       = useORBStrategy(candles1m);
  const hftResult       = useHFTScalper(candles1m, btcOrderBook, []); // aggTrades not subscribed on overview
  const obiResult       = useOBIScalper(candles1m, btcOrderBook);

  // Server agent — shared 24/7 backend positions, trades, stats (single poll for all routes)
  const serverAgent = useSharedServerAgent();

  // Fallback ticker synthesized from backend live_price when BingX stream hasn't populated yet
  const effectiveTicker: BinanceTicker | null = useMemo(() => {
    if (btcTicker) return btcTicker;
    const lp = serverAgent.status?.live_price;
    if (lp && lp > 0) {
      return {
        symbol: "BTC/USDT", last: lp, bid: lp, ask: lp,
        open_24h: lp, high_24h: lp, low_24h: lp,
        volume: 0, quote_volume: 0, change_pct: 0, updated_ms: Date.now(),
      };
    }
    return null;
  }, [btcTicker, serverAgent.status?.live_price]);

  const masterAgent = useMasterAgent(
    strategyResult, orbResult, hftResult, obiResult,
    chartCandles, candles1m,
    btcTicker, btcOrderBook,
    serverAgent.status,
  );

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
        headers: { Authorization: `Bearer ${localStorage.getItem("tradeos_token")}` },
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
    // Primary: BingX perpetual swap klines
    const candles = await seedBingXCandles("15m", 120);
    if (candles.length > 0) {
      setChartCandles(candles);
      return;
    }
    // Fallback: try backend
    const d = await authFetch("/api/market/candles/BTC%2FUSDT?timeframe=15m&limit=120");
    if (Array.isArray(d) && d.length > 0) {
      setChartCandles(d.map((c: { timestamp: string; open: number; high: number; low: number; close: number; volume: number }) => ({ ...c, is_closed: true })));
    }
  }, [authFetch]);

  const fetchSignals = useCallback(async () => {
    const d = await authFetch("/api/market/signals?symbol=BTC%2FUSDT&limit=50");
    if (d) setSignals(d);
  }, [authFetch]);

  // Seed 1m candles from BingX for ORB + Master Agent
  const seed1mCandles = useCallback(async () => {
    const candles = await seedBingXCandles("1m", 300);
    if (candles.length > 0) setCandles1m(candles);
  }, []);

  useEffect(() => {
    fetchData(); fetchActivity(); fetchChartCandles(); fetchSignals(); seed1mCandles();
    const i1 = setInterval(fetchData,         15_000);
    const i2 = setInterval(fetchActivity,     20_000);
    const i3 = setInterval(fetchChartCandles, 60_000);
    const i5 = setInterval(fetchSignals,      30_000);
    return () => [i1, i2, i3, i5].forEach(clearInterval);
  }, [fetchData, fetchActivity, fetchChartCandles, fetchSignals, seed1mCandles]);

  // ── BingX stream (BTC only) ──────────────────────────────────────────────
  useBingXStream({
    symbols: ["BTC/USDT"],
    timeframe: "15m",
    onTicker: useCallback((t: BinanceTicker) => {
      if (t.symbol === "BTC/USDT") setBtcTicker(t);
    }, []),
    onCandle: useCallback((sym: string, candle: BinanceCandle) => {
      if (sym !== "BTC/USDT") return;
      setLiveCandle(candle);
      setChartCandles(prev => {
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

  // ── 1m stream from BingX for ORB + Master Agent — keep 299 bars ───────
  useBingXStream({
    symbols: ["BTC/USDT"],
    timeframe: "1m",
    onCandle: useCallback((_sym: string, c: BinanceCandle) => {
      setCandles1m(prev => {
        if (!prev.length) return [c];
        const lMs = new Date(prev[prev.length - 1].timestamp).getTime();
        const cMs = new Date(c.timestamp).getTime();
        if (lMs === cMs) return [...prev.slice(0, -1), c];
        if (cMs > lMs)   return [...prev.slice(-299), c];
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
    <div className="flex flex-col items-center justify-center h-64 gap-4">
      <div className="relative w-10 h-10">
        <div className="absolute inset-0 rounded-full border border-blue-500/20 animate-ping" />
        <div className="absolute inset-1 rounded-full border-t border-blue-500 animate-spin" />
        <Brain size={14} className="absolute inset-0 m-auto text-blue-400" />
      </div>
      <div className="text-center">
        <div className="text-[12px] text-neutral-400">Initialising TradeOS</div>
        <div className="text-[9px] text-neutral-700 mt-0.5">Connecting to live market data…</div>
      </div>
    </div>
  );

  const d = data!;

  return (
    <>
      <ToastContainer toasts={toasts} onDismiss={id => setToasts(p => p.filter(t => t.id !== id))} />

      <div className="space-y-3">

        {/* ── Emergency stop banner ── */}
        {d?.kill_switch_active && (
          <div className="flex items-center gap-3 px-5 py-3.5 rounded-2xl"
            style={{ background: "rgba(255,69,58,0.08)", border: "1px solid rgba(255,69,58,0.2)", boxShadow: "0 0 20px rgba(255,69,58,0.05)" }}>
            <div className="w-8 h-8 rounded-xl flex items-center justify-center flex-shrink-0" style={{ background: "rgba(255,69,58,0.15)" }}>
              <ShieldCheck size={15} style={{ color: "#ff453a" }} />
            </div>
            <div>
              <div className="text-[13px] font-bold" style={{ color: "#ff453a" }}>Emergency Stop Active</div>
              <div className="text-[9px] text-red-400/60">All trading halted · Check risk settings</div>
            </div>
          </div>
        )}

        {/* ── 1. Command Bar ── */}
        <CommandBar
          ticker={effectiveTicker}
          agent={masterAgent}
          strategyResult={strategyResult}
          orbResult={orbResult}
          hftResult={hftResult}
          obiResult={obiResult}
          streamConnected={streamConnected}
          serverAgent={serverAgent}
        />

        {/* ── 2. Signal Command Center ── */}
        <SignalCommandCenter agent={masterAgent} strategyResult={strategyResult} orbResult={orbResult} serverAgent={serverAgent} />

        {/* ── 3. Market Pulse Strip ── */}
        <MarketPulseStrip ticker={effectiveTicker} orderBook={btcOrderBook} chartCandles={chartCandles} />

        {/* ── 4. Main analysis grid: Chart · Conditions · Order Book ── */}
        <div className="grid grid-cols-12 gap-3">

          {/* BTC Chart — 6 cols */}
          <div className="col-span-12 lg:col-span-6 rounded-2xl overflow-hidden"
            style={{ background: "rgba(255,255,255,0.025)", border: "1px solid rgba(255,255,255,0.07)" }}>
            <div className="flex items-center justify-between px-4 py-3" style={{ borderBottom: "1px solid rgba(255,255,255,0.06)" }}>
              <div className="flex items-center gap-3">
                <span className="text-[13px] font-bold text-white">BTC/USDT</span>
                <span className="text-[9px] px-2 py-0.5 rounded font-mono text-neutral-500"
                  style={{ background: "rgba(255,255,255,0.04)", border: "1px solid rgba(255,255,255,0.06)" }}>15m</span>
                <span className="text-[9px] text-neutral-600">Momentum Velocity</span>
                {streamConnected && (
                  <span className="flex items-center gap-1 text-[8px] text-green-400">
                    <span className="w-1 h-1 rounded-full bg-green-400 animate-pulse" />live
                  </span>
                )}
              </div>
              <div className="flex items-center gap-2">
                {effectiveTicker && <span className="text-[13px] font-mono font-bold text-white">{formatUSD(effectiveTicker.last)}</span>}
                <span className={`text-[8px] font-bold px-2 py-0.5 rounded-full ${strategyResult.bias === "long" ? "text-green-400" : strategyResult.bias === "short" ? "text-red-400" : "text-neutral-600"}`}
                  style={{ background: strategyResult.bias === "long" ? "rgba(34,197,94,0.1)" : strategyResult.bias === "short" ? "rgba(239,68,68,0.1)" : "rgba(255,255,255,0.04)", border: strategyResult.bias === "long" ? "1px solid rgba(34,197,94,0.25)" : strategyResult.bias === "short" ? "1px solid rgba(239,68,68,0.25)" : "1px solid rgba(255,255,255,0.06)" }}>
                  {strategyResult.bias === "long" ? "▲ BULLISH" : strategyResult.bias === "short" ? "▼ BEARISH" : "NEUTRAL"}
                </span>
              </div>
            </div>
            <div className="p-4">
              <StrategyChart candles={chartCandles} liveCandle={liveCandle} signals={signals} />
            </div>
          </div>

          {/* Strategy Conditions — 3 cols */}
          <div className="col-span-12 lg:col-span-3 rounded-2xl overflow-hidden"
            style={{ background: "rgba(255,255,255,0.025)", border: "1px solid rgba(255,255,255,0.07)" }}>
            <div className="flex items-center justify-between px-4 py-3" style={{ borderBottom: "1px solid rgba(255,255,255,0.06)" }}>
              <div className="flex items-center gap-1.5">
                <Activity size={11} className="text-blue-400" />
                <span className="text-[11px] font-bold text-white">Conditions</span>
              </div>
              <span className="flex items-center gap-1 text-[8px] text-green-400">
                <span className="w-1 h-1 rounded-full bg-green-400 animate-pulse" />live
              </span>
            </div>
            <div className="p-4">
              <AnalysisPanel result={strategyResult} candleCount={chartCandles.length} />
            </div>
          </div>

          {/* Order Book + BTC Market — 3 cols */}
          <div className="col-span-12 lg:col-span-3 rounded-2xl overflow-hidden"
            style={{ background: "rgba(255,255,255,0.025)", border: "1px solid rgba(255,255,255,0.07)" }}>
            <div className="flex items-center justify-between px-4 py-3" style={{ borderBottom: "1px solid rgba(255,255,255,0.06)" }}>
              <div className="flex items-center gap-1.5">
                <BookOpen size={11} className="text-neutral-500" />
                <span className="text-[11px] font-bold text-white">BTC Market</span>
              </div>
              {streamConnected
                ? <span className="flex items-center gap-1 text-[8px] text-green-400"><span className="w-1 h-1 rounded-full bg-green-400 animate-pulse" />Binance</span>
                : <span className="flex items-center gap-1 text-[8px] text-neutral-600"><WifiOff size={8} />Connecting</span>}
            </div>
            <div className="p-4">
              <BTCMarketPanel ticker={effectiveTicker} orderBook={btcOrderBook} />
            </div>
          </div>
        </div>

        {/* ── 5. Bottom row: Individual Agents + Activity ── */}
        <div className="grid grid-cols-12 gap-3">

          {/* Agents — 8 cols */}
          <div className="col-span-12 lg:col-span-8">
            <AllAgentsPanel
              strategyResult={strategyResult}
              hftResult={hftResult}
              obiResult={obiResult}
              orbResult={orbResult}
              chartCandles={chartCandles}
              activity={activity}
              btcTicker={effectiveTicker}
              serverAgent={serverAgent}
              masterAgent={masterAgent}
            />
          </div>

          {/* Activity feed — 4 cols */}
          <div className="col-span-12 lg:col-span-4 rounded-2xl overflow-hidden flex flex-col"
            style={{ background: "rgba(255,255,255,0.025)", border: "1px solid rgba(255,255,255,0.07)" }}>
            <div className="flex items-center justify-between px-4 py-3 flex-shrink-0"
              style={{ borderBottom: "1px solid rgba(255,255,255,0.06)" }}>
              <div className="flex items-center gap-2">
                <Bell size={11} className="text-neutral-600" />
                <span className="text-[12px] font-bold text-white">Live Activity</span>
                {activity.length > 0 && (
                  <span className="text-[8px] px-1.5 py-0.5 rounded-full font-bold"
                    style={{ background: "rgba(34,197,94,0.1)", border: "1px solid rgba(34,197,94,0.2)", color: "#22c55e" }}>
                    {activity.length}
                  </span>
                )}
              </div>
              <span className="flex items-center gap-1 text-[8px] text-neutral-700">
                <span className="w-1 h-1 rounded-full bg-green-500 animate-pulse" />live
              </span>
            </div>
            <div className="overflow-y-auto flex-1" style={{ maxHeight: 340 }}>
              {activity.length === 0 ? (
                <div className="flex flex-col items-center justify-center py-12 gap-3">
                  <div className="w-10 h-10 rounded-full flex items-center justify-center"
                    style={{ background: "rgba(255,255,255,0.03)", border: "1px solid rgba(255,255,255,0.06)" }}>
                    <Zap size={14} className="text-neutral-800" />
                  </div>
                  <p className="text-[10px] text-neutral-700">Waiting for signals…</p>
                  <p className="text-[8px] text-neutral-800">Fires on every bar close</p>
                </div>
              ) : (
                activity.map((ev, i) => <ActivityRow key={i} ev={ev} />)
              )}
            </div>
          </div>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between px-1 pb-2">
          <div className="flex items-center gap-2 text-[8px] text-neutral-800">
            <span className="w-1 h-1 rounded-full bg-green-500 animate-pulse" />
            <span>TradeOS · BTC/USDT live · Binance WebSocket · 5 strategies active</span>
          </div>
          {lastUpdate && <span className="text-[8px] text-neutral-800">Updated {lastUpdate.toLocaleTimeString()}</span>}
        </div>
      </div>

      {/* ── Floating Master Agent Chat ── */}
      <FloatingChat agent={masterAgent} strategyResult={strategyResult} orbResult={orbResult} />
    </>
  );
}
