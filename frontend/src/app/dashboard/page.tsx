"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import {
  DollarSign, TrendingUp, TrendingDown, Activity,
  Layers, BarChart2, ShieldCheck, RefreshCw, WifiOff,
  Zap, ArrowUpRight, ArrowDownRight, RotateCcw, Bell,
  CheckCircle2, XCircle, Clock,
} from "lucide-react";
import { overviewApi } from "@/lib/api";
import { Overview } from "@/types";
import { KPICard } from "@/components/dashboard/KPICard";
import { formatUSD, formatPct, pnlColor, sideColor, statusColor, timeAgo, cn } from "@/lib/utils";
import { useWebSocket } from "@/hooks/useWebSocket";
import { useBinanceStream, BinanceCandle } from "@/hooks/useBinanceStream";

// ─── Chart constants ──────────────────────────────────────────────────────────
const CW = 1000, CH = 220, PY = 14, BAR = 5;

// ─── Types ────────────────────────────────────────────────────────────────────
interface LivePrice { last: number; change_pct: number; }

interface AnalysisCondition {
  name: string; met: boolean; value: string; target: string; detail: string;
}
interface Analysis {
  symbol: string; timeframe: string; bias: string;
  all_met: boolean; met_count: number; total: number;
  conditions: AnalysisCondition[];
  indicators: Record<string, number | null>;
  last_signal: Record<string, unknown> | null;
}

interface ActivityEvent {
  kind: "signal" | "trade"; timestamp: string; symbol: string;
  direction: "long" | "short"; entry?: number; fill_price?: number;
  amount?: number; sl?: number | null; tp?: number | null;
  confidence?: number; strategy_name?: string; reasoning?: string; timeframe?: string;
}

interface Toast {
  id: number; kind: "signal" | "trade"; direction?: "long" | "short";
  symbol: string; price: number; strategy?: string;
}

// ─── Indicator math (frontend) ────────────────────────────────────────────────
function calcEMA(vals: number[], period: number): number[] {
  if (vals.length < period) return vals.map(() => NaN);
  const k = 2 / (period + 1);
  const out: number[] = Array(period - 1).fill(NaN);
  let prev = vals.slice(0, period).reduce((a, b) => a + b, 0) / period;
  out.push(prev);
  for (let i = period; i < vals.length; i++) {
    prev = vals[i] * k + prev * (1 - k);
    out.push(prev);
  }
  return out;
}

function calcVWAP(candles: BinanceCandle[], window = 50): number[] {
  return candles.map((_, i) => {
    const seg = candles.slice(Math.max(0, i - window + 1), i + 1);
    const tv = seg.reduce((s, c) => s + c.volume, 0);
    if (!tv) return (candles[i].high + candles[i].low + candles[i].close) / 3;
    return seg.reduce((s, c) => s + (c.high + c.low + c.close) / 3 * c.volume, 0) / tv;
  });
}

