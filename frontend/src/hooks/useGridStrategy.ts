"use client";

/**
 * useGridStrategy
 * ───────────────
 * $50 BTC Arithmetic Grid Trading Strategy (frontend mirror of backend engine).
 *
 * Visual-layer hook: tracks live BTC price, maps it onto a $50 arithmetic grid,
 * detects level crossings, and surfaces the current grid state for the UI.
 *
 * Execution happens in the backend PersistentAgent. This hook is purely for
 * display and signal awareness on the agent page / overview.
 *
 * Grid logic:
 *   • Grid anchored to nearest $50 multiple when price first arrives.
 *   • A BUY signal fires when price crosses DOWN through a $50 boundary.
 *   • TP = +$50 above entry | SL = -$50 below entry | max 5 concurrent slots.
 *   • Range: ±20 levels ($1,000 each side) before hard-stop.
 */

import { useMemo, useRef, useCallback } from "react";
import { BinanceCandle } from "./useBinanceStream";

const GRID_SIZE       = 50;    // $50 spacing
const MAX_LEVELS      = 20;    // 20 levels each side → $1,000 range
const MAX_CONCURRENT  = 5;     // max open positions at once
const DAILY_LOSS_LIM  = -300;
const DAILY_PROF_LIM  = 1000;
const SL_GRIDS        = 1;     // 1 level below entry
const TP_GRIDS        = 1;     // 1 level above entry

export interface GridLevel {
  price: number;
  type: "buy" | "sell";
  status: "pending" | "active" | "filled";
  idx: number;   // distance from center (negative = below)
}

export interface GridSignal {
  direction: "long";
  entry: number;
  tp: number;
  sl: number;
  confidence: number;
  rr: string;
  level: number;
  reasoning: string;
}

export interface GridCondition {
  label: string;
  met: boolean;
  value: string;
}

export interface GridResult {
  signal: GridSignal | null;
  bias: "long" | "neutral" | "stopped" | "paused";
  name: string;
  gridCenter: number;
  gridMin: number;
  gridMax: number;
  currentLevel: number;
  currentPrice: number;
  distToNextBuy: number;   // $ distance to next buy trigger
  distToNextSell: number;  // $ distance to next sell trigger
  levels: GridLevel[];     // nearest ±10 levels for UI ladder
  conditions: GridCondition[];
  metCount: number;
  total: number;
  dailyPnl: number;
}

// ── Tiny EMA ─────────────────────────────────────────────────────────────────
function ema(vals: number[], p: number): number[] {
  if (vals.length < p) return vals.map(() => NaN);
  const k = 2 / (p + 1);
  const out: number[] = Array(p - 1).fill(NaN);
  let prev = vals.slice(0, p).reduce((a, b) => a + b, 0) / p;
  out.push(prev);
  for (let i = p; i < vals.length; i++) {
    prev = vals[i] * k + prev * (1 - k);
    out.push(prev);
  }
  return out;
}

