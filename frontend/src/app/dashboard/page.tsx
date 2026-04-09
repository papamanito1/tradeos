"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import {
  DollarSign, TrendingUp, TrendingDown, Activity,
  Layers, BarChart2, ShieldCheck, RefreshCw, WifiOff,
  Zap, ArrowUpRight, ArrowDownRight, RotateCcw, Bell,
} from "lucide-react";
import { overviewApi } from "@/lib/api";
import { Overview } from "@/types";
import { KPICard } from "@/components/dashboard/KPICard";
import { formatUSD, formatPct, pnlColor, sideColor, statusColor, timeAgo, cn } from "@/lib/utils";
import { useWebSocket } from "@/hooks/useWebSocket";

// ─── Types ────────────────────────────────────────────────────────────────────
interface LivePrice { last: number; change_pct: number; updated_ms: number; }

interface ActivityEvent {
  kind: "signal" | "trade";
  timestamp: string;
  symbol: string;
  direction: "long" | "short";
  entry?: number;
  fill_price?: number;
  amount?: number;
  sl?: number | null;
  tp?: number | null;
  confidence?: number;
  strategy_name?: string;
  reasoning?: string;
  timeframe?: string;
  side?: string;
}

interface Toast {
  id: number;
  kind: "signal" | "trade" | "info";
  direction?: "long" | "short";
  symbol: string;
  price: number;
  strategy?: string;
  message?: string;
}

// ─── Live Price Ticker ────────────────────────────────────────────────────────
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
          <span className="text-[11px] font-mono w-14 text-right font-semibold"
            style={{ color: up ? "#30d158" : "#ff453a" }}>
            {up ? "+" : ""}{price.change_pct.toFixed(2)}%
          </span>
        </div>
      </div>
    </div>
  );
}

// ─── Toast Notification ───────────────────────────────────────────────────────
function ToastNotification({ toast, onDismiss }: { toast: Toast; onDismiss: () => void }) {
  useEffect(() => {
    const t = setTimeout(onDismiss, 8000);
    return () => clearTimeout(t);
  }, [onDismiss]);

  const isLong = toast.direction === "long";
  const isTrade = toast.kind === "trade";

  return (
    <div
      className="flex items-start gap-3 p-3 rounded-xl border backdrop-blur-sm shadow-2xl animate-slide-in"
      style={{
        background: isTrade
          ? isLong ? "rgba(48,209,88,0.10)" : "rgba(255,69,58,0.10)"
          : "rgba(10,132,255,0.10)",
        borderColor: isTrade
          ? isLong ? "rgba(48,209,88,0.25)" : "rgba(255,69,58,0.25)"
          : "rgba(10,132,255,0.25)",
        minWidth: 280,
      }}
    >
      <div className="mt-0.5">
        {isTrade ? (
          isLong
            ? <ArrowUpRight size={16} style={{ color: "#30d158" }} />
            : <ArrowDownRight size={16} style={{ color: "#ff453a" }} />
        ) : (
          <Zap size={16} style={{ color: "#0a84ff" }} />
        )}
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-[11px] font-bold uppercase tracking-wide"
            style={{ color: isTrade ? (isLong ? "#30d158" : "#ff453a") : "#0a84ff" }}>
            {isTrade ? (isLong ? "▲ LONG FILLED" : "▼ SHORT FILLED") : "⚡ SIGNAL"}
          </span>
          <span className="text-[11px] text-neutral-400">{toast.symbol.replace("/USDT", "")}</span>
        </div>
        <div className="text-[13px] font-mono font-semibold text-white mt-0.5">
          {formatUSD(toast.price)}
        </div>
        {toast.strategy && (
          <div className="text-[10px] text-neutral-500 mt-0.5 truncate">{toast.strategy}</div>
        )}
      </div>
      <button onClick={onDismiss} className="text-neutral-600 hover:text-neutral-400 text-xs mt-0.5">✕</button>
    </div>
  );
}

// ─── Toast Container ──────────────────────────────────────────────────────────
function ToastContainer({ toasts, onDismiss }: { toasts: Toast[]; onDismiss: (id: number) => void }) {
  return (
    <div className="fixed bottom-6 right-6 z-50 flex flex-col gap-2 pointer-events-none">
      {toasts.map(t => (
        <div key={t.id} className="pointer-events-auto">
          <ToastNotification toast={t} onDismiss={() => onDismiss(t.id)} />
        </div>
      ))}
    </div>
  );
}