function calcRSI(closes: number[], period = 14): number[] {
  const n = closes.length;
  if (n <= period) return Array(n).fill(NaN);
  const out: number[] = Array(period + 1).fill(NaN);
  const deltas = closes.slice(1).map((c, i) => c - closes[i]);
  const gains  = deltas.map(d => Math.max(d, 0));
  const losses = deltas.map(d => Math.abs(Math.min(d, 0)));
  let ag = gains.slice(0, period).reduce((a, b) => a + b, 0) / period;
  let al = losses.slice(0, period).reduce((a, b) => a + b, 0) / period;
  for (let i = period; i < deltas.length; i++) {
    ag = (ag * (period - 1) + gains[i])  / period;
    al = (al * (period - 1) + losses[i]) / period;
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
  signals: Array<{ timestamp: string; direction: string; entry: number; sl?: number | null; tp?: number | null }>;
}) {
  const all = liveCandle
    ? [...candles.slice(0, -1), { ...candles[candles.length - 1], ...liveCandle, is_closed: false }]
    : candles;

  const visible = all.slice(-Math.floor(CW / (BAR + 2)));
  if (visible.length < 5) return (
    <div className="flex items-center justify-center h-40 text-neutral-600 text-sm">
      Loading BTC chart…
    </div>
  );

  const closes  = visible.map(c => c.close);
  const prices  = visible.flatMap(c => [c.high, c.low]);
  const minP    = Math.min(...prices);
  const maxP    = Math.max(...prices);
  const range   = maxP - minP || 1;
  const toY     = (p: number) => PY + ((maxP - p) / range) * (CH - PY * 2);

  // Indicators
  const allCloses = all.map(c => c.close);
  const ema50full = calcEMA(allCloses, 50);
  const ema21full = calcEMA(allCloses, 21);
  const vwapFull  = calcVWAP(all as BinanceCandle[], 50);
  const rsiAll    = calcRSI(allCloses, 14);

  const offset = all.length - visible.length;
  const ema50  = ema50full.slice(offset);
  const ema21  = ema21full.slice(offset);
  const vwap   = vwapFull.slice(offset);
  const rsi    = rsiAll.slice(offset);

  const linePoints = (vals: number[]) =>
    vals.map((v, i) => isNaN(v) ? null : `${i * (BAR + 2) + BAR / 2},${toY(v)}`)
      .filter(Boolean).join(" ");

  // Signal markers
  const signalMarkers: { x: number; y: number; dir: string }[] = [];
  for (const sig of signals) {
    const sigMs = new Date(sig.timestamp).getTime();
    let best = -1, bestDiff = Infinity;
    visible.forEach((c, i) => {
      const diff = Math.abs(new Date(c.timestamp).getTime() - sigMs);
      if (diff < bestDiff) { bestDiff = diff; best = i; }
    });
    if (best >= 0 && bestDiff < 60 * 60 * 1000) {
      const c = visible[best];
      const isLong = sig.direction === "long";
      signalMarkers.push({
        x: best * (BAR + 2) + BAR / 2,
        y: isLong ? toY(c.low) + 12 : toY(c.high) - 12,
        dir: sig.direction,
      });
    }
  }

  // Last signal SL/TP
  const lastSig = signals[signals.length - 1];
  const lastClose = visible[visible.length - 1]?.close;
  const chartUp = lastClose >= (visible[0]?.close || lastClose);

  // RSI panel
  const RSI_H = 40;
  const toRsiY = (v: number) => RSI_H - (v / 100) * RSI_H;
  const rsiPts = rsi.map((v, i) => isNaN(v) ? null : `${i * (BAR + 2) + BAR / 2},${toRsiY(v)}`).filter(Boolean).join(" ");

  return (
    <div className="space-y-1">
      {/* Price chart */}
      <div className="relative">
        {/* Y-axis labels */}
        {[maxP, (maxP + minP) / 2, minP].map((p, i) => (
          <div key={i} className="absolute right-1 text-[9px] text-neutral-700 font-mono -translate-y-1/2"
            style={{ top: `${[PY / CH, 0.5, (CH - PY) / CH][i] * 100}%` }}>
            {p >= 1000 ? formatUSD(p) : p.toFixed(2)}
          </div>
        ))}

        <svg viewBox={`0 0 ${CW} ${CH}`} className="w-full" style={{ height: CH }} preserveAspectRatio="none">
          {/* Grid */}
          {[0.25, 0.5, 0.75].map(f => (
            <line key={f} x1={0} x2={CW} y1={PY + f * (CH - PY * 2)} y2={PY + f * (CH - PY * 2)}
              stroke="#1c1c2e" strokeWidth={1} />
          ))}

          {/* VWAP */}
          <polyline points={linePoints(vwap)} fill="none" stroke="#a78bfa" strokeWidth={1} strokeDasharray="4 3" opacity={0.7} />
          {/* EMA50 */}
          <polyline points={linePoints(ema50)} fill="none" stroke="#f97316" strokeWidth={1.5} opacity={0.85} />
          {/* EMA21 */}
          <polyline points={linePoints(ema21)} fill="none" stroke="#38bdf8" strokeWidth={1.2} opacity={0.85} />

          {/* EMA21 pullback zone (shaded band) */}
          {ema21.map((e21, i) => {
            if (isNaN(e21)) return null;
            const atrApprox = (visible[i]?.high - visible[i]?.low) || 0;
            const bandH = toY(e21 - atrApprox * 0.5) - toY(e21 + atrApprox * 0.5);
            return (
              <rect key={i} x={i * (BAR + 2)} y={toY(e21 + atrApprox * 0.5)}
                width={BAR + 2} height={Math.max(bandH, 0)} fill="#38bdf8" opacity={0.04} />
            );
          })}

          {/* SL/TP lines from last signal */}
          {lastSig?.sl && lastSig.sl > 0 && minP < lastSig.sl && lastSig.sl < maxP && (
            <>
              <line x1={0} x2={CW} y1={toY(lastSig.sl)} y2={toY(lastSig.sl)} stroke="#ef4444" strokeWidth={1} strokeDasharray="6 3" opacity={0.6} />
              <text x={CW - 6} y={toY(lastSig.sl) - 3} fontSize={9} fill="#ef4444" textAnchor="end" opacity={0.9}>SL</text>
            </>
          )}
          {lastSig?.tp && lastSig.tp > 0 && minP < lastSig.tp && lastSig.tp < maxP && (
            <>
              <line x1={0} x2={CW} y1={toY(lastSig.tp)} y2={toY(lastSig.tp)} stroke="#22c55e" strokeWidth={1} strokeDasharray="6 3" opacity={0.6} />
              <text x={CW - 6} y={toY(lastSig.tp) - 3} fontSize={9} fill="#22c55e" textAnchor="end" opacity={0.9}>TP</text>
            </>
          )}

          {/* Candles */}
          {visible.map((c, i) => {
            const x = i * (BAR + 2);
            const up = c.close >= c.open;
            const color = up ? "#22c55e" : "#ef4444";
            const bodyTop = toY(Math.max(c.open, c.close));
            const bodyH   = Math.max(toY(Math.min(c.open, c.close)) - bodyTop, 1);
            return (
              <g key={i}>
                <line x1={x + BAR / 2} x2={x + BAR / 2} y1={toY(c.high)} y2={toY(c.low)}
                  stroke={color} strokeWidth={1} opacity={0.5} />
                <rect x={x} y={bodyTop} width={BAR} height={bodyH}
                  fill={color} fillOpacity={c.is_closed === false ? 0.5 : 0.85} />
              </g>
            );
          })}

          {/* Live price line */}
          {lastClose && (
            <line x1={0} x2={CW} y1={toY(lastClose)} y2={toY(lastClose)}
              stroke={chartUp ? "#22c55e" : "#ef4444"} strokeWidth={1} strokeDasharray="3 3" opacity={0.5} />
          )}

          {/* Signal markers */}
          {signalMarkers.map((m, i) => {
            const isLong = m.dir === "long";
            const color  = isLong ? "#22c55e" : "#ef4444";
            const path   = isLong
              ? `M ${m.x} ${m.y - 6} L ${m.x + 5} ${m.y + 3} L ${m.x - 5} ${m.y + 3} Z`
              : `M ${m.x} ${m.y + 6} L ${m.x + 5} ${m.y - 3} L ${m.x - 5} ${m.y - 3} Z`;
            return (
              <g key={i}>
                <circle cx={m.x} cy={m.y} r={9} fill={color} opacity={0.12} />
                <path d={path} fill={color} opacity={0.95} />
              </g>
            );
          })}
        </svg>

        {/* Legend */}
        <div className="absolute top-2 left-2 flex items-center gap-3 text-[9px] font-mono">
          <span className="flex items-center gap-1"><span className="w-4 h-px bg-orange-400 inline-block" />EMA50</span>
          <span className="flex items-center gap-1"><span className="w-4 h-px bg-sky-400 inline-block" />EMA21</span>
          <span className="flex items-center gap-1"><span className="w-4 h-px bg-violet-400 inline-block opacity-70" style={{ borderTop: "1px dashed" }} />VWAP</span>
          {signalMarkers.length > 0 && <span className="text-green-400">▲ / <span className="text-red-400">▼</span> Signals</span>}
        </div>
      </div>

      {/* RSI panel */}
      <div className="relative" style={{ height: RSI_H + 16 }}>
        <div className="absolute left-0 top-0 text-[8px] text-neutral-700 font-mono px-1">RSI(14)</div>
        <svg viewBox={`0 0 ${CW} ${RSI_H}`} className="w-full" style={{ height: RSI_H }} preserveAspectRatio="none">
          <line x1={0} x2={CW} y1={toRsiY(70)} y2={toRsiY(70)} stroke="#ef444430" strokeWidth={1} />
          <line x1={0} x2={CW} y1={toRsiY(50)} y2={toRsiY(50)} stroke="#52525280" strokeWidth={1} />
          <line x1={0} x2={CW} y1={toRsiY(30)} y2={toRsiY(30)} stroke="#22c55e30" strokeWidth={1} />
          {rsiPts && <polyline points={rsiPts} fill="none" stroke="#a78bfa" strokeWidth={1.5} />}
          {!isNaN(rsi[rsi.length - 1]) && (
            <text x={CW - 4} y={toRsiY(rsi[rsi.length - 1])} fontSize={8} fill="#a78bfa" textAnchor="end">
              {rsi[rsi.length - 1].toFixed(1)}
            </text>
          )}
        </svg>
        <div className="absolute right-1 text-[8px] text-neutral-700 font-mono" style={{ top: toRsiY(70) }}>70</div>
        <div className="absolute right-1 text-[8px] text-neutral-700 font-mono" style={{ top: toRsiY(50) }}>50</div>
        <div className="absolute right-1 text-[8px] text-neutral-700 font-mono" style={{ top: toRsiY(30) }}>30</div>
      </div>

      {/* Volume bars */}
      <div className="flex items-end gap-px h-6">
        {visible.slice(-100).map((c, i) => {
          const maxV = Math.max(...visible.slice(-100).map(x => x.volume));
          return (
            <div key={i} className="flex-1 min-w-0 rounded-t"
              style={{
                height: `${Math.max((c.volume / maxV) * 100, 4)}%`,
                background: c.close >= c.open ? "rgba(34,197,94,0.3)" : "rgba(239,68,68,0.3)",
              }} />
          );
        })}
      </div>
    </div>
  );
}

