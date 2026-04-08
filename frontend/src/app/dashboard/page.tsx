"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import {
  DollarSign, TrendingUp, TrendingDown, Activity,
  Layers, BarChart2, ShieldCheck, RefreshCw, WifiOff,
} from "lucide-react";
import { overviewApi } from "@/lib/api";
import { Overview } from "@/types";
import { KPICard } from "@/components/dashboard/KPICard";
import { formatUSD, formatPct, pnlColor, sideColor, statusColor, timeAgo, cn } from "@/lib/utils";
import { useWebSocket } from "@/hooks/useWebSocket";

interface LivePrice { last: number; change_pct: number; updated_ms: number; }

function LivePriceTicker({ symbol, price }: { symbol: string; price: LivePrice | null }) {
  const prevRef = useRef<number | null>(null);
  const [flash, setFlash] = useState<"up" | "down" | null>(null);

  useEffect(() => {
    if (!price) return;
    if (prevRef.current !== null && price.last !== prevRef.current) {
      setFlash(price.last > prevRef.current ? "up" : "down");
      const t = setTimeout(() => setFlash(null), 500);
      prevRef.current = price.last;
      return () => clearTimeout(t);
    }
    prevRef.current = price.last;
  }, [price?.last]);

  if (!price) return null;
  const up = price.change_pct >= 0;

  return (
    <div style={{ borderBottom: "1px solid rgba(255,255,255,0.04)" }} className="py-1.5 last:border-0">
      <div className="flex items-center justify-between">
        <span className="text-[12px] font-semibold" style={{ color: "#5a5a7a" }}>
          {symbol.replace("/USDT", "")}
        </span>
        <div className="flex items-center gap-3">
          <span
            className="text-[13px] font-mono font-semibold transition-colors duration-300"
            style={{ color: flash === "up" ? "#30d158" : flash === "down" ? "#ff453a" : "#f0f0f8" }}
          >
            {price.last > 1 ? formatUSD(price.last) : `$${price.last.toFixed(4)}`}
          </span>
          <span
            className="text-[11px] font-mono w-14 text-right font-semibold"
            style={{ color: up ? "#30d158" : "#ff453a" }}
          >
            {up ? "+" : ""}{price.change_pct.toFixed(2)}%
          </span>
        </div>
      </div>
    </div>
  );
}

