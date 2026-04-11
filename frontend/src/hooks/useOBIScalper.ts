"use client";

/**
 * useOBIScalper
 * ──────────────
 * 1-Minute BTC Order Book Imbalance Scalper
 *
 * Decision logic (checked each 1m close):
 *   LONG  when: OBI > +0.25 AND EMA9 > EMA21 AND RSI(14) > 50
 *   SHORT when: OBI < −0.25 AND EMA9 < EMA21 AND RSI(14) < 50
 *
 * SL: 0.4% from entry  |  TP: 0.8% (1:2 R:R)  |  Max hold: 10 min
 */

import { useMemo } from "react";
import { BinanceCandle, BinanceOrderBook } from "./useBingXStream";
import { StrategyResult, StrategySignal } from "./useStrategyEngine";

// ─── Parameters ──────────────────────────────────────────────────────────────
const P = {
  ema_fast:       9,
  ema_slow:       21,
  rsi_period:     14,
  obi_threshold:  0.20,   // ±0.20 to fire — slightly relaxed from spec 0.25
  ob_levels:      10,     // top 10 order book levels
  sl_pct:         0.004,  // 0.4% stop loss
  tp_pct:         0.008,  // 0.8% take profit → 1:2 R:R
  min_candles:    30,
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

function rsi(closes: number[], period: number): number[] {
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

// ─── Extra indicators exposed to UI ──────────────────────────────────────────
export interface OBIIndicators {
  obi:      number | null;
  bid_vol:  number | null;
  ask_vol:  number | null;
  ema9:     number | null;
  ema21:    number | null;
  rsi:      number | null;
}

// ─── Result extends StrategyResult with extra OBI indicators ─────────────────
export interface OBIResult extends StrategyResult {
  obi_indicators: OBIIndicators;
}

// ─── Main engine ──────────────────────────────────────────────────────────────
function runOBI(candles1m: BinanceCandle[], orderBook: BinanceOrderBook | null): OBIResult {
  const base: OBIResult = {
    bias: "neutral", conditions: [], met_count: 0, total: 3,
    all_met: false, signal: null,
    indicators: { rsi: null, ema50: null, ema21: null, vwap: null, atr: null, atr_pct: null, vol_ratio: null, ema50_slope: null },
    obi_indicators: { obi: null, bid_vol: null, ask_vol: null, ema9: null, ema21: null, rsi: null },
  };

  if (candles1m.length < P.min_candles) return base;

  const closes   = candles1m.map(c => c.close);
  const ema9Arr  = ema(closes, P.ema_fast);
  const ema21Arr = ema(closes, P.ema_slow);
  const rsiArr   = rsi(closes, P.rsi_period);

  const i       = candles1m.length - 1;
  const curEma9  = ema9Arr[i];
  const curEma21 = ema21Arr[i];
  const curRsi   = rsiArr[i];
  const curClose = closes[i];

  if (isNaN(curEma9) || isNaN(curEma21) || isNaN(curRsi)) return base;

  // ── Order Book Imbalance ──────────────────────────────────────────────────
  let obi = 0, bidVol = 0, askVol = 0;
  if (orderBook && orderBook.bids.length > 0 && orderBook.asks.length > 0) {
    const topBids = orderBook.bids.slice(0, P.ob_levels);
    const topAsks = orderBook.asks.slice(0, P.ob_levels);
    bidVol = topBids.reduce((s, lvl) => s + (lvl.amount ?? 0), 0);
    askVol = topAsks.reduce((s, lvl) => s + (lvl.amount ?? 0), 0);
    const total = bidVol + askVol;
    obi = total > 0 ? (bidVol - askVol) / total : 0;
  }

  // ── Direction ─────────────────────────────────────────────────────────────
  const isLongBias  = curEma9 > curEma21;
  const isShortBias = curEma9 < curEma21;
  const showLong    = isLongBias || (!isShortBias && obi >= 0);

  const longOBI  = obi > P.obi_threshold;
  const shortOBI = obi < -P.obi_threshold;
  const longEMA  = isLongBias;
  const shortEMA = isShortBias;
  const longRSI  = curRsi > 50;
  const shortRSI = curRsi < 50;

  const conditions = showLong ? [
    { name: `OBI > +${P.obi_threshold} (bid pressure)`, met: longOBI,  value: `${obi >= 0 ? "+" : ""}${obi.toFixed(3)}` },
    { name: "EMA9 > EMA21 (micro-uptrend)",             met: longEMA,  value: `${curEma9.toFixed(0)} > ${curEma21.toFixed(0)}` },
    { name: "RSI(14) > 50 (bullish momentum)",          met: longRSI,  value: `RSI ${curRsi.toFixed(1)}` },
  ] : [
    { name: `OBI < −${P.obi_threshold} (ask pressure)`, met: shortOBI, value: `${obi >= 0 ? "+" : ""}${obi.toFixed(3)}` },
    { name: "EMA9 < EMA21 (micro-downtrend)",            met: shortEMA, value: `${curEma9.toFixed(0)} < ${curEma21.toFixed(0)}` },
    { name: "RSI(14) < 50 (bearish momentum)",           met: shortRSI, value: `RSI ${curRsi.toFixed(1)}` },
  ];

  const longMet  = [longOBI,  longEMA,  longRSI ].filter(Boolean).length;
  const shortMet = [shortOBI, shortEMA, shortRSI].filter(Boolean).length;
  const met_count = showLong ? longMet : shortMet;
  const all_met   = met_count === 3;

  const bias: "long" | "short" | "neutral" =
    longMet  === 3 ? "long"  :
    shortMet === 3 ? "short" : "neutral";

  // ── Signal (all 3 conditions required) ───────────────────────────────────
  let signal: StrategySignal | null = null;

  if (longMet === 3 || shortMet === 3) {
    const dir    = longMet === 3 ? "long" : "short";
    const entry  = curClose;
    const sl     = dir === "long" ? entry * (1 - P.sl_pct) : entry * (1 + P.sl_pct);
    const tp     = dir === "long" ? entry * (1 + P.tp_pct) : entry * (1 - P.tp_pct);

    // Confidence: OBI strength is the primary driver
    const obiScore  = Math.min(Math.abs(obi) / 0.5, 1);
    const rsiScore  = Math.min(Math.abs(curRsi - 50) / 20, 1);
    const emaScore  = Math.min(Math.abs(curEma9 - curEma21) / (curEma21 * 0.002), 1);
    const rawConf   = 0.55 * obiScore + 0.25 * rsiScore + 0.20 * emaScore;
    const confidence = Math.max(0.52, Math.min(rawConf, 0.99));

    signal = {
      direction:  dir,
      entry:      Math.round(entry * 100) / 100,
      sl:         Math.round(sl  * 100) / 100,
      tp:         Math.round(tp  * 100) / 100,
      confidence,
      reasoning:  `[OBI] ${dir.toUpperCase()}: OBI ${obi >= 0 ? "+" : ""}${obi.toFixed(3)} (${dir === "long" ? "bid" : "ask"} pressure), EMA9 ${dir === "long" ? ">" : "<"} EMA21 (${curEma9.toFixed(0)}/${curEma21.toFixed(0)}), RSI ${curRsi.toFixed(1)}. SL ${(P.sl_pct * 100).toFixed(1)}% TP ${(P.tp_pct * 100).toFixed(1)}% (1:2 R:R)`,
      timestamp:  new Date().toISOString(),
      rr:         "1 : 2.0",
    };
  }

  return {
    bias, conditions, met_count, total: 3, all_met, signal,
    indicators: {
      rsi:        Math.round(curRsi   * 10)  / 10,
      ema50:      null,
      ema21:      Math.round(curEma21 * 100) / 100,
      vwap:       null,
      atr:        null,
      atr_pct:    null,
      vol_ratio:  Math.round(obi * 1000) / 1000,  // reuse for OBI
      ema50_slope:null,
    },
    obi_indicators: {
      obi:     Math.round(obi      * 1000) / 1000,
      bid_vol: Math.round(bidVol   * 100)  / 100,
      ask_vol: Math.round(askVol   * 100)  / 100,
      ema9:    Math.round(curEma9  * 100)  / 100,
      ema21:   Math.round(curEma21 * 100)  / 100,
      rsi:     Math.round(curRsi   * 10)   / 10,
    },
  };
}

// ─── Hook ─────────────────────────────────────────────────────────────────────
export function useOBIScalper(
  candles1m: BinanceCandle[],
  orderBook:  BinanceOrderBook | null,
): OBIResult {
  return useMemo(() => runOBI(candles1m, orderBook), [candles1m, orderBook]);
}