// ─── Analysis Checklist ───────────────────────────────────────────────────────
function AnalysisPanel({ analysis, loading }: { analysis: Analysis | null; loading: boolean }) {
  if (loading || !analysis) return (
    <div className="flex items-center justify-center h-40">
      <div className="w-4 h-4 border-2 rounded-full animate-spin border-t-transparent border-blue-500" />
    </div>
  );

  const { conditions, indicators, bias, met_count, total, all_met, last_signal } = analysis;
  const biasCls = bias === "long" ? "text-green-400" : bias === "short" ? "text-red-400" : "text-neutral-400";

  return (
    <div className="space-y-3">
      {/* Bias header */}
      <div className="flex items-center justify-between">
        <div>
          <div className="text-[10px] text-neutral-600 uppercase tracking-wider mb-0.5">Agent Bias</div>
          <div className={`text-base font-bold uppercase tracking-wide ${biasCls}`}>
            {bias === "long" ? "▲ BULLISH" : bias === "short" ? "▼ BEARISH" : "— NEUTRAL"}
          </div>
        </div>
        <div className="text-right">
          <div className="text-[10px] text-neutral-600 mb-0.5">Conditions</div>
          <div className="flex items-center gap-1">
            <span className={`text-lg font-bold ${all_met ? "text-green-400" : "text-neutral-300"}`}>{met_count}</span>
            <span className="text-neutral-600 text-sm">/ {total}</span>
          </div>
        </div>
      </div>

      {/* Condition bar */}
      <div className="flex gap-0.5">
        {conditions.map((c, i) => (
          <div key={i} className="flex-1 h-1 rounded-full"
            style={{ background: c.met ? "#22c55e" : "#262638" }} />
        ))}
      </div>

      {/* Conditions list */}
      <div className="space-y-1.5">
        {conditions.map((c, i) => (
          <div key={i} className="flex items-start gap-2">
            {c.met
              ? <CheckCircle2 size={12} className="text-green-400 flex-shrink-0 mt-0.5" />
              : <XCircle size={12} className="text-neutral-700 flex-shrink-0 mt-0.5" />}
            <div className="flex-1 min-w-0">
              <div className="flex items-center justify-between gap-2">
                <span className={`text-[11px] font-medium ${c.met ? "text-neutral-200" : "text-neutral-600"}`}>
                  {c.name}
                </span>
                <span className={`text-[10px] font-mono flex-shrink-0 ${c.met ? "text-green-400" : "text-neutral-600"}`}>
                  {c.value}
                </span>
              </div>
            </div>
          </div>
        ))}
      </div>

      {/* Key indicator values */}
      <div className="pt-2 border-t border-neutral-800 grid grid-cols-2 gap-x-4 gap-y-1">
        {[
          ["RSI", indicators.rsi ? `${indicators.rsi.toFixed(1)}` : "—"],
          ["Vol Ratio", indicators.vol_ratio ? `${indicators.vol_ratio.toFixed(2)}×` : "—"],
          ["EMA50", indicators.ema50 ? formatUSD(indicators.ema50) : "—"],
          ["EMA21", indicators.ema21 ? formatUSD(indicators.ema21) : "—"],
          ["VWAP", indicators.vwap ? formatUSD(indicators.vwap) : "—"],
          ["ATR%", indicators.atr_pct ? `${indicators.atr_pct.toFixed(3)}%` : "—"],
        ].map(([label, val]) => (
          <div key={label} className="flex items-center justify-between">
            <span className="text-[9px] text-neutral-700">{label}</span>
            <span className="text-[10px] font-mono text-neutral-400">{val}</span>
          </div>
        ))}
      </div>

      {/* Last signal */}
      {last_signal && (
        <div className={`p-2 rounded border text-[10px] ${
          last_signal.direction === "long"
            ? "border-green-500/20 bg-green-500/5"
            : "border-red-500/20 bg-red-500/5"
        }`}>
          <div className={`font-bold mb-0.5 ${last_signal.direction === "long" ? "text-green-400" : "text-red-400"}`}>
            Last Signal: {String(last_signal.direction).toUpperCase()} @ {formatUSD(last_signal.entry as number)}
          </div>
          <div className="text-neutral-600 font-mono">
            {last_signal.sl ? `SL ${formatUSD(last_signal.sl as number)}` : ""}
            {last_signal.tp ? `  TP ${formatUSD(last_signal.tp as number)}` : ""}
          </div>
          <div className="text-neutral-700 mt-0.5">{timeAgo(last_signal.timestamp as string)}</div>
        </div>
      )}

      {all_met && (
        <div className="flex items-center gap-1.5 p-2 rounded border border-green-500/30 bg-green-500/5">
          <Zap size={11} className="text-green-400" />
          <span className="text-[11px] text-green-400 font-semibold">All conditions met — trade imminent</span>
        </div>
      )}
    </div>
  );
}

