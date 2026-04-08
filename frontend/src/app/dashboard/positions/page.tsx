"use client";

import { useEffect, useState } from "react";
import { positionsApi } from "@/lib/api";
import { Position } from "@/types";
import { formatUSD, pnlColor, sideColor, cn, timeAgo } from "@/lib/utils";
import { Modal } from "@/components/ui/Modal";
import { X, Minimize2 } from "lucide-react";

export default function PositionsPage() {
  const [positions, setPositions] = useState<Position[]>([]);
  const [loading, setLoading] = useState(true);
  const [showAll, setShowAll] = useState(false);
  const [closing, setClosing] = useState<{ id: number; symbol: string } | null>(null);
  const [reducePct, setReducePct] = useState(100);

  async function load(openOnly = !showAll) {
    setLoading(true);
    const data = await positionsApi.list(openOnly);
    setPositions(data);
    setLoading(false);
  }

  useEffect(() => { load(); }, [showAll]);

  async function closePosition() {
    if (!closing) return;
    await positionsApi.close(closing.id, reducePct);
    setClosing(null);
    load();
  }

  if (loading) return (
    <div className="flex items-center justify-center h-64">
      <div className="w-5 h-5 border-2 border-accent border-t-transparent rounded-full animate-spin" />
    </div>
  );

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-sm font-semibold text-white">{positions.length} {showAll ? "All" : "Open"} Positions</h2>
        </div>
        <div className="flex items-center gap-2">
          <label className="flex items-center gap-2 text-xs text-neutral cursor-pointer">
            <input type="checkbox" checked={showAll} onChange={(e) => setShowAll(e.target.checked)} />
            Show closed
          </label>
        </div>
      </div>

      <div className="card overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-surface-700">
              <th className="text-left px-4 py-3 label">Symbol</th>
              <th className="text-left px-4 py-3 label">Side</th>
              <th className="text-right px-4 py-3 label">Size</th>
              <th className="text-right px-4 py-3 label">Entry</th>
              <th className="text-right px-4 py-3 label">Current</th>
              <th className="text-right px-4 py-3 label">PnL</th>
              <th className="text-right px-4 py-3 label">SL</th>
              <th className="text-right px-4 py-3 label">TP</th>
              <th className="text-right px-4 py-3 label">Mode</th>
              <th className="text-right px-4 py-3 label">Opened</th>
              <th className="px-4 py-3 label">Actions</th>
            </tr>
          </thead>
          <tbody>
            {positions.length === 0 ? (
              <tr>
                <td colSpan={11} className="px-4 py-8 text-center text-neutral">
                  No positions found
                </td>
              </tr>
            ) : (
              positions.map((p) => (
                <tr key={p.id} className="border-b border-surface-700/50 table-row-hover">
                  <td className="px-4 py-3 text-gray-200 font-medium">{p.symbol}</td>
                  <td className={cn("px-4 py-3 uppercase font-semibold", sideColor(p.side))}>{p.side}</td>
                  <td className="px-4 py-3 text-right tabular-nums text-gray-300">{p.size.toFixed(6)}</td>
                  <td className="px-4 py-3 text-right tabular-nums text-gray-300">{p.entry_price.toFixed(2)}</td>
                  <td className="px-4 py-3 text-right tabular-nums text-gray-300">{p.current_price.toFixed(2)}</td>
                  <td className={cn("px-4 py-3 text-right tabular-nums font-medium", pnlColor(p.unrealized_pnl))}>
                    {formatUSD(p.unrealized_pnl)}
                  </td>
                  <td className="px-4 py-3 text-right tabular-nums text-neutral">
                    {p.stop_loss ? p.stop_loss.toFixed(2) : "—"}
                  </td>
                  <td className="px-4 py-3 text-right tabular-nums text-neutral">
                    {p.take_profit ? p.take_profit.toFixed(2) : "—"}
                  </td>
                  <td className="px-4 py-3 text-right">
                    <span className={cn("text-[10px] font-medium uppercase", p.mode === "live" ? "text-warning" : "text-accent")}>
                      {p.mode}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-right text-neutral">{timeAgo(p.opened_at)}</td>
                  <td className="px-4 py-3">
                    {p.is_open && (
                      <div className="flex items-center gap-1.5">
                        <button
                          onClick={() => { setClosing({ id: p.id, symbol: p.symbol }); setReducePct(100); }}
                          className="flex items-center gap-1 px-2 py-1 rounded text-[11px] bg-loss/20 text-loss hover:bg-loss/30 transition-colors"
                        >
                          <X className="w-3 h-3" /> Close
                        </button>
                        <button
                          onClick={() => { setClosing({ id: p.id, symbol: p.symbol }); setReducePct(50); }}
                          className="flex items-center gap-1 px-2 py-1 rounded text-[11px] bg-surface-700 text-neutral hover:bg-surface-600 transition-colors"
                        >
                          <Minimize2 className="w-3 h-3" /> 50%
                        </button>
                      </div>
                    )}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      <Modal
        open={!!closing}
        onClose={() => setClosing(null)}
        title={`Close Position — ${closing?.symbol}`}
      >
        <div className="space-y-4">
          <p className="text-neutral text-sm">
            Confirm closing {reducePct}% of this position at current market price.
          </p>
          <div>
            <label className="label block mb-1">Reduce % (1–100)</label>
            <input
              className="input"
              type="number"
              min={1}
              max={100}
              value={reducePct}
              onChange={(e) => setReducePct(Number(e.target.value))}
            />
          </div>
          <div className="flex gap-2">
            <button onClick={closePosition} className="btn-danger flex-1">
              Confirm Close {reducePct}%
            </button>
            <button onClick={() => setClosing(null)} className="btn-ghost">Cancel</button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
