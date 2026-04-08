"use client";

import { useEffect, useState } from "react";
import { settingsApi } from "@/lib/api";
import { AlertTriangle, Check } from "lucide-react";
import { cn } from "@/lib/utils";
import { Modal } from "@/components/ui/Modal";

export default function SettingsPage() {
  const [settings, setSettings] = useState<any>(null);
  const [showLiveConfirm, setShowLiveConfirm] = useState(false);
  const [modeChanging, setModeChanging] = useState(false);
  const [saved, setSaved] = useState("");

  useEffect(() => { settingsApi.get().then(setSettings); }, []);

  async function switchToPaper() {
    setModeChanging(true);
    const res = await settingsApi.setTradingMode("paper", true);
    if (res.trading_mode) {
      setSettings((s: any) => ({ ...s, trading_mode: res.trading_mode }));
      setSaved("Switched to paper mode");
    }
    setModeChanging(false);
  }

  async function switchToLive() {
    if (!settings?.allow_live_trading) return;
    const res = await settingsApi.setTradingMode("live", true);
    if (res.trading_mode === "live") {
      setSettings((s: any) => ({ ...s, trading_mode: "live" }));
      setSaved("Switched to live mode");
    }
    setShowLiveConfirm(false);
    setModeChanging(false);
  }

  if (!settings) return (
    <div className="flex items-center justify-center h-64">
      <div className="w-5 h-5 border-2 border-accent border-t-transparent rounded-full animate-spin" />
    </div>
  );

  return (
    <div className="space-y-4 max-w-2xl">
      {saved && (
        <div className="flex items-center gap-2 text-profit bg-profit/10 border border-profit/20 rounded px-3 py-2 text-sm">
          <Check className="w-3.5 h-3.5" /> {saved}
        </div>
      )}

      {/* Trading Mode */}
      <div className="card p-4">
        <h3 className="text-sm font-semibold text-white mb-4">Trading Mode</h3>
        <div className="grid grid-cols-2 gap-3">
          <button
            onClick={switchToPaper}
            disabled={settings.trading_mode === "paper" || modeChanging}
            className={cn(
              "p-4 rounded-lg border-2 text-left transition-all",
              settings.trading_mode === "paper"
                ? "border-accent bg-accent/10"
                : "border-surface-600 hover:border-accent/50"
            )}
          >
            <div className="text-sm font-semibold text-white">Paper Trading</div>
            <div className="text-xs text-neutral mt-1">Simulated orders — no real money at risk</div>
            {settings.trading_mode === "paper" && (
              <div className="text-[10px] text-accent mt-2 uppercase font-semibold">Active</div>
            )}
          </button>

          <button
            onClick={() => settings.allow_live_trading ? setShowLiveConfirm(true) : null}
            disabled={settings.trading_mode === "live" || modeChanging || !settings.allow_live_trading}
            className={cn(
              "p-4 rounded-lg border-2 text-left transition-all relative",
              !settings.allow_live_trading ? "opacity-50 cursor-not-allowed" : "",
              settings.trading_mode === "live"
                ? "border-warning bg-warning/10"
                : "border-surface-600 hover:border-warning/50"
            )}
          >
            <div className="text-sm font-semibold text-white">Live Trading</div>
            <div className="text-xs text-neutral mt-1">Real exchange orders with real funds</div>
            {!settings.allow_live_trading && (
              <div className="text-[10px] text-neutral mt-2">Disabled in config (ALLOW_LIVE_TRADING=0)</div>
            )}
            {settings.trading_mode === "live" && (
              <div className="text-[10px] text-warning mt-2 uppercase font-semibold">Active</div>
            )}
          </button>
        </div>
      </div>

      {/* Exchange */}
      <div className="card p-4">
        <h3 className="text-sm font-semibold text-white mb-4">Exchange Configuration</h3>
        <div className="grid grid-cols-2 gap-3 text-sm">
          <div>
            <div className="label mb-1">Exchange</div>
            <div className="text-gray-200 capitalize">{settings.exchange_id}</div>
          </div>
          <div>
            <div className="label mb-1">Mode</div>
            <div className="text-gray-200">{settings.exchange_testnet ? "Testnet" : "Mainnet"}</div>
          </div>
          <div>
            <div className="label mb-1">Mock Exchange</div>
            <div className={cn("text-sm", settings.use_mock_exchange ? "text-accent" : "text-neutral")}>
              {settings.use_mock_exchange ? "Enabled (no real API calls)" : "Disabled"}
            </div>
          </div>
        </div>
        <p className="text-neutral text-xs mt-4 bg-surface-900 rounded px-3 py-2">
          API keys are configured via environment variables (.env) for security — they cannot be set from the UI.
        </p>
      </div>

      {/* System info */}
      <div className="card p-4">
        <h3 className="text-sm font-semibold text-white mb-4">System</h3>
        <div className="text-xs text-neutral space-y-1.5">
          <div>Version: TradeOS v1.0.0</div>
          <div>Backend: FastAPI + SQLAlchemy + Redis</div>
          <div>Frontend: Next.js 14 + Tailwind CSS</div>
          <div>Exchange: CCXT</div>
        </div>
      </div>

      {/* Live mode confirmation modal */}
      <Modal
        open={showLiveConfirm}
        onClose={() => setShowLiveConfirm(false)}
        title="Activate Live Trading"
      >
        <div className="space-y-4">
          <div className="flex items-start gap-3 bg-warning/10 border border-warning/20 rounded p-3">
            <AlertTriangle className="w-4 h-4 text-warning flex-shrink-0 mt-0.5" />
            <div>
              <p className="text-warning text-sm font-medium">Real Money Warning</p>
              <p className="text-neutral text-xs mt-1">
                Live mode places REAL orders on the exchange with REAL funds.
                All risk rules still apply but mistakes cannot be undone.
              </p>
            </div>
          </div>
          <p className="text-sm text-gray-300">
            Are you absolutely sure you want to switch to live trading?
          </p>
          <div className="flex gap-2">
            <button
              onClick={switchToLive}
              className="btn flex-1 bg-warning/20 text-warning border border-warning/30 hover:bg-warning/30 justify-center"
            >
              Yes, activate live trading
            </button>
            <button onClick={() => setShowLiveConfirm(false)} className="btn-ghost flex-1 justify-center">
              Cancel
            </button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
