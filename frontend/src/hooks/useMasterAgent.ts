"use client";

/**
 * useMasterAgent — 4-Strategy Confluence Brain v2
 * ─────────────────────────────────────────────────
 * Aggregates ALL 4 live strategy signals + server-side agent data.
 * Models a senior hedge fund analyst with full market context access:
 *
 *  Layer 1 — Strategy Consensus   : 5 strategies vote (need ≥2/5 aligned)
 *  Layer 2 — Conviction Scoring   : Weighted 0-100, 4-strategy Kelly sizing
 *  Layer 3 — Market Regime        : Trending / Ranging / Volatile / Unknown
 *  Layer 4 — Kelly Position Size  : Fractional Kelly scaled by conviction
 *  Layer 5 — Risk Protocol        : Multi-timeframe gates, ATR volatility filter
 *  Layer 6 — Commentary Engine    : Bloomberg-style live narrative, 30s rotation
 *  Layer 7 — Site Intelligence    : Full access to server agent P&L, positions,
 *                                   trades, logs, and all strategy conditions
 *
 *  Conviction grades (4-strategy model):
 *   A+ (85+)  → 5/5 consensus or 4/5 + strong signals  → Full size
 *   A  (70+)  → 3/4 consensus, conditions mostly met    → 75% size
 *   B  (55+)  → 2/4 consensus, good signal quality      → 50% size
 *   C  (40+)  → 2/4 weak or diverging                  → 25% size
 *   X  (<40)  → No trade — wait for confluence
 */

import { useState, useEffect, useRef, useCallback } from "react";
import { StrategyResult } from "./useStrategyEngine";
import { ORBResult } from "./useORBStrategy";
import { HFTResult } from "./useHFTScalper";
import { OBIResult } from "./useOBIScalper";
import { GridResult } from "./useGridStrategy";
import { BinanceCandle, BinanceTicker, BinanceOrderBook } from "./useBinanceStream";
import { ServerStatus } from "./useServerAgent";

// ─── Types ────────────────────────────────────────────────────────────────────
export type ConvictionGrade = "A+" | "A" | "B" | "C" | "X";
export type MarketRegime   = "TRENDING" | "RANGING" | "VOLATILE" | "UNKNOWN";
export type AgentDirection = "LONG" | "SHORT" | "FLAT";

export interface ChatMessage {
  id:        number;
  role:      "agent" | "user";
  content:   string;
  timestamp: string;
  type?:     "auto" | "response" | "system";
}

export interface StrategyVote {
  name:     string;
  bias:     "long" | "short" | "neutral";
  signal:   boolean;
  conf:     number;
  met_pct:  number;
  timeframe: string;
  reasoning?: string;
}

export interface MasterSignal {
  direction:  AgentDirection;
  entry:      number;
  sl:         number;
  tp:         number;
  size_pct:   number;
  rr:         string;
  conviction: number;
  grade:      ConvictionGrade;
  reasoning:  string;
  timestamp:  string;
}

export interface MasterState {
  direction:        AgentDirection;
  conviction:       number;
  grade:            ConvictionGrade;
  signal:           MasterSignal | null;
  votes:            StrategyVote[];
  consensus_count:  number;
  regime:           MarketRegime;
  thoughts:         string[];
  chat:             ChatMessage[];
  last_update:      string;
  // paper_position is always null — server agent handles all paper trading now.
  // Keep the full union type so legacy UI code (?.open etc.) still compiles.
  paper_position: {
    open: boolean; direction?: AgentDirection; entry?: number; sl?: number;
    tp?: number; current?: number; pnl_usd?: number; pnl_pct?: number;
    size_usdc?: number; btc_qty?: number;
  } | null;
  paper_stats: {
    total_pnl: number; wins: number; losses: number;
    win_rate: number;  total_trades: number; best: number; worst: number;
  };
  last_price:  number | null;
  price_24h:   number | null;
}

// ─── Indicator helpers ────────────────────────────────────────────────────────
function ema(vals: number[], p: number): number {
  if (vals.length < p) return NaN;
  const k = 2 / (p + 1);
  let v = vals.slice(0, p).reduce((a, b) => a + b, 0) / p;
  for (let i = p; i < vals.length; i++) v = vals[i] * k + v * (1 - k);
  return v;
}

function atr(candles: BinanceCandle[], p = 14): number {
  const trs = candles.slice(1).map((c, i) => Math.max(
    c.high - c.low, Math.abs(c.high - candles[i].close), Math.abs(c.low - candles[i].close)
  ));
  if (trs.length < p) return NaN;
  return trs.slice(-p).reduce((a, b) => a + b, 0) / p;
}