// ─── Toast notifications ──────────────────────────────────────────────────────
function ToastNotification({ toast, onDismiss }: { toast: Toast; onDismiss: () => void }) {
  useEffect(() => { const t = setTimeout(onDismiss, 8000); return () => clearTimeout(t); }, [onDismiss]);
  const isLong  = toast.direction === "long";
  const isTrade = toast.kind === "trade";
  return (
    <div className="flex items-start gap-3 p-3 rounded-xl border backdrop-blur-sm shadow-2xl animate-slide-in"
      style={{
        background: isTrade ? (isLong ? "rgba(34,197,94,0.10)" : "rgba(239,68,68,0.10)") : "rgba(10,132,255,0.10)",
        borderColor: isTrade ? (isLong ? "rgba(34,197,94,0.25)" : "rgba(239,68,68,0.25)") : "rgba(10,132,255,0.25)",
        minWidth: 280,
      }}>
      <div className="mt-0.5">
        {isTrade
          ? isLong ? <ArrowUpRight size={16} className="text-green-400" /> : <ArrowDownRight size={16} className="text-red-400" />
          : <Zap size={16} className="text-blue-400" />}
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className={`text-[11px] font-bold uppercase tracking-wide ${isTrade ? (isLong ? "text-green-400" : "text-red-400") : "text-blue-400"}`}>
            {isTrade ? (isLong ? "▲ LONG FILLED" : "▼ SHORT FILLED") : "⚡ SIGNAL"}
          </span>
          <span className="text-[11px] text-neutral-400">{toast.symbol.replace("/USDT", "")}</span>
        </div>
        <div className="text-[13px] font-mono font-semibold text-white mt-0.5">{formatUSD(toast.price)}</div>
        {toast.strategy && <div className="text-[10px] text-neutral-500 mt-0.5 truncate">{toast.strategy}</div>}
      </div>
      <button onClick={onDismiss} className="text-neutral-600 hover:text-neutral-400 text-xs mt-0.5">✕</button>
    </div>
  );
}