// ── Hook ─────────────────────────────────────────────────────────────────────
export function useGridStrategy(
  candles1m: BinanceCandle[],
  livePrice: number,
  serverGridState?: { center?: number; daily_pnl?: number; daily_trades?: number } | null,
): GridResult {
  // Persist grid centre and last price across renders
  const gridCenterRef  = useRef<number>(0);
  const lastPriceRef   = useRef<number>(0);
  const dailyPnlRef    = useRef<number>(0);

  return useMemo(() => {
    const NULL: GridResult = {
      signal: null, bias: "neutral", name: "Grid $50",
      gridCenter: 0, gridMin: 0, gridMax: 0, currentLevel: 0,
      currentPrice: livePrice, distToNextBuy: 0, distToNextSell: 0,
      levels: [], conditions: [], metCount: 0, total: 5, dailyPnl: 0,
    };

    if (livePrice <= 0 || candles1m.length < 5) return NULL;

    // Sync grid centre from server if available (source of truth)
    if (serverGridState?.center && serverGridState.center > 0) {
      gridCenterRef.current = serverGridState.center;
    }
    // Anchor on first live price if not yet set
    if (!gridCenterRef.current) {
      gridCenterRef.current = Math.round(livePrice / GRID_SIZE) * GRID_SIZE;
    }
    if (serverGridState?.daily_pnl !== undefined) {
      dailyPnlRef.current = serverGridState.daily_pnl;
    }

    const center    = gridCenterRef.current;
    const lastPrice = lastPriceRef.current || livePrice;
    const dailyPnl  = dailyPnlRef.current;

    const gridMin = center - MAX_LEVELS * GRID_SIZE;
    const gridMax = center + MAX_LEVELS * GRID_SIZE;

    // ── Conditions ──────────────────────────────────────────────────────────
    const inRange     = livePrice >= gridMin && livePrice <= gridMax;
    const pnlOk       = dailyPnl > DAILY_LOSS_LIM && dailyPnl < DAILY_PROF_LIM;
    const curFloor    = Math.floor(livePrice / GRID_SIZE) * GRID_SIZE;
    const lastFloor   = Math.floor(lastPrice  / GRID_SIZE) * GRID_SIZE;
    const crossedDown = curFloor < lastFloor;

    const vols       = candles1m.slice(-5).map((c) => c.volume);
    const volOk      = vols.reduce((a, b) => a + b, 0) > 0;

    const condList: GridCondition[] = [
      { label: "In grid range",   met: inRange,     value: inRange ? `$${gridMin.toLocaleString()}–$${gridMax.toLocaleString()}` : "out of range" },
      { label: "P&L limits OK",   met: pnlOk,       value: `Daily ${dailyPnl >= 0 ? "+" : ""}$${dailyPnl.toFixed(0)}` },
      { label: "$50 level cross", met: crossedDown, value: crossedDown ? `↓ through $${curFloor.toLocaleString()}` : `at $${curFloor.toLocaleString()}` },
      { label: "Candle data",     met: candles1m.length >= 5, value: `${candles1m.length} bars` },
      { label: "Volume active",   met: volOk,       value: volOk ? "yes" : "zero" },
    ];
    const metCount = condList.filter((c) => c.met).length;

    // ── Grid level ladder (±10 levels centred on current price) ─────────────
    const levels: GridLevel[] = [];
    const lvlRange = 10;
    for (let i = -lvlRange; i <= lvlRange; i++) {
      const price = center + i * GRID_SIZE;
      const type: "buy" | "sell" = price < livePrice ? "buy" : "sell";
      levels.push({ price, type, status: "pending", idx: i });
    }

    // ── Signal ──────────────────────────────────────────────────────────────
    let signal: GridSignal | null = null;
    if (inRange && pnlOk && crossedDown && candles1m.length >= 5) {
      const entry = livePrice;
      const tp    = curFloor + GRID_SIZE * TP_GRIDS;
      const sl    = curFloor - GRID_SIZE * SL_GRIDS;
      const depth = (livePrice - gridMin) / (gridMax - gridMin);
      const conf  = Math.max(0.60, Math.min(0.85, 0.72 + (volOk ? 0.05 : 0) - Math.abs(depth - 0.5) * 0.1));
      signal = {
        direction: "long", entry: +entry.toFixed(2),
        tp: +tp.toFixed(2), sl: +sl.toFixed(2),
        confidence: +conf.toFixed(3), rr: "1:1.0",
        level: curFloor,
        reasoning: `Grid $50: price crossed $${curFloor.toLocaleString()} → $${(curFloor + GRID_SIZE).toLocaleString()} · center $${center.toLocaleString()} · max ${MAX_CONCURRENT} slots`,
      };
    }

    // Update last price ref for next render
    lastPriceRef.current = livePrice;

    const bias: GridResult["bias"] = !inRange ? "stopped" : !pnlOk ? "paused" : "long";

    return {
      signal,
      bias,
      name: "Grid $50",
      gridCenter: center,
      gridMin,
      gridMax,
      currentLevel: curFloor,
      currentPrice: livePrice,
      distToNextBuy:  livePrice - curFloor,               // $ above nearest buy trigger
      distToNextSell: curFloor + GRID_SIZE - livePrice,   // $ below nearest sell trigger
      levels,
      conditions: condList,
      metCount,
      total: 5,
      dailyPnl,
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [livePrice, candles1m, serverGridState]);
}
