"use client";

/**
 * usePhantomAgent — multi-strategy edition
 * ─────────────────────────────────────────
 * Each strategy gets its own independent position slot. Multiple positions
 * can be open simultaneously (e.g. Momentum LONG + ORB SHORT at the same time).
 * Shared: config, combined trade log, aggregate paper stats, agent log.
 */

import { useState, useEffect, useRef, useCallback } from "react";
import { useWallet, useConnection } from "@solana/wallet-adapter-react";
import { Transaction, VersionedTransaction } from "@solana/web3.js";
import { StrategyResult } from "./useStrategyEngine";

// ─── Token mints ─────────────────────────────────────────────────────────────
const USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v";
const WBTC_MINT = "3NZ9JMVBmGAqocybic2c7LQCJScmgsAZ6vQqTDzcqmJh";

// ─── Types ────────────────────────────────────────────────────────────────────
export type AgentState = "idle" | "scanning" | "signal_detected" | "executing" | "position_open" | "error";

/** One strategy slot passed to the hook */
export interface StrategySlot {
  result: StrategyResult;
  name:   string;   // display name e.g. "Momentum 15m"
  key:    string;   // storage key   e.g. "momentum"
}

export interface AgentTrade {
  id: string;
  timestamp: string;
  direction: "long" | "short";
  entry: number;
  sl: number | null;
  tp: number | null;
  size_usdc: number;
  confidence: number;
  status: "pending" | "submitted" | "confirmed" | "failed";
  tx_signature?: string;
  error?: string;
  reasoning: string;
  is_paper?: boolean;
  exit_price?: number;
  exit_reason?: "tp" | "sl" | "manual";
  pnl_usd?: number;
  pnl_pct?: number;
  strategy_key?: string;   // which strategy fired this trade
  strategy_name?: string;
}

export interface PaperPosition {
  id: string;
  direction: "long" | "short";
  entry: number;
  sl: number | null;
  tp: number | null;
  size_usdc: number;
  confidence: number;
  reasoning: string;
  timestamp: string;
  current_price: number;
  unrealized_pnl: number;
  unrealized_pct: number;
  btc_size: number;
  strategy_key:  string;
  strategy_name: string;
}

export interface PaperStats {
  total_trades: number;
  wins: number;
  losses: number;
  win_rate: number;
  total_pnl: number;
  avg_rr: number;
  best_trade: number;
  worst_trade: number;
}

export interface AgentConfig {
  enabled: boolean;
  size_usdc: number;
  min_confidence: number;
  min_conditions: number;
  mode: "paper" | "spot" | "perps";
  auto_execute: boolean;
}

// Bump whenever defaults change — forces a reset for existing users
const CONFIG_VERSION = "v3";

const DEFAULT_CONFIG: AgentConfig = {
  enabled:        false,
  size_usdc:      100,
  min_confidence: 0.50,
  min_conditions: 3,
  mode:           "paper",
  auto_execute:   true,
};

const EMPTY_STATS: PaperStats = {
  total_trades: 0, wins: 0, losses: 0, win_rate: 0,
  total_pnl: 0, avg_rr: 0, best_trade: 0, worst_trade: 0,
};

async function jupiterQuote(inputMint: string, outputMint: string, amountLamports: number) {
  const url = `https://quote-api.jup.ag/v6/quote?inputMint=${inputMint}&outputMint=${outputMint}&amount=${amountLamports}&slippageBps=50`;
  const r = await fetch(url);
  if (!r.ok) throw new Error(`Jupiter quote failed: ${r.status}`);
  return r.json();
}

async function jupiterSwapTx(quoteResponse: unknown, userPublicKey: string): Promise<string> {
  const r = await fetch("https://quote-api.jup.ag/v6/swap", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ quoteResponse, userPublicKey, wrapAndUnwrapSol: true }),
  });
  if (!r.ok) throw new Error(`Jupiter swap failed: ${r.status}`);
  const data = await r.json();
  return data.swapTransaction;
}