function ToastContainer({ toasts, onDismiss }: { toasts: Toast[]; onDismiss: (id: number) => void }) {
  return (
    <div className="fixed bottom-6 right-6 z-50 flex flex-col gap-2 pointer-events-none">
      {toasts.map(t => (
        <div key={t.id} className="pointer-events-auto">
          <ToastNotification toast={t} onDismiss={() => onDismiss(t.id)} />
        </div>
      ))}
    </div>
  );
}

// ─── Activity row ─────────────────────────────────────────────────────────────
function ActivityRow({ ev }: { ev: ActivityEvent }) {
  const isLong = ev.direction === "long";
  const isTrade = ev.kind === "trade";
  const timeStr = new Date(ev.timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  return (
    <div className="flex items-start gap-2 px-4 py-2 border-b last:border-0 hover:bg-white/[0.02]"
      style={{ borderColor: "rgba(255,255,255,0.04)" }}>
      <div className={`mt-0.5 w-4 h-4 rounded-full flex items-center justify-center flex-shrink-0 ${isTrade ? (isLong ? "bg-green-500/15" : "bg-red-500/15") : "bg-blue-500/15"}`}>
        {isTrade
          ? isLong ? <ArrowUpRight size={9} className="text-green-400" /> : <ArrowDownRight size={9} className="text-red-400" />
          : <Zap size={9} className="text-blue-400" />}
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-1.5">
          <span className={`text-[10px] font-bold ${isLong ? "text-green-400" : "text-red-400"}`}>{isLong ? "▲" : "▼"}</span>
          <span className="text-[11px] font-semibold text-white">{ev.symbol.replace("/USDT", "")}</span>
          {isTrade && <span className="text-[9px] bg-blue-500/10 border border-blue-500/20 text-blue-400 px-1 rounded">PAPER</span>}
        </div>
        <div className="flex gap-2 text-[10px] font-mono mt-0.5">
          <span className="text-neutral-400">{formatUSD(isTrade ? (ev.fill_price || 0) : (ev.entry || 0))}</span>
          {ev.sl && ev.sl > 0 && <span className="text-red-400">SL {formatUSD(ev.sl)}</span>}
          {ev.tp && ev.tp > 0 && <span className="text-green-400">TP {formatUSD(ev.tp)}</span>}
        </div>
      </div>
      <span className="text-[9px] font-mono text-neutral-700 flex-shrink-0">{timeStr}</span>
    </div>
  );
}

// ─── Reset button ─────────────────────────────────────────────────────────────
function ResetPaperButton({ onReset }: { onReset: () => void }) {
  const [loading, setLoading] = useState(false);
  const [done, setDone] = useState(false);
  const go = async () => {
    if (!confirm("Reset paper account to $10,000? All positions cleared.")) return;
    setLoading(true);
    try {
      const r = await fetch(`${process.env.NEXT_PUBLIC_API_URL || ""}/api/paper/reset`, {
        method: "POST", headers: { Authorization: `Bearer ${localStorage.getItem("token")}` },
      });
      if (r.ok) { setDone(true); setTimeout(() => setDone(false), 3000); onReset(); }
    } finally { setLoading(false); }
  };
  return (
    <button onClick={go} disabled={loading}
      className="flex items-center gap-1 px-2 py-1 rounded text-[10px] font-medium border transition-colors"
      style={{ background: done ? "rgba(34,197,94,0.1)" : "rgba(255,255,255,0.03)", borderColor: done ? "rgba(34,197,94,0.3)" : "rgba(255,255,255,0.06)", color: done ? "#22c55e" : "#52525e" }}>
      <RotateCcw size={9} className={loading ? "animate-spin" : ""} />
      {done ? "Done!" : "Reset $10k"}
    </button>
  );
}

// ─── Live Price Ticker ────────────────────────────────────────────────────────
function LivePriceTicker({ symbol, price }: { symbol: string; price: LivePrice | null }) {
  const prev = useRef<number | null>(null);
  const [flash, setFlash] = useState<"up" | "down" | null>(null);
  useEffect(() => {
    if (!price) return;
    if (prev.current !== null && price.last !== prev.current)
      setFlash(price.last > prev.current ? "up" : "down");
    prev.current = price.last;
    const t = setTimeout(() => setFlash(null), 500);
    return () => clearTimeout(t);
  }, [price?.last]);
  if (!price) return null;
  return (
    <div className="py-1.5 border-b last:border-0" style={{ borderColor: "rgba(255,255,255,0.04)" }}>
      <div className="flex items-center justify-between">
        <span className="text-[11px] font-semibold" style={{ color: "#5a5a7a" }}>{symbol.replace("/USDT", "")}</span>
        <div className="flex items-center gap-2">
          <span className="text-[12px] font-mono font-semibold transition-colors duration-300"
            style={{ color: flash === "up" ? "#30d158" : flash === "down" ? "#ff453a" : "#f0f0f8" }}>
            {formatUSD(price.last)}
          </span>
          <span className="text-[10px] font-mono w-12 text-right"
            style={{ color: price.change_pct >= 0 ? "#30d158" : "#ff453a" }}>
            {price.change_pct >= 0 ? "+" : ""}{price.change_pct.toFixed(2)}%
          </span>
        </div>
      </div>
    </div>
  );
}

// ─── Page ─────────────────────────────────────────────────────────────────────
const SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "ADA/USDT"];