function rsiLast(closes: number[], p = 14): number {
  if (closes.length <= p) return NaN;
  const d = closes.slice(1).map((c, i) => c - closes[i]);
  const g = d.map(x => Math.max(x, 0)), l = d.map(x => Math.abs(Math.min(x, 0)));
  let ag = g.slice(0, p).reduce((a, b) => a + b, 0) / p;
  let al = l.slice(0, p).reduce((a, b) => a + b, 0) / p;
  for (let i = p; i < d.length; i++) {
    ag = (ag * (p - 1) + g[i]) / p; al = (al * (p - 1) + l[i]) / p;
  }
  return al === 0 ? 100 : 100 - 100 / (1 + ag / al);
}

function grade(conviction: number): ConvictionGrade {
  if (conviction >= 85) return "A+";
  if (conviction >= 70) return "A";
  if (conviction >= 55) return "B";
  if (conviction >= 40) return "C";
  return "X";
}

// ─── Market regime detector ───────────────────────────────────────────────────
function detectRegime(candles: BinanceCandle[]): MarketRegime {
  if (candles.length < 30) return "UNKNOWN";
  const closes = candles.slice(-30).map(c => c.close);
  const e14 = ema(closes, 14), e28 = ema(closes.slice(0, -5), 14);
  const atrV = atr(candles.slice(-20));
  const mid  = closes[closes.length - 1];
  if (!isNaN(atrV) && atrV / mid > 0.02) return "VOLATILE";
  if (!isNaN(e14) && !isNaN(e28) && Math.abs((e14 - e28) / mid) > 0.002) return "TRENDING";
  if (!isNaN(e14)) return "RANGING";
  return "UNKNOWN";
}

// ─── Conviction calculator (4-strategy model) ─────────────────────────────────
function calcConviction(
  votes:     StrategyVote[],
  direction: AgentDirection,
  atrPct:    number | null,
): number {
  if (direction === "FLAT") return 0;
  const dir = direction.toLowerCase();
  const aligned = votes.filter(v => v.bias === dir);
  const n = aligned.length;
  if (n === 0) return 0;

  // Alignment multiplier: rewards strong consensus
  const alignMult = n === 4 ? 2.0 : n === 3 ? 1.4 : n === 2 ? 1.0 : 0.5;

  // Quality score: avg conditions met × avg signal confidence
  const avgMet  = aligned.reduce((s, v) => s + v.met_pct, 0) / n;
  const signals = aligned.filter(v => v.signal);
  const avgConf = signals.length
    ? signals.reduce((s, v) => s + v.conf, 0) / signals.length
    : avgMet * 0.7;

  // Signal bonus: extra weight when strategies actually fire signals
  const signalBonus = signals.length / 4;

  // Volatility discount: reduce size in high-vol environments
  const volDiscount = atrPct != null && atrPct > 0.018 ? 0.82 : 1.0;

  const raw = 30 * alignMult * (0.35 + avgMet * 0.35 + avgConf * 0.30 + signalBonus * 0.15) * volDiscount;
  return Math.min(Math.round(raw), 100);
}

// ─── Kelly position sizing (4-strategy blend) ──────────────────────────────
// Blended Kelly from all 4 strategy backtests:
//   Momentum 15m: 55% WR, 2.0 R:R → Kelly = 32.5%
//   HFT Scalper:  52% WR, 1.5 R:R → Kelly = 18.7%
//   ORB-30:       55% WR, 2.2 R:R → Kelly = 35.0%
//   OBI Scalper:  53% WR, 2.0 R:R → Kelly = 24.5%
// Blended ≈ 27.7%  →  25% fractional = 6.9%  →  cap at 5%
function calcSizePct(conviction: number, alignedCount: number): number {
  const fracKelly = 0.277 * 0.25;
  const convScale = conviction / 100;
  const alignBonus = alignedCount >= 4 ? 1.4 : alignedCount === 3 ? 1.15 : 1.0;
  return Math.min(Math.round(fracKelly * convScale * alignBonus * 1000) / 10, 5.0);
}

