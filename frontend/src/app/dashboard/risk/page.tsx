"use client";

import { useEffect, useState } from "react";
import { riskApi } from "@/lib/api";
import { RiskSettings } from "@/types";
import { Power, ShieldCheck, X } from "lucide-react";
import { cn } from "@/lib/utils";

export default function RiskPage() {
  const [settings, setSettings] = useState<RiskSettings | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [newSymbol, setNewSymbol] = useState("");

  useEffect(() => {
    riskApi.getSettings().then(setSettings);
  }, []);

  async function save() {
    if (!settings) return;
    setSaving(true);
    await riskApi.updateSettings({
      max_daily_loss_usd: settings.max_daily_loss_usd,
      max_daily_loss_pct: settings.max_daily_loss_pct,
      max_position_size_usd: settings.max_position_size_usd,
      max_position_size_pct: settings.max_position_size_pct,
      max_leverage: settings.max_leverage,
      max_open_trades: settings.max_open_trades,
      max_symbol_exposure_pct: settings.max_symbol_exposure_pct,
      cooldown_after_losses: settings.cooldown_after_losses,
      cooldown_minutes: settings.cooldown_minutes,
      circuit_breaker_enabled: settings.circuit_breaker_enabled,
      circuit_breaker_threshold_pct: settings.circuit_breaker_threshold_pct,
      symbol_blacklist: settings.symbol_blacklist,
    });
    setSaving(false);
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  }

  async function toggleKillSwitch() {
    if (!settings) return;
    const msg = settings.kill_switch_active
      ? "Deactivate kill switch? Trading will resume."
      : "ACTIVATE KILL SWITCH? All trading will halt immediately.";
    if (!confirm(msg)) return;
    const res = await riskApi.toggleKillSwitch(!settings.kill_switch_active);
    setSettings((s) => s ? { ...s, kill_switch_active: res.kill_switch_active } : s);
  }

  function field(key: keyof RiskSettings, label: string, type: "number" | "checkbox" = "number", step = "0.01") {
    if (!settings) return null;
    if (type === "checkbox") {
      return (
        <label key={key} className="flex items-center gap-3 cursor-pointer">
          <input
            type="checkbox"
            checked={settings[key] as boolean}
            onChange={(e) => setSettings({ ...settings, [key]: e.target.checked })}
            className="w-4 h-4"
          />
          <span className="text-sm text-gray-200">{label}</span>
        </label>
      );
    }
    return (
      <div key={key}>
        <label className="label block mb-1">{label}</label>
        <input
          className="input"
          type="number"
          step={step}
          value={settings[key] as number}
          onChange={(e) => setSettings({ ...settings, [key]: Number(e.target.value) })}
        />
      </div>
    );
  }

  if (!settings) return (
    <div className="flex items-center justify-center h-64">
      <div className="w-5 h-5 border-2 border-accent border-t-transparent rounded-full animate-spin" />
    </div>
  );

  return (
    <div className="space-y-4 max-w-3xl">
      {/* Kill switch banner */}
      <div className={cn(
        "rounded-lg px-4 py-4 border flex items-center justify-between",
        settings.kill_switch_active
          ? "bg-loss/20 border-loss/30"
          : "bg-surface-800 border-surface-700"
      )}>
        <div className="flex items-center gap-3">
          <ShieldCheck className={cn("w-5 h-5", settings.kill_switch_active ? "text-loss" : "text-neutral")} />
          <div>
            <div className={cn("font-medium text-sm", settings.kill_switch_active ? "text-loss" : "text-gray-200")}>
              {settings.kill_switch_active ? "Kill Switch ACTIVE — All trading halted" : "Kill Switch Inactive"}
            </div>
            <div className="text-xs text-neutral mt-0.5">
              Immediately stops all order placement when activated
            </div>
          </div>
        </div>
        <button
          onClick={toggleKillSwitch}
          className={cn(
            "flex items-center gap-2 px-4 py-2 rounded text-sm font-medium transition-colors",
            settings.kill_switch_active
              ? "bg-profit/20 text-profit hover:bg-profit/30 border border-profit/30"
              : "bg-loss/20 text-loss hover:bg-loss/30 border border-loss/30"
          )}
        >
          <Power className="w-4 h-4" />
          {settings.kill_switch_active ? "Deactivate" : "Activate"}
        </button>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Loss Limits */}
        <div className="card p-4">
          <h3 className="text-sm font-semibold text-white mb-4">Loss Limits</h3>
          <div className="space-y-3">
            {field("max_daily_loss_usd", "Max Daily Loss (USD)", "number", "10")}
            {field("max_daily_loss_pct", "Max Daily Loss (%)", "number", "0.1")}
            {field("circuit_breaker_threshold_pct", "Circuit Breaker Threshold (%)", "number", "0.1")}
            {field("circuit_breaker_enabled", "Circuit Breaker Enabled", "checkbox")}
          </div>
        </div>

        {/* Position Sizing */}
        <div className="card p-4">
          <h3 className="text-sm font-semibold text-white mb-4">Position Sizing</h3>
          <div className="space-y-3">
            {field("max_position_size_usd", "Max Position Size (USD)", "number", "10")}
            {field("max_position_size_pct", "Max Position Size (% of balance)", "number", "0.1")}
            {field("max_leverage", "Max Leverage (x)", "number", "0.5")}
            {field("max_open_trades", "Max Open Trades", "number", "1")}
            {field("max_symbol_exposure_pct", "Max Symbol Exposure (%)", "number", "0.5")}
          </div>
        </div>

        {/* Cooldown */}
        <div className="card p-4">
          <h3 className="text-sm font-semibold text-white mb-4">Cooldown Rules</h3>
          <div className="space-y-3">
            {field("cooldown_after_losses", "Consecutive Losses Before Cooldown", "number", "1")}
            {field("cooldown_minutes", "Cooldown Duration (minutes)", "number", "5")}
          </div>
        </div>

        {/* Blacklist */}
        <div className="card p-4">
          <h3 className="text-sm font-semibold text-white mb-4">Symbol Blacklist</h3>
          <div className="flex gap-2 mb-3">
            <input
              className="input"
              placeholder="e.g. DOGE/USDT"
              value={newSymbol}
              onChange={(e) => setNewSymbol(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && newSymbol) {
                  setSettings({ ...settings, symbol_blacklist: [...settings.symbol_blacklist, newSymbol.toUpperCase()] });
                  setNewSymbol("");
                }
              }}
            />
            <button
              className="btn-ghost flex-shrink-0"
              onClick={() => {
                if (newSymbol) {
                  setSettings({ ...settings, symbol_blacklist: [...settings.symbol_blacklist, newSymbol.toUpperCase()] });
                  setNewSymbol("");
                }
              }}
            >
              Add
            </button>
          </div>
          <div className="flex flex-wrap gap-1.5">
            {settings.symbol_blacklist.length === 0 && (
              <span className="text-neutral text-xs">No symbols blacklisted</span>
            )}
            {settings.symbol_blacklist.map((sym) => (
              <span key={sym} className="flex items-center gap-1 bg-loss/10 text-loss border border-loss/20 rounded px-2 py-0.5 text-[11px]">
                {sym}
                <button
                  onClick={() => setSettings({
                    ...settings,
                    symbol_blacklist: settings.symbol_blacklist.filter((s) => s !== sym),
                  })}
                >
                  <X className="w-2.5 h-2.5" />
                </button>
              </span>
            ))}
          </div>
        </div>
      </div>

      <div className="flex items-center gap-3">
        <button onClick={save} disabled={saving} className="btn-primary px-6">
          {saving ? "Saving..." : saved ? "Saved ✓" : "Save Settings"}
        </button>
        <p className="text-neutral text-xs">Changes take effect immediately — risk engine reloads</p>
      </div>
    </div>
  );
}
