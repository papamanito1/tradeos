"use client";

import { useEffect, useState } from "react";
import { settingsApi } from "@/lib/api";
import { Zap, Shield, Server, Wallet } from "lucide-react";

export default function SettingsPage() {
  const [settings, setSettings] = useState<Record<string, unknown> | null>(null);

  useEffect(() => { settingsApi.get().then(setSettings).catch(() => {}); }, []);

  return (
    <div className="space-y-4 max-w-2xl">

      {/* Trading Mode — Live only */}
      <div className="card p-5">
        <div className="flex items-center gap-2 mb-4">
          <Zap size={14} className="text-green-400" />
          <h3 className="text-sm font-semibold text-white">Trading Mode</h3>
        </div>
        <div className="flex items-center gap-4 p-4 rounded-xl border border-green-500/20 bg-green-500/5">
          <div className="w-10 h-10 rounded-xl bg-green-500/15 flex items-center justify-center flex-shrink-0">
            <Zap size={18} className="text-green-400" />
          </div>
          <div className="flex-1">
            <div className="text-sm font-bold text-green-400">LIVE TRADING</div>
            <div className="text-xs text-neutral-500 mt-0.5">
              Real-time execution via Phantom wallet on Solana · BTC Perpetuals
            </div>
          </div>
          <div className="flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-green-500/10 border border-green-500/25">
            <span className="w-1.5 h-1.5 rounded-full bg-green-400 animate-pulse" />
            <span className="text-[11px] font-bold text-green-400">ACTIVE</span>
          </div>
        </div>
        <p className="text-[11px] text-neutral-700 mt-3">
          This system is configured for live trading only. All trades are executed on-chain via the Phantom wallet using the BTC Momentum Velocity strategy.
        </p>
      </div>

      {/* Phantom / Execution */}
      <div className="card p-5">
        <div className="flex items-center gap-2 mb-4">
          <Wallet size={14} className="text-violet-400" />
          <h3 className="text-sm font-semibold text-white">Execution Layer</h3>
        </div>
        <div className="grid grid-cols-2 gap-3">
          {[
            ["Protocol",   "Phantom Perps (Solana)"],
            ["Asset",      "BTC Perpetuals"],
            ["Strategy",   "Momentum Velocity 15m"],
            ["Signals",    "Frontend engine (live)"],
            ["Agent",      "Live Agent (wallet-signed)"],
            ["Risk R:R",   "1 : 2 (ATR-adaptive)"],
          ].map(([label, val]) => (
            <div key={label} className="bg-neutral-900 rounded-lg p-3">
              <div className="text-[9px] text-neutral-600 mb-1">{label}</div>
              <div className="text-[12px] font-semibold text-white">{val}</div>
            </div>
          ))}
        </div>
      </div>

      {/* Risk */}
      <div className="card p-5">
        <div className="flex items-center gap-2 mb-4">
          <Shield size={14} className="text-blue-400" />
          <h3 className="text-sm font-semibold text-white">Risk Configuration</h3>
        </div>
        <div className="grid grid-cols-2 gap-3">
          {[
            ["Min Conditions",   "5 / 7 to fire signal"],
            ["Min Confidence",   "65% threshold"],
            ["Stop Loss",        "1.5 × ATR"],
            ["Take Profit",      "3.0 × ATR"],
            ["ATR Filter",       "0.1% – 1.2% of price"],
            ["Volume Filter",    "≥ 1.4× 20-bar avg"],
          ].map(([label, val]) => (
            <div key={label} className="bg-neutral-900 rounded-lg p-3">
              <div className="text-[9px] text-neutral-600 mb-1">{label}</div>
              <div className="text-[12px] font-semibold text-neutral-200">{val}</div>
            </div>
          ))}
        </div>
        <p className="text-[10px] text-neutral-700 mt-3">
          Risk parameters are enforced by the frontend strategy engine on every bar close. Adjust them on the Live Agent page.
        </p>
      </div>

      {/* System */}
      <div className="card p-5">
        <div className="flex items-center gap-2 mb-4">
          <Server size={14} className="text-neutral-500" />
          <h3 className="text-sm font-semibold text-white">System</h3>
        </div>
        <div className="grid grid-cols-2 gap-3 text-[12px]">
          {[
            ["Frontend",   "Next.js 14 · Vercel"],
            ["Backend",    "FastAPI · Railway"],
            ["Market Data","Binance WebSocket (direct)"],
            ["Blockchain", "Solana Mainnet"],
            ["Version",    "TradeOS v1.0"],
            ["Mode",       "Live Only"],
          ].map(([label, val]) => (
            <div key={label}>
              <div className="text-[9px] text-neutral-700 mb-0.5">{label}</div>
              <div className="text-neutral-400">{val}</div>
            </div>
          ))}
        </div>
        {settings && (
          <div className="mt-4 p-3 bg-neutral-900 rounded-lg text-[10px] text-neutral-600">
            Backend exchange: <span className="text-neutral-400">{String(settings.exchange_id ?? "binance")}</span>
            {" · "}
            {settings.exchange_testnet ? "Testnet" : "Mainnet"}
          </div>
        )}
      </div>
    </div>
  );
}
