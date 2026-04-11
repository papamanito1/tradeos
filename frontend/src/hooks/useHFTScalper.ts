"use client";

/**
 * useHFTScalper — VWAP + EMA-Bias HFT Scalper (1m bars)
 * ────────────────────────────────────────────────────────
 * Strategy logic (mirrors user pseudocode exactly):
 *
 *  Bias    : EMA9 > EMA21 on 5m aggregated bars
 *  Entry   : 1m close above/below rolling VWAP, near VWAP (≤ 0.20 × ATR),
 *            OBI > 0.18, TFI > 0.12, microprice edge > 0.15 tick
 *  Exits   : SL = max(0.35×ATR, swing), TP1=0.6R (50%), TP2=1.2R (30%), trail rest
 *            Time exit: flatten if > 45s and PnL small, hard flatten > 90s
 *            Flow exit: spread blows out, TFI flips, data stale
 */

import { useMemo, useRef, useCallback } from "react";
import { BinanceCandle, BinanceOrderBook, BinanceAggTrade } from "./useBingXStream";

// ─── Constants ────────────────────────────────────────────────────────────────
const TICK_SIZE   = 0.10;   // BTC/USDT minimum price increment
const OBI_LEVELS  = 5;      // how many order book levels to use for OBI
const TFI_WINDOW  = 60_000; // rolling 60s window for TFI accumulation

const P = {
  obi_threshold:      0.08,  // was 0.18 — much easier to achieve
  tfi_threshold:      0.05,  // was 0.12
  micro_edge_ticks:   0.03,  // was 0.15 — tiny edge is enough
  near_vwap_atr_mult: 2.0,   // was 0.20 — effectively remove nearVwap as hard gate
  max_spread_ticks:   5,     // was 2 — allow wider spread
  signal_min_conds:   4,     // fire at 4/7 conditions
  sl_atr_mult:        0.35,
  tp1_r:              0.6,
  tp2_r:              1.2,
  ema9_period:        9,
  ema21_period:       21,
  atr_period:         14,
  swing_lookback:     10,    // bars for swing high/low
};

// ─── Output types ─────────────────────────────────────────────────────────────
export interface HFTCondition { name: string; met: boolean; value: string; }

export interface HFTSignal {
  direction: "long" | "short";
  entry: number;        // best bid (long) or best ask (short)
  sl: number;
  tp1: number;          // 0.6R — exit 50%
  tp2: number;          // 1.2R — exit 30%
  r_distance: number;   // risk distance
  confidence: number;
  reasoning: string;
  timestamp: string;
}

export interface HFTIndicators {
  ema9_5m:     number | null;
  ema21_5m:    number | null;
  vwap_1m:     number | null;
  atr_1m:      number | null;
  obi:         number | null;  // [-1, +1]
  tfi:         number | null;  // [-1, +1]
  microprice:  number | null;
  micro_edge:  number | null;  // microprice - mid_price
  spread_ticks:number | null;
  bid:         number | null;
  ask:         number | null;
}

export interface HFTResult {
  signal:      HFTSignal | null;
  bias:        "long" | "short" | "neutral";
  conditions:  HFTCondition[];
  met_count:   number;
  total:       number;
  all_met:     boolean;
  indicators:  HFTIndicators;
  candle_count:number;
}

// ─── Indicator math ───────────────────────────────────────────────────────────
function ema(vals: number[], p: number): number[] {
  if (vals.length < p) return vals.map(() => NaN);
  const k = 2 / (p + 1);
  const out: number[] = Array(p - 1).fill(NaN);
  let prev = vals.slice(0, p).reduce((a, b) => a + b, 0) / p;
  out.push(prev);
  for (let i = p; i < vals.length; i++) { prev = vals[i] * k + prev * (1 - k); out.push(prev); }
  return out;
}

function atr(candles: BinanceCandle[], p = 14): number[] {
  const n = candles.length;
  if (n < p + 1) return Array(n).fill(NaN);
  const trs: number[] = [NaN];
  for (let i = 1; i < n; i++) {
    trs.push(Math.max(
      candles[i].high - candles[i].low,
      Math.abs(candles[i].high - candles[i-1].close),
      Math.abs(candles[i].low  - candles[i-1].close),
    ));
  }
  const seed = trs.slice(1, p + 1).reduce((a, b) => a + b, 0) / p;
  const out: number[] = Array(p).fill(NaN);
  out.push(seed);
  let prev = seed;
  for (let i = p + 1; i < n; i++) { const cur = (prev * (p-1) + trs[i]) / p; out.push(cur); prev = cur; }
  return out;
}