// ─── Activity Event Row ───────────────────────────────────────────────────────
function ActivityRow({ ev }: { ev: ActivityEvent }) {
  const isLong = ev.direction === "long";
  const isTrade = ev.kind === "trade";
  const ts = new Date(ev.timestamp);
  const timeStr = ts.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });

  return (
    <div
      className="flex items-start gap-3 px-4 py-2.5 border-b last:border-0 hover:bg-white/[0.02] transition-colors"
      style={{ borderColor: "rgba(255,255,255,0.04)" }}
    >
      {/* Icon */}
      <div className={`mt-0.5 w-5 h-5 rounded-full flex items-center justify-center flex-shrink-0 ${
        isTrade
          ? isLong ? "bg-green-500/15" : "bg-red-500/15"
          : "bg-blue-500/15"
      }`}>
        {isTrade
          ? isLong
            ? <ArrowUpRight size={11} className="text-green-400" />
            : <ArrowDownRight size={11} className="text-red-400" />
          : <Zap size={11} className="text-blue-400" />
        }
      </div>

      {/* Content */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className={`text-[11px] font-bold ${isLong ? "text-green-400" : "text-red-400"}`}>
            {isLong ? "▲ LONG" : "▼ SHORT"}
          </span>
          <span className="text-[12px] font-semibold text-white">{ev.symbol.replace("/USDT", "")}</span>
          <span className="text-[10px] text-neutral-600">{ev.timeframe}</span>
          {isTrade && (
            <span className="text-[10px] bg-blue-500/10 border border-blue-500/20 text-blue-400 px-1.5 py-0.5 rounded font-medium">
              PAPER
            </span>
          )}
        </div>
        <div className="flex items-center gap-3 mt-0.5 text-[11px] font-mono">
          <span className="text-neutral-300">
            {formatUSD(isTrade ? (ev.fill_price || 0) : (ev.entry || 0))}
          </span>
          {ev.sl && ev.sl > 0 && <span className="text-red-400">SL {formatUSD(ev.sl)}</span>}
          {ev.tp && ev.tp > 0 && <span className="text-green-400">TP {formatUSD(ev.tp)}</span>}
          {isTrade && ev.amount && (
            <span className="text-neutral-500">{ev.amount.toFixed(5)} {ev.symbol.split("/")[0]}</span>
          )}
        </div>
        {ev.strategy_name && (
          <div className="text-[10px] text-neutral-600 mt-0.5 truncate">{ev.strategy_name}</div>
        )}
        {!isTrade && ev.reasoning && (
          <div className="text-[10px] text-neutral-700 mt-0.5 line-clamp-1">{ev.reasoning}</div>
        )}
      </div>

      {/* Confidence + time */}
      <div className="text-right flex-shrink-0">
        {!isTrade && ev.confidence !== undefined && (
          <div className="text-[10px] text-neutral-500 mb-0.5">{(ev.confidence * 100).toFixed(0)}%</div>
        )}
        <div className="text-[10px] font-mono text-neutral-600">{timeStr}</div>
      </div>
    </div>
  );
}

// ─── Reset Button ─────────────────────────────────────────────────────────────
function ResetPaperButton({ onReset }: { onReset: () => void }) {
  const [loading, setLoading] = useState(false);
  const [done, setDone] = useState(false);

  const handleReset = async () => {
    if (!confirm("Reset paper account to $10,000? All open positions and orders will be cleared.")) return;
    setLoading(true);
    try {
      const token = localStorage.getItem("token");
      const resp = await fetch(
        `${process.env.NEXT_PUBLIC_API_URL || ""}/api/paper/reset`,
        { method: "POST", headers: { Authorization: `Bearer ${token}` } }
      );
      if (resp.ok) { setDone(true); setTimeout(() => setDone(false), 3000); onReset(); }
    } finally { setLoading(false); }
  };

  return (
    <button
      onClick={handleReset}
      disabled={loading}
      className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-[11px] font-medium transition-all border"
      style={{
        background: done ? "rgba(48,209,88,0.1)" : "rgba(255,255,255,0.04)",
        borderColor: done ? "rgba(48,209,88,0.3)" : "rgba(255,255,255,0.06)",
        color: done ? "#30d158" : "#5a5a7a",
      }}
    >
      <RotateCcw size={10} className={loading ? "animate-spin" : ""} />
      {done ? "Reset!" : "Reset $10k"}
    </button>
  );
}