export default function OverviewPage() {
  const [data, setData] = useState<Overview | null>(null);
  const [loading, setLoading] = useState(true);
  const [lastUpdate, setLastUpdate] = useState<Date | null>(null);
  const [livePrices, setLivePrices] = useState<Record<string, LivePrice>>({});
  const [streamConnected, setStreamConnected] = useState(false);

  const fetchData = useCallback(async () => {
    try {
      const res = await overviewApi.get();
      setData(res);
      setLastUpdate(new Date());
    } catch { /* silent */ } finally { setLoading(false); }
  }, []);

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 15000);
    return () => clearInterval(interval);
  }, [fetchData]);

  const { lastMessage, connected } = useWebSocket();
  useEffect(() => { setStreamConnected(connected); }, [connected]);

  useEffect(() => {
    if (!lastMessage) return;
    const { type, data: msgData } = lastMessage;
    if (type === "market:ticker" && msgData?.symbol) {
      setLivePrices((prev) => ({
        ...prev,
        [msgData.symbol]: { last: msgData.last, change_pct: msgData.change_pct, updated_ms: msgData.updated_ms },
      }));
    }
    if (type && ["execution:order_placed", "risk:position_closed", "snapshot"].includes(type)) fetchData();
  }, [lastMessage, fetchData]);

  if (loading) return (
    <div className="flex items-center justify-center h-64">
      <div
        className="w-5 h-5 border-2 border-t-transparent rounded-full animate-spin"
        style={{ borderColor: "rgba(10,132,255,0.2) rgba(10,132,255,0.2) rgba(10,132,255,0.2) #0a84ff" }}
      />
    </div>
  );

  const d = data!;

  return (
    <div className="space-y-4">
      {/* Kill switch banner */}
      {d?.kill_switch_active && (
        <div
          className="flex items-center gap-3 px-5 py-4 rounded-apple"
          style={{ background: "rgba(255,69,58,0.08)", border: "1px solid rgba(255,69,58,0.2)" }}
        >
          <ShieldCheck size={16} style={{ color: "#ff453a", flexShrink: 0 }} />
          <span className="text-[14px] font-semibold" style={{ color: "#ff453a" }}>
            Emergency Stop Active — All trading halted
          </span>
        </div>
      )}

      {/* KPI row 1 */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <KPICard label="Total Equity" value={formatUSD(d?.equity || 0)}
          subValue={`Available ${formatUSD(d?.available_balance || 0)}`} icon={DollarSign} trend="neutral" />
        <KPICard label="Daily PnL" value={formatUSD(d?.daily_pnl || 0)}
          subValue={formatPct(d?.daily_pnl_pct || 0)} subColor={pnlColor(d?.daily_pnl || 0)}
          icon={d?.daily_pnl >= 0 ? TrendingUp : TrendingDown} trend={d?.daily_pnl >= 0 ? "up" : "down"} />
        <KPICard label="Unrealized PnL" value={formatUSD(d?.unrealized_pnl || 0)}
          subValue={`${d?.open_positions || 0} open`} subColor={pnlColor(d?.unrealized_pnl || 0)}
          icon={Activity} trend={d?.unrealized_pnl >= 0 ? "up" : "down"} />
        <KPICard label="Win Rate" value={`${(d?.win_rate || 0).toFixed(1)}%`}
          subValue={`${d?.total_trades || 0} trades`} icon={BarChart2} />
      </div>

      {/* KPI row 2 */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <KPICard label="Active Strategies" value={String(d?.active_strategies || 0)} icon={Layers} />
        <KPICard label="Open Positions" value={String(d?.open_positions || 0)} icon={TrendingUp} />

        {/* Exchange card */}
        <div className="card p-5">
          <div className="label mb-3">Exchange</div>
          <div className="flex items-center gap-2 mb-1">
            <span
              className="w-2 h-2 rounded-full flex-shrink-0"
              style={{
                background: d?.exchange_connected ? "#30d158" : "#ff453a",
                boxShadow: d?.exchange_connected ? "0 0 0 3px rgba(48,209,88,0.12)" : "0 0 0 3px rgba(255,69,58,0.12)",
              }}
            />
            <span className="text-[14px] font-semibold text-white">
              {d?.exchange_connected ? "Connected" : "Offline"}
            </span>
          </div>
          <div className="text-[12px]" style={{ color: "#3a3a52" }}>Binance · Public</div>
        </div>

        {/* Mode card */}
        <div className="card p-5">
          <div className="label mb-3">Trading Mode</div>
          <div
            className="text-[18px] font-bold uppercase"
            style={{ color: d?.trading_mode === "live" ? "#ffd60a" : "#0a84ff", letterSpacing: "0.04em" }}
          >
            {d?.trading_mode || "paper"}
          </div>
          <div className="text-[12px] mt-1" style={{ color: "#3a3a52" }}>
            {d?.trading_mode === "paper" ? "Simulated · Safe" : "Real orders · Caution"}
          </div>
        </div>
      </div>

      {/* Live Market strip */}
      <div className="card px-5 py-4">
        <div className="flex items-center justify-between mb-3">
          <span className="label">Live Market</span>
          {streamConnected ? (
            <span className="flex items-center gap-1.5 text-[11px] font-semibold" style={{ color: "#30d158" }}>
              <span
                className="w-[5px] h-[5px] rounded-full"
                style={{ background: "#30d158", boxShadow: "0 0 0 2px rgba(48,209,88,0.2)", animation: "pulse 2s ease infinite" }}
              />
              Streaming
            </span>
          ) : (
            <span className="flex items-center gap-1.5 text-[11px]" style={{ color: "#3a3a52" }}>
              <WifiOff size={10} /> Connecting
            </span>
          )}
        </div>
        <div className="grid grid-cols-5 gap-6">
          {["BTC/USDT","ETH/USDT","SOL/USDT","BNB/USDT","ADA/USDT"].map((sym) => (
            <LivePriceTicker key={sym} symbol={sym} price={livePrices[sym] || null} />
          ))}
        </div>
      </div>

      {/* Bottom panels */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Open positions */}
        <div className="card overflow-hidden">
          <div
            className="flex items-center justify-between px-5 py-3.5"
            style={{ borderBottom: "1px solid rgba(255,255,255,0.05)" }}
          >
            <span className="text-[13px] font-semibold text-white">Open Positions</span>
            <button
              onClick={fetchData}
              className="transition-all duration-150 rounded-lg p-1.5"
              style={{ color: "#3a3a52" }}
              onMouseEnter={(e) => { const el = e.currentTarget as HTMLElement; el.style.color = "#fff"; el.style.background = "rgba(255,255,255,0.06)"; }}
              onMouseLeave={(e) => { const el = e.currentTarget as HTMLElement; el.style.color = "#3a3a52"; el.style.background = ""; }}
            >
              <RefreshCw size={12} />
            </button>
          </div>
          {!d?.positions?.length ? (
            <p className="px-5 py-10 text-center text-[13px]" style={{ color: "#2a2a3e" }}>No open positions</p>
          ) : (
            <table className="w-full text-[13px]">
              <thead>
                <tr style={{ borderBottom: "1px solid rgba(255,255,255,0.04)" }}>
                  {["Symbol","Side","Entry","PnL"].map((h, i) => (
                    <th key={h} className={`${i > 1 ? "text-right" : "text-left"} px-5 py-2.5 label`}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {d.positions.map((p) => (
                  <tr key={p.id} className="table-row-hover" style={{ borderBottom: "1px solid rgba(255,255,255,0.03)" }}>
                    <td className="px-5 py-3 font-semibold text-white">{p.symbol}</td>
                    <td className={cn("px-5 py-3 uppercase font-bold text-[11px]", sideColor(p.side))}>{p.side}</td>
                    <td className="px-5 py-3 text-right font-numeric" style={{ color: "#e8e8f0" }}>{p.entry_price.toFixed(2)}</td>
                    <td className={cn("px-5 py-3 text-right font-numeric font-semibold", pnlColor(p.unrealized_pnl))}>
                      {formatUSD(p.unrealized_pnl)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        {/* Recent orders */}
        <div className="card overflow-hidden">
          <div className="px-5 py-3.5" style={{ borderBottom: "1px solid rgba(255,255,255,0.05)" }}>
            <span className="text-[13px] font-semibold text-white">Recent Orders</span>
          </div>
          {!d?.recent_orders?.length ? (
            <p className="px-5 py-10 text-center text-[13px]" style={{ color: "#2a2a3e" }}>No recent orders</p>
          ) : (
            <table className="w-full text-[13px]">
              <thead>
                <tr style={{ borderBottom: "1px solid rgba(255,255,255,0.04)" }}>
                  {["Symbol","Side","Status","Time"].map((h, i) => (
                    <th key={h} className={`${i === 3 ? "text-right" : "text-left"} px-5 py-2.5 label`}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {d.recent_orders.map((o) => (
                  <tr key={o.id} className="table-row-hover" style={{ borderBottom: "1px solid rgba(255,255,255,0.03)" }}>
                    <td className="px-5 py-3 font-semibold text-white">{o.symbol}</td>
                    <td className={cn("px-5 py-3 uppercase font-bold text-[11px]", sideColor(o.side))}>{o.side}</td>
                    <td className={cn("px-5 py-3 uppercase font-semibold text-[11px]", statusColor(o.status))}>{o.status}</td>
                    <td className="px-5 py-3 text-right text-[12px]" style={{ color: "#3a3a52" }}>{timeAgo(o.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>

      {lastUpdate && (
        <p className="text-[11px] text-right" style={{ color: "#1e1e2e" }}>
          Updated {lastUpdate.toLocaleTimeString()}
        </p>
      )}
    </div>
  );
}