// ─── Commentary builder ───────────────────────────────────────────────────────
function buildThought(ctx: {
  price: number | null;
  atrPct: number | null;
  votes: StrategyVote[];
  regime: MarketRegime;
  direction: AgentDirection;
  conviction: number;
  grade: ConvictionGrade;
  signal?: MasterSignal | null;
  serverData: ServerStatus | null;
  obi: number | null;
  rsi15m: number | null;
}, ts: string): string {
  const { price, direction, conviction, grade: g, signal, votes, regime, serverData, obi, rsi15m, atrPct } = ctx;
  const pStr = price ? `$${price.toLocaleString(undefined, { maximumFractionDigits: 0 })}` : "—";
  const aligned = votes.filter(v => v.bias === direction.toLowerCase()).length;
  const serverPos = serverData?.open_positions ?? [];
  const serverStats = serverData?.stats;

  // Active signal → always show the trade card
  if (signal && direction !== "FLAT" && conviction >= 55) {
    const riskPct = ((Math.abs(signal.entry - signal.sl) / signal.entry) * 100).toFixed(2);
    const lines = [
      `${direction}  ·  ${pStr}  ·  SL $${signal.sl.toFixed(0)} (${riskPct}%)  ·  TP $${signal.tp.toFixed(0)}  ·  ${signal.rr}`,
      `Grade ${g}  ·  ${conviction}/100  ·  ${aligned}/5 strategies aligned  ·  ${regime}`,
    ];
    if (serverPos.length > 0)
      lines.push(`Server: ${serverPos.length} open position${serverPos.length > 1 ? "s" : ""}`);
    return lines.join("\n");
  }

  // Bias but no signal yet
  if (direction !== "FLAT") {
    const needed = Math.max(0, 55 - conviction);
    const signallingVotes = votes.filter(v => v.signal && v.bias === direction.toLowerCase());
    const parts = [
      `${direction} bias  ·  ${conviction}/100 (${g})  ·  ${aligned}/4 aligned  ·  BTC ${pStr}`,
      `Need ${needed > 0 ? `+${needed} pts` : "signal trigger"}  ·  ${signallingVotes.length}/5 strategies signalling`,
    ];
    if (obi !== null) parts.push(`OBI ${obi >= 0 ? "+" : ""}${obi.toFixed(3)}  ·  RSI(15m) ${rsi15m?.toFixed(1) ?? "—"}  ·  ATR ${atrPct ? (atrPct * 100).toFixed(2) + "%" : "—"}`);
    return parts.join("\n");
  }

  // Flat — describe what we're watching
  const mostVoted = votes.reduce((acc: Record<string, number>, v) => {
    acc[v.bias] = (acc[v.bias] || 0) + 1; return acc;
  }, {});
  const dominant = Object.entries(mostVoted).sort((a, b) => b[1] - a[1])[0];
  const serverStat = serverStats ? `Server: ${serverStats.total_trades} trades · P&L ${serverStats.total_pnl >= 0 ? "+" : ""}$${serverStats.total_pnl.toFixed(2)}` : "";
  return [
    `FLAT  ·  No edge  ·  ${conviction}/100  ·  ${dominant ? `${dominant[1]}/4 leaning ${dominant[0].toUpperCase()}` : "strategies diverging"}`,
    `Regime: ${regime}  ·  BTC ${pStr}${obi !== null ? `  ·  OBI ${obi >= 0 ? "+" : ""}${obi.toFixed(3)}` : ""}`,
    serverStat,
  ].filter(Boolean).join("\n");
}

// ─── Smart chat response engine ───────────────────────────────────────────────
interface ResponseCtx {
  price:          number | null;
  direction:      AgentDirection;
  conviction:     number;
  grade:          ConvictionGrade;
  regime:         MarketRegime;
  votes:          StrategyVote[];
  signal:         MasterSignal | null;
  atrPct:         number | null;
  obi:            number | null;
  rsi15m:         number | null;
  ema50v:         number | null;
  ema21v:         number | null;
  momentumResult: StrategyResult;
  orbResult:      ORBResult;
  hftResult:      HFTResult;
  obiResult:      OBIResult;
  ticker:         BinanceTicker | null;
  serverData:     ServerStatus | null;
}

function fmtSig(sig: MasterSignal, conv: number, g: ConvictionGrade, aligned: number): string {
  const riskUsd  = Math.abs(sig.entry - sig.sl);
  const rwdUsd   = Math.abs(sig.tp - sig.entry);
  const riskPct  = (riskUsd / sig.entry * 100).toFixed(2);
  return [
    `${sig.direction === "LONG" ? "▲ LONG" : "▼ SHORT"}  ·  Grade ${g}  ·  ${conv}/100  ·  ${aligned}/5 strategies`,
    `Entry  $${sig.entry.toFixed(0)}`,
    `SL     $${sig.sl.toFixed(0)}  (−${riskPct}% / −$${riskUsd.toFixed(0)})`,
    `TP     $${sig.tp.toFixed(0)}  (+$${rwdUsd.toFixed(0)})`,
    `R:R    ${sig.rr}  ·  Size ${sig.size_pct}% of account`,
  ].join("\n");
}

