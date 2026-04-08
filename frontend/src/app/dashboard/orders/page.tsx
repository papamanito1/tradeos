"use client";

import { useEffect, useState } from "react";
import { ordersApi } from "@/lib/api";
import { Order } from "@/types";
import { cn, formatUSD, sideColor, statusColor, timeAgo } from "@/lib/utils";
import { Modal } from "@/components/ui/Modal";
import { Plus, XCircle } from "lucide-react";

const STATUSES = ["all", "open", "filled", "cancelled", "error"];

export default function OrdersPage() {
  const [orders, setOrders] = useState<Order[]>([]);
  const [loading, setLoading] = useState(true);
  const [status, setStatus] = useState("all");
  const [showManual, setShowManual] = useState(false);
  const [form, setForm] = useState({
    symbol: "BTC/USDT", side: "buy", order_type: "market", amount: 0.001, price: "",
  });

  async function load() {
    setLoading(true);
    const data = await ordersApi.list({ status: status === "all" ? undefined : status, limit: 100 });
    setOrders(data);
    setLoading(false);
  }

  useEffect(() => { load(); }, [status]);

  async function cancelOrder(id: number) {
    await ordersApi.cancel(id);
    load();
  }

  async function placeManualOrder() {
    await ordersApi.placeManual({
      symbol: form.symbol, side: form.side, order_type: form.order_type,
      amount: form.amount, price: form.price ? Number(form.price) : undefined,
    });
    setShowManual(false);
    load();
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1 bg-surface-800 rounded-lg p-1 border border-surface-700">
          {STATUSES.map((s) => (
            <button
              key={s}
              onClick={() => setStatus(s)}
              className={cn(
                "px-3 py-1 rounded text-xs font-medium capitalize transition-colors",
                status === s ? "bg-accent text-white" : "text-neutral hover:text-gray-200"
              )}
            >
              {s}
            </button>
          ))}
        </div>
        <button onClick={() => setShowManual(true)} className="btn-primary">
          <Plus className="w-3.5 h-3.5" /> Manual Order
        </button>
      </div>

      <div className="card overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-surface-700">
              <th className="text-left px-4 py-3 label">ID</th>
              <th className="text-left px-4 py-3 label">Symbol</th>
              <th className="text-left px-4 py-3 label">Type</th>
              <th className="text-left px-4 py-3 label">Side</th>
              <th className="text-right px-4 py-3 label">Amount</th>
              <th className="text-right px-4 py-3 label">Filled</th>
              <th className="text-right px-4 py-3 label">Avg Price</th>
              <th className="text-right px-4 py-3 label">Fee</th>
              <th className="text-left px-4 py-3 label">Status</th>
              <th className="text-left px-4 py-3 label">Mode</th>
              <th className="text-right px-4 py-3 label">Time</th>
              <th className="px-4 py-3 label"></th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr><td colSpan={12} className="px-4 py-8 text-center">
                <div className="w-4 h-4 border-2 border-accent border-t-transparent rounded-full animate-spin mx-auto" />
              </td></tr>
            ) : orders.length === 0 ? (
              <tr><td colSpan={12} className="px-4 py-8 text-center text-neutral">No orders</td></tr>
            ) : (
              orders.map((o) => (
                <tr key={o.id} className="border-b border-surface-700/50 table-row-hover">
                  <td className="px-4 py-3 text-neutral">#{o.id}</td>
                  <td className="px-4 py-3 text-gray-200 font-medium">{o.symbol}</td>
                  <td className="px-4 py-3 text-neutral capitalize">{o.order_type}</td>
                  <td className={cn("px-4 py-3 uppercase font-medium", sideColor(o.side))}>{o.side}</td>
                  <td className="px-4 py-3 text-right tabular-nums text-gray-300">{o.amount.toFixed(6)}</td>
                  <td className="px-4 py-3 text-right tabular-nums text-gray-300">{o.filled.toFixed(6)}</td>
                  <td className="px-4 py-3 text-right tabular-nums text-gray-300">
                    {o.average_fill_price ? o.average_fill_price.toFixed(2) : "—"}
                  </td>
                  <td className="px-4 py-3 text-right tabular-nums text-neutral">{formatUSD(o.fee, 4)}</td>
                  <td className={cn("px-4 py-3 uppercase text-[11px] font-medium", statusColor(o.status))}>
                    {o.status}
                  </td>
                  <td className="px-4 py-3">
                    <span className={cn("text-[10px] font-medium uppercase", o.mode === "live" ? "text-warning" : "text-accent")}>
                      {o.mode}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-right text-neutral">{timeAgo(o.created_at)}</td>
                  <td className="px-4 py-3">
                    {o.status === "open" && (
                      <button
                        onClick={() => cancelOrder(o.id)}
                        className="text-neutral hover:text-loss transition-colors"
                        title="Cancel order"
                      >
                        <XCircle className="w-3.5 h-3.5" />
                      </button>
                    )}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      <Modal open={showManual} onClose={() => setShowManual(false)} title="Place Manual Order">
        <div className="space-y-3">
          <p className="text-xs text-warning bg-warning/10 border border-warning/20 rounded px-3 py-2">
            Manual orders still pass all risk checks server-side.
          </p>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="label block mb-1">Symbol</label>
              <input className="input" value={form.symbol} onChange={(e) => setForm({ ...form, symbol: e.target.value })} />
            </div>
            <div>
              <label className="label block mb-1">Side</label>
              <select className="input" value={form.side} onChange={(e) => setForm({ ...form, side: e.target.value })}>
                <option value="buy">Buy / Long</option>
                <option value="sell">Sell / Short</option>
              </select>
            </div>
            <div>
              <label className="label block mb-1">Order Type</label>
              <select className="input" value={form.order_type} onChange={(e) => setForm({ ...form, order_type: e.target.value })}>
                <option value="market">Market</option>
                <option value="limit">Limit</option>
              </select>
            </div>
            <div>
              <label className="label block mb-1">Amount</label>
              <input className="input" type="number" step="any" value={form.amount} onChange={(e) => setForm({ ...form, amount: Number(e.target.value) })} />
            </div>
            {form.order_type === "limit" && (
              <div className="col-span-2">
                <label className="label block mb-1">Limit Price</label>
                <input className="input" type="number" step="any" value={form.price} onChange={(e) => setForm({ ...form, price: e.target.value })} />
              </div>
            )}
          </div>
          <div className="flex gap-2 pt-2">
            <button onClick={placeManualOrder} className="btn-primary flex-1">Place Order</button>
            <button onClick={() => setShowManual(false)} className="btn-ghost">Cancel</button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