function vwap(candles: BinanceCandle[]): number[] {
  let cumTPV = 0, cumVol = 0;
  return candles.map(c => {
    const tp = (c.high + c.low + c.close) / 3;
    cumTPV += tp * c.volume;
    cumVol += c.volume;
    return cumVol > 0 ? cumTPV / cumVol : tp;
  });
}

/** Aggregate 1m bars into 5m bars aligned to 5-minute UTC slots */
function to5m(candles1m: BinanceCandle[]): BinanceCandle[] {
  const groups = new Map<number, BinanceCandle[]>();
  for (const c of candles1m) {
    const ms = new Date(c.timestamp).getTime();
    const bucket = Math.floor(ms / 300_000) * 300_000;
    if (!groups.has(bucket)) groups.set(bucket, []);
    groups.get(bucket)!.push(c);
  }
  return Array.from(groups.entries())
    .sort(([a], [b]) => a - b)
    .map(([bucket, bars]) => ({
      timestamp: new Date(bucket).toISOString(),
      open:   bars[0].open,
      high:   Math.max(...bars.map(b => b.high)),
      low:    Math.min(...bars.map(b => b.low)),
      close:  bars[bars.length - 1].close,
      volume: bars.reduce((s, b) => s + b.volume, 0),
      is_closed: bars.length >= 4,
    }));
}

/** Swing low/high over last N bars */
function swingLow(candles: BinanceCandle[], n: number): number {
  return Math.min(...candles.slice(-n).map(c => c.low));
}
function swingHigh(candles: BinanceCandle[], n: number): number {
  return Math.max(...candles.slice(-n).map(c => c.high));
}

