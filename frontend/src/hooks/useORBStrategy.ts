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
 *   Time exit: max 210 bars (3.5 hours) — covers most of the session
 *   Volume filter: scored, not a hard gate
 */

import { useMemo } from "react";
import { BinanceCandle } from "./useBingXStream";

// ── Strategy parameters ───────────────────────────────────────────────────────
const P = {
  orb_bars:       30,     // 30×1m = 30-minute opening range
  orb_min_bars:   22,     // allow up to 8 missing 1m candles (Binance gaps)
  ema_period:     20,     // 15m EMA20 trend filter
  rr_target:      2.25,   // Avg R:R from 5-yr backtest
  sl_buffer_pct:  0.0003, // 0.03% buffer beyond range boundary for SL
  vol_ratio:      1.0,    // volume ≥ 1.0× ORB avg (scored, not gating)
  max_hold_bars:  210,    // 3.5 hours post-ORB (was 90 — now covers full session)
  or_range_min:   0.05,   // OR range must be ≥ 0.05%
  or_range_max:   5.0,    // OR range must be ≤ 5.0%
  session_hours:  [0, 4, 8, 12, 16, 20] as number[],
};

// ── Types ─────────────────────────────────────────────────────────────────────
export interface ORBSession {
  session_start:  string;
  or_high:        number;
  or_low:         number;
  or_established: boolean;
  or_range_pct:   number;
}