// ─── Page ─────────────────────────────────────────────────────────────────────
export default function OverviewPage() {
  const [data, setData] = useState<Overview | null>(null);
  const [loading, setLoading] = useState(true);
  const [lastUpdate, setLastUpdate] = useState<Date | null>(null);
  const [livePrices, setLivePrices] = useState<Record<string, LivePrice>>({});
  const [streamConnected, setStreamConnected] = useState(false);
  const [activity, setActivity] = useState<ActivityEvent[]>([]);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const toastIdRef = useRef(0);

  const addToast = useCallback((t: Omit<Toast, "id">) => {
    const id = ++toastIdRef.current;
    setToasts(prev => [...prev.slice(-4), { ...t, id }]);
  }, []);

  const dismissToast = useCallback((id: number) => {
    setToasts(prev => prev.filter(t => t.id !== id));
  }, []);

  const fetchData = useCallback(async () => {
    try {
      const res = await overviewApi.get();
      setData(res);
      setLastUpdate(new Date());
    } catch { /* silent */ } finally { setLoading(false); }
  }, []);

  const fetchActivity = useCallback(async () => {
    try {
      const token = localStorage.getItem("token");
      const resp = await fetch(
        `${process.env.NEXT_PUBLIC_API_URL || ""}/api/paper/activity?limit=40`,
        { headers: { Authorization: `Bearer ${token}` } }
      );
      if (resp.ok) setActivity(await resp.json());
    } catch { /* silent */ }
  }, []);

  useEffect(() => {
    fetchData();
    fetchActivity();
    const iv1 = setInterval(fetchData, 15_000);
    const iv2 = setInterval(fetchActivity, 20_000);
    return () => { clearInterval(iv1); clearInterval(iv2); };
  }, [fetchData, fetchActivity]);

  const { lastMessage, connected } = useWebSocket();
  useEffect(() => { setStreamConnected(connected); }, [connected]);

  useEffect(() => {
    if (!lastMessage) return;
    const { type, data: msgData } = lastMessage as { type?: string; data?: Record<string, unknown> };

    if (type === "market:ticker" && msgData?.symbol) {
      setLivePrices(prev => ({
        ...prev,
        [msgData.symbol as string]: {
          last: msgData.last as number,
          change_pct: msgData.change_pct as number,
          updated_ms: Date.now(),
        },
      }));
    }

    // Signal fired — show toast + refresh activity
    if (type === "signal:new" && msgData) {
      const dir = msgData.direction as string;
      if (dir === "long" || dir === "short") {
        addToast({
          kind: "signal",
          direction: dir,
          symbol: msgData.symbol as string,
          price: (msgData.entry as number) || 0,
          strategy: msgData.strategy_name as string,
        });
        fetchActivity();
      }
    }

    // Trade executed — show toast + refresh overview + activity
    if (type === "execution:order_placed" && msgData && msgData.event !== "paper_reset") {
      const side = msgData.side as string;
      addToast({
        kind: "trade",
        direction: side === "buy" ? "long" : "short",
        symbol: msgData.symbol as string,
        price: (msgData.fill_price as number) || 0,
        strategy: "Paper Trade Executed",
      });
      fetchData();
      fetchActivity();
    }

    if (type && ["risk:position_closed", "snapshot"].includes(type)) {
      fetchData();
      fetchActivity();
    }
  }, [lastMessage, fetchData, fetchActivity, addToast]);

  if (loading) return (
    <div className="flex items-center justify-center h-64">
      <div className="w-5 h-5 border-2 border-t-transparent rounded-full animate-spin"
        style={{ borderColor: "rgba(10,132,255,0.2) rgba(10,132,255,0.2) rgba(10,132,255,0.2) #0a84ff" }} />
    </div>
  );

  const d = data!;

  return (
    <>
      {/* ── Toast overlay ── */}
      <ToastContainer toasts={toasts} onDismiss={dismissToast} />

      <div className="space-y-4">
        {/* Kill switch banner */}
        {d?.kill_switch_active && (
          <div className="flex items-center gap-3 px-5 py-4 rounded-apple"
            style={{ background: "rgba(255,69,58,0.08)", border: "1px solid rgba(255,69,58,0.2)" }}>
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
              <span className="w-2 h-2 rounded-full flex-shrink-0" style={{
                background: d?.exchange_connected ? "#30d158" : "#ff453a",
                boxShadow: d?.exchange_connected
                  ? "0 0 0 3px rgba(48,209,88,0.12)"
                  : "0 0 0 3px rgba(255,69,58,0.12)",
              }} />
              <span className="text-[14px] font-semibold text-white">
                {d?.exchange_connected ? "Connected" : "Offline"}
              </span>
            </div>
            <div className="text-[12px]" style={{ color: "#3a3a52" }}>Binance · Public</div>
          </div>

          {/* Mode card */}
          <div className="card p-5">
            <div className="label mb-3">Trading Mode</div>
            <div className="text-[18px] font-bold uppercase" style={{
              color: d?.trading_mode === "live" ? "#ffd60a" : "#0a84ff",
              letterSpacing: "0.04em",
            }}>
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
                <span className="w-[5px] h-[5px] rounded-full"
                  style={{ background: "#30d158", boxShadow: "0 0 0 2px rgba(48,209,88,0.2)", animation: "pulse 2s ease infinite" }} />
                Streaming
              </span>
            ) : (
              <span className="flex items-center gap-1.5 text-[11px]" style={{ color: "#3a3a52" }}>
                <WifiOff size={10} /> Connecting
              </span>
            )}
          </div>
          <div className="grid grid-cols-5 gap-6">
            {["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "ADA/USDT"].map(sym => (
              <LivePriceTicker key={sym} symbol={sym} price={livePrices[sym] || null} />
            ))}
          </div>
        </div>

        {/* Bottom panels — 3 columns: positions | orders | live activity */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
          {/* Open positions */}
          <div className="card overflow-hidden">
            <div className="flex items-center justify-between px-5 py-3.5"
              style={{ borderBottom: "1px solid rgba(255,255,255,0.05)" }}>
              <span className="text-[13px] font-semibold text-white">Open Positions</span>
              <button onClick={fetchData} className="transition-all duration-150 rounded-lg p-1.5"
                style={{ color: "#3a3a52" }}
                onMouseEnter={e => { (e.currentTarget as HTMLElement).style.color = "#fff"; }}
                onMouseLeave={e => { (e.currentTarget as HTMLElement).style.color = "#3a3a52"; }}>
                <RefreshCw size={12} />
              </button>
            </div>
            {!d?.positions?.length ? (
              <p className="px-5 py-10 text-center text-[13px]" style={{ color: "#2a2a3e" }}>No open positions</p>
            ) : (
              <table className="w-full text-[13px]">
                <thead>
                  <tr style={{ borderBottom: "1px solid rgba(255,255,255,0.04)" }}>
                    {["Symbol", "Side", "Entry", "PnL"].map((h, i) => (
                      <th key={h} className={`${i > 1 ? "text-right" : "text-left"} px-4 py-2.5 label`}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {d.positions.map(p => (
                    <tr key={p.id} className="table-row-hover" style={{ borderBottom: "1px solid rgba(255,255,255,0.03)" }}>
                      <td className="px-4 py-2.5 font-semibold text-white text-[12px]">{p.symbol.replace("/USDT", "")}</td>
                      <td className={cn("px-4 py-2.5 uppercase font-bold text-[10px]", sideColor(p.side))}>{p.side}</td>
                      <td className="px-4 py-2.5 text-right font-mono text-[11px]" style={{ color: "#e8e8f0" }}>
                        {p.entry_price.toFixed(0)}
                      </td>
                      <td className={cn("px-4 py-2.5 text-right font-mono font-semibold text-[11px]", pnlColor(p.unrealized_pnl))}>
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
                    {["Symbol", "Side", "Status", "Time"].map((h, i) => (
                      <th key={h} className={`${i === 3 ? "text-right" : "text-left"} px-4 py-2.5 label`}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {d.recent_orders.map(o => (
                    <tr key={o.id} className="table-row-hover" style={{ borderBottom: "1px solid rgba(255,255,255,0.03)" }}>
                      <td className="px-4 py-2.5 font-semibold text-white text-[12px]">{o.symbol.replace("/USDT", "")}</td>
                      <td className={cn("px-4 py-2.5 uppercase font-bold text-[10px]", sideColor(o.side))}>{o.side}</td>
                      <td className={cn("px-4 py-2.5 uppercase font-semibold text-[10px]", statusColor(o.status))}>{o.status}</td>
                      <td className="px-4 py-2.5 text-right text-[11px]" style={{ color: "#3a3a52" }}>{timeAgo(o.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>

          {/* Live Activity Feed */}
          <div className="card overflow-hidden flex flex-col">
            <div className="flex items-center justify-between px-5 py-3.5 flex-shrink-0"
              style={{ borderBottom: "1px solid rgba(255,255,255,0.05)" }}>
              <div className="flex items-center gap-2">
                <Bell size={13} style={{ color: "#5a5a7a" }} />
                <span className="text-[13px] font-semibold text-white">Live Activity</span>
                {activity.length > 0 && (
                  <span className="text-[10px] bg-blue-500/10 border border-blue-500/20 text-blue-400 px-1.5 py-0.5 rounded font-mono">
                    {activity.length}
                  </span>
                )}
              </div>
              <ResetPaperButton onReset={() => { fetchData(); fetchActivity(); }} />
            </div>

            <div className="overflow-y-auto flex-1 max-h-80">
              {activity.length === 0 ? (
                <div className="px-5 py-10 text-center">
                  <Zap size={20} className="text-neutral-700 mx-auto mb-2" />
                  <p className="text-[13px]" style={{ color: "#2a2a3e" }}>Waiting for signals…</p>
                  <p className="text-[11px] mt-1" style={{ color: "#1e1e2e" }}>
                    Strategies run every 60s
                  </p>
                </div>
              ) : (
                activity.map((ev, i) => <ActivityRow key={i} ev={ev} />)
              )}
            </div>
          </div>
        </div>

        {lastUpdate && (
          <p className="text-[11px] text-right" style={{ color: "#1e1e2e" }}>
            Updated {lastUpdate.toLocaleTimeString()}
          </p>
        )}
      </div>
    </>
  );
}
