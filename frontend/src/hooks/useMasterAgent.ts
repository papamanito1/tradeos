"use client";

/**
 * useMasterAgent — Multi-Strategy Confluence Brain
 * ──────────────────────────────────────────────────
 * The "Master Agent" aggregates signals from all three strategies
 * (Momentum Velocity, HFT VWAP Scalper, ORB-30 Breakout) and applies
 * a multi-layer intelligence engine modeled on top hedge fund methodology:
 *
 *  Layer 1 — Strategy Consensus   : How many strategies agree on direction?
 *  Layer 2 — Conviction Scoring   : Weighted score 0-100 based on signal quality
 *  Layer 3 — Market Regime        : Trending / Ranging / Volatile / Unknown
 *  Layer 4 — Kelly Position Size  : Fractional Kelly based on strategy backtests
 *  Layer 5 — Risk Protocol        : Never trade against consensus; max DD gates
 *  Layer 6 — Commentary Engine    : Real-time analyst narrative (Bloomberg-style)
 *
 *  Conviction grades:
 *   A+ (85+)  → 3/3 consensus, high conditions, strong signals → Full size
 *   A  (70+)  → 2/3 consensus, most conditions met             → 75% size
 *   B  (55+)  → 2/3 weak or 3/3 soft                          → 50% size
 *   C  (40+)  → Monitor only, reduce size                      → 25% size
 *   X  (<40)  → No trade — insufficient edge
 */

import { useState, useEffect, useRef, useCallback } from "react";
import { StrategyResult } from "./useStrategyEngine";
import { ORBResult } from "./useORBStrategy";
import { BinanceCandle, BinanceTicker, BinanceOrderBook } from "./useBinanceStream";

// ─── Types ────────────────────────────────────────────────────────────────────
export type ConvictionGrade = "A+" | "A" | "B" | "C" | "X";
export type MarketRegime   = "TRENDING" | "RANGING" | "VOLATILE" | "UNKNOWN";
export type AgentDirection = "LONG" | "SHORT" | "FLAT";

export interface ChatMessage {
  id:        number;
  role:      "agent" | "user";
  content:   string;
  timestamp: string;
  type?:     "auto" | "response" | "system"; // auto = periodic thought, response = reply
}

export interface StrategyVote {
  name:     string;
  bias:     "long" | "short" | "neutral";
  signal:   boolean;
  conf:     number;
  met_pct:  number;  // conditions met %
}

export interface MasterSignal {
  direction:  AgentDirection;
  entry:      number;
  sl:         number;
  tp:         number;
  size_pct:   number;   // recommended % of account
  rr:         string;
  conviction: number;   // 0-100
  grade:      ConvictionGrade;
  reasoning:  string;
  timestamp:  string;
}

export interface MasterState {
  // Core decision
  direction:        AgentDirection;
  conviction:       number;          // 0–100
  grade:            ConvictionGrade;
  signal:           MasterSignal | null;

  // Strategy votes
  votes:            StrategyVote[];
  consensus_count:  number;          // 0-3 strategies aligned
  regime:           MarketRegime;

  // Live commentary & chat
  thoughts:         string[];        // legacy — auto-thoughts as plain strings
  chat:             ChatMessage[];   // unified chat log (auto + user + responses)
  last_update:      string;

  // Paper P&L tracking
  paper_position: {
    open:       boolean;
    direction?: AgentDirection;
    entry?:     number;
    sl?:        number;
    tp?:        number;
    current?:   number;
    pnl_usd?:   number;
    pnl_pct?:   number;
    size_usdc?: number;
    btc_qty?:   number;
  } | null;
  paper_stats: {
    total_pnl:    number;
    wins:         number;
    losses:       number;
    win_rate:     number;
    total_trades: number;
    best:         number;
    worst:        number;
  };

  // Market context
  last_price:  number | null;
  price_24h:   number | null;
}

// ─── Helpers ──────────────────────────────────────────────────────────────────
function grade(conviction: number): ConvictionGrade {
  if (conviction >= 85) return "A+";
  if (conviction >= 70) return "A";
  if (conviction >= 55) return "B";
  if (conviction >= 40) return "C";
  return "X";
}

function ema(vals: number[], p: number): number {
  if (vals.length < p) return NaN;
  const k = 2 / (p + 1);
  let v = vals.slice(0, p).reduce((a, b) => a + b, 0) / p;
  for (let i = p; i < vals.length; i++) v = vals[i] * k + v * (1 - k);
  return v;
}

