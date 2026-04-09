"use client";

/**
 * usePhantomAgent
 * ────────────────
 * The living agent brain. Receives the StrategyResult from the frontend
 * strategy engine (no backend calls) and either:
 *   - Paper mode:  simulates fills instantly, tracks P&L as price moves
 *   - Perps mode:  opens Phantom Perps deep-link
 *   - Spot mode:   executes USDC↔wBTC swap via Jupiter v6
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
  // Paper trading fields
  is_paper?: boolean;
  exit_price?: number;
  exit_reason?: "tp" | "sl" | "manual";
  pnl_usd?: number;
  pnl_pct?: number;
}

/** Live paper position (open) */
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
  unrealized_pnl: number;   // USD
  unrealized_pct: number;   // %
  btc_size: number;         // entry size in BTC
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

const DEFAULT_CONFIG: AgentConfig = {
  enabled: false,
  size_usdc: 100,
  min_confidence: 0.65,
  min_conditions: 5,
  mode: "paper",   // default to paper (safe)
  auto_execute: false,
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

// ─── Hook — accepts live strategy result + optional live price for paper P&L ──
export function usePhantomAgent(strategyResult: StrategyResult, livePrice?: number) {
  const { publicKey, signTransaction, connected } = useWallet();
  const { connection } = useConnection();

  const [config, setConfig]             = useState<AgentConfig>(DEFAULT_CONFIG);
  const [agentState, setAgentState]     = useState<AgentState>("idle");
  const [trades, setTrades]             = useState<AgentTrade[]>([]);
  const [pendingTrade, setPendingTrade] = useState<AgentTrade | null>(null);
  const [scanCount, setScanCount]       = useState(0);
  const [lastScan, setLastScan]         = useState<string | null>(null);
  const [agentLog, setAgentLog]         = useState<string[]>([]);

  // ── Paper trading state ───────────────────────────────────────────────────
  const [paperPosition, setPaperPosition] = useState<PaperPosition | null>(null);
  const [paperStats, setPaperStats]       = useState<PaperStats>({
    total_trades: 0, wins: 0, losses: 0, win_rate: 0,
    total_pnl: 0, avg_rr: 0, best_trade: 0, worst_trade: 0,
  });

  const inFlightRef  = useRef(false);
  const intervalRef  = useRef<ReturnType<typeof setInterval> | null>(null);
  const strategyRef  = useRef<StrategyResult>(strategyResult);
  const configRef    = useRef<AgentConfig>(config);
  const tradesRef    = useRef<AgentTrade[]>(trades);
  const paperPosRef  = useRef<PaperPosition | null>(paperPosition);

  useEffect(() => { strategyRef.current = strategyResult; }, [strategyResult]);
  useEffect(() => { configRef.current   = config;         }, [config]);
  useEffect(() => { tradesRef.current   = trades;         }, [trades]);
  useEffect(() => { paperPosRef.current = paperPosition;  }, [paperPosition]);

  const log = useCallback((msg: string) => {
    setAgentLog(prev => [`[${new Date().toLocaleTimeString()}] ${msg}`, ...prev].slice(0, 100));
  }, []);

  // ── Update paper position P&L as live price changes ───────────────────────
  useEffect(() => {
    if (!livePrice || !paperPosRef.current) return;
    const pos = paperPosRef.current;
    const price = livePrice;

    // Check SL/TP hit
    const hitTP = pos.direction === "long"  ? price >= (pos.tp ?? Infinity)
                : price <= (pos.tp ?? -Infinity);
    const hitSL = pos.direction === "long"  ? price <= (pos.sl ?? -Infinity)
                : price >= (pos.sl ?? Infinity);

    if (hitTP || hitSL) {
      const exitReason = hitTP ? "tp" : "sl";
      const exitPrice  = hitTP ? (pos.tp ?? price) : (pos.sl ?? price);
      const priceDiff  = pos.direction === "long"
        ? exitPrice - pos.entry
        : pos.entry - exitPrice;
      const pnl_usd    = priceDiff * pos.btc_size;
      const pnl_pct    = priceDiff / pos.entry * 100;

      log(`${exitReason.toUpperCase()} hit at $${exitPrice.toFixed(0)} · P&L ${pnl_usd >= 0 ? "+" : ""}$${pnl_usd.toFixed(2)}`);

      // Close paper position
      setPaperPosition(null);
      setAgentState("idle");

      // Record closed trade
      const closedTrade: AgentTrade = {
        id: pos.id, timestamp: pos.timestamp,
        direction: pos.direction, entry: pos.entry,
        sl: pos.sl, tp: pos.tp, size_usdc: pos.size_usdc,
        confidence: pos.confidence, reasoning: pos.reasoning,
        status: "confirmed", is_paper: true,
        exit_price: exitPrice, exit_reason: exitReason,
        pnl_usd: Math.round(pnl_usd * 100) / 100,
        pnl_pct:  Math.round(pnl_pct * 100) / 100,
      };
      setTrades(prev => [closedTrade, ...prev].slice(0, 50));

      // Update stats
      setPaperStats(prev => {
        const wins   = prev.wins   + (exitReason === "tp" ? 1 : 0);
        const losses = prev.losses + (exitReason === "sl" ? 1 : 0);
        const total  = prev.total_trades + 1;
        const allPnls = [...tradesRef.current.filter(t => t.is_paper && t.pnl_usd != null).map(t => t.pnl_usd!), pnl_usd];
        return {
          total_trades: total,
          wins, losses,
          win_rate:    Math.round(wins / total * 1000) / 10,
          total_pnl:   Math.round((prev.total_pnl + pnl_usd) * 100) / 100,
          avg_rr:      prev.avg_rr,  // approximate
          best_trade:  Math.max(prev.best_trade, pnl_usd),
          worst_trade: Math.min(prev.worst_trade, pnl_usd),
        };
      });
    } else {
      // Just update unrealized P&L
      const priceDiff    = pos.direction === "long" ? price - pos.entry : pos.entry - price;
      const unrealized   = priceDiff * pos.btc_size;
      const unrealizedPct = priceDiff / pos.entry * 100;
      setPaperPosition(p => p ? { ...p, current_price: price, unrealized_pnl: unrealized, unrealized_pct: unrealizedPct } : null);
    }
  }, [livePrice, log]);

  // ── Paper execution (instant fill, no wallet needed) ──────────────────────
  const executePaperTrade = useCallback((trade: AgentTrade) => {
    if (paperPosRef.current) {
      log("Paper position already open — skipping");
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
    };
    setPaperPosition(pos);
    setPendingTrade(null);
    setAgentState("position_open");
    log(`📄 PAPER ${trade.direction.toUpperCase()} filled @ $${trade.entry.toFixed(0)} · size $${trade.size_usdc} · ${btcSize.toFixed(6)} BTC`);
    log(`   SL $${trade.sl?.toFixed(0) ?? "—"} · TP $${trade.tp?.toFixed(0) ?? "—"}`);
  }, [log]);

  // ── Close paper position manually ─────────────────────────────────────────
  const closePaperPosition = useCallback(() => {
    const pos = paperPosRef.current;
    if (!pos) return;
    const price = pos.current_price;
    const priceDiff = pos.direction === "long" ? price - pos.entry : pos.entry - price;
    const pnl_usd   = priceDiff * pos.btc_size;
    const pnl_pct   = priceDiff / pos.entry * 100;

    log(`📄 Paper position closed manually @ $${price.toFixed(0)} · P&L ${pnl_usd >= 0 ? "+" : ""}$${pnl_usd.toFixed(2)}`);
    const closedTrade: AgentTrade = {
      id: pos.id, timestamp: pos.timestamp,
      direction: pos.direction, entry: pos.entry,
      sl: pos.sl, tp: pos.tp, size_usdc: pos.size_usdc,
      confidence: pos.confidence, reasoning: pos.reasoning,
      status: "confirmed", is_paper: true,
      exit_price: price, exit_reason: "manual",
      pnl_usd:  Math.round(pnl_usd * 100) / 100,
      pnl_pct:  Math.round(pnl_pct * 100) / 100,
    };
    setTrades(prev => [closedTrade, ...prev].slice(0, 50));
    setPaperPosition(null);
    setAgentState("idle");
    setPaperStats(prev => ({
      ...prev,
      total_trades: prev.total_trades + 1,
      total_pnl: Math.round((prev.total_pnl + pnl_usd) * 100) / 100,
      best_trade:  Math.max(prev.best_trade, pnl_usd),
      worst_trade: Math.min(prev.worst_trade, pnl_usd),
    }));
  }, [log]);

  // ── Execute live trade (Phantom wallet) ───────────────────────────────────
  const executeTrade = useCallback(async (trade: AgentTrade) => {
    const mode = configRef.current.mode;

    if (mode === "paper") {
      executePaperTrade(trade);
      return;
    }

    if (!publicKey || !signTransaction) {
      log("Wallet not connected — cannot execute");
      return;
    }

    setAgentState("executing");
    log(`Executing ${trade.direction.toUpperCase()} · $${trade.size_usdc} USDC via ${mode}`);
    setTrades(prev => [{ ...trade, status: "submitted" as const }, ...prev].slice(0, 50));
    setPendingTrade(null);

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
      let sig: string;
      try {
        const vtx    = VersionedTransaction.deserialize(swapTxBuf);
        const signed = await (signTransaction as (tx: VersionedTransaction) => Promise<VersionedTransaction>)(vtx);
        sig = await connection.sendRawTransaction(signed.serialize(), { skipPreflight: false, preflightCommitment: "confirmed" });
      } catch {
        const tx     = Transaction.from(swapTxBuf);
        const signed = await (signTransaction as (tx: Transaction) => Promise<Transaction>)(tx);
        sig = await connection.sendRawTransaction(signed.serialize(), { skipPreflight: false, preflightCommitment: "confirmed" });
      }

      log(`Submitted! Tx: ${sig.slice(0, 8)}…`);
      setTrades(prev => prev.map(t => t.id === trade.id
        ? { ...t, status: "confirmed" as const, tx_signature: sig } : t));
      setAgentState("position_open");
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      log(`Execution failed: ${msg}`);
      setTrades(prev => prev.map(t => t.id === trade.id
        ? { ...t, status: "failed" as const, error: msg } : t));
      setAgentState("error");
    }
  }, [publicKey, signTransaction, connection, log, executePaperTrade]);

  // ── Core scan ─────────────────────────────────────────────────────────────
  const scan = useCallback(() => {
    if (inFlightRef.current) return;
    inFlightRef.current = true;
    setAgentState("scanning");
    setScanCount(n => n + 1);
    setLastScan(new Date().toLocaleTimeString());

    try {
      const result   = strategyRef.current;
      const cfg      = configRef.current;
      const openPos  = paperPosRef.current !== null ||
        tradesRef.current.some(t => !t.is_paper && (t.status === "submitted" || t.status === "confirmed"));
      const sig      = result.signal;

      const conditionsOk = result.met_count >= cfg.min_conditions;
      const confidenceOk = sig ? sig.confidence >= cfg.min_confidence : false;

      log(`Scan: ${result.met_count}/${result.total ?? 7} conds · ${sig?.direction?.toUpperCase() ?? "no signal"} · conf ${((sig?.confidence ?? 0) * 100).toFixed(0)}% · bias ${result.bias}`);

      if (conditionsOk && confidenceOk && sig && !openPos) {
        log(`★ Signal qualified! ${sig.direction.toUpperCase()} · confidence ${(sig.confidence * 100).toFixed(0)}%`);
        setAgentState("signal_detected");

        const trade: AgentTrade = {
          id:         `${Date.now()}`,
          timestamp:  new Date().toISOString(),
          direction:  sig.direction,
          entry:      sig.entry,
          sl:         sig.sl,
          tp:         sig.tp,
          size_usdc:  cfg.size_usdc,
          confidence: sig.confidence,
          status:     "pending",
          reasoning:  sig.reasoning,
          is_paper:   cfg.mode === "paper",
        };
        setPendingTrade(trade);

        const isPaper = cfg.mode === "paper";
        const canLive = !isPaper && connected && publicKey && signTransaction;

        if (cfg.auto_execute && (isPaper || canLive)) {
          executeTrade(trade);
        }
      } else {
        if (!sig)             log("No signal — strategy conditions not fully met");
        else if (!conditionsOk) log(`Only ${result.met_count}/${cfg.min_conditions} conditions met`);
        else if (!confidenceOk) log(`Confidence ${((sig.confidence) * 100).toFixed(0)}% < threshold`);
        else if (openPos)     log("Open position exists — skipping new entry");
        setAgentState("idle");
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
      log(`Agent STARTED [${modeLabel}] — scanning every 60s`);
      scan();
      intervalRef.current = setInterval(scan, 60_000);
    } else {
      log("Agent STOPPED");
      setAgentState("idle");
    }
    return () => { if (intervalRef.current) clearInterval(intervalRef.current); };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [config.enabled]);

  const confirmTrade = useCallback(() => {
    if (pendingTrade) executeTrade(pendingTrade);
  }, [pendingTrade, executeTrade]);

  const dismissPendingTrade = useCallback(() => {
    setPendingTrade(null);
    setAgentState("idle");
    log("Trade dismissed");
  }, [log]);

  const updateConfig = useCallback((patch: Partial<AgentConfig>) =>
    setConfig(prev => ({ ...prev, ...patch })), []);

  const clearTrades = useCallback(() => {
    setTrades([]);
    setPaperStats({ total_trades: 0, wins: 0, losses: 0, win_rate: 0, total_pnl: 0, avg_rr: 0, best_trade: 0, worst_trade: 0 });
  }, []);

  const resetPaperAccount = useCallback(() => {
    setPaperPosition(null);
    setTrades([]);
    setPaperStats({ total_trades: 0, wins: 0, losses: 0, win_rate: 0, total_pnl: 0, avg_rr: 0, best_trade: 0, worst_trade: 0 });
    log("📄 Paper account reset — balance $" + configRef.current.size_usdc);
  }, [log]);

  return {
    config, updateConfig,
    agentState,
    analysis: { bias: strategyResult.bias, met_count: strategyResult.met_count, all_met: strategyResult.all_met },
    trades, clearTrades,
    pendingTrade, confirmTrade, dismissPendingTrade,
    scanCount, lastScan,
    agentLog,
    walletConnected: connected,
    walletAddress: publicKey?.toBase58() ?? null,
    forceScan: scan,
    // Paper trading
    paperPosition,
    paperStats,
    closePaperPosition,
    resetPaperAccount,
  };
}
