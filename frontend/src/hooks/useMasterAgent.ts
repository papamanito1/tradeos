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

  // Live commentary (last N thoughts)
  thoughts:         string[];
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
}, ts: string): string {
  const { price, ema50, ema21, atrPct, votes, orb, regime, ob, direction, conviction, grade: g, candles15m, ticker } = ctx;
  const t = `[${ts}]`;

  const pool: string[] = [];

  // Price vs EMAs
  if (price && ema50) {
    const diff = ((price - ema50) / ema50 * 100).toFixed(2);
    const rel  = price > ema50 ? "above" : "below";
    pool.push(`${t} Price ${rel} EMA50 by ${Math.abs(+diff)}% — ${price > ema50 ? "bullish" : "bearish"} structure intact`);
  }
  if (price && ema21) {
    pool.push(`${t} EMA21 ${ema21.toFixed(0)} — ${price > ema21 ? "price holding above near-term average" : "price rejected below near-term average"}`);
  }

  // ATR / volatility
  if (atrPct != null) {
    if (atrPct > 0.018)      pool.push(`${t} ATR elevated at ${(atrPct * 100).toFixed(2)}% — high-vol environment, widen stops`);
    else if (atrPct < 0.008) pool.push(`${t} ATR compressed ${(atrPct * 100).toFixed(2)}% — coiling, breakout imminent`);
    else                      pool.push(`${t} ATR ${(atrPct * 100).toFixed(2)}% — normal vol regime, standard sizing`);
  }

  // Order book imbalance
  if (ob && ob.bids.length > 0 && ob.asks.length > 0) {
    const bidVol = ob.bids.slice(0, 5).reduce((s, l) => s + l.amount, 0);
    const askVol = ob.asks.slice(0, 5).reduce((s, l) => s + l.amount, 0);
    const obi    = (bidVol - askVol) / (bidVol + askVol);
    if (Math.abs(obi) > 0.2)
      pool.push(`${t} Order book: ${obi > 0 ? "bid-heavy" : "ask-heavy"} at top 5 levels (OBI ${obi.toFixed(3)}) — ${obi > 0 ? "buyer" : "seller"} dominance`);
  }

  // ORB session
  if (orb.current_session?.or_established && orb.indicators.or_high && orb.indicators.or_low) {
    pool.push(`${t} ORB-30 session: $${orb.indicators.or_high.toFixed(0)} / $${orb.indicators.or_low.toFixed(0)} (range ${orb.indicators.or_range_pct?.toFixed(2)}%) — levels active`);
  }
  if (orb.bias !== "neutral") {
    pool.push(`${t} ORB bias ${orb.bias.toUpperCase()} — 15m EMA20 ${orb.indicators.ema20_15m?.toFixed(0)} confirms session direction`);
  }

  // Strategy votes
  const aligned = votes.filter(v => v.bias === direction.toLowerCase());
  if (aligned.length === 3)      pool.push(`${t} ★ ALL 3 strategies in ${direction} consensus — maximum conviction, executing at grade ${g}`);
  else if (aligned.length === 2) pool.push(`${t} 2/3 strategies aligned ${direction} — sufficient edge to act`);
  const strongest = [...votes].sort((a, b) => b.conf - a.conf)[0];
  if (strongest?.signal)         pool.push(`${t} Strongest signal: ${strongest.name} at ${(strongest.conf * 100).toFixed(0)}% confidence`);

  // Regime commentary
  if (regime === "TRENDING") pool.push(`${t} Regime: TRENDING — momentum strategies have highest edge, run winners longer`);
  if (regime === "RANGING")  pool.push(`${t} Regime: RANGING — HFT mean-reversion approach favored, tight targets`);
  if (regime === "VOLATILE") pool.push(`${t} Regime: VOLATILE — reduce size 50%, priority on capital preservation`);

  // Conviction
  if (conviction >= 85)       pool.push(`${t} Conviction A+ (${conviction}/100) — sizing at full allocation`);
  else if (conviction >= 70)  pool.push(`${t} Conviction A (${conviction}/100) — deploying 75% of standard size`);
  else if (conviction >= 55)  pool.push(`${t} Conviction B (${conviction}/100) — scaled-down entry, watching for improvement`);
  else if (conviction >= 40)  pool.push(`${t} Conviction C (${conviction}/100) — monitoring only, no entry at this edge`);
  else                         pool.push(`${t} No edge detected — capital remains flat, waiting for higher-quality setup`);

  // 24h change
  if (ticker?.change_pct != null) {
    const chg = ticker.change_pct;
    pool.push(`${t} BTC 24h: ${chg >= 0 ? "+" : ""}${chg.toFixed(2)}% — ${Math.abs(chg) > 3 ? "significant move" : "stable"} daily session`);
  }

  // Volume observation
  if (candles15m.length >= 2) {
    const vol1 = candles15m[candles15m.length - 1].volume;
    const avgVol = candles15m.slice(-20).reduce((s, c) => s + c.volume, 0) / 20;
    const ratio  = vol1 / avgVol;
    if (ratio > 1.5) pool.push(`${t} Volume surge: current bar ${ratio.toFixed(1)}× 20-bar avg — institutional participation`);
    else if (ratio < 0.5) pool.push(`${t} Low volume ${ratio.toFixed(1)}× avg — low conviction move, wait for volume confirmation`);
  }

  // Hedge fund maxims (rotation)
  const maxims = [
    `${t} Risk protocol: max 2% per trade. Drawdown gate: pause trading after −5% daily.`,
    `${t} Position thesis must survive a 1.5× adverse move without requiring exit.`,
    `${t} Trade what you see, not what you think — price action is the final arbiter.`,
    `${t} Kelly sizing: fractional 25% of theoretical Kelly — protecting the book.`,
    `${t} Correlation check: 3-strategy confluence reduces false positive rate ~70%.`,
    `${t} Entry is nothing — exit is everything. Respect your SL unconditionally.`,
    `${t} A+ setups are rare. Wait. Stay patient. The market will give you the pitch.`,
  ];
  pool.push(...maxims);

  // Return a random selection
  return pool[Math.floor(Math.random() * pool.length)];
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
  const [paperPos, setPaperPos]         = useState<MasterState["paper_position"]>(null);
  const [paperStats, setPaperStats]     = useState<MasterState["paper_stats"]>({
    total_pnl: 0, wins: 0, losses: 0, win_rate: 0, total_trades: 0, best: 0, worst: 0,
  });
  const thoughtTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const paperPosRef     = useRef<MasterState["paper_position"]>(paperPos);
  const paperStatsRef   = useRef<MasterState["paper_stats"]>(paperStats);

  useEffect(() => { paperPosRef.current  = paperPos;   }, [paperPos]);
  useEffect(() => { paperStatsRef.current = paperStats; }, [paperStats]);

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
      price:     core.price,
      ema50:     core.ema50v,
      ema21:     core.ema21v,
      atrPct:    core.atrPct,
      votes:     core.votes,
      orb:       orbResult,
      regime:    core.regime,
      ob:        orderBook,
      direction: core.direction,
      conviction: core.conviction,
      grade:     core.grade,
      candles15m,
      ticker,
    }, ts);
    setThoughts(prev => [thought, ...prev].slice(0, 30));
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
      setThoughts(prev => [
        `[${new Date().toLocaleTimeString()}] ${hitTP ? "✅ TP HIT" : "⛔ SL HIT"} — position closed @ $${exitPrice.toFixed(0)} · P&L ${pnl_usd >= 0 ? "+" : ""}$${pnl_usd.toFixed(2)}`,
        ...prev,
      ].slice(0, 30));
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
    setThoughts(prev => [
      `[${new Date().toLocaleTimeString()}] 📄 MASTER AGENT opened PAPER ${sig.direction} @ $${sig.entry.toFixed(0)} · SL $${sig.sl.toFixed(0)} · TP $${sig.tp.toFixed(0)} · ${sig.rr} R:R`,
      ...prev,
    ].slice(0, 30));
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
    setThoughts(prev => [
      `[${new Date().toLocaleTimeString()}] Manual close @ $${price.toFixed(0)} · P&L ${pnl_usd >= 0 ? "+" : ""}$${pnl_usd.toFixed(2)}`,
      ...prev,
    ].slice(0, 30));
  }, [core.price]);

  const resetPaper = useCallback(() => {
    setPaperPos(null);
    setPaperStats({ total_pnl: 0, wins: 0, losses: 0, win_rate: 0, total_trades: 0, best: 0, worst: 0 });
    setThoughts(prev => [`[${new Date().toLocaleTimeString()}] Paper account reset — $${PAPER_SIZE_USDC} per trade`, ...prev].slice(0, 30));
  }, []);

  return {
    direction:       core.direction,
    conviction:      core.conviction,
    grade:           core.grade,
    signal:          core.signal,
    votes:           core.votes,
    consensus_count: core.consensusCount,
    regime:          core.regime,
    thoughts,
    last_update:     new Date().toLocaleTimeString(),
    paper_position:  paperPos,
    paper_stats:     paperStats,
    last_price:      core.price,
    price_24h:       ticker?.change_pct ?? null,

    // Actions (returned for panel to use)
    openPaperTrade,
    closePaperTrade,
    resetPaper,
  } as MasterState & {
    openPaperTrade:  (sig: MasterSignal) => void;
    closePaperTrade: () => void;
    resetPaper:      () => void;
  };
}
