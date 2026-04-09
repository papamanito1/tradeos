"use client";

/**
 * useServerAgent
 * ──────────────
 * Polls the Railway backend /api/agent247 every 5 seconds.
 * The backend runs 24/7 — trades continue even when the browser is closed.
 */

import { useState, useEffect, useCallback, useRef } from "react";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const POLL_MS  = 5_000;

export interface ServerPosition {
  id:             string;
  strategy_key:   string;
  strategy_name:  string;
  direction:      "long" | "short";
  entry:          number;
  sl:             number | null;
  tp:             number | null;
  size_usdc:      number;
  confidence:     number;
  reasoning:      string | null;
  opened_at:      string;
  current_price:  number | null;
  unrealized_pnl: number;
  unrealized_pct: number;
  btc_size:       number;
}

export interface ServerTrade {
  id:             string;
  strategy_key:   string | null;
  strategy_name:  string | null;
  direction:      "long" | "short";
  entry:          number;
  sl:             number | null;
  tp:             number | null;
  size_usdc:      number;
  confidence:     number | null;
  exit_price:     number | null;
  exit_reason:    "tp" | "sl" | "manual" | null;
  pnl_usd:        number | null;
  pnl_pct:        number | null;
  status:         string;
  opened_at:      string | null;
  closed_at:      string | null;
}

export interface ServerStats {
  total_trades: number;
  wins:         number;
  losses:       number;
  win_rate:     number;
  total_pnl:    number;
  best_trade:   number;
  worst_trade:  number;
  avg_rr:       number;
}

export interface ServerConfig {
  enabled:        boolean;
  size_usdc:      number;
  min_confidence: number;
  min_conditions: number;
  mode:           "paper";
  auto_execute:   boolean;
}

export function useServerAgent(token: string | null) {
  const [config,    setConfig]    = useState<ServerConfig | null>(null);
  const [positions, setPositions] = useState<ServerPosition[]>([]);
  const [stats,     setStats]     = useState<ServerStats | null>(null);
  const [log,       setLog]       = useState<string[]>([]);
  const [trades,    setTrades]    = useState<ServerTrade[]>([]);
  const [running,   setRunning]   = useState(false);
  const [online,    setOnline]    = useState(false);
  const [error,     setError]     = useState<string | null>(null);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const authHeaders = useCallback(() => ({
    "Content-Type": "application/json",
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  }), [token]);

  const poll = useCallback(async () => {
    if (!token) return;
    try {
      const r = await fetch(`${API_URL}/api/agent247/status`, { headers: authHeaders() });
      if (!r.ok) throw new Error(`${r.status}`);
      const data = await r.json();
      setConfig(data.config);
      setPositions(data.open_positions ?? []);
      setStats(data.stats);
      setLog(data.log ?? []);
      setRunning(data.worker_running);
      setOnline(true);
      setError(null);
    } catch (e) {
      setOnline(false);
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [token, authHeaders]);

  const fetchTrades = useCallback(async () => {
    if (!token) return;
    try {
      const r = await fetch(`${API_URL}/api/agent247/trades`, { headers: authHeaders() });
      if (r.ok) setTrades(await r.json());
    } catch { /* ignore */ }
  }, [token, authHeaders]);

  useEffect(() => {
    poll();
    fetchTrades();
    intervalRef.current = setInterval(() => { poll(); fetchTrades(); }, POLL_MS);
    return () => { if (intervalRef.current) clearInterval(intervalRef.current); };
  }, [poll, fetchTrades]);

  const startAgent = useCallback(async () => {
    if (!token) return;
    await fetch(`${API_URL}/api/agent247/start`, { method: "POST", headers: authHeaders() });
    await poll();
  }, [token, authHeaders, poll]);

  const stopAgent = useCallback(async () => {
    if (!token) return;
    await fetch(`${API_URL}/api/agent247/stop`, { method: "POST", headers: authHeaders() });
    await poll();
  }, [token, authHeaders, poll]);

  const updateConfig = useCallback(async (patch: Partial<ServerConfig>) => {
    if (!token) return;
    await fetch(`${API_URL}/api/agent247/config`, {
      method: "POST",
      headers: authHeaders(),
      body: JSON.stringify(patch),
    });
    await poll();
  }, [token, authHeaders, poll]);

  const closePosition = useCallback(async (strategyKey: string) => {
    if (!token) return;
    await fetch(`${API_URL}/api/agent247/close/${strategyKey}`, { method: "POST", headers: authHeaders() });
    await poll();
  }, [token, authHeaders, poll]);

  const resetAccount = useCallback(async () => {
    if (!token) return;
    await fetch(`${API_URL}/api/agent247/reset`, { method: "POST", headers: authHeaders() });
    await poll();
    await fetchTrades();
  }, [token, authHeaders, poll, fetchTrades]);

  return {
    // State
    config, positions, stats, log, trades, running, online, error,
    // Actions
    startAgent, stopAgent, updateConfig, closePosition, resetAccount,
    refresh: poll,
  };
}
