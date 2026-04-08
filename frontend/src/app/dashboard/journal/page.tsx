"use client";

import { useEffect, useState, useCallback } from "react";
import { journalApi } from "@/lib/api";
import { JournalEntry } from "@/types";
import { cn, formatDate } from "@/lib/utils";
import { Search, Plus } from "lucide-react";
import { Modal } from "@/components/ui/Modal";
import { useWebSocket, WSMessage } from "@/hooks/useWebSocket";

const ENTRY_TYPES = ["all", "signal", "trade", "risk_block", "error", "system", "manual"];
const LEVELS = ["all", "info", "warning", "error", "critical"];

const TYPE_COLOR: Record<string, string> = {
  signal: "text-accent",
  trade: "text-profit",
  risk_block: "text-warning",
  error: "text-loss",
  system: "text-neutral",
  manual: "text-gray-300",
};

const LEVEL_COLOR: Record<string, string> = {
  info: "text-neutral",
  warning: "text-warning",
  error: "text-loss",
  critical: "text-loss",
};

export default function JournalPage() {
  const [entries, setEntries] = useState<JournalEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [type, setType] = useState("all");
  const [level, setLevel] = useState("all");
  const [showAdd, setShowAdd] = useState(false);
  const [newMsg, setNewMsg] = useState("");
  const [newDetails, setNewDetails] = useState("");

  const load = useCallback(async () => {
    const data = await journalApi.list({
      entry_type: type === "all" ? undefined : type,
      level: level === "all" ? undefined : level,
      search: search || undefined,
      limit: 200,
    });
    setEntries(data);
    setLoading(false);
  }, [type, level, search]);

  useEffect(() => {
    const timer = setTimeout(load, 300);
    return () => clearTimeout(timer);
  }, [load]);

  // Live log updates via WebSocket
  const handleWsMessage = useCallback((msg: WSMessage) => {
    if (msg.event === "journal:new") {
      const entry = msg.data as JournalEntry;
      setEntries((prev) => [entry, ...prev.slice(0, 199)]);
    }
  }, []);

  useWebSocket({ onMessage: handleWsMessage });

  async function addManual() {
    if (!newMsg.trim()) return;
    await journalApi.addManual({ message: newMsg, details: newDetails || undefined });
    setShowAdd(false);
    setNewMsg("");
    setNewDetails("");
    load();
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3 flex-wrap">
        {/* Search */}
        <div className="relative flex-1 min-w-48">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-neutral" />
          <input
            className="input pl-9"
            placeholder="Search logs..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>

        {/* Type filter */}
        <div className="flex items-center gap-1 bg-surface-800 rounded-lg p-1 border border-surface-700">
          {ENTRY_TYPES.map((t) => (
            <button
              key={t}
              onClick={() => setType(t)}
              className={cn(
                "px-2.5 py-1 rounded text-[11px] font-medium capitalize transition-colors",
                type === t ? "bg-accent text-white" : "text-neutral hover:text-gray-200"
              )}
            >
              {t === "risk_block" ? "risk" : t}
            </button>
          ))}
        </div>

        <button onClick={() => setShowAdd(true)} className="btn-ghost">
          <Plus className="w-3.5 h-3.5" /> Add Note
        </button>
      </div>

      <div className="card">
        {loading ? (
          <div className="flex items-center justify-center py-12">
            <div className="w-5 h-5 border-2 border-accent border-t-transparent rounded-full animate-spin" />
          </div>
        ) : entries.length === 0 ? (
          <p className="text-neutral text-center py-12 text-sm">No log entries</p>
        ) : (
          <div className="divide-y divide-surface-700/50">
            {entries.map((e) => (
              <div key={e.id} className="flex gap-3 px-4 py-3 hover:bg-surface-700/30 transition-colors">
                <div className="flex-shrink-0 pt-0.5">
                  <div className={cn(
                    "w-1.5 h-1.5 rounded-full mt-1.5",
                    e.level === "error" || e.level === "critical" ? "bg-loss" :
                    e.level === "warning" ? "bg-warning" :
                    e.entry_type === "trade" ? "bg-profit" :
                    e.entry_type === "signal" ? "bg-accent" : "bg-neutral"
                  )} />
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-0.5">
                    <span className={cn("text-[10px] font-semibold uppercase", TYPE_COLOR[e.entry_type] || "text-neutral")}>
                      {e.entry_type.replace("_", " ")}
                    </span>
                    {e.symbol && (
                      <span className="text-[10px] text-neutral">{e.symbol}</span>
                    )}
                    <span className="text-[10px] text-neutral ml-auto flex-shrink-0">
                      {formatDate(e.created_at)}
                    </span>
                  </div>
                  <p className="text-sm text-gray-300 leading-snug">{e.message}</p>
                  {e.details && (
                    <p className="text-xs text-neutral mt-1">{e.details}</p>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      <Modal open={showAdd} onClose={() => setShowAdd(false)} title="Add Journal Note">
        <div className="space-y-3">
          <div>
            <label className="label block mb-1">Note</label>
            <input
              className="input"
              placeholder="Why did I take this trade..."
              value={newMsg}
              onChange={(e) => setNewMsg(e.target.value)}
            />
          </div>
          <div>
            <label className="label block mb-1">Details (optional)</label>
            <textarea
              className="input min-h-20 resize-none"
              placeholder="Additional context..."
              value={newDetails}
              onChange={(e) => setNewDetails(e.target.value)}
            />
          </div>
          <div className="flex gap-2">
            <button onClick={addManual} className="btn-primary flex-1">Save Note</button>
            <button onClick={() => setShowAdd(false)} className="btn-ghost">Cancel</button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