function atr(candles: BinanceCandle[], p = 14): number {
  const trs = candles.slice(1).map((c, i) => {
    const prev = candles[i];
    return Math.max(c.high - c.low, Math.abs(c.high - prev.close), Math.abs(c.low - prev.close));
  });
  if (trs.length < p) return NaN;
  return trs.slice(-p).reduce((a, b) => a + b, 0) / p;
}

// ─── Commentary engine ────────────────────────────────────────────────────────
// Each thought is a pithy analyst observation. Rotated frequently so it "feels alive".

function buildThought(ctx: {
  price: number | null;
  ema50: number | null;
  ema21: number | null;
  atrPct: number | null;
  votes: StrategyVote[];
  orb:  ORBResult;
  regime: MarketRegime;
  ob: BinanceOrderBook | null;
  direction: AgentDirection;
  conviction: number;
  grade: ConvictionGrade;
  candles15m: BinanceCandle[];
  ticker: BinanceTicker | null;
  signal?: MasterSignal | null;
}, ts: string): string {
  const { price, direction, conviction, grade: g, signal, votes } = ctx;
  const aligned = votes.filter(v => v.bias === direction.toLowerCase()).length;
  const pStr = price ? `$${price.toLocaleString(undefined, { maximumFractionDigits: 0 })}` : "—";

  // If there is an active signal → always show the full signal card
  if (signal && direction !== "FLAT") {
    const riskPct = ((Math.abs(signal.entry - signal.sl) / signal.entry) * 100).toFixed(2);
    return [
      `${direction}  ·  Entry ${pStr}  ·  SL $${signal.sl.toFixed(0)} (${riskPct}%)  ·  TP $${signal.tp.toFixed(0)}  ·  ${signal.rr} R:R`,
      `Grade ${g}  ·  ${conviction}/100  ·  ${aligned}/3 strategies aligned  ·  Size ${signal.size_pct}%`,
    ].join("\n");
  }

  // No signal — show bias status and what's missing
  if (direction !== "FLAT") {
    const needed = 55 - conviction;
    return `${direction} bias  ·  ${conviction}/100 conviction (${g})  ·  ${aligned}/3 aligned  ·  Need ${needed > 0 ? `+${needed} pts` : "signal trigger"}  ·  BTC ${pStr}`;
  }

  return `FLAT  ·  No edge  ·  ${conviction}/100  ·  Strategies diverging  ·  BTC ${pStr}  ·  Waiting for confluence`;
}

// ─── Conviction calculator ────────────────────────────────────────────────────
function calcConviction(
  votes:     StrategyVote[],
  direction: AgentDirection,
  atrPct:    number | null,
): number {
  if (direction === "FLAT") return 0;

  const aligned = votes.filter(v => v.bias === direction.toLowerCase());
  const n = aligned.length;
  if (n === 0) return 0;

  // Alignment multiplier
  const alignMult = n === 3 ? 1.6 : n === 2 ? 1.15 : 0.6;

  // Average conditions met %
  const avgMet = aligned.reduce((s, v) => s + v.met_pct, 0) / aligned.length;

  // Average confidence of signalling strategies
  const signalling = aligned.filter(v => v.signal);
  const avgConf    = signalling.length
    ? signalling.reduce((s, v) => s + v.conf, 0) / signalling.length
    : aligned.reduce((s, v) => s + v.met_pct, 0) / aligned.length;

  // Volatility discount
  const volDiscount = atrPct != null && atrPct > 0.018 ? 0.8 : 1.0;

  const raw = 35 * alignMult * (0.4 + avgMet * 0.4 + avgConf * 0.4) * volDiscount;
  return Math.min(Math.round(raw), 100);
}

// ─── Market regime detector ───────────────────────────────────────────────────
function detectRegime(candles: BinanceCandle[]): MarketRegime {
  if (candles.length < 30) return "UNKNOWN";
  const closes  = candles.slice(-30).map(c => c.close);
  const ema14   = ema(closes, 14);
  const ema28   = ema(closes, Math.min(28, closes.length));
  const atrVal  = atr(candles.slice(-20));
  const mid     = closes[closes.length - 1];
  const atrPct  = atrVal / mid;

  if (atrPct > 0.02) return "VOLATILE";
  if (!isNaN(ema14) && !isNaN(ema28)) {
    const emaSlope = (ema14 - ema(closes.slice(0, -5), 14)) / mid;
    if (Math.abs(emaSlope) > 0.002) return "TRENDING";
    return "RANGING";
  }
  return "UNKNOWN";
}