function generateResponse(userMsg: string, ctx: ResponseCtx): string {
  const m   = userMsg.toLowerCase();
  const p   = ctx.price;
  const pStr = p ? `$${p.toLocaleString(undefined, { maximumFractionDigits: 0 })}` : "—";
  const { direction: dir, conviction: conv, grade: g, signal: sig, votes, regime, serverData } = ctx;
  const aligned = votes.filter(v => v.bias === dir.toLowerCase()).length;
  const server  = serverData;
  const stats   = server?.stats;
  const positions = server?.open_positions ?? [];

  // ── Helper: always prepend signal if active ──────────────────────────────
  const sigBlock = (suffix = "") => sig && dir !== "FLAT" && conv >= 55
    ? fmtSig(sig, conv, g, aligned) + (suffix ? "\n\n" + suffix : "")
    : null;

  // ── Server: positions / open trades ──────────────────────────────────────
  if (/position|open trade|open pos|current trade/i.test(m)) {
    if (positions.length === 0) {
      return sigBlock("No open server positions — agent scanning for entry.") ?? "No open server positions. Agent is scanning all 5 strategies.";
    }
    const posLines = positions.map(pos => {
      const pnl = pos.unrealized_pnl ?? 0;
      return `  ${pos.direction.toUpperCase()} via ${pos.strategy_name}  ·  Entry $${pos.entry.toFixed(0)}  ·  P&L ${pnl >= 0 ? "+" : ""}$${pnl.toFixed(2)}  ·  ${pos.confidence ? `conf ${(pos.confidence * 100).toFixed(0)}%` : ""}`;
    }).join("\n");
    return `Open positions (${positions.length}):\n${posLines}`;
  }

  // ── Server: P&L / stats / performance ─────────────────────────────────────
  if (/pnl|p&l|profit|performance|stats|win rate|trades|how.*doing|account/i.test(m)) {
    if (!stats) return "Server agent not connected — cannot retrieve stats.";
    const unrealized = positions.reduce((s, p) => s + (p.unrealized_pnl ?? 0), 0);
    const lines = [
      `Server Agent Performance`,
      `Trades:  ${stats.total_trades} (${stats.wins}W / ${stats.losses}L)`,
      `Win Rate: ${stats.win_rate.toFixed(1)}%`,
      `Realized P&L: ${stats.total_pnl >= 0 ? "+" : ""}$${stats.total_pnl.toFixed(2)}`,
      positions.length > 0 ? `Unrealized: ${unrealized >= 0 ? "+" : ""}$${unrealized.toFixed(2)} (${positions.length} positions)` : "No open positions",
      stats.best_trade  ? `Best trade:  +$${stats.best_trade.toFixed(2)}` : "",
      stats.worst_trade ? `Worst trade: $${stats.worst_trade.toFixed(2)}` : "",
    ].filter(Boolean);
    const s = sigBlock();
    return s ? `${s}\n\n${lines.join("\n")}` : lines.join("\n");
  }

  // ── Agent log / activity ──────────────────────────────────────────────────
  if (/log|activity|what.*doing|last.*scan|recent/i.test(m)) {
    const logs = server?.log?.slice(0, 5) ?? [];
    if (logs.length === 0) return "No agent log data — server may be starting up.";
    return `Recent agent activity:\n${logs.map(l => `  ${l}`).join("\n")}`;
  }

  // ── Individual strategy queries ───────────────────────────────────────────
  if (/momentum|mv15|15m|15 min/i.test(m) && !/orb|hft|obi/i.test(m)) {
    const mv = ctx.momentumResult;
    const vs = votes.find(v => v.name === "Momentum 15m");
    const lines = [
      `Momentum 15m  ·  Bias: ${mv.bias.toUpperCase()}  ·  ${mv.met_count}/${mv.total ?? 7} conditions`,
      mv.signal ? `Signal: ${mv.signal.direction.toUpperCase()} @ $${mv.signal.entry?.toFixed(0)} · SL $${mv.signal.sl?.toFixed(0)} · TP $${mv.signal.tp?.toFixed(0)} · conf ${(mv.signal.confidence * 100).toFixed(0)}%` : "No signal",
      `EMA50: $${ctx.ema50v?.toFixed(0) ?? "—"}  ·  EMA21: $${ctx.ema21v?.toFixed(0) ?? "—"}`,
      vs ? `Agent weight: ${(vs.met_pct * 100).toFixed(0)}% conditions met` : "",
    ].filter(Boolean);
    return lines.join("\n");
  }

  if (/orb|opening range|breakout/i.test(m) && !/hft|obi|momentum/i.test(m)) {
    const orb = ctx.orbResult;
    return [
      `ORB-30  ·  Bias: ${orb.bias.toUpperCase()}  ·  ${orb.met_count}/${orb.total ?? 4} conditions`,
      orb.signal ? `Signal: ${orb.signal.direction.toUpperCase()} @ $${orb.signal.entry?.toFixed(0)} · conf ${(orb.signal.confidence * 100).toFixed(0)}%` : "No signal — waiting for OR breakout",
    ].join("\n");
  }

  if (/hft|vwap|scalp.*1m|high freq/i.test(m) && !/obi|momentum|orb/i.test(m)) {
    const hft = ctx.hftResult;
    const ind = hft.indicators;
    return [
      `HFT Scalper  ·  Bias: ${hft.bias.toUpperCase()}  ·  ${hft.met_count}/${hft.total ?? 7} conditions`,
      hft.signal ? `Signal: ${hft.signal.direction.toUpperCase()} @ $${hft.signal.entry?.toFixed(0)} · SL $${hft.signal.sl?.toFixed(0)} · conf ${(hft.signal.confidence * 100).toFixed(0)}%` : "No signal",
      `VWAP: $${ind.vwap_1m?.toFixed(0) ?? "—"}  ·  OBI: ${ind.obi?.toFixed(3) ?? "—"}  ·  ATR: $${ind.atr_1m?.toFixed(0) ?? "—"}`,
    ].join("\n");
  }

  if (/obi|order book|imbalance|book/i.test(m) && !/momentum|orb/i.test(m)) {
    const obi = ctx.obiResult;
    const ind = obi.obi_indicators;
    const obiVal = ind.obi ?? ctx.obi;
    return [
      `OBI Scalper  ·  Bias: ${obi.bias.toUpperCase()}  ·  ${obi.met_count}/${obi.total ?? 3} conditions`,
      obi.signal ? `Signal: ${obi.signal.direction.toUpperCase()} @ $${obi.signal.entry?.toFixed(0)} · conf ${(obi.signal.confidence * 100).toFixed(0)}%` : "No signal",
      `OBI: ${obiVal != null ? (obiVal >= 0 ? "+" : "") + obiVal.toFixed(3) : "—"}  ·  EMA9: $${ind.ema9?.toFixed(0) ?? "—"}  ·  RSI: ${ind.rsi?.toFixed(1) ?? "—"}`,
    ].join("\n");
  }

  // ── Regime / trend / volatility ───────────────────────────────────────────
  if (/regime|trend|volatile|vol|atr|market condition/i.test(m)) {
    const atrUsd  = p && ctx.atrPct ? (p * ctx.atrPct).toFixed(0) : "—";
    const rsiStr  = ctx.rsi15m?.toFixed(1) ?? "—";
    const e50Str  = ctx.ema50v?.toFixed(0) ?? "—";
    const e21Str  = ctx.ema21v?.toFixed(0) ?? "—";
    return [
      `Market Regime: ${regime}  ·  BTC ${pStr}`,
      `ATR(14): $${atrUsd} (${ctx.atrPct ? (ctx.atrPct * 100).toFixed(2) + "%" : "—"} of price)`,
      `RSI(15m): ${rsiStr}  ·  EMA50: $${e50Str}  ·  EMA21: $${e21Str}`,
      regime === "TRENDING" ? `Trending market — momentum and breakout strategies favoured` :
      regime === "RANGING"  ? `Ranging market — mean-reversion and OBI scalping favoured` :
      regime === "VOLATILE" ? `High volatility — reduce size, widen stops, use OBI only` :
      `Low data — monitoring before committing edge`,
    ].join("\n");
  }

  // ── Price / BTC specific ──────────────────────────────────────────────────
  if (/btc|bitcoin|price|where.*btc|what.*price/i.test(m)) {
    const chg = ctx.ticker?.change_pct;
    return [
      `BTC ${pStr}${chg !== undefined ? ` (${chg >= 0 ? "+" : ""}${chg?.toFixed(2)}% 24h)` : ""}`,
      `EMA50: $${ctx.ema50v?.toFixed(0) ?? "—"}  ·  EMA21: $${ctx.ema21v?.toFixed(0) ?? "—"}  ·  RSI: ${ctx.rsi15m?.toFixed(1) ?? "—"}`,
      sigBlock() ?? `Regime: ${regime}  ·  Conviction: ${conv}/100`,
    ].filter(Boolean).join("\n");
  }

  // ── Risk / sizing ──────────────────────────────────────────────────────────
  if (/risk|size|kelly|how much|position size|capital/i.test(m)) {
    const sizePct = sig?.size_pct ?? calcSizePct(conv, aligned);
    return [
      `Position sizing (fractional Kelly, 4-strategy blend)`,
      `Conviction: ${conv}/100 (${g})  ·  ${aligned}/5 strategies aligned`,
      `Recommended size: ${sizePct}% of account`,
      `Logic: ${g === "A+" ? "Full Kelly — maximum edge, 5-way confluence" : g === "A" ? "75% Kelly — strong edge, 4-way confluence" : g === "B" ? "50% Kelly — moderate edge, 3-way consensus" : "25% Kelly or less — edge insufficient for large size"}`,
      conv < 55 ? `Not yet trading — need ${55 - conv} more conviction pts` : `✓ Trade criteria met`,
    ].join("\n");
  }

  // ── Full status dump ──────────────────────────────────────────────────────
  if (/status|everything|full|overview|all strategies|summary/i.test(m)) {
    const voteLines = votes.map(v =>
      `  ${v.name.padEnd(14)} ${v.bias.toUpperCase().padEnd(7)} ${v.signal ? "✓ SIGNAL" : `${(v.met_pct * 100).toFixed(0)}% conds`}`
    );
    const serverLine = stats
      ? `\nServer: ${stats.total_trades} trades · ${stats.win_rate.toFixed(1)}% WR · P&L ${stats.total_pnl >= 0 ? "+" : ""}$${stats.total_pnl.toFixed(2)} · ${positions.length} open`
      : "";
    const s = sigBlock();
    return (s ? s + "\n\n" : "") + [
      `BTC ${pStr}  ·  ${regime}  ·  ${conv}/100 (${g})`,
      `Strategy votes:`,
      ...voteLines,
      serverLine,
    ].filter(Boolean).join("\n");
  }

  // ── Signal / entry / should I trade ──────────────────────────────────────
  if (sig && dir !== "FLAT" && conv >= 55) {
    const base = fmtSig(sig, conv, g, aligned);
    if (/wait|patience|should|enter|execute|go/i.test(m)) {
      const verdict = g === "A+" ? "A+ setup — execute with conviction." : g === "A" ? "Grade A — take it at 75% size." : "Grade B — 50% size, tighten SL.";
      return `${base}\n\n${verdict}`;
    }
    return base;
  }

  // ── Default: no signal, show best explanation ─────────────────────────────
  const needed = Math.max(0, 55 - conv);
  const voteLines = votes.map(v =>
    `  ${v.name.padEnd(14)} ${v.bias.toUpperCase().padEnd(7)} ${v.signal ? "✓ SIGNAL" : `${(v.met_pct * 100).toFixed(0)}% conds`}`
  );
  return [
    `NO SIGNAL  ·  BTC ${pStr}  ·  Conviction ${conv}/100 (${g})`,
    `Need +${needed} pts to trigger. ${aligned}/5 strategies aligned (${dir}).`,
    `Strategy votes:`,
    ...voteLines,
    ctx.atrPct ? `ATR: ${(ctx.atrPct * 100).toFixed(2)}%  ·  Regime: ${regime}` : `Regime: ${regime}`,
  ].join("\n");
}

