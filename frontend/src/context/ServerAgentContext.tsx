"use client";

/**
 * ServerAgentContext
 * ──────────────────
 * Provides a single shared useServerAgent instance across the entire dashboard.
 * Both the overview and agent pages read from this same poll — one HTTP request
 * every 5s instead of two, and the sidebar can show running status without
 * re-mounting its own hook.
 */

import { createContext, useContext, ReactNode } from "react";
import { useServerAgent } from "@/hooks/useServerAgent";

type ServerAgentCtx = ReturnType<typeof useServerAgent>;

export const ServerAgentContext = createContext<ServerAgentCtx | null>(null);

export function ServerAgentProvider({ children }: { children: ReactNode }) {
  const agent = useServerAgent();
  return (
    <ServerAgentContext.Provider value={agent}>
      {children}
    </ServerAgentContext.Provider>
  );
}

export function useSharedServerAgent(): ServerAgentCtx {
  const ctx = useContext(ServerAgentContext);
  if (!ctx) throw new Error("useSharedServerAgent must be used inside ServerAgentProvider");
  return ctx;
}