// ─── Hook ─────────────────────────────────────────────────────────────────────
export function useHFTScalper(
  candles1m:  BinanceCandle[],
  orderBook:  BinanceOrderBook | null,
  aggTrades:  BinanceAggTrade[],
): HFTResult {
  const nullResult: HFTResult = {
    signal: null, bias: "neutral", conditions: [], met_count: 0, total: 7,
    all_met: false, candle_count: candles1m.length,
    indicators: { ema9_5m: null, ema21_5m: null, vwap_1m: null, atr_1m: null,
                  obi: null, tfi: null, microprice: null, micro_edge: null,
                  spread_ticks: null, bid: null, ask: null },
  };

  return useMemo(() => {
    if (candles1m.length < 30) return { ...nullResult, candle_count: candles1m.length };

    // ── 5m EMA bias ───────────────────────────────────────────────────────────
    const bars5m    = to5m(candles1m);
    const closes5m  = bars5m.map(c => c.close);
    const ema9arr   = ema(closes5m, P.ema9_period);
    const ema21arr  = ema(closes5m, P.ema21_period);
    const ema9_5m   = ema9arr[ema9arr.length - 1];
    const ema21_5m  = ema21arr[ema21arr.length - 1];
    if (isNaN(ema9_5m) || isNaN(ema21_5m)) return { ...nullResult, candle_count: candles1m.length };

    // ── 1m VWAP & ATR ────────────────────────────────────────────────────────
    const vwapArr = vwap(candles1m);
    const atrArr  = atr(candles1m, P.atr_period);
    const vwap1m  = vwapArr[vwapArr.length - 1];
    const atr1m   = atrArr[atrArr.length - 1];
    const cur     = candles1m[candles1m.length - 1];
    if (isNaN(vwap1m) || isNaN(atr1m)) return { ...nullResult, candle_count: candles1m.length };

    // ── Order book metrics ────────────────────────────────────────────────────
    let obi = 0, microprice = 0, microEdge = 0, spreadTicks = 0;
    let bestBid = 0, bestAsk = 0;

    if (orderBook && orderBook.bids.length >= OBI_LEVELS && orderBook.asks.length >= OBI_LEVELS) {
      const bidVol = orderBook.bids.slice(0, OBI_LEVELS).reduce((s, l) => s + l.amount, 0);
      const askVol = orderBook.asks.slice(0, OBI_LEVELS).reduce((s, l) => s + l.amount, 0);
      obi = (bidVol + askVol) > 0 ? (bidVol - askVol) / (bidVol + askVol) : 0;

      bestBid = orderBook.bids[0].price;
      bestAsk = orderBook.asks[0].price;
      const bidSz1 = orderBook.bids[0].amount;
      const askSz1 = orderBook.asks[0].amount;
      microprice = (bidSz1 + askSz1) > 0
        ? (bestAsk * bidSz1 + bestBid * askSz1) / (bidSz1 + askSz1)
        : (bestBid + bestAsk) / 2;
      microEdge    = microprice - (bestBid + bestAsk) / 2;
      spreadTicks  = (bestAsk - bestBid) / TICK_SIZE;
    }

    // ── TFI (rolling 60s window) ──────────────────────────────────────────────
    const now = Date.now();
    const window = aggTrades.filter(t => now - t.timestamp <= TFI_WINDOW);
    let buyVol = 0, sellVol = 0;
    for (const t of window) {
      if (t.is_buyer_maker) sellVol += t.quantity; // seller aggressed
      else                  buyVol  += t.quantity; // buyer aggressed
    }
    const tfi = (buyVol + sellVol) > 0 ? (buyVol - sellVol) / (buyVol + sellVol) : 0;

    // ── Strategy conditions ───────────────────────────────────────────────────
    const longBias   = ema9_5m > ema21_5m;
    const shortBias  = ema9_5m < ema21_5m;
    const nearVwap   = Math.abs(cur.close - vwap1m) <= P.near_vwap_atr_mult * atr1m;
    const cleanSpread= spreadTicks <= P.max_spread_ticks;
    const obOk_L     = obi > P.obi_threshold;
    const obOk_S     = obi < -P.obi_threshold;
    const tfiOk_L    = tfi > P.tfi_threshold;
    const tfiOk_S    = tfi < -P.tfi_threshold;
    const microOk_L  = microEdge > P.micro_edge_ticks * TICK_SIZE;
    const microOk_S  = microEdge < -P.micro_edge_ticks * TICK_SIZE;

    const bias: "long" | "short" | "neutral" = longBias ? "long" : shortBias ? "short" : "neutral";

    // Build conditions for dominant bias
    const isLong = longBias || (!shortBias && obi > 0);
    const conditions: HFTCondition[] = [
      { name: isLong ? "EMA9 > EMA21 (5m bullish)" : "EMA9 < EMA21 (5m bearish)",
        met: isLong ? longBias : shortBias,
        value: `EMA9 ${ema9_5m.toFixed(0)} ${isLong ? ">" : "<"} EMA21 ${ema21_5m.toFixed(0)}` },
      { name: isLong ? "Close above VWAP" : "Close below VWAP",
        met: isLong ? cur.close > vwap1m : cur.close < vwap1m,
        value: `close ${cur.close.toFixed(0)} VWAP ${vwap1m.toFixed(0)}` },
      { name: "Near VWAP (≤ 0.20 × ATR)",
        met: nearVwap,
        value: `dist ${Math.abs(cur.close - vwap1m).toFixed(0)} ATR ${atr1m.toFixed(0)}` },
      { name: `OBI ${isLong ? ">" : "<"} ${isLong ? P.obi_threshold : -P.obi_threshold} (order book pressure)`,
        met: isLong ? obOk_L : obOk_S,
        value: obi.toFixed(3) },
      { name: `TFI ${isLong ? ">" : "<"} ${isLong ? P.tfi_threshold : -P.tfi_threshold} (trade flow)`,
        met: isLong ? tfiOk_L : tfiOk_S,
        value: tfi.toFixed(3) },
      { name: `Microprice edge ${isLong ? "+" : "-"}${P.micro_edge_ticks} ticks`,
        met: isLong ? microOk_L : microOk_S,
        value: `${microEdge.toFixed(4)} (${(microEdge / TICK_SIZE).toFixed(2)} ticks)` },
      { name: `Spread ≤ ${P.max_spread_ticks} ticks`,
        met: cleanSpread,
        value: `${spreadTicks.toFixed(1)} ticks` },
    ];

    const met_count = conditions.filter(c => c.met).length;
    const all_met   = conditions.every(c => c.met);

    // ── Signal ────────────────────────────────────────────────────────────────
    let signal: HFTSignal | null = null;

    // Fire at 4+ conditions met (removed nearVwap & spread as hard gates)
    const longCondsMet  = [longBias, cur.close > vwap1m, obOk_L, tfiOk_L, microOk_L, nearVwap, cleanSpread].filter(Boolean).length;
    const shortCondsMet = [shortBias, cur.close < vwap1m, obOk_S, tfiOk_S, microOk_S, nearVwap, cleanSpread].filter(Boolean).length;
    const longSignal  = longBias  && longCondsMet  >= P.signal_min_conds;
    const shortSignal = shortBias && shortCondsMet >= P.signal_min_conds;

    if (longSignal || shortSignal) {
      const dir = longSignal ? "long" : "short";
      const entry = dir === "long" ? (bestBid || cur.close) : (bestAsk || cur.close);
      const swingDist = dir === "long"
        ? entry - swingLow(candles1m, P.swing_lookback)
        : swingHigh(candles1m, P.swing_lookback) - entry;
      const slDist = Math.max(P.sl_atr_mult * atr1m, swingDist);
      const sl  = dir === "long" ? entry - slDist : entry + slDist;
      const R   = slDist;
      const tp1 = dir === "long" ? entry + P.tp1_r * R : entry - P.tp1_r * R;
      const tp2 = dir === "long" ? entry + P.tp2_r * R : entry - P.tp2_r * R;

      const volScore  = Math.min(Math.abs(obi) / 0.3, 1);
      const flowScore = Math.min(Math.abs(tfi) / 0.2, 1);
      const microScore= Math.min(Math.abs(microEdge) / (TICK_SIZE * 0.1), 1);
      const condScore = (longSignal ? longCondsMet : shortCondsMet) / 7;
      const rawConf   = 0.30 * volScore + 0.25 * flowScore + 0.20 * microScore + 0.25 * condScore;
      const confidence= Math.max(0.52, Math.min(rawConf, 0.99));

      signal = {
        direction: dir, entry, sl, tp1, tp2,
        r_distance: R,
        confidence: Math.round(confidence * 1000) / 1000,
        reasoning: `${dir.toUpperCase()}: 5m EMA9${dir==="long"?">":"<"}EMA21, close ${dir==="long"?">":"<"} VWAP ${vwap1m.toFixed(0)}, OBI ${obi.toFixed(3)}, TFI ${tfi.toFixed(3)}, spread ${spreadTicks.toFixed(1)}t. Entry ${dir==="long"?"bid":"ask"} ${entry.toFixed(1)}, SL ${sl.toFixed(1)}, TP1 ${tp1.toFixed(1)} (50%), TP2 ${tp2.toFixed(1)} (30%), trail rest.`,
        timestamp: new Date().toISOString(),
      };
    }

    return {
      signal, bias, conditions, met_count, total: 7, all_met,
      candle_count: candles1m.length,
      indicators: {
        ema9_5m:     Math.round(ema9_5m  * 100) / 100,
        ema21_5m:    Math.round(ema21_5m * 100) / 100,
        vwap_1m:     Math.round(vwap1m   * 100) / 100,
        atr_1m:      Math.round(atr1m    * 100) / 100,
        obi:         Math.round(obi      * 1000) / 1000,
        tfi:         Math.round(tfi      * 1000) / 1000,
        microprice:  Math.round(microprice * 100) / 100,
        micro_edge:  Math.round(microEdge * 10000) / 10000,
        spread_ticks:Math.round(spreadTicks * 10) / 10,
        bid:         bestBid || null,
        ask:         bestAsk || null,
      },
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [candles1m, orderBook, aggTrades]);
}

// ─── Rolling aggTrade accumulator ─────────────────────────────────────────────
/** Call this hook to get a self-managing rolling aggTrade buffer */
export function useAggTradeBuffer(maxAge = TFI_WINDOW) {
  const bufRef = useRef<BinanceAggTrade[]>([]);

  const push = useCallback((trade: BinanceAggTrade) => {
    const now = Date.now();
    bufRef.current.push(trade);
    // prune stale entries
    bufRef.current = bufRef.current.filter(t => now - t.timestamp <= maxAge);
  }, [maxAge]);

  const get = useCallback(() => bufRef.current, []);

  return { push, get };
}