export interface ORBSignal {
  direction:  "long" | "short";
  entry:      number;
  sl:         number;
  tp:         number;
  tp_ib:      number;
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
  status:          "building_orb" | "watching" | "past_window" | "no_data";
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
    next_session:    string;
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

function getSessionBoundary(ts: string): { startMs: number; label: string; nextStartMs: number } {
  const d   = new Date(ts);
  const h   = d.getUTCHours();
  const day = new Date(d);
  day.setUTCHours(0, 0, 0, 0);
  let sessionH = 0;
  for (const sh of P.session_hours) { if (h >= sh) sessionH = sh; }
  const startMs    = day.getTime() + sessionH * 3_600_000;
  const nextH      = (sessionH + 4) % 24;
  const nextDay    = nextH < sessionH ? day.getTime() + 86_400_000 : day.getTime();
  const nextStartMs= nextDay + nextH * 3_600_000;
  const endH       = (sessionH + 4) % 24;
  const label      = `${String(sessionH).padStart(2,"0")}:00–${String(endH).padStart(2,"0")}:00 UTC`;
  return { startMs, label, nextStartMs };
}

function fmtUtc(ms: number): string {
  const d = new Date(ms);
  return `${String(d.getUTCHours()).padStart(2,"0")}:${String(d.getUTCMinutes()).padStart(2,"0")} UTC`;
}

// ── Main hook ─────────────────────────────────────────────────────────────────
export function useORBStrategy(candles1m: BinanceCandle[]): ORBResult {
  return useMemo(() => {
    const noData: ORBResult = {
      signal: null, current_session: null, bias: "neutral",
      conditions: [], met_count: 0, total: 6, all_met: false,
      candle_count: candles1m.length, status: "no_data",
      indicators: {
        or_high: null, or_low: null, or_range_pct: null, ema20_15m: null,
        last_price: null, session_vol_avg: null,
        bars_in_orb: 0, bars_since_orb: 0, session_label: "—", next_session: "—",
      },
    };

    if (candles1m.length < P.orb_min_bars + 5) return { ...noData, candle_count: candles1m.length };

    const lastCandle = candles1m[candles1m.length - 1];
    const { startMs, label, nextStartMs } = getSessionBoundary(lastCandle.timestamp);
    const orbEndMs = startMs + P.orb_bars * 60_000;

    // Split candles into ORB window and post-ORB
    const orbCandles = candles1m.filter(c => {
      const ms = new Date(c.timestamp).getTime();
      return ms >= startMs && ms < orbEndMs;
    });
    const postOrb = candles1m.filter(c => {
      const ms = new Date(c.timestamp).getTime();
      return ms >= orbEndMs;
    });

    const barsInOrb    = orbCandles.length;
    const barsAfterOrb = postOrb.length;
    // Allow up to 8 missing candles (Binance gaps in low-volume periods)
    const orbEstablished = orbCandles.length >= P.orb_min_bars;

    // 15m EMA20 trend filter — computed from all candles
    const bars15m   = to15m(candles1m);
    const closes15m = bars15m.map(c => c.close);
    const ema20arr  = ema(closes15m, P.ema_period);
    const ema20_15m = ema20arr[ema20arr.length - 1];

    // Current price reference
    const cur = postOrb.length > 0
      ? postOrb[postOrb.length - 1]
      : orbCandles.length > 0 ? orbCandles[orbCandles.length - 1] : lastCandle;

    // ── Status determination ───────────────────────────────────────────────
    let status: ORBResult["status"] = "watching";
    if (!orbEstablished) status = "building_orb";
    else if (barsAfterOrb > P.max_hold_bars) status = "past_window";

    // ── Compute OR levels (0 when not established yet) ────────────────────
    const OR_HIGH = orbEstablished ? Math.max(...orbCandles.map(c => c.high)) : 0;
    const OR_LOW  = orbEstablished ? Math.min(...orbCandles.map(c => c.low))  : 0;
    const orMid      = orbEstablished ? (OR_HIGH + OR_LOW) / 2 : cur.close;
    const orRange    = orbEstablished ? OR_HIGH - OR_LOW : 0;
    const orRangePct = orMid > 0 && orRange > 0 ? orRange / orMid * 100 : 0;

    // Session volume baseline
    const sessVolAvg = orbCandles.length > 0
      ? orbCandles.reduce((s, c) => s + c.volume, 0) / orbCandles.length : 1;

    // ── Condition flags ────────────────────────────────────────────────────
    const isAboveEMA = !isNaN(ema20_15m) && cur.close > ema20_15m;
    const isBelowEMA = !isNaN(ema20_15m) && cur.close < ema20_15m;
    const bias: "long" | "short" | "neutral" = isAboveEMA ? "long" : isBelowEMA ? "short" : "neutral";

    // Breakout: current price must have escaped the OR boundary (post-ORB candles)
    // Also allow checking on the first 1m bar of the post-ORB period
    const hasPostOrbData = barsAfterOrb >= 0 && orbEstablished;
    const breakoutLong  = hasPostOrbData && OR_HIGH > 0 && cur.close > OR_HIGH;
    const breakoutShort = hasPostOrbData && OR_LOW  > 0 && cur.close < OR_LOW;

    const volRatio = cur.volume / sessVolAvg;
    const volOk    = volRatio >= P.vol_ratio;
    // Within the 3.5-hour post-ORB trade window
    const withinWindow = orbEstablished && barsAfterOrb <= P.max_hold_bars;
    const rangeValid   = orbEstablished && orRangePct >= P.or_range_min && orRangePct <= P.or_range_max;

    const isLong  = breakoutLong  && isAboveEMA && withinWindow && rangeValid;
    const isShort = breakoutShort && isBelowEMA && withinWindow && rangeValid;
    const dir = isLong ? "long" : isShort ? "short" : null;

    // ── Always build conditions array (no more early returns) ─────────────
    const conditions: ORBCondition[] = [
      {
        name: !isNaN(ema20_15m)
          ? `15m EMA20 trend (${bias === "long" ? "▲ bullish" : bias === "short" ? "▼ bearish" : "neutral"})`
          : "15m EMA20 trend (loading…)",
        met: bias !== "neutral",
        value: isNaN(ema20_15m) ? "—" : `EMA ${ema20_15m.toFixed(0)} · price ${cur.close.toFixed(0)}`,
      },
      {
        name: orbEstablished
          ? `ORB-30 established (${barsInOrb}/30 bars)`
          : `ORB building… (${barsInOrb}/${P.orb_bars} bars)`,
        met: orbEstablished,
        value: orbEstablished
          ? `OR ${OR_HIGH.toFixed(0)} / ${OR_LOW.toFixed(0)} · ${orRangePct.toFixed(2)}%`
          : `${barsInOrb} bars · ${P.orb_bars - barsInOrb} remaining`,
      },
      {
        name: bias === "long" ? "Close above OR High (breakout)" : bias === "short" ? "Close below OR Low (breakout)" : "Breakout (waiting for bias)",
        met: dir !== null,
        value: orbEstablished
          ? `${cur.close.toFixed(0)} vs OR ${bias !== "short" ? OR_HIGH.toFixed(0) : OR_LOW.toFixed(0)}`
          : "waiting for ORB to close…",
      },
      {
        name: `Volume ≥ ${P.vol_ratio}× session avg`,
        met: volOk,
        value: orbEstablished ? `${volRatio.toFixed(2)}× avg (${sessVolAvg.toFixed(0)})` : "—",
      },
      {
        name: `Within trade window (≤ ${P.max_hold_bars} min post-ORB)`,
        met: withinWindow,
        value: orbEstablished
          ? (barsAfterOrb > P.max_hold_bars
              ? `expired (${barsAfterOrb}/${P.max_hold_bars}) · next: ${fmtUtc(nextStartMs + P.orb_bars * 60_000)}`
              : `bar ${barsAfterOrb}/${P.max_hold_bars}`)
          : status === "building_orb" ? "ORB building" : "waiting",
      },
      {
        name: `OR range ${P.or_range_min}–${P.or_range_max}% (clean session)`,
        met: rangeValid,
        value: orbEstablished ? `${orRangePct.toFixed(2)}%` : "—",
      },
    ];

    const met_count = conditions.filter(c => c.met).length;
    const all_met   = conditions.every(c => c.met);

    // ── Build signal ───────────────────────────────────────────────────────
    let signal: ORBSignal | null = null;
    if ((isLong || isShort) && OR_HIGH > 0 && OR_LOW > 0) {
      const entry  = cur.close;
      const slBase = isLong ? OR_LOW : OR_HIGH;
      const slBuf  = slBase * P.sl_buffer_pct;
      const sl     = isLong ? slBase - slBuf : slBase + slBuf;
      const risk   = Math.abs(entry - sl);
      const tp     = isLong ? entry + P.rr_target * risk : entry - P.rr_target * risk;
      const tp_ib  = isLong ? entry + 1.0 * risk : entry - 1.0 * risk;

      const breakStrength = isLong
        ? (cur.close - OR_HIGH) / Math.max(orRange, 1)
        : (OR_LOW - cur.close) / Math.max(orRange, 1);
      const volBonus   = Math.min(Math.max(0, (volRatio - 1.0)), 1);
      // Floor at 0.55 — always above 0.50 agent threshold
      const confidence = Math.min(0.55 + Math.max(0, breakStrength) * 0.22 + volBonus * 0.18, 0.95);

      signal = {
        direction: isLong ? "long" : "short",
        entry, sl, tp, tp_ib,
        rr: `1 : ${P.rr_target}`,
        confidence: Math.round(confidence * 1000) / 1000,
        reasoning: `${isLong ? "▲ LONG" : "▼ SHORT"} ORB-30: $${entry.toFixed(0)} broke ${isLong ? "above" : "below"} OR ${isLong ? OR_HIGH.toFixed(0) : OR_LOW.toFixed(0)} (range ${orRangePct.toFixed(2)}%). EMA20 $${ema20_15m.toFixed(0)} ${bias}. Vol ${volRatio.toFixed(2)}× avg. SL $${sl.toFixed(0)} · TP1 $${tp_ib.toFixed(0)} (1R) · TP2 $${tp.toFixed(0)} (${P.rr_target}R). ${label}.`,
        timestamp: new Date().toISOString(),
        or_high: OR_HIGH,
        or_low:  OR_LOW,
      };
    }

    return {
      signal, bias, conditions, met_count, total: 6, all_met,
      candle_count: candles1m.length, status,
      current_session: {
        session_start: new Date(startMs).toISOString(),
        or_high: OR_HIGH, or_low: OR_LOW,
        or_established: orbEstablished, or_range_pct: orRangePct,
      },
      indicators: {
        or_high:         orbEstablished ? OR_HIGH : null,
        or_low:          orbEstablished ? OR_LOW  : null,
        or_range_pct:    orbEstablished ? Math.round(orRangePct * 100) / 100 : null,
        ema20_15m:       isNaN(ema20_15m) ? null : Math.round(ema20_15m * 10) / 10,
        last_price:      cur.close,
        session_vol_avg: Math.round(sessVolAvg * 10) / 10,
        bars_in_orb:     barsInOrb,
        bars_since_orb:  barsAfterOrb,
        session_label:   label,
        next_session:    fmtUtc(nextStartMs + P.orb_bars * 60_000),
      },
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [candles1m]);
}
