"use client";

/**
 * useORBStrategy — Opening Range Breakout 30 (ORB-30)
 * ─────────────────────────────────────────────────────
 * Based on the 5-year NASDAQ backtest (2020-2024):
 *   Total Return: +94.3%  |  Win Rate: 55.3%  |  Profit Factor: 1.81
 *   Sharpe: 1.74  |  Avg R:R: 2.25:1  |  Max DD: -13.8%
 *
 * Adapted for BTC/USDT 24/7:
 *   Sessions: 4-hour UTC blocks (00,04,08,12,16,20)
 *   ORB window: first 30×1m bars of each session (30 min)
 *   After ORB: trade breakout of OR_HIGH (long) or OR_LOW (short)
 *   Trend filter: 15m EMA20 — price above = long bias, below = short bias
 *   Stop loss: opposite ORB boundary (± buffer)
 *   Take profit: 2.25R (matches backtest avg R:R)
 *   Time exit: max 90 bars (90 min) per session
 *   Volume filter: breakout bar volume > 1.3× session ORB avg
 */

import { useMemo } from "react";
import { BinanceCandle } from "./useBinanceStream";

// ── Strategy parameters (matching the backtest stats) ─────────────────────────
const P = {
  orb_bars:       30,      // 30×1m = 30-minute opening range
  ema_period:     20,      // 15m EMA20 trend filter (from PDF recommendation)
  rr_target:      2.25,    // Avg R:R from 5-yr backtest
  sl_buffer_pct:  0.0003,  // 0.03% buffer beyond range boundary for SL
  vol_ratio:      1.3,     // breakout bar volume ≥ 1.3× ORB session avg
  max_hold_bars:  90,      // max 90 min per session (2× avg trade duration)
  or_range_min:   0.08,    // OR range must be ≥ 0.08% (avoid dead markets)
  or_range_max:   3.5,     // OR range must be ≤ 3.5% (avoid gap blow-outs)
  session_hours:  [0, 4, 8, 12, 16, 20] as number[],
};

// ── Types ─────────────────────────────────────────────────────────────────────
export interface ORBSession {
  session_start:    string;
  or_high:          number;
  or_low:           number;
  or_established:   boolean;
  or_range_pct:     number;
}

export interface ORBSignal {
  direction:  "long" | "short";
  entry:      number;
  sl:         number;
  tp:         number;
  tp_ib:      number;       // IB-level target (1R — use as 50% exit)
  rr:         string;
  confidence: number;
  reasoning:  string;
  timestamp:  string;
  or_high:    number;
  or_low:     number;
}

export interface ORBCondition { name: string; met: boolean; value: string; }

export interface ORBResult {
  signal:          ORBSignal | null;
  current_session: ORBSession | null;
  bias:            "long" | "short" | "neutral";
  conditions:      ORBCondition[];
  met_count:       number;
  total:           number;
  all_met:         boolean;
  candle_count:    number;
  indicators: {
    or_high:         number | null;
    or_low:          number | null;
    or_range_pct:    number | null;
    ema20_15m:       number | null;
    last_price:      number | null;
    session_vol_avg: number | null;
    bars_in_orb:     number;
    bars_since_orb:  number;
    session_label:   string;
  };
}

