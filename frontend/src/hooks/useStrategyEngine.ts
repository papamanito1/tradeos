"use client";

/**
 * useStrategyEngine
 * ──────────────────
 * Runs the BTC Momentum Velocity Scalper entirely in the browser
 * on live Binance 15m candle data. Zero backend dependency.
 *
 * Mirrors btc_momentum_velocity.py exactly.
 */

import { useMemo } from "react";
import { BinanceCandle } from "./useBinanceStream";

// ─── Parameters (mirror Python defaults) ─────────────────────────────────────
const P = {
  ema_trend_period:   50,
  ema_pullback_period:21,
  rsi_period:         14,
  atr_period:         14,
  vwap_window:        50,
  vol_avg_period:     20,
  rsi_cross_lookback: 3,
  rsi_trigger_long:   52,
  rsi_trigger_short:  48,
  vol_ratio_min:      1.4,
  body_ratio_min:     0.50,
  pullback_atr_mult:  1.2,
  sl_atr_mult:        1.5,
  tp_atr_mult:        3.0,
  atr_min_pct:        0.001,
  atr_max_pct:        0.012,
  ema_slope_bars:     5,
};

// ─── Indicator helpers ────────────────────────────────────────────────────────
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

function rsi(closes: number[], period: number = 14): number[] {
  const n = closes.length;
  if (n <= period) return Array(n).fill(NaN);
  const deltas = closes.slice(1).map((c, i) => c - closes[i]);
  const gains  = deltas.map(d => Math.max(d, 0));
  const losses = deltas.map(d => Math.abs(Math.min(d, 0)));
  let ag = gains.slice(0, period).reduce((a, b) => a + b, 0) / period;
  let al = losses.slice(0, period).reduce((a, b) => a + b, 0) / period;
  const out: number[] = Array(period + 1).fill(NaN);
  for (let i = period; i < deltas.length; i++) {
    ag = (ag * (period - 1) + gains[i]) / period;
    al = (al * (period - 1) + losses[i]) / period;
    out.push(al === 0 ? 100 : 100 - 100 / (1 + ag / al));
  }
  return out;
}

function atr(candles: BinanceCandle[], period: number = 14): number[] {
  const n = candles.length;
  if (n < period + 1) return Array(n).fill(NaN);
  const trs: number[] = [NaN];
  for (let i = 1; i < n; i++) {
    const tr = Math.max(
      candles[i].high - candles[i].low,
      Math.abs(candles[i].high - candles[i - 1].close),
      Math.abs(candles[i].low  - candles[i - 1].close),
    );
    trs.push(tr);
  }
  const seed = trs.slice(1, period + 1).reduce((a, b) => a + b, 0) / period;
  const out: number[] = Array(period).fill(NaN);
  out.push(seed);
  let prev = seed;
  for (let i = period + 1; i < n; i++) {
    const cur = (prev * (period - 1) + trs[i]) / period;
    out.push(cur);
    prev = cur;
  }
  return out;
}

function vwap(candles: BinanceCandle[], window: number = 50): number[] {
  return candles.map((_, i) => {
    const seg = candles.slice(Math.max(0, i - window + 1), i + 1);
    const tv  = seg.reduce((s, c) => s + c.volume, 0);
    if (!tv) return (candles[i].high + candles[i].low + candles[i].close) / 3;
    return seg.reduce((s, c) => s + (c.high + c.low + c.close) / 3 * c.volume, 0) / tv;
  });
}

function smaVol(candles: BinanceCandle[], period: number = 20): number[] {
  return candles.map((_, i) => {
    if (i < period - 1) return NaN;
    return candles.slice(i - period + 1, i + 1).reduce((s, c) => s + c.volume, 0) / period;
  });
}

// ─── Output types ─────────────────────────────────────────────────────────────
export interface StrategyCondition {
  name: string;
  met: boolean;
  value: string;
}

export interface StrategySignal {
  direction: "long" | "short";
  entry: number;
  sl: number;
  tp: number;
  confidence: number;
  reasoning: string;
  timestamp: string;
  rr: string;
}

export interface StrategyResult {
  bias: "long" | "short" | "neutral";
  conditions: StrategyCondition[];
  met_count: number;
  total: number;
  all_met: boolean;
  signal: StrategySignal | null;
  indicators: {
    rsi: number | null;
    ema50: number | null;
    ema21: number | null;
    vwap: number | null;
    atr: number | null;
    atr_pct: number | null;
    vol_ratio: number | null;
    ema50_slope: number | null;
  };
}

