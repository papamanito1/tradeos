"use client";

/**
 * useServerAgent
 * ──────────────
 * Polls the backend 24/7 agent every 5 seconds.
 * The backend (Railway) runs all 5 strategies continuously even when
 * the browser is closed. This hook is read-only except for control calls.
 */

import { useState, useEffect, useCallback, useRef } from "react";

const API = process.env.NEXT_PUBLIC_API_URL || "";

// ── Types ─────────────────────────────────────────────────────────────────────
export interface ServerPosition {
  id: string;
  strategy_key:   string;
  strategy_name:  string;
  direction:      "long" | "short";
  entry:          number;
  sl:             number | null;
  tp:             number | null;
  size_usdc:      number;
  confidence:     number;
  reasoning:      string;
  rr:             string;
  timestamp:      string;
  current_price:  number;
  unrealized_pnl: number;
  unrealized_pct: number;
  btc_size:       number;
  is_paper:       boolean;
}

export interface ServerTrade extends ServerPosition {
  exit_price:  number;
  exit_reason: "tp" | "sl" | "manual";
  pnl_usd:     number;
  pnl_pct:     number;
  closed_at:   string;
  status:      "confirmed" | "failed";
}

export interface ServerStats {
  total_trades: number;
  wins:         number;
  losses:       number;
  win_rate:     number;
  total_pnl:    number;
  best_trade:   number;
  worst_trade:  number;
}

export interface StrategyOverride {
  enabled:        boolean;
  size_usdc:      number;
  leverage:       number;
  min_confidence: number;
  min_conditions: number;
}

export interface ServerAgentConfig {
  enabled:              boolean;
  size_usdc:            number;
  min_confidence:       number;
  min_conditions:       number;
  mode:                 string;   // "paper" | "live"
  auto_execute:         boolean;
  leverage:             number;
  strategy_overrides?:  Record<string, StrategyOverride>;
  daily_loss_limit?:    number;   // live trading circuit breaker ($)
  max_position_usdc?:   number;   // hard cap per position ($)
}

export interface LiveExecutorStatus {
  connected:           boolean;
  halted:              boolean;
  daily_pnl:           number;
  daily_loss_limit:    number;
  max_position_usdc:   number;
  open_count:          number;
  live_positions:      ServerPosition[];
  mode?:               string;
  keys_set?:           boolean;
  message?:            string;
}

export interface GridStateData {
  center?:       number;
  last_price?:   number;
  daily_pnl?:    number;
  daily_trades?: number;
  last_reset?:   string;
}

export interface ServerStatus {
  running:          boolean;
  config:           ServerAgentConfig;
  scan_count:       number;
  last_scan:        string | null;
  live_price:       number;
  open_positions:   ServerPosition[];
  trades:           ServerTrade[];
  stats:            ServerStats;
  log:              string[];
  grid_state?:      GridStateData;
  grid_positions?:  ServerPosition[];
  live_executor?:   LiveExecutorStatus | null;
}

async function apiFetch(path: string, opts?: RequestInit) {
  const r = await fetch(`${API}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (!r.ok) throw new Error(`${r.status}`);
  return r.json();
}

// ── Hook ──────────────────────────────────────────────────────────────────────
export function useServerAgent() {
  const [status, setStatus]   = useState<ServerStatus | null>(null);
  const [error,  setError]    = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const refresh = useCallback(async () => {
    try {
      const data = await apiFetch("/api/agent/status");
      setStatus(data);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Connection error");
    } finally {
      setLoading(false);
    }
  }, []);

  // Poll every 5 seconds
  useEffect(() => {
    refresh();
    pollRef.current = setInterval(refresh, 5_000);
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [refresh]);

  // ── Control actions ──────────────────────────────────────────────────────
  const startAgent = useCallback(async () => {
    await apiFetch("/api/agent/start", { method: "POST" });
    await refresh();
  }, [refresh]);

  const stopAgent = useCallback(async () => {
    await apiFetch("/api/agent/stop", { method: "POST" });
    await refresh();
  }, [refresh]);

  const updateConfig = useCallback(async (patch: Partial<ServerAgentConfig>) => {
    await apiFetch("/api/agent/config", {
      method: "POST",
      body: JSON.stringify(patch),
    });
    await refresh();
  }, [refresh]);

  const resetAccount = useCallback(async () => {
    await apiFetch("/api/agent/reset", { method: "POST" });
    await refresh();
  }, [refresh]);

  const closePosition = useCallback(async (strategyKey: string) => {
    await apiFetch(`/api/agent/close/${strategyKey}`, { method: "POST" });
    await refresh();
  }, [refresh]);

  const forceScan = useCallback(async () => {
    await apiFetch("/api/agent/force-scan", { method: "POST" });
    await refresh();
  }, [refresh]);

  const closeLivePosition = useCallback(async (strategyKey: string) => {
    await apiFetch(`/api/agent/live/close/${strategyKey}`, { method: "POST" });
    await refresh();
  }, [refresh]);

  const resetCircuitBreaker = useCallback(async () => {
    await apiFetch("/api/agent/live/reset-circuit-breaker", { method: "POST" });
    await refresh();
  }, [refresh]);

  const fetchLiveBalance = useCallback(async () => {
    return await apiFetch("/api/agent/live/balance");
  }, []);

  // Auto-start the agent when the hook mounts if it's not running
  useEffect(() => {
    if (status && !status.running) {
      apiFetch("/api/agent/start", { method: "POST" }).then(() => refresh()).catch(() => {});
    }
  }, [status?.running, refresh]);

  return {
    status, error, loading,
    refresh,
    startAgent, stopAgent,
    updateConfig, resetAccount, closePosition, forceScan,
    closeLivePosition, resetCircuitBreaker, fetchLiveBalance,
    // Convenience shortcuts
    running:          status?.running         ?? false,
    config:           status?.config          ?? null,
    openPositions:    status?.open_positions  ?? [],
    trades:           status?.trades          ?? [],
    stats:            status?.stats           ?? null,
    log:              status?.log             ?? [],
    scanCount:        status?.scan_count      ?? 0,
    lastScan:         status?.last_scan       ?? null,
    livePrice:        status?.live_price      ?? 0,
    gridState:        status?.grid_state      ?? null,
    gridPositions:    status?.grid_positions  ?? [],
    liveExecutor:     status?.live_executor   ?? null,
  };
}