// ─── Main hook ────────────────────────────────────────────────────────────────
export function useMasterAgent(
  momentumResult: StrategyResult,
  orbResult:      ORBResult,
  hftResult:      HFTResult,
  obiResult:      OBIResult,
  gridResult:     GridResult,
  candles15m:     BinanceCandle[],
  candles1m:      BinanceCandle[],
  ticker:         BinanceTicker | null,
  orderBook:      BinanceOrderBook | null,
  serverData:     ServerStatus | null,
): MasterState & {
  openPaperTrade:  (sig: MasterSignal) => void;
  closePaperTrade: () => void;
  resetPaper:      () => void;
  sendMessage:     (msg: string) => void;
} {
  const [thoughts, setThoughts] = useState<string[]>([]);
  const [chat, setChat]         = useState<ChatMessage[]>([]);
  const thoughtTimerRef         = useRef<ReturnType<typeof setInterval> | null>(null);
  const chatMsgId               = useRef(0);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const coreRef                 = useRef<any>(null);

  // ── Compute core state ────────────────────────────────────────────────────
  const core = (() => {
    const price = ticker?.last ?? candles15m[candles15m.length - 1]?.close ?? null;

    // Order book imbalance
    let obi: number | null = null;
    if (orderBook && orderBook.bids.length > 0 && orderBook.asks.length > 0) {
      const bidVol = orderBook.bids.slice(0, 10).reduce((s, l) => s + l.amount, 0);
      const askVol = orderBook.asks.slice(0, 10).reduce((s, l) => s + l.amount, 0);
      const tot = bidVol + askVol;
      obi = tot > 0 ? (bidVol - askVol) / tot : 0;
    }
    // Use OBI from obiResult if available
    if (obiResult.obi_indicators.obi !== null) obi = obiResult.obi_indicators.obi;

    // Technical indicators
    const closes15 = candles15m.map(c => c.close);
    const ema50v   = closes15.length >= 50 ? ema(closes15, 50) : null;
    const ema21v   = closes15.length >= 21 ? ema(closes15, 21) : null;
    const rsi15m   = closes15.length > 14   ? rsiLast(closes15) : null;
    const atrVal   = atr(candles15m.slice(-20));
    const atrPct   = price && !isNaN(atrVal) ? atrVal / price : null;

    // Strategy votes — all 4
    const votes: StrategyVote[] = [
      {
        name: "Momentum 15m",
        bias: momentumResult.bias,
        signal: !!momentumResult.signal,
        conf: momentumResult.signal?.confidence ?? (momentumResult.met_count / (momentumResult.total ?? 7)) * 0.65,
        met_pct: momentumResult.met_count / (momentumResult.total ?? 7),
        timeframe: "15m",
        reasoning: momentumResult.signal?.reasoning,
      },
      {
        name: "ORB-30",
        bias: orbResult.bias,
        signal: !!orbResult.signal,
        conf: orbResult.signal?.confidence ?? (orbResult.met_count / (orbResult.total ?? 4)) * 0.60,
        met_pct: orbResult.met_count / (orbResult.total ?? 4),
        timeframe: "1m",
        reasoning: orbResult.signal?.reasoning,
      },
      {
        name: "HFT Scalper",
        bias: hftResult.bias,
        signal: !!hftResult.signal,
        conf: hftResult.signal?.confidence ?? (hftResult.met_count / (hftResult.total ?? 7)) * 0.60,
        met_pct: hftResult.met_count / (hftResult.total ?? 7),
        timeframe: "1m",
        reasoning: hftResult.signal?.reasoning,
      },
      {
        name: "OBI Scalper",
        bias: obiResult.bias,
        signal: !!obiResult.signal,
        conf: obiResult.signal?.confidence ?? (obiResult.met_count / (obiResult.total ?? 3)) * 0.55,
        met_pct: obiResult.met_count / (obiResult.total ?? 3),
        timeframe: "1m",
        reasoning: obiResult.signal?.reasoning,
      },
      {
        name: "Grid $50",
        bias: gridResult.bias === "stopped" || gridResult.bias === "paused" ? "neutral" : gridResult.bias,
        signal: !!gridResult.signal,
        conf: gridResult.signal?.confidence ?? (gridResult.metCount / (gridResult.total ?? 5)) * 0.60,
        met_pct: gridResult.metCount / (gridResult.total ?? 5),
        timeframe: "continuous",
        reasoning: gridResult.signal?.reasoning,
      },
    ];

    // Consensus direction — need ≥2/5 strategies aligned
    const longCount  = votes.filter(v => v.bias === "long").length;
    const shortCount = votes.filter(v => v.bias === "short").length;
    let direction: AgentDirection = "FLAT";
    if (longCount >= 2 && longCount > shortCount)        direction = "LONG";
    else if (shortCount >= 2 && shortCount > longCount)  direction = "SHORT";
    else if (longCount === 1 && shortCount === 0)         direction = "LONG";
    else if (shortCount === 1 && longCount === 0)         direction = "SHORT";

    const consensusCount = direction === "LONG" ? longCount : direction === "SHORT" ? shortCount : 0;
    const regime         = detectRegime(candles15m);
    const conviction     = calcConviction(votes, direction, atrPct);
    const g              = grade(conviction);
    const sizePct        = calcSizePct(conviction, consensusCount);

    // Master signal — pick best aligned signal from all 4
    let signal: MasterSignal | null = null;
    if (conviction >= 55 && direction !== "FLAT" && price) {
      const dir = direction.toLowerCase();
      const dirSigs = [
        momentumResult.signal?.direction === dir ? momentumResult.signal : null,
        orbResult.signal?.direction === dir       ? orbResult.signal      : null,
        hftResult.signal?.direction === dir       ? { ...hftResult.signal, sl: hftResult.signal.sl, tp: hftResult.signal.tp1 } : null,
        obiResult.signal?.direction === dir       ? obiResult.signal      : null,
        gridResult.signal?.direction === dir      ? gridResult.signal     : null,
      ].filter(Boolean) as Array<{ direction: string; entry: number; sl: number; tp: number; confidence: number; reasoning?: string }>;

      // Best signal = highest confidence among aligned ones
      const best = dirSigs.sort((a, b) => b.confidence - a.confidence)[0];

      let entry = price, sl: number, tp: number;
      if (best) {
        entry = best.entry || price;
        sl    = best.sl;
        tp    = best.tp;
      } else if (!isNaN(atrVal)) {
        const slD = atrVal * 1.5, tpD = atrVal * 3.0;
        sl = direction === "LONG" ? entry - slD : entry + slD;
        tp = direction === "LONG" ? entry + tpD : entry - tpD;
      } else {
        sl = direction === "LONG" ? entry * 0.985 : entry * 1.015;
        tp = direction === "LONG" ? entry * 1.030 : entry * 0.970;
      }

      const risk = Math.abs(entry - sl), rwd = Math.abs(tp - entry);
      const rr   = risk > 0 ? `1:${(rwd / risk).toFixed(1)}` : "—";

      const signallingNames = votes
        .filter(v => v.signal && v.bias === dir)
        .map(v => v.name).join(", ");

      signal = {
        direction,
        entry: Math.round(entry * 100) / 100,
        sl:    Math.round(sl * 100) / 100,
        tp:    Math.round(tp * 100) / 100,
        rr, size_pct: sizePct, conviction, grade: g,
        reasoning: `${consensusCount}/5 strategies aligned (${signallingNames || direction}) · regime: ${regime}`,
        timestamp: new Date().toISOString(),
      };
    }

    return { votes, direction, consensusCount, conviction, grade: g, signal, regime, price, atrPct, ema50v, ema21v, rsi15m, obi };
  })();

  // Always keep core ref up to date
  useEffect(() => { coreRef.current = { ...core, momentumResult, orbResult, hftResult, obiResult, ticker, serverData }; });

  // ── Commentary — refreshes every 30s or on conviction change ─────────────
  const generateThought = useCallback(() => {
    const ts = new Date().toLocaleTimeString();
    const thought = buildThought({
      price: core.price, atrPct: core.atrPct, votes: core.votes,
      regime: core.regime, direction: core.direction,
      conviction: core.conviction, grade: core.grade,
      signal: core.signal, serverData,
      obi: core.obi, rsi15m: core.rsi15m,
    }, ts);
    setThoughts(prev => [thought, ...prev].slice(0, 30));
    setChat(prev => [
      ...prev,
      { id: ++chatMsgId.current, role: "agent" as const, content: thought, timestamp: ts, type: "auto" as const },
    ].slice(-80));
  }, [core, serverData]);

  useEffect(() => {
    generateThought();
    if (thoughtTimerRef.current) clearInterval(thoughtTimerRef.current);
    thoughtTimerRef.current = setInterval(generateThought, 30_000);
    return () => { if (thoughtTimerRef.current) clearInterval(thoughtTimerRef.current); };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [core.direction, core.conviction, core.regime, serverData?.stats?.total_trades]);

  // ── Chat: sendMessage ─────────────────────────────────────────────────────
  const sendMessage = useCallback((userMsg: string) => {
    if (!userMsg.trim()) return;
    const ts = new Date().toLocaleTimeString();
    setChat(prev => [...prev, { id: ++chatMsgId.current, role: "user" as const, content: userMsg.trim(), timestamp: ts }]);

    setTimeout(() => {
      const c = coreRef.current ?? core;
      const response = generateResponse(userMsg, {
        price:     c.price,
        direction: c.direction,
        conviction: c.conviction,
        grade:     c.grade,
        regime:    c.regime,
        votes:     c.votes,
        signal:    c.signal,
        atrPct:    c.atrPct,
        obi:       c.obi,
        rsi15m:    c.rsi15m,
        ema50v:    c.ema50v,
        ema21v:    c.ema21v,
        momentumResult: c.momentumResult ?? momentumResult,
        orbResult:      c.orbResult      ?? orbResult,
        hftResult:      c.hftResult      ?? hftResult,
        obiResult:      c.obiResult      ?? obiResult,
        ticker:         c.ticker         ?? ticker,
        serverData:     c.serverData     ?? serverData,
      });
      const rTs = new Date().toLocaleTimeString();
      setChat(prev => [...prev, { id: ++chatMsgId.current, role: "agent" as const, content: response, timestamp: rTs, type: "response" as const }].slice(-80));
    }, 350 + Math.random() * 250);
  }, [momentumResult, orbResult, hftResult, obiResult, ticker, serverData, core]);

  // Deprecated paper trade stubs (server handles paper trading now)
  const openPaperTrade  = useCallback(() => {}, []);
  const closePaperTrade = useCallback(() => {}, []);
  const resetPaper      = useCallback(() => {}, []);

  return {
    direction:       core.direction,
    conviction:      core.conviction,
    grade:           core.grade,
    signal:          core.signal,
    votes:           core.votes,
    consensus_count: core.consensusCount,
    regime:          core.regime,
    thoughts,
    chat,
    last_update:     new Date().toLocaleTimeString(),
    paper_position:  null,
    paper_stats: {
      total_pnl:    serverData?.stats?.total_pnl    ?? 0,
      wins:         serverData?.stats?.wins          ?? 0,
      losses:       serverData?.stats?.losses        ?? 0,
      win_rate:     serverData?.stats?.win_rate      ?? 0,
      total_trades: serverData?.stats?.total_trades  ?? 0,
      best:         serverData?.stats?.best_trade    ?? 0,
      worst:        serverData?.stats?.worst_trade   ?? 0,
    },
    last_price: core.price,
    price_24h:  ticker?.change_pct ?? null,
    openPaperTrade,
    closePaperTrade,
    resetPaper,
    sendMessage,
  };
}
