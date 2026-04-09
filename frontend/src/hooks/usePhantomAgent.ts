"use client";

/**
 * usePhantomAgent
 * ────────────────
 * The living agent brain. Monitors BTC Momentum Velocity strategy signals
 * and executes trades on Solana via the connected Phantom wallet using
 * Jupiter's swap API (USDC ↔ wrapped BTC) as the on-chain execution layer.
 *
 * For leveraged perps, the agent opens the Phantom Perps deep-link
 * and logs the attempted trade so the user can one-click confirm.
 */

import { useState, useEffect, useRef, useCallback } from "react";
import { useWallet, useConnection } from "@solana/wallet-adapter-react";
import { Transaction, VersionedTransaction } from "@solana/web3.js";

// ─── Token mints ─────────────────────────────────────────────────────────────
const USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v";
const WBTC_MINT = "3NZ9JMVBmGAqocybic2c7LQCJScmgsAZ6vQqTDzcqmJh"; // Jupiter-bridged BTC

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
}

export interface AgentConfig {
  enabled: boolean;
  size_usdc: number;          // USDC per trade
  min_confidence: number;     // 0–1 threshold
  min_conditions: number;     // out of 7
  mode: "spot" | "perps";    // spot = Jupiter swap, perps = Phantom Perps link
  auto_execute: boolean;      // false = confirm modal, true = auto-submit
}

interface Signal {
  direction: "long" | "short";
  entry: number; sl?: number | null; tp?: number | null;
  confidence: number; reasoning: string; timestamp: string;
}

interface Analysis {
  bias: string; met_count: number; all_met: boolean;
  last_signal: Signal | null;
}

const DEFAULT_CONFIG: AgentConfig = {
  enabled: false,
  size_usdc: 100,
  min_confidence: 0.65,
  min_conditions: 5,
  mode: "perps",
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
  return data.swapTransaction; // base64
}