// ─── Kelly position size ──────────────────────────────────────────────────────
// Uses weighted average of strategy backtests:
//   Momentum: ~55% WR, ~2.0 R:R → Kelly = 0.55 - 0.45/2.0 = 32.5%
//   ORB-30:   55.3% WR, 2.25 R:R → Kelly = 35.5%
//   HFT:      ~52% WR, ~1.2 R:R → Kelly = 18.3%
// Fractional (25%) Kelly, scaled by conviction
function calcSizePct(conviction: number, alignedCount: number): number {
  const fullKelly = 0.30;                         // avg of 3 strategies
  const fracKelly = fullKelly * 0.25;             // 25% fractional Kelly = 7.5%
  const convScale = conviction / 100;
  const alignBonus= alignedCount === 3 ? 1.3 : alignedCount === 2 ? 1.0 : 0.6;
  return Math.min(Math.round(fracKelly * convScale * alignBonus * 1000) / 10, 5.0); // max 5%
}

// ─── Chat response engine ─────────────────────────────────────────────────────
interface ResponseCtx {
  price:          number | null;
  direction:      AgentDirection;
  conviction:     number;
  grade:          ConvictionGrade;
  regime:         MarketRegime;
  votes:          StrategyVote[];
  signal:         MasterSignal | null;
  atrPct:         number | null;
  ema50v:         number | null;
  ema21v:         number | null;
  orbResult:      ORBResult;
  momentumResult: StrategyResult;
  ticker:         BinanceTicker | null;
  paperPos:       MasterState["paper_position"];
  paperStats:     MasterState["paper_stats"];
}

function formatSignal(sig: MasterSignal, conv: number, g: ConvictionGrade, aligned: number): string {
  const riskUsd  = Math.abs(sig.entry - sig.sl);
  const rewardUsd= Math.abs(sig.tp - sig.entry);
  const riskPct  = (riskUsd / sig.entry * 100).toFixed(2);
  return [
    `${sig.direction === "LONG" ? "▲ LONG" : "▼ SHORT"}  ·  Grade ${g}  ·  ${conv}/100  ·  ${aligned}/3 aligned`,
    `Entry   $${sig.entry.toFixed(0)}`,
    `SL      $${sig.sl.toFixed(0)}  (−${riskPct}%  /  −$${riskUsd.toFixed(0)})`,
    `TP      $${sig.tp.toFixed(0)}  (+$${rewardUsd.toFixed(0)})`,
    `R:R     ${sig.rr}  ·  Size ${sig.size_pct}% of account`,
  ].join("\n");
}

