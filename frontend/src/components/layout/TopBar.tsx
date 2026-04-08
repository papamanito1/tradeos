"use client";

import { useState, useCallback } from "react";
import { Power, Zap } from "lucide-react";
import { riskApi } from "@/lib/api";
import { cn } from "@/lib/utils";

interface TopBarProps {
  title: string;
  wsConnected: boolean;
  mode: string;
  killSwitchActive: boolean;
  onKillSwitchChange?: (active: boolean) => void;
}

export function TopBar({ title, wsConnected, mode, killSwitchActive, onKillSwitchChange }: TopBarProps) {
  const [loading, setLoading] = useState(false);
  const [confirming, setConfirming] = useState(false);

  const handleKillSwitch = useCallback(async () => {
    if (!killSwitchActive && !confirming) { setConfirming(true); return; }
    setConfirming(false);
    setLoading(true);
    try {
      await riskApi.toggleKillSwitch(!killSwitchActive);
      onKillSwitchChange?.(!killSwitchActive);
    } finally { setLoading(false); }
  }, [killSwitchActive, confirming, onKillSwitchChange]);

  return (
    <header
      className="h-12 flex items-center px-5 gap-4 flex-shrink-0"
      style={{
        background: "rgba(6,7,14,0.85)",
        backdropFilter: "blur(40px) saturate(160%)",
        WebkitBackdropFilter: "blur(40px) saturate(160%)",
        borderBottom: "1px solid rgba(255,255,255,0.05)",
        boxShadow: "0 1px 0 rgba(255,255,255,0.02)",
      }}
    >
      <h1
        className="text-[15px] font-semibold flex-1 text-white"
        style={{ letterSpacing: "-0.02em" }}
      >
        {title}
      </h1>

      <div className="flex items-center gap-2">
        {/* Live indicator */}
        <div
          className="flex items-center gap-1.5 px-3 py-1 rounded-full"
          style={{
            background: wsConnected ? "rgba(48,209,88,0.08)" : "rgba(255,255,255,0.03)",
            border: `1px solid ${wsConnected ? "rgba(48,209,88,0.2)" : "rgba(255,255,255,0.06)"}`,
          }}
        >
          <span
            className="w-[5px] h-[5px] rounded-full flex-shrink-0"
            style={{
              background: wsConnected ? "#30d158" : "#3a3a52",
              boxShadow: wsConnected ? "0 0 0 2px rgba(48,209,88,0.2)" : "none",
              animation: wsConnected ? "pulse 2s ease infinite" : "none",
            }}
          />
          <span
            className="text-[11px] font-semibold"
            style={{ color: wsConnected ? "#30d158" : "#3a3a52" }}
          >
            {wsConnected ? "Live" : "Offline"}
          </span>
        </div>

        {/* Mode badge */}
        <div
          className="flex items-center gap-1.5 px-3 py-1 rounded-full"
          style={{
            background: mode === "live" ? "rgba(255,214,10,0.08)" : "rgba(10,132,255,0.08)",
            border: `1px solid ${mode === "live" ? "rgba(255,214,10,0.2)" : "rgba(10,132,255,0.2)"}`,
          }}
        >
          <Zap size={10} style={{ color: mode === "live" ? "#ffd60a" : "#0a84ff" }} />
          <span
            className="text-[11px] font-bold uppercase"
            style={{ color: mode === "live" ? "#ffd60a" : "#0a84ff", letterSpacing: "0.06em" }}
          >
            {mode}
          </span>
        </div>

        {/* Emergency Stop */}
        <button
          onClick={handleKillSwitch}
          disabled={loading}
          className={cn(
            "flex items-center gap-1.5 px-3 py-1 rounded-full text-[12px] font-semibold transition-all duration-150 border",
            loading && "opacity-50 cursor-not-allowed"
          )}
          style={
            killSwitchActive
              ? { background: "rgba(255,69,58,0.12)", borderColor: "rgba(255,69,58,0.25)", color: "#ff453a" }
              : confirming
              ? { background: "rgba(255,214,10,0.1)", borderColor: "rgba(255,214,10,0.25)", color: "#ffd60a", animation: "pulse 1s ease infinite" }
              : { background: "rgba(255,255,255,0.04)", borderColor: "rgba(255,255,255,0.07)", color: "#3a3a52" }
          }
          onMouseEnter={(e) => {
            if (!killSwitchActive && !confirming && !loading) {
              const el = e.currentTarget as HTMLElement;
              el.style.background = "rgba(255,69,58,0.09)";
              el.style.borderColor = "rgba(255,69,58,0.2)";
              el.style.color = "#ff453a";
            }
          }}
          onMouseLeave={(e) => {
            if (!killSwitchActive && !confirming && !loading) {
              const el = e.currentTarget as HTMLElement;
              el.style.background = "rgba(255,255,255,0.04)";
              el.style.borderColor = "rgba(255,255,255,0.07)";
              el.style.color = "#3a3a52";
            }
          }}
        >
          <Power size={11} />
          {loading ? "..." : killSwitchActive ? "Kill Active" : confirming ? "Confirm?" : "Stop All"}
        </button>
      </div>
    </header>
  );
}