// ── Helpers ───────────────────────────────────────────────────────────────────
function ema(vals: number[], period: number): number[] {
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

function to15m(candles: BinanceCandle[]): BinanceCandle[] {
  const groups = new Map<number, BinanceCandle[]>();
  for (const c of candles) {
    const ms     = new Date(c.timestamp).getTime();
    const bucket = Math.floor(ms / 900_000) * 900_000;
    if (!groups.has(bucket)) groups.set(bucket, []);
    groups.get(bucket)!.push(c);
  }
  return Array.from(groups.entries())
    .sort(([a], [b]) => a - b)
    .map(([, bars]) => ({
      timestamp: bars[0].timestamp,
      open:   bars[0].open,
      high:   Math.max(...bars.map(b => b.high)),
      low:    Math.min(...bars.map(b => b.low)),
      close:  bars[bars.length - 1].close,
      volume: bars.reduce((s, b) => s + b.volume, 0),
      is_closed: bars.length >= 14,
    }));
}

function getSessionBoundary(ts: string): { startMs: number; label: string } {
  const d  = new Date(ts);
  const h  = d.getUTCHours();
  const day = new Date(d);
  day.setUTCHours(0, 0, 0, 0);
  let sessionH = 0;
  for (const sh of P.session_hours) { if (h >= sh) sessionH = sh; }
  const startMs = day.getTime() + sessionH * 3_600_000;
  const endH    = (sessionH + 4) % 24;
  const label   = `${String(sessionH).padStart(2,"0")}:00–${String(endH).padStart(2,"0")}:00 UTC`;
  return { startMs, label };
}

// ── Main hook ─────────────────────────────────────────────────────────────────
export function useORBStrategy(candles1m: BinanceCandle[]): ORBResult {
  const nullResult: ORBResult = {
    signal: null, current_session: null, bias: "neutral",
    conditions: [], met_count: 0, total: 6, all_met: false,
    candle_count: candles1m.length,
    indicators: {
      or_high: null, or_low: null, or_range_pct: null, ema20_15m: null,
      last_price: null, session_vol_avg: null,
      bars_in_orb: 0, bars_since_orb: 0, session_label: "—",
    },
  };

  return useMemo(() => {
    if (candles1m.length < P.orb_bars + 5) return { ...nullResult, candle_count: candles1m.length };

    const lastCandle = candles1m[candles1m.length - 1];
    const { startMs, label } = getSessionBoundary(lastCandle.timestamp);
    const orbEndMs = startMs + P.orb_bars * 60_000;

    // Split into ORB window and post-ORB
    const orbCandles  = candles1m.filter(c => {
      const ms = new Date(c.timestamp).getTime();
      return ms >= startMs && ms < orbEndMs;
    });
    const postOrb     = candles1m.filter(c => {
      const ms = new Date(c.timestamp).getTime();
      return ms >= orbEndMs;
    });

    const orbEstablished  = orbCandles.length >= P.orb_bars;
    const barsInOrb       = orbCandles.length;
    const barsAfterOrb    = postOrb.length;

    // 15m EMA20 trend filter
    const bars15m    = to15m(candles1m);
    const closes15m  = bars15m.map(c => c.close);
    const ema20arr   = ema(closes15m, P.ema_period);
    const ema20_15m  = ema20arr[ema20arr.length - 1];

    if (!orbEstablished) {
      return {
        ...nullResult, candle_count: candles1m.length,
        indicators: {
          ...nullResult.indicators, bars_in_orb: barsInOrb,
          bars_since_orb: 0, session_label: label,
          ema20_15m: isNaN(ema20_15m) ? null : ema20_15m,
          last_price: lastCandle.close,
        },
        current_session: { session_start: new Date(startMs).toISOString(),
          or_high: 0, or_low: 0, or_established: false, or_range_pct: 0 },
      };
    }

    // Opening Range
    const OR_HIGH = Math.max(...orbCandles.map(c => c.high));
    const OR_LOW  = Math.min(...orbCandles.map(c => c.low));
    const orMid   = (OR_HIGH + OR_LOW) / 2;
    const orRange = OR_HIGH - OR_LOW;
    const orRangePct = orRange / orMid * 100;

    // Session volume baseline
    const sessVolAvg = orbCandles.length > 0
      ? orbCandles.reduce((s, c) => s + c.volume, 0) / orbCandles.length
      : 1;

    const cur = postOrb.length > 0
      ? postOrb[postOrb.length - 1]
      : orbCandles[orbCandles.length - 1];

    // Conditions
    const isAboveEMA = !isNaN(ema20_15m) && cur.close > ema20_15m;
    const isBelowEMA = !isNaN(ema20_15m) && cur.close < ema20_15m;
    const bias: "long" | "short" | "neutral" = isAboveEMA ? "long" : isBelowEMA ? "short" : "neutral";

    const breakoutLong  = postOrb.length > 0 && cur.close > OR_HIGH;
    const breakoutShort = postOrb.length > 0 && cur.close < OR_LOW;
    const volRatio      = cur.volume / sessVolAvg;
    const volOk         = volRatio >= P.vol_ratio;
    const withinSession = barsAfterOrb > 0 && barsAfterOrb <= P.max_hold_bars;
    const rangeValid    = orRangePct >= P.or_range_min && orRangePct <= P.or_range_max;

    const isLong  = breakoutLong  && isAboveEMA && volOk && withinSession && rangeValid;
    const isShort = breakoutShort && isBelowEMA && volOk && withinSession && rangeValid;

    const dir = isLong ? "long" : isShort ? "short" : null;

    const conditions: ORBCondition[] = [
      {
        name: `15m EMA20 trend (${bias === "long" ? "bullish" : bias === "short" ? "bearish" : "neutral"})`,
        met: bias !== "neutral",
        value: isNaN(ema20_15m) ? "—" : `EMA ${ema20_15m.toFixed(0)} · close ${cur.close.toFixed(0)}`,
      },
      {
        name: "ORB-30 established (first 30 min)",
        met: orbEstablished,
        value: `OR ${OR_HIGH.toFixed(0)} / ${OR_LOW.toFixed(0)} · range ${orRangePct.toFixed(2)}%`,
      },
      {
        name: bias === "long" ? "Close above OR High (breakout)" : "Close below OR Low (breakout)",
        met: dir !== null,
        value: postOrb.length > 0
          ? `${cur.close.toFixed(0)} vs OR ${dir === "long" || (!dir && isAboveEMA) ? OR_HIGH.toFixed(0) : OR_LOW.toFixed(0)}`
          : "waiting…",
      },
      {
        name: `Volume ≥ ${P.vol_ratio}× session avg (confirmation)`,
        met: volOk,
        value: `${volRatio.toFixed(2)}× (avg ${sessVolAvg.toFixed(1)})`,
      },
      {
        name: `Within session window (≤ ${P.max_hold_bars} bars)`,
        met: withinSession,
        value: `bar ${barsAfterOrb}/${P.max_hold_bars}`,
      },
      {
        name: "OR range 0.08–3.5% (clean range)",
        met: rangeValid,
        value: `${orRangePct.toFixed(2)}%`,
      },
    ];

    const met_count = conditions.filter(c => c.met).length;
    const all_met   = conditions.every(c => c.met);

    let signal: ORBSignal | null = null;
    if (isLong || isShort) {
      const entry   = cur.close;
      const slBase  = isLong ? OR_LOW : OR_HIGH;
      const slBuf   = slBase * P.sl_buffer_pct;
      const sl      = isLong ? slBase - slBuf : slBase + slBuf;
      const risk    = Math.abs(entry - sl);
      const tp      = isLong ? entry + P.rr_target * risk : entry - P.rr_target * risk;
      const tp_ib   = isLong ? entry + 1.0 * risk : entry - 1.0 * risk; // 1R IB-level partial exit

      const breakStrength = isLong
        ? (cur.close - OR_HIGH) / orRange
        : (OR_LOW - cur.close) / orRange;
      const volBonus  = Math.min((volRatio - P.vol_ratio) / P.vol_ratio, 1);
      const confidence = Math.min(0.48 + Math.max(0, breakStrength) * 0.25 + volBonus * 0.27, 0.95);

      signal = {
        direction: isLong ? "long" : "short",
        entry, sl, tp, tp_ib,
        rr: `1 : ${P.rr_target}`,
        confidence: Math.round(confidence * 1000) / 1000,
        reasoning: `${isLong ? "▲ LONG" : "▼ SHORT"} ORB-30: close ${entry.toFixed(0)} broke ${isLong ? "above OR High" : "below OR Low"} ${isLong ? OR_HIGH.toFixed(0) : OR_LOW.toFixed(0)} (range ${orRangePct.toFixed(2)}%). 15m EMA20 ${ema20_15m.toFixed(0)} confirms ${bias} bias. Vol ${volRatio.toFixed(2)}× avg. SL ${sl.toFixed(0)}, TP1 ${tp_ib.toFixed(0)} (50% at 1R), TP2 ${tp.toFixed(0)} (${P.rr_target}R). Session: ${label}.`,
        timestamp: new Date().toISOString(),
        or_high: OR_HIGH,
        or_low:  OR_LOW,
      };
    }

    return {
      signal, bias, conditions, met_count, total: 6, all_met,
      candle_count: candles1m.length,
      current_session: {
        session_start: new Date(startMs).toISOString(),
        or_high: OR_HIGH, or_low: OR_LOW,
        or_established: orbEstablished, or_range_pct: orRangePct,
      },
      indicators: {
        or_high: OR_HIGH, or_low: OR_LOW,
        or_range_pct: Math.round(orRangePct * 100) / 100,
        ema20_15m:    isNaN(ema20_15m) ? null : Math.round(ema20_15m * 10) / 10,
        last_price:   cur.close,
        session_vol_avg: Math.round(sessVolAvg * 10) / 10,
        bars_in_orb:    barsInOrb,
        bars_since_orb: barsAfterOrb,
        session_label:  label,
      },
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [candles1m]);
}