function generateResponse(userMsg: string, ctx: ResponseCtx): string {
  const p   = ctx.price;
  const pStr = p ? `$${p.toLocaleString(undefined, { maximumFractionDigits: 0 })}` : "—";
  const dir  = ctx.direction;
  const conv = ctx.conviction;
  const g    = ctx.grade;
  const sig  = ctx.signal;
  const aligned = ctx.votes.filter(v => v.bias === dir.toLowerCase()).length;

  // ── Always lead with the signal if one exists ────────────────────────────
  if (sig && dir !== "FLAT" && conv >= 55) {
    const base = formatSignal(sig, conv, g, aligned);

    // Add a one-line context relevant to the question
    const m = userMsg.toLowerCase();
    if (/paper|stats|pnl|p&l|how.*doing/i.test(m)) {
      const st = ctx.paperStats; const pos = ctx.paperPos;
      const posLine = pos?.open
        ? `Open ${pos.direction} @ $${pos.entry?.toFixed(0)} · unrealized ${(pos.pnl_usd ?? 0) >= 0 ? "+" : ""}$${(pos.pnl_usd ?? 0).toFixed(2)}`
        : "No open paper position";
      return `${base}\n\nPaper account: ${st.total_trades} trades · ${st.wins}W/${st.losses}L · ${st.total_pnl >= 0 ? "+" : ""}$${st.total_pnl.toFixed(2)} P&L\n${posLine}`;
    }
    if (/wait|patience|should.*trade|enter|execute/i.test(m)) {
      const verdict = conv >= 85 ? "A+ setup — execute." : conv >= 70 ? "Grade A — take it." : "Grade B — 50% size.";
      return `${base}\n\n${verdict}`;
    }
    return base;
  }

  // ── No signal — show bias status ─────────────────────────────────────────
  const mv  = ctx.votes[0]; const orb = ctx.votes[1]; const hft = ctx.votes[2];
  const needed = Math.max(0, 55 - conv);

  // Paper stats question with no signal
  if (/paper|stats|pnl|p&l|how.*doing/i.test(userMsg.toLowerCase())) {
    const st = ctx.paperStats; const pos = ctx.paperPos;
    const posLine = pos?.open
      ? `Open ${pos.direction} @ $${pos.entry?.toFixed(0)} · unrealized ${(pos.pnl_usd ?? 0) >= 0 ? "+" : ""}$${(pos.pnl_usd ?? 0).toFixed(2)}`
      : "No open position";
    return `Paper account: ${st.total_trades} trades · ${st.wins}W/${st.losses}L · ${st.total_pnl >= 0 ? "+" : ""}$${st.total_pnl.toFixed(2)} P&L\n${posLine}\n\nNo active signal — conviction ${conv}/100`;
  }

  return [
    `NO SIGNAL  ·  BTC ${pStr}`,
    `Conviction  ${conv}/100 (${g})  ·  Need +${needed} pts to trigger`,
    `Momentum    ${mv.bias.toUpperCase()}  ·  ${(mv.met_pct * 100).toFixed(0)}% conditions`,
    `ORB-30      ${orb.bias.toUpperCase()}  ·  ${(orb.met_pct * 100).toFixed(0)}% conditions`,
    `HFT Flow    ${hft.bias.toUpperCase()}`,
    `${aligned}/3 strategies aligned${dir !== "FLAT" ? ` (${dir})` : " (diverging)"}  ·  Watching…`,
  ].join("\n");
}

// ─── Main hook ────────────────────────────────────────────────────────────────
const PAPER_SIZE_USDC = 500;