export default function OverviewPage() {
  const [data, setData] = useState<Overview | null>(null);
  const [loading, setLoading] = useState(true);
  const [lastUpdate, setLastUpdate] = useState<Date | null>(null);
  const [livePrices, setLivePrices] = useState<Record<string, LivePrice>>({});
  const [streamConnected, setStreamConnected] = useState(false);
  const [activity, setActivity] = useState<ActivityEvent[]>([]);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [analysisLoading, setAnalysisLoading] = useState(true);
  const [chartCandles, setChartCandles] = useState<BinanceCandle[]>([]);
  const [liveCandle, setLiveCandle] = useState<BinanceCandle | null>(null);
  const [signals, setSignals] = useState<Array<{ timestamp: string; direction: string; entry: number; sl?: number | null; tp?: number | null }>>([]);
  const toastId = useRef(0);

  const addToast = useCallback((t: Omit<Toast, "id">) => {
    setToasts(p => [...p.slice(-4), { ...t, id: ++toastId.current }]);
  }, []);

  const authFetch = useCallback(async (url: string) => {
    const r = await fetch(`${process.env.NEXT_PUBLIC_API_URL || ""}${url}`, {
      headers: { Authorization: `Bearer ${localStorage.getItem("token")}` },
    });
    if (r.ok) return r.json();
    return null;
  }, []);

  const fetchData     = useCallback(async () => {
    try { const r = await overviewApi.get(); setData(r); setLastUpdate(new Date()); }
    catch { } finally { setLoading(false); }
  }, []);

  const fetchActivity = useCallback(async () => {
    const d = await authFetch("/api/paper/activity?limit=40");
    if (d) setActivity(d);
  }, [authFetch]);

  // Load chart candles directly from market API (Bybit-backed, reliable)
  const fetchChartCandles = useCallback(async () => {
    try {
      const d = await authFetch("/api/market/candles/BTC%2FUSDT?timeframe=15m&limit=120");
      if (Array.isArray(d) && d.length > 0) {
        setChartCandles(d.map((c: { timestamp: string; open: number; high: number; low: number; close: number; volume: number }) => ({
          ...c, is_closed: true,
        })));
      }
    } catch { /* silent */ }
  }, [authFetch]);

  const fetchAnalysis = useCallback(async () => {
    setAnalysisLoading(true);
    try {
      const d: Analysis | null = await authFetch("/api/paper/analysis");
      if (d) setAnalysis(d);
    } finally {
      setAnalysisLoading(false);
    }
  }, [authFetch]);

  const fetchSignals = useCallback(async () => {
    const d = await authFetch("/api/market/signals?symbol=BTC%2FUSDT&limit=50");
    if (d) setSignals(d);
  }, [authFetch]);

  useEffect(() => {
    fetchData(); fetchActivity(); fetchChartCandles(); fetchAnalysis(); fetchSignals();
    const i1 = setInterval(fetchData,         15_000);
    const i2 = setInterval(fetchActivity,     20_000);
    const i3 = setInterval(fetchChartCandles, 60_000);  // refresh every 15m candle
    const i4 = setInterval(fetchAnalysis,     60_000);
    const i5 = setInterval(fetchSignals,      30_000);
    return () => { clearInterval(i1); clearInterval(i2); clearInterval(i3); clearInterval(i4); clearInterval(i5); };
  }, [fetchData, fetchActivity, fetchChartCandles, fetchAnalysis, fetchSignals]);

  // ── Binance direct stream for live BTC 15m candle ────────────────────────
  useBinanceStream({
    symbols: SYMBOLS,
    timeframe: "15m",
    onTicker: useCallback((t: import("@/hooks/useBinanceStream").BinanceTicker) => {
      setLivePrices(p => ({ ...p, [t.symbol]: { last: t.last, change_pct: t.change_pct } }));
    }, []),
    onCandle: useCallback((sym: string, candle: BinanceCandle) => {
      if (sym !== "BTC/USDT") return;
      setLiveCandle(candle);
      // Merge live candle into the historical chart candles
      setChartCandles(prev => {
        if (!prev.length) return prev;
        const last = prev[prev.length - 1];
        const lastMs = new Date(last.timestamp).getTime();
        const curMs  = new Date(candle.timestamp).getTime();
        if (lastMs === curMs) return [...prev.slice(0, -1), { ...candle }];
        if (curMs > lastMs)   return [...prev.slice(-119), { ...candle }];
        return prev;
      });
    }, []),
  });

  // ── WebSocket backend events ──────────────────────────────────────────────
  const { lastMessage, connected } = useWebSocket();
  useEffect(() => { setStreamConnected(connected); }, [connected]);
  useEffect(() => {
    if (!lastMessage) return;
    const { type, data: d } = lastMessage as { type?: string; data?: Record<string, unknown> };
    if (type === "signal:new" && d) {
      const dir = d.direction as string;
      if (dir === "long" || dir === "short") {
        addToast({ kind: "signal", direction: dir, symbol: d.symbol as string, price: (d.entry as number) || 0, strategy: d.strategy_name as string });
        fetchActivity(); fetchSignals();
      }
    }
    if (type === "execution:order_placed" && d && d.event !== "paper_reset") {
      const side = d.side as string;
      addToast({ kind: "trade", direction: side === "buy" ? "long" : "short", symbol: d.symbol as string, price: (d.fill_price as number) || 0, strategy: "Paper Trade Executed" });
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
          <div className="flex items-center gap-3 px-5 py-4 rounded-apple"
            style={{ background: "rgba(255,69,58,0.08)", border: "1px solid rgba(255,69,58,0.2)" }}>
            <ShieldCheck size={16} style={{ color: "#ff453a" }} />
            <span className="text-[14px] font-semibold" style={{ color: "#ff453a" }}>Emergency Stop Active — All trading halted</span>
          </div>
        )}

        {/* KPI row */}
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
          <KPICard label="Total Equity" value={formatUSD(d?.equity || 0)} subValue={`Available ${formatUSD(d?.available_balance || 0)}`} icon={DollarSign} trend="neutral" />
          <KPICard label="Daily PnL" value={formatUSD(d?.daily_pnl || 0)} subValue={formatPct(d?.daily_pnl_pct || 0)} subColor={pnlColor(d?.daily_pnl || 0)} icon={d?.daily_pnl >= 0 ? TrendingUp : TrendingDown} trend={d?.daily_pnl >= 0 ? "up" : "down"} />
          <KPICard label="Win Rate" value={`${(d?.win_rate || 0).toFixed(1)}%`} subValue={`${d?.total_trades || 0} trades`} icon={BarChart2} />
          <KPICard label="Active Strategies" value={String(d?.active_strategies || 0)} icon={Layers} />
        </div>

        {/* ── MAIN: Chart (left) + Analysis (right) ── */}
        <div className="grid grid-cols-12 gap-4">
          {/* Chart */}
          <div className="col-span-12 lg:col-span-8 card p-4">
            <div className="flex items-center justify-between mb-3">
              <div className="flex items-center gap-3">
                <span className="text-[13px] font-semibold text-white">BTC/USDT · 15m</span>
                <span className="text-[11px] text-neutral-600">Momentum Velocity Strategy</span>
                {connected && (
                  <span className="flex items-center gap-1 text-[10px] text-green-400">
                    <span className="w-1 h-1 rounded-full bg-green-400 animate-pulse" /> live
                  </span>
                )}
              </div>
              <div className="flex items-center gap-3">
                {analysis && (
                  <span className="text-[11px] font-mono text-neutral-400">
                    {analysis.indicators.price ? formatUSD(analysis.indicators.price) : ""}
                  </span>
                )}
                <div className={`text-[10px] font-bold px-2 py-0.5 rounded border ${
                  analysis?.bias === "long"  ? "border-green-500/30 bg-green-500/10 text-green-400" :
                  analysis?.bias === "short" ? "border-red-500/30 bg-red-500/10 text-red-400" :
                  "border-neutral-700 text-neutral-500"}`}>
                  {analysis?.bias === "long" ? "▲ BULLISH" : analysis?.bias === "short" ? "▼ BEARISH" : "NEUTRAL"}
                </div>
              </div>
            </div>
            <StrategyChart
              candles={chartCandles}
              liveCandle={liveCandle}
              signals={signals}
            />
          </div>

          {/* Analysis panel */}
          <div className="col-span-12 lg:col-span-4 card p-4">
            <div className="flex items-center justify-between mb-3">
              <div className="flex items-center gap-2">
                <Activity size={13} className="text-neutral-500" />
                <span className="text-[13px] font-semibold text-white">Agent Analysis</span>
              </div>
              <div className="flex items-center gap-2">
                <span className="text-[9px] text-neutral-600 flex items-center gap-1">
                  <Clock size={9} /> 60s cycle
                </span>
                <button onClick={fetchAnalysis} className="p-1 rounded hover:bg-neutral-800">
                  <RefreshCw size={10} className={`text-neutral-600 ${analysisLoading ? "animate-spin" : ""}`} />
                </button>
              </div>
            </div>
            <AnalysisPanel analysis={analysis} loading={analysisLoading} />
          </div>
        </div>

        {/* Live market strip */}
        <div className="card px-5 py-3">
          <div className="flex items-center justify-between mb-2">
            <span className="label">Live Market</span>
            {streamConnected
              ? <span className="flex items-center gap-1 text-[10px] text-green-400"><span className="w-1 h-1 rounded-full bg-green-400 animate-pulse" />Streaming</span>
              : <span className="flex items-center gap-1 text-[10px] text-neutral-600"><WifiOff size={9} /> Connecting</span>}
          </div>
          <div className="grid grid-cols-5 gap-6">
            {SYMBOLS.map(sym => <LivePriceTicker key={sym} symbol={sym} price={livePrices[sym] || null} />)}
          </div>
        </div>

        {/* Bottom: positions + orders + activity */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
          {/* Open positions */}
          <div className="card overflow-hidden">
            <div className="flex items-center justify-between px-5 py-3.5" style={{ borderBottom: "1px solid rgba(255,255,255,0.05)" }}>
              <span className="text-[13px] font-semibold text-white">Open Positions</span>
              <button onClick={fetchData} className="p-1.5 rounded-lg text-neutral-700 hover:text-white hover:bg-neutral-800">
                <RefreshCw size={11} />
              </button>
            </div>
            {!d?.positions?.length
              ? <p className="px-5 py-8 text-center text-[12px]" style={{ color: "#2a2a3e" }}>No open positions</p>
              : <table className="w-full text-[12px]">
                  <thead><tr style={{ borderBottom: "1px solid rgba(255,255,255,0.04)" }}>
                    {["Symbol", "Side", "Entry", "PnL"].map((h, i) => (
                      <th key={h} className={`${i > 1 ? "text-right" : "text-left"} px-4 py-2 label`}>{h}</th>
                    ))}
                  </tr></thead>
                  <tbody>
                    {d.positions.map(p => (
                      <tr key={p.id} style={{ borderBottom: "1px solid rgba(255,255,255,0.03)" }}>
                        <td className="px-4 py-2.5 font-semibold text-white">{p.symbol.replace("/USDT", "")}</td>
                        <td className={cn("px-4 py-2.5 uppercase font-bold text-[10px]", sideColor(p.side))}>{p.side}</td>
                        <td className="px-4 py-2.5 text-right font-mono text-[11px] text-neutral-300">{p.entry_price.toFixed(0)}</td>
                        <td className={cn("px-4 py-2.5 text-right font-mono font-semibold text-[11px]", pnlColor(p.unrealized_pnl))}>{formatUSD(p.unrealized_pnl)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>}
          </div>

          {/* Recent orders */}
          <div className="card overflow-hidden">
            <div className="px-5 py-3.5" style={{ borderBottom: "1px solid rgba(255,255,255,0.05)" }}>
              <span className="text-[13px] font-semibold text-white">Recent Orders</span>
            </div>
            {!d?.recent_orders?.length
              ? <p className="px-5 py-8 text-center text-[12px]" style={{ color: "#2a2a3e" }}>No recent orders</p>
              : <table className="w-full text-[12px]">
                  <thead><tr style={{ borderBottom: "1px solid rgba(255,255,255,0.04)" }}>
                    {["Symbol", "Side", "Status", "Time"].map((h, i) => (
                      <th key={h} className={`${i === 3 ? "text-right" : "text-left"} px-4 py-2 label`}>{h}</th>
                    ))}
                  </tr></thead>
                  <tbody>
                    {d.recent_orders.map(o => (
                      <tr key={o.id} style={{ borderBottom: "1px solid rgba(255,255,255,0.03)" }}>
                        <td className="px-4 py-2.5 font-semibold text-white">{o.symbol.replace("/USDT", "")}</td>
                        <td className={cn("px-4 py-2.5 uppercase font-bold text-[10px]", sideColor(o.side))}>{o.side}</td>
                        <td className={cn("px-4 py-2.5 uppercase font-semibold text-[10px]", statusColor(o.status))}>{o.status}</td>
                        <td className="px-4 py-2.5 text-right text-[10px] text-neutral-600">{timeAgo(o.created_at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>}
          </div>

          {/* Live activity feed */}
          <div className="card overflow-hidden flex flex-col">
            <div className="flex items-center justify-between px-5 py-3.5 flex-shrink-0" style={{ borderBottom: "1px solid rgba(255,255,255,0.05)" }}>
              <div className="flex items-center gap-2">
                <Bell size={12} className="text-neutral-600" />
                <span className="text-[13px] font-semibold text-white">Live Activity</span>
                {activity.length > 0 && (
                  <span className="text-[9px] bg-blue-500/10 border border-blue-500/20 text-blue-400 px-1.5 py-0.5 rounded font-mono">{activity.length}</span>
                )}
              </div>
              <ResetPaperButton onReset={() => { fetchData(); fetchActivity(); }} />
            </div>
            <div className="overflow-y-auto flex-1 max-h-72">
              {activity.length === 0
                ? <div className="px-5 py-8 text-center">
                    <Zap size={18} className="text-neutral-800 mx-auto mb-2" />
                    <p className="text-[12px] text-neutral-700">Waiting for signals…</p>
                    <p className="text-[10px] mt-1 text-neutral-800">Agent runs every 60s on minute close</p>
                  </div>
                : activity.map((ev, i) => <ActivityRow key={i} ev={ev} />)}
            </div>
          </div>
        </div>

        {lastUpdate && (
          <p className="text-[10px] text-right" style={{ color: "#1e1e2e" }}>Updated {lastUpdate.toLocaleTimeString()}</p>
        )}
      </div>
    </>
  );
}