// ─── localStorage helpers ─────────────────────────────────────────────────────
const LS_CONFIG    = "tradeos_agent_config";
const LS_POSITIONS = "tradeos_paper_positions_v2";  // Record<key, PaperPosition|null>
const LS_PAPER_ST  = "tradeos_paper_stats";
const LS_TRADES    = "tradeos_agent_trades";

function lsGet<T>(key: string, fallback: T): T {
  try { const v = localStorage.getItem(key); return v ? (JSON.parse(v) as T) : fallback; }
  catch { return fallback; }
}
function lsSet(key: string, value: unknown) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* ignore */ }
}

// ─── Hook ─────────────────────────────────────────────────────────────────────
export function usePhantomAgent(strategies: StrategySlot[], livePrice?: number) {
  const { publicKey, signTransaction, connected } = useWallet();
  const { connection } = useConnection();

  // ── Config (shared across all strategies) ────────────────────────────────
  const [config, setConfig] = useState<AgentConfig>(() => {
    try {
      const raw = localStorage.getItem(LS_CONFIG);
      if (!raw) return DEFAULT_CONFIG;
      const parsed = JSON.parse(raw) as AgentConfig & { _v?: string };
      if (parsed._v !== CONFIG_VERSION) return { ...DEFAULT_CONFIG, enabled: parsed.enabled ?? false, mode: parsed.mode ?? "paper" };
      return parsed;
    } catch { return DEFAULT_CONFIG; }
  });

  // ── Per-strategy open positions: Record<strategyKey, PaperPosition|null> ─
  const [paperPositions, setPaperPositions] = useState<Record<string, PaperPosition | null>>(
    () => lsGet<Record<string, PaperPosition | null>>(LS_POSITIONS, {})
  );

  // ── Shared state ─────────────────────────────────────────────────────────
  const [agentState,  setAgentState]  = useState<AgentState>("idle");
  const [trades,      setTrades]      = useState<AgentTrade[]>(() => lsGet<AgentTrade[]>(LS_TRADES, []));
  const [paperStats,  setPaperStats]  = useState<PaperStats>(() => lsGet<PaperStats>(LS_PAPER_ST, EMPTY_STATS));
  const [scanCount,   setScanCount]   = useState(0);
  const [lastScan,    setLastScan]    = useState<string | null>(null);
  const [agentLog,    setAgentLog]    = useState<string[]>([]);

  // ── Persist to localStorage ───────────────────────────────────────────────
  useEffect(() => { lsSet(LS_CONFIG,    { ...config, _v: CONFIG_VERSION }); }, [config]);
  useEffect(() => { lsSet(LS_POSITIONS, paperPositions); }, [paperPositions]);
  useEffect(() => { lsSet(LS_PAPER_ST,  paperStats);     }, [paperStats]);
  useEffect(() => { lsSet(LS_TRADES,    trades);         }, [trades]);

  // ── Refs for use inside callbacks ─────────────────────────────────────────
  const inFlightRef      = useRef(false);
  const intervalRef      = useRef<ReturnType<typeof setInterval> | null>(null);
  const strategiesRef    = useRef<StrategySlot[]>(strategies);
  const configRef        = useRef<AgentConfig>(config);
  const tradesRef        = useRef<AgentTrade[]>(trades);
  const paperPosRef      = useRef<Record<string, PaperPosition | null>>(paperPositions);

  useEffect(() => { strategiesRef.current  = strategies;     }, [strategies]);
  useEffect(() => { configRef.current      = config;         }, [config]);
  useEffect(() => { tradesRef.current      = trades;         }, [trades]);
  useEffect(() => { paperPosRef.current    = paperPositions; }, [paperPositions]);

  // ── Restore state on mount if positions were open ────────────────────────
  useEffect(() => {
    const anyOpen = Object.values(paperPosRef.current).some(p => p !== null);
    if (anyOpen) setAgentState("position_open");
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const log = useCallback((msg: string) => {
    setAgentLog(prev => [`[${new Date().toLocaleTimeString()}] ${msg}`, ...prev].slice(0, 200));
  }, []);

  // ── Update all open positions' P&L as price changes ──────────────────────
  useEffect(() => {
    if (!livePrice) return;
    const positions = paperPosRef.current;
    const anyOpen   = Object.values(positions).some(p => p !== null);
    if (!anyOpen) return;

    setPaperPositions(prev => {
      let changed = false;
      const next = { ...prev };

      for (const key of Object.keys(next)) {
        const pos = next[key];
        if (!pos) continue;

        const hitTP = pos.direction === "long"  ? livePrice >= (pos.tp ?? Infinity)
                    : livePrice <= (pos.tp ?? -Infinity);
        const hitSL = pos.direction === "long"  ? livePrice <= (pos.sl ?? -Infinity)
                    : livePrice >= (pos.sl ?? Infinity);

        if (hitTP || hitSL) {
          const exitReason = hitTP ? "tp" : "sl";
          const exitPrice  = hitTP ? (pos.tp ?? livePrice) : (pos.sl ?? livePrice);
          const priceDiff  = pos.direction === "long" ? exitPrice - pos.entry : pos.entry - exitPrice;
          const pnl_usd    = priceDiff * pos.btc_size;
          const pnl_pct    = priceDiff / pos.entry * 100;

          log(`[${pos.strategy_name}] ${exitReason.toUpperCase()} hit @ $${exitPrice.toFixed(0)} · P&L ${pnl_usd >= 0 ? "+" : ""}$${pnl_usd.toFixed(2)}`);

          const closedTrade: AgentTrade = {
            id: pos.id, timestamp: pos.timestamp,
            direction: pos.direction, entry: pos.entry,
            sl: pos.sl, tp: pos.tp, size_usdc: pos.size_usdc,
            confidence: pos.confidence, reasoning: pos.reasoning,
            status: "confirmed", is_paper: true,
            exit_price: exitPrice, exit_reason: exitReason,
            pnl_usd: Math.round(pnl_usd * 100) / 100,
            pnl_pct: Math.round(pnl_pct * 100) / 100,
            strategy_key: pos.strategy_key, strategy_name: pos.strategy_name,
          };
          setTrades(t => [closedTrade, ...t].slice(0, 100));
          setPaperStats(s => {
            const wins   = s.wins   + (exitReason === "tp" ? 1 : 0);
            const losses = s.losses + (exitReason === "sl" ? 1 : 0);
            const total  = s.total_trades + 1;
            return {
              total_trades: total, wins, losses,
              win_rate:    Math.round(wins / total * 1000) / 10,
              total_pnl:   Math.round((s.total_pnl + pnl_usd) * 100) / 100,
              avg_rr:      s.avg_rr,
              best_trade:  Math.max(s.best_trade, pnl_usd),
              worst_trade: Math.min(s.worst_trade, pnl_usd),
            };
          });

          next[key] = null;
          changed = true;
        } else {
          // Update unrealized P&L
          const priceDiff     = pos.direction === "long" ? livePrice - pos.entry : pos.entry - livePrice;
          const unrealized    = priceDiff * pos.btc_size;
          const unrealizedPct = priceDiff / pos.entry * 100;
          const updated = { ...pos, current_price: livePrice, unrealized_pnl: unrealized, unrealized_pct: unrealizedPct };
          if (updated.unrealized_pnl !== pos.unrealized_pnl) { next[key] = updated; changed = true; }
        }
      }

      if (changed) {
        paperPosRef.current = next;
        const stillOpen = Object.values(next).some(p => p !== null);
        if (!stillOpen) setAgentState("idle");
        return next;
      }
      return prev;
    });
  }, [livePrice, log]);

  // ── Paper execution (instant fill, no wallet needed) ─────────────────────
  const executePaperTrade = useCallback((trade: AgentTrade, slot: StrategySlot) => {
    const existingPos = paperPosRef.current[slot.key];
    if (existingPos) {
      log(`[${slot.name}] Position already open — skipping`);
      return;
    }
    const btcSize = trade.size_usdc / trade.entry;
    const pos: PaperPosition = {
      id:             trade.id,
      direction:      trade.direction,
      entry:          trade.entry,
      sl:             trade.sl,
      tp:             trade.tp,
      size_usdc:      trade.size_usdc,
      confidence:     trade.confidence,
      reasoning:      trade.reasoning,
      timestamp:      trade.timestamp,
      current_price:  trade.entry,
      unrealized_pnl: 0,
      unrealized_pct: 0,
      btc_size:       btcSize,
      strategy_key:   slot.key,
      strategy_name:  slot.name,
    };
    setPaperPositions(prev => {
      const next = { ...prev, [slot.key]: pos };
      paperPosRef.current = next;
      return next;
    });
    setAgentState("position_open");
    log(`📄 [${slot.name}] PAPER ${trade.direction.toUpperCase()} @ $${trade.entry.toFixed(0)} · $${trade.size_usdc} · ${btcSize.toFixed(6)} BTC`);
    log(`   SL $${trade.sl?.toFixed(0) ?? "—"} · TP $${trade.tp?.toFixed(0) ?? "—"}`);
  }, [log]);

  // ── Close a specific strategy's paper position manually ──────────────────
  const closePaperPosition = useCallback((strategyKey: string) => {
    const pos = paperPosRef.current[strategyKey];
    if (!pos) return;
    const price    = pos.current_price;
    const diff     = pos.direction === "long" ? price - pos.entry : pos.entry - price;
    const pnl_usd  = diff * pos.btc_size;
    const pnl_pct  = diff / pos.entry * 100;

    log(`[${pos.strategy_name}] Closed manually @ $${price.toFixed(0)} · P&L ${pnl_usd >= 0 ? "+" : ""}$${pnl_usd.toFixed(2)}`);
    const closedTrade: AgentTrade = {
      id: pos.id, timestamp: pos.timestamp,
      direction: pos.direction, entry: pos.entry,
      sl: pos.sl, tp: pos.tp, size_usdc: pos.size_usdc,
      confidence: pos.confidence, reasoning: pos.reasoning,
      status: "confirmed", is_paper: true,
      exit_price: price, exit_reason: "manual",
      pnl_usd: Math.round(pnl_usd * 100) / 100,
      pnl_pct: Math.round(pnl_pct * 100) / 100,
      strategy_key: pos.strategy_key, strategy_name: pos.strategy_name,
    };
    setTrades(t => [closedTrade, ...t].slice(0, 100));
    setPaperPositions(prev => {
      const next = { ...prev, [strategyKey]: null };
      paperPosRef.current = next;
      const anyOpen = Object.values(next).some(p => p !== null);
      if (!anyOpen) setAgentState("idle");
      return next;
    });
    setPaperStats(s => ({
      ...s,
      total_trades: s.total_trades + 1,
      total_pnl:   Math.round((s.total_pnl + pnl_usd) * 100) / 100,
      best_trade:  Math.max(s.best_trade, pnl_usd),
      worst_trade: Math.min(s.worst_trade, pnl_usd),
    }));
  }, [log]);

  // ── Execute live trade (Phantom wallet) ───────────────────────────────────
  const executeTrade = useCallback(async (trade: AgentTrade, slot: StrategySlot) => {
    const mode = configRef.current.mode;

    if (mode === "paper") {
      executePaperTrade(trade, slot);
      return;
    }

    if (!publicKey || !signTransaction) {
      log("Wallet not connected — cannot execute");
      return;
    }

    setAgentState("executing");
    log(`[${slot.name}] Executing ${trade.direction.toUpperCase()} · $${trade.size_usdc} via ${mode}`);
    setTrades(prev => [{ ...trade, status: "submitted" as const }, ...prev].slice(0, 100));

    if (mode === "perps") {
      window.open("https://trade.phantom.com/perps/BTC", "_blank", "noopener,noreferrer");
      log(`Opened Phantom Perps (${trade.direction.toUpperCase()} BTC · $${trade.size_usdc})`);
      setTrades(prev => prev.map(t => t.id === trade.id ? { ...t, status: "confirmed" as const } : t));
      setAgentState("position_open");
      return;
    }

    try {
      const amountMicro = Math.floor(trade.size_usdc * 1_000_000);
      const [inMint, outMint] = trade.direction === "long"
        ? [USDC_MINT, WBTC_MINT] : [WBTC_MINT, USDC_MINT];

      log("Fetching Jupiter quote…");
      const quote = await jupiterQuote(inMint, outMint, amountMicro);
      log(`Quote received · impact ${quote.priceImpactPct}%`);

      log("Building swap transaction…");
      const swapTxB64 = await jupiterSwapTx(quote, publicKey.toBase58());
      const swapTxBuf = Buffer.from(swapTxB64, "base64");

      log("Sending to Phantom for signing…");
      let txSig: string;
      try {
        const vtx    = VersionedTransaction.deserialize(swapTxBuf);
        const signed = await (signTransaction as (tx: VersionedTransaction) => Promise<VersionedTransaction>)(vtx);
        txSig = await connection.sendRawTransaction(signed.serialize(), { skipPreflight: false, preflightCommitment: "confirmed" });
      } catch {
        const tx     = Transaction.from(swapTxBuf);
        const signed = await (signTransaction as (tx: Transaction) => Promise<Transaction>)(tx);
        txSig = await connection.sendRawTransaction(signed.serialize(), { skipPreflight: false, preflightCommitment: "confirmed" });
      }

      log(`Submitted! Tx: ${txSig.slice(0, 8)}…`);
      setTrades(prev => prev.map(t => t.id === trade.id
        ? { ...t, status: "confirmed" as const, tx_signature: txSig } : t));
      setAgentState("position_open");
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      log(`[${slot.name}] Execution failed: ${msg}`);
      setTrades(prev => prev.map(t => t.id === trade.id
        ? { ...t, status: "failed" as const, error: msg } : t));
      setAgentState("error");
    }
  }, [publicKey, signTransaction, connection, log, executePaperTrade]);

  // ── Core scan — checks ALL strategies independently ───────────────────────
  const scan = useCallback(() => {
    if (inFlightRef.current) return;
    inFlightRef.current = true;
    setAgentState(prev => prev === "position_open" ? prev : "scanning");
    setScanCount(n => n + 1);
    setLastScan(new Date().toLocaleTimeString());

    try {
      const cfg       = configRef.current;
      const slots     = strategiesRef.current;
      const positions = paperPosRef.current;
      let anySignal   = false;

      for (const slot of slots) {
        const result = slot.result;
        const sig    = result.signal;
        const openPos = positions[slot.key] !== null && positions[slot.key] !== undefined;

        const conditionsOk = result.met_count >= cfg.min_conditions;
        const confidenceOk = sig ? sig.confidence >= cfg.min_confidence : false;

        log(`[${slot.name}] ${result.met_count}/${result.total ?? 7} conds · ${sig?.direction?.toUpperCase() ?? "no sig"} · conf ${((sig?.confidence ?? 0) * 100).toFixed(0)}% · ${openPos ? "POS OPEN" : result.bias}`);

        if (conditionsOk && confidenceOk && sig && !openPos) {
          anySignal = true;
          log(`★ [${slot.name}] Signal! ${sig.direction.toUpperCase()} @ $${sig.entry.toFixed(0)} · conf ${(sig.confidence * 100).toFixed(0)}%`);

          const trade: AgentTrade = {
            id:            `${slot.key}-${Date.now()}`,
            timestamp:     new Date().toISOString(),
            direction:     sig.direction,
            entry:         sig.entry,
            sl:            sig.sl,
            tp:            sig.tp,
            size_usdc:     cfg.size_usdc,
            confidence:    sig.confidence,
            status:        "pending",
            reasoning:     sig.reasoning,
            is_paper:      cfg.mode === "paper",
            strategy_key:  slot.key,
            strategy_name: slot.name,
          };

          const isPaper = cfg.mode === "paper";
          const canLive = !isPaper && connected && publicKey && signTransaction;

          if (cfg.auto_execute && (isPaper || canLive)) {
            executeTrade(trade, slot);
          }
        } else if (openPos) {
          // position is live — do nothing, P&L tracked via price effect
        }
      }

      if (!anySignal) {
        const anyOpen = Object.values(positions).some(p => p !== null);
        if (!anyOpen) setAgentState("idle");
      } else {
        setAgentState("signal_detected");
      }
    } finally {
      inFlightRef.current = false;
    }
  }, [log, connected, publicKey, signTransaction, executeTrade]);

  // ── Agent loop ────────────────────────────────────────────────────────────
  useEffect(() => {
    if (intervalRef.current) clearInterval(intervalRef.current);
    if (config.enabled) {
      const modeLabel = config.mode === "paper" ? "PAPER" : config.mode.toUpperCase();
      log(`Agent STARTED [${modeLabel}] — all 3 strategies scanning every 20s`);
      scan();
      intervalRef.current = setInterval(scan, 20_000);
    } else {
      log("Agent STOPPED");
      setAgentState("idle");
    }
    return () => { if (intervalRef.current) clearInterval(intervalRef.current); };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [config.enabled]);

  const updateConfig = useCallback((patch: Partial<AgentConfig>) =>
    setConfig(prev => ({ ...prev, ...patch })), []);

  const clearTrades = useCallback(() => {
    setTrades([]);
    setPaperStats(EMPTY_STATS);
  }, []);

  const resetPaperAccount = useCallback(() => {
    setPaperPositions({});
    setTrades([]);
    setPaperStats(EMPTY_STATS);
    lsSet(LS_POSITIONS, {});
    lsSet(LS_PAPER_ST,  EMPTY_STATS);
    lsSet(LS_TRADES,    []);
    paperPosRef.current = {};
    setAgentState("idle");
    log("📄 Paper account reset — all positions cleared");
  }, [log]);

  // Derived: list of all currently open positions for UI
  const openPositions = Object.values(paperPositions).filter((p): p is PaperPosition => p !== null);
  const anyPositionOpen = openPositions.length > 0;

  // Best current result (highest-confidence signal, or most conditions) for UI display
  const bestResult = strategies.reduce<StrategyResult | null>((best, slot) => {
    if (!best) return slot.result;
    const bConf = best.signal?.confidence ?? 0;
    const sConf = slot.result.signal?.confidence ?? 0;
    if (sConf > bConf) return slot.result;
    if (sConf === 0 && slot.result.met_count > best.met_count) return slot.result;
    return best;
  }, null) ?? strategies[0]?.result ?? { bias: "neutral", conditions: [], met_count: 0, total: 7, all_met: false, signal: null, indicators: { rsi: null, ema50: null, ema21: null, vwap: null, atr: null, atr_pct: null, vol_ratio: null, ema50_slope: null } };

  return {
    config, updateConfig,
    agentState: anyPositionOpen ? "position_open" as AgentState : agentState,
    analysis: { bias: bestResult.bias, met_count: bestResult.met_count, all_met: bestResult.all_met },
    trades, clearTrades,
    scanCount, lastScan,
    agentLog,
    walletConnected: connected,
    walletAddress: publicKey?.toBase58() ?? null,
    forceScan: scan,
    // Multi-position paper trading
    paperPositions,        // Record<strategyKey, PaperPosition|null>
    openPositions,         // PaperPosition[] — only open ones
    anyPositionOpen,
    paperStats,
    closePaperPosition,    // (strategyKey: string) => void
    resetPaperAccount,
  };
}