export function usePhantomAgent(apiBase: string, token: string) {
  const { publicKey, signTransaction, connected } = useWallet();
  const { connection } = useConnection();

  const [config, setConfig]       = useState<AgentConfig>(DEFAULT_CONFIG);
  const [agentState, setAgentState] = useState<AgentState>("idle");
  const [trades, setTrades]       = useState<AgentTrade[]>([]);
  const [analysis, setAnalysis]   = useState<Analysis | null>(null);
  const [pendingTrade, setPendingTrade] = useState<AgentTrade | null>(null);
  const [scanCount, setScanCount] = useState(0);
  const [lastScan, setLastScan]   = useState<string | null>(null);
  const [agentLog, setAgentLog]   = useState<string[]>([]);

  const intervalRef  = useRef<ReturnType<typeof setInterval> | null>(null);
  const inFlightRef  = useRef(false);

  const log = useCallback((msg: string) => {
    setAgentLog(prev => [`[${new Date().toLocaleTimeString()}] ${msg}`, ...prev].slice(0, 100));
  }, []);

  const authFetch = useCallback(async (url: string) => {
    const r = await fetch(`${apiBase}${url}`, { headers: { Authorization: `Bearer ${token}` } });
    if (!r.ok) throw new Error(`API ${url} → ${r.status}`);
    return r.json();
  }, [apiBase, token]);

  // ── Core scan: fetch analysis, decide if trade is warranted ──────────────
  const scan = useCallback(async () => {
    if (inFlightRef.current) return;
    inFlightRef.current = true;
    setAgentState("scanning");
    setScanCount(n => n + 1);
    setLastScan(new Date().toLocaleTimeString());

    try {
      const an: Analysis = await authFetch("/api/paper/analysis");
      setAnalysis(an);

      const sig = an.last_signal;
      const conditionsOk = an.met_count >= config.min_conditions;
      const confidenceOk = sig ? sig.confidence >= config.min_confidence : false;
      const fresh = sig ? (Date.now() - new Date(sig.timestamp).getTime()) < 15 * 60_000 : false;
      const hasOpenPosition = trades.some(t => t.status === "submitted" || t.status === "confirmed");

      log(`Scan: ${an.met_count}/7 conditions · ${sig?.direction?.toUpperCase() ?? "no signal"} · confidence ${((sig?.confidence ?? 0) * 100).toFixed(0)}%`);

      if (conditionsOk && confidenceOk && fresh && sig && !hasOpenPosition) {
        log(`★ Signal qualified! ${sig.direction.toUpperCase()} · confidence ${(sig.confidence * 100).toFixed(0)}%`);
        setAgentState("signal_detected");

        const trade: AgentTrade = {
          id: `${Date.now()}`,
          timestamp: new Date().toISOString(),
          direction: sig.direction,
          entry: sig.entry,
          sl: sig.sl ?? null,
          tp: sig.tp ?? null,
          size_usdc: config.size_usdc,
          confidence: sig.confidence,
          status: "pending",
          reasoning: sig.reasoning,
        };
        setPendingTrade(trade);

        if (config.auto_execute && connected && publicKey && signTransaction) {
          await executeTrade(trade);
        }
      } else {
        setAgentState("idle");
      }
    } catch (e) {
      log(`Scan error: ${e instanceof Error ? e.message : String(e)}`);
      setAgentState("error");
    } finally {
      inFlightRef.current = false;
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [config, trades, connected, publicKey, signTransaction, authFetch, log]);

  // ── Execute trade via Jupiter Swap or Phantom Perps link ─────────────────
  const executeTrade = useCallback(async (trade: AgentTrade) => {
    if (!publicKey || !signTransaction) {
      log("Wallet not connected — cannot execute");
      return;
    }
    setAgentState("executing");
    log(`Executing ${trade.direction.toUpperCase()} · $${trade.size_usdc} USDC via ${config.mode}`);

    const updatedTrade = { ...trade, status: "submitted" as const };
    setTrades(prev => [updatedTrade, ...prev].slice(0, 50));
    setPendingTrade(null);

    if (config.mode === "perps") {
      // Open Phantom Perps — user confirms in the wallet interface
      window.open("https://trade.phantom.com/perps/BTC", "_blank", "noopener,noreferrer");
      log(`Opened Phantom Perps (${trade.direction.toUpperCase()} BTC · size $${trade.size_usdc})`);
      setTrades(prev => prev.map(t => t.id === trade.id ? { ...t, status: "confirmed" as const } : t));
      setAgentState("position_open");
      return;
    }

    // Spot mode: Jupiter USDC ↔ wBTC swap
    try {
      const amountMicro = Math.floor(trade.size_usdc * 1_000_000); // USDC has 6 decimals
      const [inMint, outMint] = trade.direction === "long"
        ? [USDC_MINT, WBTC_MINT]
        : [WBTC_MINT, USDC_MINT];

      log("Fetching Jupiter quote…");
      const quote = await jupiterQuote(inMint, outMint, amountMicro);
      log(`Quote: ${quote.outAmount} out · impact ${quote.priceImpactPct}%`);

      log("Building swap transaction…");
      const swapTxB64 = await jupiterSwapTx(quote, publicKey.toBase58());
      const swapTxBuf = Buffer.from(swapTxB64, "base64");

      log("Sending to Phantom for signing…");
      let sig: string;
      try {
        // Try as versioned transaction first
        const vtx = VersionedTransaction.deserialize(swapTxBuf);
        const signed = await (signTransaction as (tx: VersionedTransaction) => Promise<VersionedTransaction>)(vtx);
        sig = await connection.sendRawTransaction(signed.serialize(), { skipPreflight: false, preflightCommitment: "confirmed" });
      } catch {
        // Fall back to legacy Transaction
        const tx = Transaction.from(swapTxBuf);
        const signed = await (signTransaction as (tx: Transaction) => Promise<Transaction>)(tx);
        sig = await connection.sendRawTransaction(signed.serialize(), { skipPreflight: false, preflightCommitment: "confirmed" });
      }

      log(`Submitted! Tx: ${sig.slice(0, 8)}…`);
      setTrades(prev => prev.map(t => t.id === trade.id
        ? { ...t, status: "confirmed" as const, tx_signature: sig }
        : t
      ));
      setAgentState("position_open");
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      log(`Execution failed: ${msg}`);
      setTrades(prev => prev.map(t => t.id === trade.id
        ? { ...t, status: "failed" as const, error: msg }
        : t
      ));
      setAgentState("error");
    }
  }, [publicKey, signTransaction, connection, config.mode, log]);

  // ── Confirm pending trade (manual mode) ──────────────────────────────────
  const confirmTrade = useCallback(() => {
    if (pendingTrade) executeTrade(pendingTrade);
  }, [pendingTrade, executeTrade]);

  const dismissPendingTrade = useCallback(() => {
    setPendingTrade(null);
    setAgentState("idle");
    log("Trade dismissed by user");
  }, [log]);

  // ── Agent loop ─────────────────────────────────────────────────────────────
  useEffect(() => {
    if (intervalRef.current) clearInterval(intervalRef.current);
    if (config.enabled) {
      log(`Agent STARTED — scanning every 60s`);
      scan();
      intervalRef.current = setInterval(scan, 60_000);
    } else {
      log("Agent STOPPED");
      setAgentState("idle");
    }
    return () => { if (intervalRef.current) clearInterval(intervalRef.current); };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [config.enabled]);

  const updateConfig = useCallback((patch: Partial<AgentConfig>) => {
    setConfig(prev => ({ ...prev, ...patch }));
  }, []);

  const clearTrades = useCallback(() => setTrades([]), []);

  return {
    config, updateConfig,
    agentState, analysis,
    trades, clearTrades,
    pendingTrade, confirmTrade, dismissPendingTrade,
    scanCount, lastScan,
    agentLog,
    walletConnected: connected,
    walletAddress: publicKey?.toBase58() ?? null,
    forceScan: scan,
  };
}