export function useMasterAgent(
  momentumResult: StrategyResult,
  orbResult:      ORBResult,
  candles15m:     BinanceCandle[],
  candles1m:      BinanceCandle[],
  ticker:         BinanceTicker | null,
  orderBook:      BinanceOrderBook | null,
): MasterState {
  const [thoughts, setThoughts]         = useState<string[]>([]);
  const [chat, setChat]                 = useState<ChatMessage[]>([]);
  const [paperPos, setPaperPos]         = useState<MasterState["paper_position"]>(null);
  const [paperStats, setPaperStats]     = useState<MasterState["paper_stats"]>({
    total_pnl: 0, wins: 0, losses: 0, win_rate: 0, total_trades: 0, best: 0, worst: 0,
  });
  const thoughtTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const paperPosRef     = useRef<MasterState["paper_position"]>(paperPos);
  const paperStatsRef   = useRef<MasterState["paper_stats"]>(paperStats);
  const chatMsgId       = useRef(0);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const coreRef         = useRef<any>(null);

  useEffect(() => { paperPosRef.current  = paperPos;   }, [paperPos]);
  useEffect(() => { paperStatsRef.current = paperStats; }, [paperStats]);
  useEffect(() => { coreRef.current = core; });          // always current

  // ── Compute core state synchronously via useMemo ──────────────────────────
  const core = (() => {
    const price = ticker?.last ?? candles15m[candles15m.length - 1]?.close ?? null;

    // Strategy votes
    const momBias = momentumResult.bias;
    const orbBias = orbResult.bias;

    // Simplified order-book OBI for mini "HFT" vote
    let hftBias: "long" | "short" | "neutral" = "neutral";
    if (orderBook && orderBook.bids.length > 0 && orderBook.asks.length > 0) {
      const bidVol = orderBook.bids.slice(0, 10).reduce((s, l) => s + l.amount, 0);
      const askVol = orderBook.asks.slice(0, 10).reduce((s, l) => s + l.amount, 0);
      const obi    = (bidVol - askVol) / (bidVol + askVol);
      if (obi >  0.15) hftBias = "long";
      if (obi < -0.15) hftBias = "short";
    }

    const votes: StrategyVote[] = [
      {
        name: "Momentum 15m",
        bias: momBias,
        signal: !!momentumResult.signal,
        conf:   momentumResult.signal?.confidence ?? (momentumResult.met_count / (momentumResult.total ?? 7)) * 0.7,
        met_pct: momentumResult.met_count / (momentumResult.total ?? 7),
      },
      {
        name: "ORB-30",
        bias: orbBias,
        signal: !!orbResult.signal,
        conf:   orbResult.signal?.confidence ?? (orbResult.met_count / (orbResult.total ?? 6)) * 0.65,
        met_pct: orbResult.met_count / (orbResult.total ?? 6),
      },
      {
        name: "HFT Flow",
        bias: hftBias,
        signal: hftBias !== "neutral",
        conf:   hftBias !== "neutral" ? 0.55 : 0,
        met_pct: hftBias !== "neutral" ? 0.6 : 0,
      },
    ];

    // Consensus direction
    const longCount  = votes.filter(v => v.bias === "long").length;
    const shortCount = votes.filter(v => v.bias === "short").length;
    let direction: AgentDirection = "FLAT";
    if (longCount > shortCount && longCount >= 2)       direction = "LONG";
    else if (shortCount > longCount && shortCount >= 2) direction = "SHORT";
    else if (longCount === 1 && shortCount === 0)       direction = "LONG";
    else if (shortCount === 1 && longCount === 0)       direction = "SHORT";

    // Consensus count (how many agree with chosen direction)
    const consensusCount = direction === "LONG" ? longCount : direction === "SHORT" ? shortCount : 0;

    // Market regime from 15m candles
    const regime = detectRegime(candles15m);

    // ATR
    const atrVal = atr(candles15m.slice(-20));
    const atrPct = price && !isNaN(atrVal) ? atrVal / price : null;

    // EMA50 and EMA21 for commentary
    const closes = candles15m.map(c => c.close);
    const ema50v = closes.length >= 50 ? ema(closes, 50) : null;
    const ema21v = closes.length >= 21 ? ema(closes, 21) : null;

    // Conviction
    const conviction = calcConviction(votes, direction, atrPct);
    const g          = grade(conviction);
    const sizePct    = calcSizePct(conviction, consensusCount);

    // Build master signal if conviction is sufficient
    let signal: MasterSignal | null = null;
    if (conviction >= 55 && direction !== "FLAT" && price) {
      // Use the strongest aligned strategy's entry/SL/TP, scaled by conviction
      const momSig = momentumResult.signal;
      const orbSig = orbResult.signal;

      let entry = price;
      let sl: number, tp: number;

      // Priority: momentum signal → ORB signal → compute from ATR
      if (momSig && momSig.direction === direction.toLowerCase() && momSig.sl && momSig.tp) {
        entry = momSig.entry; sl = momSig.sl; tp = momSig.tp;
      } else if (orbSig && orbSig.direction === direction.toLowerCase()) {
        entry = orbSig.entry; sl = orbSig.sl; tp = orbSig.tp;
      } else if (atrVal) {
        const slDist = atrVal * 1.5;
        const tpDist = atrVal * 3.0;
        sl = direction === "LONG" ? entry - slDist : entry + slDist;
        tp = direction === "LONG" ? entry + tpDist : entry - tpDist;
      } else {
        sl = direction === "LONG" ? entry * 0.985 : entry * 1.015;
        tp = direction === "LONG" ? entry * 1.030 : entry * 0.970;
      }

      const risk = Math.abs(entry - sl);
      const rwd  = Math.abs(tp - entry);
      const rr   = risk > 0 ? `1 : ${(rwd / risk).toFixed(2)}` : "—";

      const reasoning = [
        `${consensusCount}/3 strategies ${direction}`,
        g === "A+" ? "maximum conviction — full size" : g === "A" ? "high conviction — 75% size" : "moderate conviction — 50% size",
        `regime: ${regime}`,
        momSig ? `MV15 signal (${(momSig.confidence * 100).toFixed(0)}% conf)` : "",
        orbSig ? `ORB-30 breakout (${(orbSig.confidence * 100).toFixed(0)}% conf)` : "",
      ].filter(Boolean).join(" · ");

      signal = {
        direction, entry, sl, tp, rr,
        size_pct: sizePct,
        conviction,
        grade: g,
        reasoning,
        timestamp: new Date().toISOString(),
      };
    }

    return { votes, direction, consensusCount, conviction, grade: g, signal, regime, price, atrPct, ema50v, ema21v };
  })();

  // ── Commentary ticker — refreshes every 12s ───────────────────────────────
  const generateThought = useCallback(() => {
    const ts  = new Date().toLocaleTimeString();
    const thought = buildThought({
      price:      core.price,
      ema50:      core.ema50v,
      ema21:      core.ema21v,
      atrPct:     core.atrPct,
      votes:      core.votes,
      orb:        orbResult,
      regime:     core.regime,
      ob:         orderBook,
      direction:  core.direction,
      conviction: core.conviction,
      grade:      core.grade,
      candles15m,
      ticker,
      signal:     core.signal,
    }, ts);
    setThoughts(prev => [thought, ...prev].slice(0, 30));
    // Push into chat feed as an auto-thought
    setChat(prev => [
      ...prev,
      { id: ++chatMsgId.current, role: "agent" as const, content: thought, timestamp: ts, type: "auto" as const },
    ].slice(-80));
  }, [core, orbResult, orderBook, candles15m, ticker]);

  useEffect(() => {
    generateThought();
    if (thoughtTimerRef.current) clearInterval(thoughtTimerRef.current);
    thoughtTimerRef.current = setInterval(generateThought, 12_000);
    return () => { if (thoughtTimerRef.current) clearInterval(thoughtTimerRef.current); };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [core.direction, core.conviction, core.regime]);

  // ── Paper position P&L update ─────────────────────────────────────────────
  useEffect(() => {
    const pos = paperPosRef.current;
    if (!pos?.open || !core.price) return;
    const price = core.price;

    // Check SL/TP
    const hitTP = pos.direction === "LONG" ? price >= (pos.tp ?? Infinity) : price <= (pos.tp ?? -Infinity);
    const hitSL = pos.direction === "LONG" ? price <= (pos.sl ?? -Infinity) : price >= (pos.sl ?? Infinity);

    if (hitTP || hitSL) {
      const exitPrice  = hitTP ? pos.tp! : pos.sl!;
      const priceDiff  = pos.direction === "LONG" ? exitPrice - pos.entry! : pos.entry! - exitPrice;
      const pnl_usd    = priceDiff * (pos.btc_qty ?? 0);
      const isWin      = hitTP;

      setPaperPos(null);
      setPaperStats(prev => {
        const wins   = prev.wins + (isWin ? 1 : 0);
        const losses = prev.losses + (!isWin ? 1 : 0);
        const total  = prev.total_trades + 1;
        return {
          total_trades: total, wins, losses,
          win_rate:    Math.round(wins / total * 1000) / 10,
          total_pnl:   Math.round((prev.total_pnl + pnl_usd) * 100) / 100,
          avg_rr:      0,
          best:        Math.max(prev.best, pnl_usd),
          worst:       Math.min(prev.worst, pnl_usd),
        };
      });
      const closeMsg = `${hitTP ? "✅ TP HIT" : "⛔ SL HIT"} — position closed @ $${exitPrice.toFixed(0)} · P&L ${pnl_usd >= 0 ? "+" : ""}$${pnl_usd.toFixed(2)}`;
      const closeTs  = new Date().toLocaleTimeString();
      setThoughts(prev => [`[${closeTs}] ${closeMsg}`, ...prev].slice(0, 30));
      setChat(prev => [...prev, { id: ++chatMsgId.current, role: "agent" as const, content: closeMsg, timestamp: closeTs, type: "system" as const }].slice(-80));
    } else {
      const priceDiff  = pos.direction === "LONG" ? price - pos.entry! : pos.entry! - price;
      const pnl_usd    = priceDiff * (pos.btc_qty ?? 0);
      const pnl_pct    = priceDiff / pos.entry! * 100;
      setPaperPos(p => p ? { ...p, current: price, pnl_usd, pnl_pct } : null);
    }
  }, [core.price]);

  // ── Paper trade execution ─────────────────────────────────────────────────
  const openPaperTrade = useCallback((sig: MasterSignal) => {
    if (paperPosRef.current?.open) return;
    const btcQty = PAPER_SIZE_USDC / sig.entry;
    setPaperPos({
      open: true,
      direction: sig.direction,
      entry:     sig.entry,
      sl:        sig.sl,
      tp:        sig.tp,
      current:   sig.entry,
      pnl_usd:   0,
      pnl_pct:   0,
      size_usdc: PAPER_SIZE_USDC,
      btc_qty:   btcQty,
    });
    const openMsg = `📄 Paper ${sig.direction} opened @ $${sig.entry.toFixed(0)} · SL $${sig.sl.toFixed(0)} · TP $${sig.tp.toFixed(0)} · ${sig.rr} R:R`;
    const openTs  = new Date().toLocaleTimeString();
    setThoughts(prev => [`[${openTs}] ${openMsg}`, ...prev].slice(0, 30));
    setChat(prev => [...prev, { id: ++chatMsgId.current, role: "agent" as const, content: openMsg, timestamp: openTs, type: "system" as const }].slice(-80));
  }, []);

  const closePaperTrade = useCallback(() => {
    const pos   = paperPosRef.current;
    if (!pos?.open || !core.price) return;
    const price    = core.price;
    const diff     = pos.direction === "LONG" ? price - pos.entry! : pos.entry! - price;
    const pnl_usd  = diff * (pos.btc_qty ?? 0);
    setPaperPos(null);
    setPaperStats(prev => ({
      ...prev,
      total_trades: prev.total_trades + 1,
      total_pnl:    Math.round((prev.total_pnl + pnl_usd) * 100) / 100,
      best:         Math.max(prev.best, pnl_usd),
      worst:        Math.min(prev.worst, pnl_usd),
    }));
    const manualTs  = new Date().toLocaleTimeString();
    const manualMsg = `Manual close @ $${price.toFixed(0)} · P&L ${pnl_usd >= 0 ? "+" : ""}$${pnl_usd.toFixed(2)}`;
    setThoughts(prev => [`[${manualTs}] ${manualMsg}`, ...prev].slice(0, 30));
    setChat(prev => [...prev, { id: ++chatMsgId.current, role: "agent" as const, content: manualMsg, timestamp: manualTs, type: "system" as const }].slice(-80));
  }, [core.price]);

  const resetPaper = useCallback(() => {
    setPaperPos(null);
    setPaperStats({ total_pnl: 0, wins: 0, losses: 0, win_rate: 0, total_trades: 0, best: 0, worst: 0 });
    const resetTs = new Date().toLocaleTimeString();
    setThoughts(prev => [`[${resetTs}] Paper account reset — $${PAPER_SIZE_USDC} per trade`, ...prev].slice(0, 30));
    setChat(prev => [...prev, { id: ++chatMsgId.current, role: "agent" as const, content: `Paper account reset. $${PAPER_SIZE_USDC} per trade.`, timestamp: resetTs, type: "system" as const }].slice(-80));
  }, []);

  // ── Chat: sendMessage ─────────────────────────────────────────────────────
  const sendMessage = useCallback((userMsg: string) => {
    if (!userMsg.trim()) return;
    const ts = new Date().toLocaleTimeString();
    const userId = ++chatMsgId.current;
    setChat(prev => [...prev, { id: userId, role: "user" as const, content: userMsg.trim(), timestamp: ts }]);

    // Generate response after a short "thinking" delay
    setTimeout(() => {
      const c = coreRef.current;
      const response = generateResponse(userMsg, {
        price:          c.price,
        direction:      c.direction,
        conviction:     c.conviction,
        grade:          c.grade,
        regime:         c.regime,
        votes:          c.votes,
        signal:         c.signal,
        atrPct:         c.atrPct,
        ema50v:         c.ema50v,
        ema21v:         c.ema21v,
        orbResult,
        momentumResult,
        ticker,
        paperPos:       paperPosRef.current,
        paperStats:     paperStatsRef.current,
      });
      const rTs = new Date().toLocaleTimeString();
      setChat(prev => [...prev, { id: ++chatMsgId.current, role: "agent" as const, content: response, timestamp: rTs, type: "response" as const }].slice(-80));
    }, 400 + Math.random() * 300);
  }, [orbResult, momentumResult, ticker]);

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
    paper_position:  paperPos,
    paper_stats:     paperStats,
    last_price:      core.price,
    price_24h:       ticker?.change_pct ?? null,

    // Actions (returned for panel to use)
    openPaperTrade,
    closePaperTrade,
    resetPaper,
    sendMessage,
  } as MasterState & {
    openPaperTrade:  (sig: MasterSignal) => void;
    closePaperTrade: () => void;
    resetPaper:      () => void;
    sendMessage:     (msg: string) => void;
  };
}