// ─── Main engine ──────────────────────────────────────────────────────────────
function runStrategy(candles: BinanceCandle[]): StrategyResult {
  const nullResult: StrategyResult = {
    bias: "neutral", conditions: [], met_count: 0, total: 7,
    all_met: false, signal: null,
    indicators: { rsi: null, ema50: null, ema21: null, vwap: null, atr: null, atr_pct: null, vol_ratio: null, ema50_slope: null },
  };

  const minBars = Math.max(P.ema_trend_period + P.ema_slope_bars + 5, P.rsi_period + P.rsi_cross_lookback + 5, 60);
  if (candles.length < minBars) return nullResult;

  const closes = candles.map(c => c.close);

  const ema50  = ema(closes, P.ema_trend_period);
  const ema21  = ema(closes, P.ema_pullback_period);
  const rsiArr = rsi(closes, P.rsi_period);
  const atrArr = atr(candles, P.atr_period);
  const vwapArr= vwap(candles, P.vwap_window);
  const volMa  = smaVol(candles, P.vol_avg_period);

  const i = candles.length - 1;
  const cur = candles[i];
  const curClose  = cur.close;
  const curHigh   = cur.high;
  const curLow    = cur.low;
  const curOpen   = cur.open;
  const curVol    = cur.volume;
  const curAtr    = atrArr[i];
  const curEma50  = ema50[i];
  const curEma21  = ema21[i];
  const curVwap   = vwapArr[i];
  const curRsi    = rsiArr[i];
  const curVolMa  = volMa[i];
  const prevEma50 = ema50[i - P.ema_slope_bars];

  // Guard NaN
  for (const v of [curAtr, curEma50, curEma21, curVwap, curRsi, curVolMa, prevEma50]) {
    if (isNaN(v) || v === 0) return nullResult;
  }

  const atrPct      = curAtr / curClose;
  const volRatio    = curVolMa > 0 ? curVol / curVolMa : 0;
  const barRange    = curHigh - curLow;
  const body        = Math.abs(curClose - curOpen);
  const bodyRatio   = barRange > 0 ? body / barRange : 0;
  const ema50Slope  = (curEma50 - prevEma50) / prevEma50;

  const rsiWindow   = rsiArr.slice(i - P.rsi_cross_lookback, i + 1);
  if (rsiWindow.some(isNaN)) return nullResult;

  const indicators = {
    rsi:        Math.round(curRsi * 10) / 10,
    ema50:      Math.round(curEma50 * 100) / 100,
    ema21:      Math.round(curEma21 * 100) / 100,
    vwap:       Math.round(curVwap * 100) / 100,
    atr:        Math.round(curAtr * 100) / 100,
    atr_pct:    Math.round(atrPct * 100000) / 1000,
    vol_ratio:  Math.round(volRatio * 100) / 100,
    ema50_slope:Math.round(ema50Slope * 1000000) / 1000000,
  };

  // ── Evaluate both directions ──────────────────────────────────────────────
  const isLongBias  = ema50Slope > 0;
  const isShortBias = ema50Slope < 0;

  // Individual conditions for the dominant bias
  const longConds: StrategyCondition[] = [
    { name: "EMA50 trending up",          met: ema50Slope > 0.0002,                                                        value: `slope ${(ema50Slope * 100).toFixed(4)}%` },
    { name: "Price above EMA50",          met: curClose > curEma50,                                                         value: `${curClose.toFixed(0)} > ${curEma50.toFixed(0)}` },
    { name: "RSI crossed above 50",       met: curRsi >= P.rsi_trigger_long && Math.min(...rsiWindow.slice(0, -1)) < 50,    value: `RSI ${curRsi.toFixed(1)}` },
    { name: "Bullish conviction candle",  met: curClose > curOpen,                                                          value: `body ${(bodyRatio * 100).toFixed(0)}%` },
    { name: "Pullback to EMA21/VWAP",     met: Math.abs(curLow - curEma21) <= P.pullback_atr_mult * curAtr || Math.abs(curLow - curVwap) <= P.pullback_atr_mult * curAtr, value: `EMA21 ${curEma21.toFixed(0)} VWAP ${curVwap.toFixed(0)}` },
    { name: `Volume surge >${P.vol_ratio_min}×`,  met: volRatio >= P.vol_ratio_min,                                        value: `${volRatio.toFixed(2)}×` },
    { name: "ATR in tradeable range",     met: atrPct >= P.atr_min_pct && atrPct <= P.atr_max_pct,                         value: `${(atrPct * 100).toFixed(3)}%` },
  ];

  const shortConds: StrategyCondition[] = [
    { name: "EMA50 trending down",        met: ema50Slope < -0.0002,                                                       value: `slope ${(ema50Slope * 100).toFixed(4)}%` },
    { name: "Price below EMA50",          met: curClose < curEma50,                                                         value: `${curClose.toFixed(0)} < ${curEma50.toFixed(0)}` },
    { name: "RSI crossed below 50",       met: curRsi <= P.rsi_trigger_short && Math.max(...rsiWindow.slice(0, -1)) > 50,  value: `RSI ${curRsi.toFixed(1)}` },
    { name: "Bearish conviction candle",  met: curClose < curOpen,                                                          value: `body ${(bodyRatio * 100).toFixed(0)}%` },
    { name: "Rejection at EMA21/VWAP",   met: Math.abs(curHigh - curEma21) <= P.pullback_atr_mult * curAtr || Math.abs(curHigh - curVwap) <= P.pullback_atr_mult * curAtr, value: `EMA21 ${curEma21.toFixed(0)} VWAP ${curVwap.toFixed(0)}` },
    { name: `Volume surge >${P.vol_ratio_min}×`,  met: volRatio >= P.vol_ratio_min,                                        value: `${volRatio.toFixed(2)}×` },
    { name: "ATR in tradeable range",     met: atrPct >= P.atr_min_pct && atrPct <= P.atr_max_pct,                         value: `${(atrPct * 100).toFixed(3)}%` },
  ];

  // Choose which set of conditions to show based on bias
  const conditions = isLongBias || !isShortBias ? longConds : shortConds;
  const met_count = conditions.filter(c => c.met).length;
  const all_met   = conditions.every(c => c.met);

  // ── Bias ──────────────────────────────────────────────────────────────────
  const longMet  = longConds.filter(c => c.met).length;
  const shortMet = shortConds.filter(c => c.met).length;
  const bias: "long" | "short" | "neutral" =
    longMet  >= 4 ? "long"  :
    shortMet >= 4 ? "short" : "neutral";

  // ── Signal (all 7 conditions for long or short) ───────────────────────────
  const fullLong  = longConds.every(c => c.met);
  const fullShort = shortConds.every(c => c.met);

  let signal: StrategySignal | null = null;

  if (fullLong || fullShort) {
    const dir = fullLong ? "long" : "short";
    const slDist = P.sl_atr_mult * curAtr;
    const tpDist = P.tp_atr_mult * curAtr;
    const entry  = curClose;
    const sl     = dir === "long" ? entry - slDist : entry + slDist;
    const tp     = dir === "long" ? entry + tpDist : entry - tpDist;

    const volScore   = Math.min(volRatio / 3, 1);
    const rsiScore   = Math.min(Math.abs(curRsi - 50) / 20, 1);
    const slopeScore = Math.min(Math.abs(ema50Slope) / 0.003, 1);
    const bodyScore  = Math.min(bodyRatio / 0.85, 1);
    const confidence = 0.35 * volScore + 0.30 * rsiScore + 0.20 * slopeScore + 0.15 * bodyScore;

    const reasoning = dir === "long"
      ? `LONG: EMA50 slope +${(ema50Slope * 100).toFixed(3)}%/bar, RSI ${curRsi.toFixed(1)} crossed above 50, vol ${volRatio.toFixed(1)}× avg, body ratio ${(bodyRatio * 100).toFixed(0)}%, pullback to EMA21 (${curEma21.toFixed(0)}) / VWAP (${curVwap.toFixed(0)}). SL -${slDist.toFixed(0)} TP +${tpDist.toFixed(0)} (1:2 R:R)`
      : `SHORT: EMA50 slope ${(ema50Slope * 100).toFixed(3)}%/bar, RSI ${curRsi.toFixed(1)} crossed below 50, vol ${volRatio.toFixed(1)}× avg, body ratio ${(bodyRatio * 100).toFixed(0)}%, rejection at EMA21 (${curEma21.toFixed(0)}) / VWAP (${curVwap.toFixed(0)}). SL +${slDist.toFixed(0)} TP -${tpDist.toFixed(0)} (1:2 R:R)`;

    signal = {
      direction: dir,
      entry:      Math.round(entry * 100) / 100,
      sl:         Math.round(sl    * 100) / 100,
      tp:         Math.round(tp    * 100) / 100,
      confidence: Math.round(confidence * 1000) / 1000,
      reasoning,
      timestamp:  new Date().toISOString(),
      rr:         `1 : ${(P.tp_atr_mult / P.sl_atr_mult).toFixed(1)}`,
    };
  }

  return { bias, conditions, met_count, total: 7, all_met, signal, indicators };
}

// ─── Hook ─────────────────────────────────────────────────────────────────────
export function useStrategyEngine(candles: BinanceCandle[]): StrategyResult {
  return useMemo(() => {
    if (candles.length < 60) return {
      bias: "neutral", conditions: [], met_count: 0, total: 7,
      all_met: false, signal: null,
      indicators: { rsi: null, ema50: null, ema21: null, vwap: null, atr: null, atr_pct: null, vol_ratio: null, ema50_slope: null },
    };
    return runStrategy(candles);
  }, [candles]);
}
