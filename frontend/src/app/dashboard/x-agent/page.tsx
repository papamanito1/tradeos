"use client";
import { useState, useEffect, useCallback } from "react";

const API   = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const LOCAL = "http://localhost:4242";

async function postViaLocal(text: string, type: string): Promise<{ ok: boolean; msg: string }> {
  try {
    const r = await fetch(`${LOCAL}/post`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, type }),
    });
    if (r.ok) return { ok: true, msg: "Posting now…" };
  } catch {}
  return { ok: false, msg: "local_poster.py not running" };
}

async function triggerPost(endpoint: string): Promise<{ ok: boolean; msg: string }> {
  try {
    const token = typeof window !== "undefined" ? localStorage.getItem("tradeos_token") : null;
    const r = await fetch(`${API}${endpoint}`, {
      method: "POST",
      headers: token ? { "Authorization": `Bearer ${token}` } : {},
    });
    const d = await r.json();
    if (!d.ok) return { ok: false, msg: d.error || "Failed" };
    if (d.posted) return { ok: true, msg: "Posted on X ✓" };
    if (d.text) {
      try {
        const lr = await fetch(`${LOCAL}/post`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text: d.text, type: d.type || "auto" }),
        });
        if (lr.ok) return { ok: true, msg: "Sending via local poster…" };
      } catch {}
    }
    return { ok: true, msg: "Queued — local_poster.py will send it" };
  } catch {
    return { ok: false, msg: "Cannot reach backend" };
  }
}

interface Post {
  id: string;
  type: string;
  text: string;
  ts: number;
  url: string;
}

interface PendingTweet {
  id: string;
  text: string;
  post_type: string;
  created_at: number;
  expires_in: number;   // seconds until auto-post
}

interface GrokStatus {
  enabled: boolean;
  cache_fresh: boolean;
  cache_age_min: number | null;
  current_narrative: string;
  btc_sentiment: string;
  viral_hook_style: string;
  trending_topics: string[];
  viral_angles: string[];
}

interface Status {
  enabled: boolean;
  posting_method?: string;
  tweepy_available?: boolean;
  intro_posted: boolean;
  mood?: string;
  ai_brain?: string;
  daily_posts: number;
  daily_budget: number;
  last_signal: number;
  last_result: number;
  last_contrarian: number;
  last_psychology: number;
  last_poll: number;
  last_trade_breakdown?: number;
  last_trending_hook?: number;
  last_viral_commentary?: number;
  last_bold_prediction?: number;
  last_grok_viral?: number;
  pending_approvals?: PendingTweet[];
  grok_intelligence?: GrokStatus;
  recent_posts: Post[];
  posts_per_hour?: number;
  total_posts?: number;
  last_error?: string;
  queue_length?: number;
}

const TYPE_META: Record<string, { label: string; icon: string; accent: string; dot: string }> = {
  signal:            { label: "Trade Signal",      icon: "↑↓", accent: "text-emerald-400", dot: "bg-emerald-400" },
  result:            { label: "Trade Result",       icon: "✓",  accent: "text-violet-400",  dot: "bg-violet-400"  },
  contrarian:        { label: "Contrarian",         icon: "◆",  accent: "text-rose-400",    dot: "bg-rose-400"    },
  psychology_thread: { label: "Psychology",         icon: "◇",  accent: "text-cyan-400",    dot: "bg-cyan-400"    },
  poll:              { label: "Poll",               icon: "○",  accent: "text-amber-400",   dot: "bg-amber-400"   },
  trade_breakdown:   { label: "Breakdown",          icon: "◈",  accent: "text-blue-400",    dot: "bg-blue-400"    },
  trending_hook:     { label: "Trending Hook",      icon: "↗",  accent: "text-blue-400",    dot: "bg-blue-400"    },
  viral_commentary:  { label: "Viral Commentary",   icon: "◉",  accent: "text-indigo-400",  dot: "bg-indigo-400"  },
  bold_prediction:   { label: "Bold Prediction",    icon: "◎",  accent: "text-pink-400",    dot: "bg-pink-400"    },
  grok_viral:        { label: "Grok Viral",         icon: "⚡", accent: "text-indigo-400",  dot: "bg-indigo-400"  },
  contrarian_take:   { label: "Contrarian",         icon: "◆",  accent: "text-rose-400",    dot: "bg-rose-400"    },
  market_insight:    { label: "Market Insight",     icon: "◈",  accent: "text-cyan-400",    dot: "bg-cyan-400"    },
  psychology:        { label: "Psychology",         icon: "◇",  accent: "text-cyan-400",    dot: "bg-cyan-400"    },
  viral_reaction:    { label: "Viral Reaction",     icon: "◉",  accent: "text-pink-400",    dot: "bg-pink-400"    },
  daily:             { label: "Daily Recap",        icon: "◉",  accent: "text-slate-400",   dot: "bg-slate-400"   },
  weekly:            { label: "Weekly Recap",       icon: "◈",  accent: "text-slate-400",   dot: "bg-slate-400"   },
  intro:             { label: "Intro",              icon: "◎",  accent: "text-emerald-400", dot: "bg-emerald-400" },
  manual:            { label: "Manual",             icon: "✏",  accent: "text-white/40",    dot: "bg-white/20"    },
};

function timeAgo(ts: number): string {
  const diff = Math.floor(Date.now() / 1000 - ts);
  if (diff < 60)    return `${diff}s ago`;
  if (diff < 3600)  return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

function nextIn(last: number, cooldown: number): string {
  if (!last) return "Ready";
  const remaining = cooldown - (Date.now() / 1000 - last);
  if (remaining <= 0) return "Ready";
  const h = Math.floor(remaining / 3600);
  const m = Math.floor((remaining % 3600) / 60);
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
}

// ── Pending Approval Panel ─────────────────────────────────────────────────
function PendingApprovals({ pending, onAction }: { pending: PendingTweet[]; onAction: () => void }) {
  const [acting, setActing] = useState<Record<string, string>>({});

  const act = async (pid: string, action: "approve" | "reject") => {
    setActing(a => ({ ...a, [pid]: action }));
    const token = typeof window !== "undefined" ? localStorage.getItem("tradeos_token") : null;
    await fetch(`${API}/api/x-agent/${action}/${pid}`, {
      method: "POST",
      headers: token ? { "Authorization": `Bearer ${token}` } : {},
    });
    setTimeout(onAction, 600);
  };

  if (!pending.length) return null;

  return (
    <div className="rounded-2xl border overflow-hidden"
      style={{ borderColor: "rgba(251,191,36,0.25)", background: "rgba(251,191,36,0.04)" }}>
      <div className="px-5 py-3.5 flex items-center gap-2.5 border-b" style={{ borderColor: "rgba(251,191,36,0.12)" }}>
        <span className="w-2 h-2 rounded-full bg-yellow-400 animate-pulse shrink-0" />
        <span className="text-[12px] font-semibold text-yellow-300">
          {pending.length} tweet{pending.length > 1 ? "s" : ""} pending approval
        </span>
        <span className="text-[11px] text-yellow-400/40 ml-auto">auto-posts when timer expires</span>
      </div>
      <div className="divide-y" style={{ borderColor: "rgba(251,191,36,0.08)" }}>
        {pending.map(p => {
          const done = acting[p.id];
          const mins = Math.floor(p.expires_in / 60);
          const secs = p.expires_in % 60;
          const typeLabel = p.post_type.replace(/_/g, " ");
          return (
            <div key={p.id} className={`px-5 py-4 transition-all duration-300 ${done ? "opacity-40" : ""}`}>
              <div className="flex items-center justify-between gap-2 mb-2.5">
                <div className="flex items-center gap-2">
                  <span className="text-[10px] font-semibold uppercase tracking-wider text-yellow-400/60">{typeLabel}</span>
                  <span className="text-[10px] text-yellow-400/30">
                    {done ? (done === "approve" ? "✓ Posting…" : "✗ Cancelled") : `Auto-posts in ${mins}:${String(secs).padStart(2, "0")}`}
                  </span>
                </div>
                {!done && (
                  <div className="flex items-center gap-2 shrink-0">
                    <button onClick={() => act(p.id, "approve")}
                      className="px-3.5 py-1.5 rounded-lg text-[12px] font-semibold transition-all active:scale-95"
                      style={{ background: "rgba(52,211,153,0.15)", color: "rgb(52,211,153)", border: "1px solid rgba(52,211,153,0.3)" }}>
                      Post Now
                    </button>
                    <button onClick={() => act(p.id, "reject")}
                      className="px-3 py-1.5 rounded-lg text-[12px] font-medium transition-all active:scale-95"
                      style={{ background: "rgba(255,255,255,0.04)", color: "rgba(255,255,255,0.35)", border: "1px solid rgba(255,255,255,0.08)" }}>
                      Cancel
                    </button>
                  </div>
                )}
              </div>
              <p className="text-[13px] text-white/70 leading-relaxed whitespace-pre-wrap">{p.text}</p>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── Grok Intelligence Card ─────────────────────────────────────────────────
function GrokCard({ grok, lastFired }: { grok?: GrokStatus; lastFired: string }) {
  if (!grok?.enabled) return null;
  return (
    <div className="rounded-2xl border p-5 space-y-4"
      style={{ background: "rgba(99,102,241,0.05)", borderColor: "rgba(99,102,241,0.2)" }}>
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2.5">
          <div className="w-7 h-7 rounded-lg flex items-center justify-center text-sm"
            style={{ background: "rgba(99,102,241,0.2)", border: "1px solid rgba(99,102,241,0.3)" }}>
            ◎
          </div>
          <span className="text-[13px] font-semibold text-white">Grok Intelligence</span>
          <span className="text-[10px] px-2 py-0.5 rounded-full font-medium"
            style={{ background: grok.cache_fresh ? "rgba(52,211,153,0.1)" : "rgba(255,255,255,0.05)", color: grok.cache_fresh ? "rgb(52,211,153)" : "rgba(255,255,255,0.3)", border: `1px solid ${grok.cache_fresh ? "rgba(52,211,153,0.2)" : "rgba(255,255,255,0.08)"}` }}>
            {grok.cache_fresh ? `Fresh · ${grok.cache_age_min?.toFixed(0)}m ago` : "Fetching next cycle…"}
          </span>
        </div>
        <span className="text-[11px] text-white/30">Next viral post: {lastFired}</span>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        <div className="rounded-xl p-3.5" style={{ background: "rgba(0,0,0,0.2)", border: "1px solid rgba(255,255,255,0.06)" }}>
          <div className="text-[9px] uppercase tracking-widest text-white/25 mb-1.5">BTC Narrative</div>
          <div className="text-[12px] text-white/70 leading-relaxed">
            {grok.current_narrative || <span className="text-white/25 italic">Waiting for first fetch…</span>}
          </div>
        </div>
        <div className="rounded-xl p-3.5" style={{ background: "rgba(0,0,0,0.2)", border: "1px solid rgba(255,255,255,0.06)" }}>
          <div className="text-[9px] uppercase tracking-widest text-white/25 mb-1.5">Sentiment</div>
          <div className="text-[12px] text-white/70 leading-relaxed">
            {grok.btc_sentiment || <span className="text-white/25 italic">Waiting…</span>}
          </div>
        </div>
        <div className="rounded-xl p-3.5" style={{ background: "rgba(0,0,0,0.2)", border: "1px solid rgba(255,255,255,0.06)" }}>
          <div className="text-[9px] uppercase tracking-widest text-white/25 mb-1.5">Viral Format</div>
          <div className="text-[12px] text-white/70 leading-relaxed">
            {grok.viral_hook_style || <span className="text-white/25 italic">Waiting…</span>}
          </div>
        </div>
      </div>

      {grok.trending_topics?.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {grok.trending_topics.slice(0, 6).map((t, i) => (
            <span key={i} className="text-[10px] px-2 py-0.5 rounded-full"
              style={{ background: "rgba(99,102,241,0.12)", color: "rgba(165,180,252,0.8)", border: "1px solid rgba(99,102,241,0.2)" }}>
              {t}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Trigger Card ───────────────────────────────────────────────────────────
interface TriggerCardProps {
  icon: string; label: string; description: string;
  nextPost: string; endpoint: string; onTriggered: () => void;
  highlight?: boolean;
}

function TriggerCard({ icon, label, description, nextPost, endpoint, onTriggered, highlight }: TriggerCardProps) {
  const [loading, setLoading] = useState(false);
  const [result, setResult]   = useState<{ ok: boolean; msg: string } | null>(null);
  const isReady = nextPost === "Ready";

  const trigger = async () => {
    setLoading(true);
    setResult(null);
    const res = await triggerPost(endpoint);
    setResult(res);
    if (res.ok) onTriggered();
    setLoading(false);
    setTimeout(() => setResult(null), 8000);
  };

  if (highlight) {
    return (
      <div className="col-span-full rounded-2xl border p-5 transition-all duration-300"
        style={{ background: isReady ? "rgba(99,102,241,0.08)" : "rgba(99,102,241,0.04)", borderColor: isReady ? "rgba(99,102,241,0.35)" : "rgba(99,102,241,0.15)" }}>
        <div className="flex items-center justify-between gap-4">
          <div className="flex items-center gap-3 min-w-0">
            <div className="w-11 h-11 rounded-xl flex items-center justify-center text-xl shrink-0"
              style={{ background: "rgba(99,102,241,0.15)", border: "1px solid rgba(99,102,241,0.3)" }}>
              {icon}
            </div>
            <div className="min-w-0">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-[14px] font-semibold text-white">{label}</span>
                <span className="text-[9px] font-semibold px-2 py-0.5 rounded-full tracking-widest"
                  style={{ background: "rgba(99,102,241,0.2)", color: "rgba(165,180,252,1)", border: "1px solid rgba(99,102,241,0.3)" }}>
                  LIVE X SEARCH
                </span>
              </div>
              <div className="text-[11px] text-white/35 mt-0.5">{description}</div>
            </div>
          </div>
          <div className="flex items-center gap-3 shrink-0">
            <span className="text-[11px] font-medium hidden sm:block"
              style={{ color: isReady ? "rgba(167,243,208,0.9)" : "rgba(255,255,255,0.25)" }}>
              {loading ? "Searching X…" : isReady ? "Ready" : nextPost}
            </span>
            <button onClick={trigger} disabled={loading}
              className="px-5 py-2.5 rounded-xl text-[13px] font-semibold transition-all duration-200 active:scale-[0.97] disabled:opacity-50"
              style={{ background: isReady ? "rgba(99,102,241,0.7)" : "rgba(99,102,241,0.25)", color: "white", border: "1px solid rgba(99,102,241,0.5)" }}>
              {loading ? (
                <span className="flex items-center gap-2">
                  <span className="w-3 h-3 rounded-full border border-white/20 border-t-white/80 animate-spin" />
                  Thinking…
                </span>
              ) : "Post Now"}
            </button>
          </div>
        </div>
        {result && (
          <div className="mt-3 text-[11px] px-3 py-2 rounded-lg"
            style={{ background: result.ok ? "rgba(52,211,153,0.08)" : "rgba(239,68,68,0.08)", color: result.ok ? "rgb(52,211,153)" : "rgb(239,68,68)" }}>
            {result.ok ? `✓ ${result.msg}` : `✗ ${result.msg}`}
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="group rounded-2xl bg-white/[0.03] border border-white/[0.07] p-4 hover:bg-white/[0.06] hover:border-white/[0.12] transition-all duration-200">
      <div className="flex items-start justify-between gap-2 mb-3">
        <div className="flex items-center gap-2.5">
          <span className="text-base">{icon}</span>
          <div>
            <div className="text-[12px] font-semibold text-white/80">{label}</div>
            <div className="text-[10px] text-white/30 mt-0.5 leading-snug">{description}</div>
          </div>
        </div>
        <span className={`text-[9px] font-semibold px-2 py-0.5 rounded-full shrink-0 border ${
          isReady ? "bg-emerald-500/10 text-emerald-400 border-emerald-500/20" : "bg-white/[0.04] text-white/25 border-white/[0.06]"
        }`}>
          {isReady ? "READY" : nextPost}
        </span>
      </div>
      <button onClick={trigger} disabled={loading}
        className={`w-full py-2 rounded-lg text-[12px] font-medium transition-all duration-150 border ${
          loading
            ? "bg-white/[0.03] text-white/20 border-white/[0.05] cursor-wait"
            : result
              ? result.ok
                ? "bg-emerald-500/10 text-emerald-400 border-emerald-500/15"
                : "bg-red-500/10 text-red-400 border-red-500/15"
              : "bg-white/[0.05] text-white/60 border-white/[0.08] hover:bg-white/[0.1] hover:text-white cursor-pointer active:scale-[0.98]"
        }`}>
        {loading
          ? <span className="flex items-center justify-center gap-1.5"><span className="w-2.5 h-2.5 rounded-full border border-white/20 border-t-white/60 animate-spin" />Queuing…</span>
          : result ? result.msg : "Post Now"}
      </button>
    </div>
  );
}

// ── Fire All ───────────────────────────────────────────────────────────────
function FireAllButton({ onDone }: { onDone: () => void }) {
  const [loading, setLoading] = useState(false);
  const [result, setResult]   = useState<string | null>(null);

  const fireAll = async () => {
    setLoading(true);
    try {
      const token = typeof window !== "undefined" ? localStorage.getItem("tradeos_token") : null;
      const r = await fetch(`${API}/api/x-agent/fire-all`, {
        method: "POST",
        headers: token ? { "Authorization": `Bearer ${token}` } : {},
      });
      const d = await r.json();
      setResult(d.ok ? `${Object.values(d.results || {}).filter(v => v !== "failed").length} queued` : "Failed");
      if (d.ok) onDone();
    } catch { setResult("Error"); }
    setLoading(false);
    setTimeout(() => setResult(null), 4000);
  };

  return (
    <button onClick={fireAll} disabled={loading}
      className="px-3.5 py-2 rounded-xl text-[12px] font-medium border border-white/[0.1] bg-white/[0.06] hover:bg-white/[0.1] text-white/60 hover:text-white transition-all duration-200 flex items-center gap-1.5">
      {loading ? <span className="w-3 h-3 rounded-full border border-white/20 border-t-white/60 animate-spin" /> : "⚡"}
      {result || "Post All"}
    </button>
  );
}

// ── Main Page ──────────────────────────────────────────────────────────────
export default function XAgentPage() {
  const [status, setStatus]         = useState<Status | null>(null);
  const [manualText, setManualText] = useState("");
  const [posting, setPosting]       = useState(false);
  const [postResult, setPostResult] = useState<{ ok: boolean; msg: string } | null>(null);
  const [localOnline, setLocalOnline] = useState<boolean | null>(null);
  const [showDiag, setShowDiag]     = useState(false);
  const [diagResult, setDiagResult] = useState<Record<string, unknown> | null>(null);
  const [diagLoading, setDiagLoading] = useState(false);
  const charCount = manualText.length;

  useEffect(() => {
    const check = async () => {
      try {
        await fetch(`${LOCAL}/post`, { method: "OPTIONS", signal: AbortSignal.timeout(1500) });
        setLocalOnline(true);
      } catch { setLocalOnline(false); }
    };
    check();
    const iv = setInterval(check, 15000);
    return () => clearInterval(iv);
  }, []);

  const fetchStatus = useCallback(async () => {
    try {
      const r = await fetch(`${API}/api/x-agent/status`);
      if (r.ok) setStatus(await r.json());
    } catch {}
  }, []);

  useEffect(() => {
    fetchStatus();
    const iv = setInterval(fetchStatus, 15000);
    return () => clearInterval(iv);
  }, [fetchStatus]);

  const resetCooldowns = async () => {
    const token = typeof window !== "undefined" ? localStorage.getItem("tradeos_token") : null;
    await fetch(`${API}/api/x-agent/reset-cooldowns`, {
      method: "POST",
      headers: token ? { "Authorization": `Bearer ${token}` } : {},
    });
    fetchStatus();
  };

  const postManual = async () => {
    if (!manualText.trim() || charCount > 280) return;
    setPosting(true);
    setPostResult(null);
    const localRes = await postViaLocal(manualText.trim(), "manual");
    if (localRes.ok) {
      setPostResult({ ok: true, msg: "Posting now…" });
      setManualText("");
      fetchStatus();
      setPosting(false);
      setTimeout(() => setPostResult(null), 7000);
      return;
    }
    try {
      const token = typeof window !== "undefined" ? localStorage.getItem("tradeos_token") : null;
      const r = await fetch(`${API}/api/x-agent/post`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...(token ? { "Authorization": `Bearer ${token}` } : {}) },
        body: JSON.stringify({ text: manualText.trim() }),
      });
      const d = await r.json();
      if (d.ok) { setPostResult({ ok: true, msg: "Posted ✓" }); setManualText(""); fetchStatus(); }
      else setPostResult({ ok: false, msg: d.error || "Failed" });
    } catch { setPostResult({ ok: false, msg: "Cannot reach backend" }); }
    setPosting(false);
    setTimeout(() => setPostResult(null), 7000);
  };

  const runDiagnose = async () => {
    setDiagLoading(true);
    try {
      const r = await fetch(`${API}/api/x-agent/diagnose`);
      setDiagResult(await r.json());
    } catch { setDiagResult({ error: "Cannot reach backend" }); }
    setDiagLoading(false);
  };

  const todayPosts  = status?.daily_posts ?? 0;
  const dailyBudget = status?.daily_budget ?? 5;
  const totalPosts  = status?.total_posts ?? status?.recent_posts.length ?? 0;
  const budgetPct   = Math.min(todayPosts / dailyBudget, 1);
  const budgetLeft  = Math.max(dailyBudget - todayPosts, 0);

  const CONTRARIAN_CD = 21600;
  const PSYCHOLOGY_CD = 43200;
  const POLL_CD       = 43200;
  const BREAKDOWN_CD  = 43200;
  const GROK_VIRAL_CD = 1500;

  const triggers = [
    {
      icon: "⚡", label: "Grok Viral Post",
      description: "Searches X live → picks what's trending → avoids repeats → posts in @Tradeous voice",
      nextPost: nextIn(status?.last_grok_viral || 0, GROK_VIRAL_CD),
      endpoint: "/api/x-agent/trigger/grok-viral",
      highlight: true,
    },
    {
      icon: "◆", label: "Contrarian",
      description: "Cold data-backed take against the crowd",
      nextPost: nextIn(status?.last_contrarian || 0, CONTRARIAN_CD),
      endpoint: "/api/x-agent/trigger/contrarian",
    },
    {
      icon: "◇", label: "Psychology",
      description: "Expose a common trader mistake",
      nextPost: nextIn(status?.last_psychology || 0, PSYCHOLOGY_CD),
      endpoint: "/api/x-agent/trigger/psychology",
    },
    {
      icon: "○", label: "Poll",
      description: "Market read poll with algo answer",
      nextPost: nextIn(status?.last_poll || 0, POLL_CD),
      endpoint: "/api/x-agent/trigger/poll",
    },
    {
      icon: "◈", label: "Breakdown",
      description: "How the algo evaluates setups right now",
      nextPost: nextIn(status?.last_trade_breakdown || 0, BREAKDOWN_CD),
      endpoint: "/api/x-agent/trigger/breakdown",
    },
  ];

  const recentSorted = [...(status?.recent_posts ?? [])].reverse();
  const lastPost = recentSorted[0];
  const grokNextIn = nextIn(status?.last_grok_viral || 0, GROK_VIRAL_CD);

  // Only show error if it's a real problem (not just the expected 226 queue)
  const showError = status?.last_error &&
    !status.last_error.includes("Queued for local_poster") &&
    !status.last_error.includes("226");

  return (
    <div className="min-h-screen text-white">
      <div className="max-w-5xl mx-auto px-4 py-6 space-y-5">

        {/* ── Header ── */}
        <div className="relative rounded-2xl overflow-hidden border border-white/[0.08]"
          style={{ background: "linear-gradient(135deg, rgba(255,255,255,0.04) 0%, rgba(255,255,255,0.01) 100%)" }}>
          <div className="px-6 py-5 flex items-center justify-between gap-4">
            <div className="flex items-center gap-4">
              <div className="relative w-12 h-12 rounded-xl bg-white/[0.07] border border-white/[0.1] flex items-center justify-center shrink-0">
                <span className="text-xl font-bold text-white">𝕏</span>
                <span className={`absolute -top-1 -right-1 w-2.5 h-2.5 rounded-full border-2 border-[#0d0d0d] ${status?.enabled ? "bg-emerald-400 animate-pulse" : "bg-red-400"}`} />
              </div>
              <div>
                <div className="flex items-center gap-2.5 flex-wrap">
                  <h1 className="text-xl font-bold text-white tracking-tight">X Agent</h1>
                  <span className="text-white/30 text-sm font-medium">@tradeous</span>
                  {status?.mood && (
                    <span className="text-[10px] text-white/25 font-mono px-2 py-0.5 rounded-full border border-white/[0.08]">{status.mood}</span>
                  )}
                  {status?.posting_method && (
                    <span className={`text-[10px] font-mono px-2 py-0.5 rounded-full border ${
                      status.posting_method === "cookie_graphql"
                        ? "text-yellow-400/70 border-yellow-500/20 bg-yellow-500/[0.07]"
                        : "text-emerald-400/70 border-emerald-500/20 bg-emerald-500/[0.07]"
                    }`}>
                      {status.posting_method === "cookie_graphql" ? "via local poster" : "API v2"}
                    </span>
                  )}
                </div>
                <p className="text-[12px] text-white/30 mt-0.5">
                  {status?.ai_brain === "grok" ? "Grok-powered · " : ""}cold + data-driven · {dailyBudget} posts/day
                </p>
              </div>
            </div>
            <div className="flex items-center gap-2 shrink-0">
              <div className={`flex items-center gap-2 px-3 py-1.5 rounded-xl text-[11px] font-bold border ${
                status?.enabled
                  ? "bg-emerald-500/10 text-emerald-400 border-emerald-500/20"
                  : "bg-red-500/10 text-red-400 border-red-500/20"
              }`}>
                <span className={`w-1.5 h-1.5 rounded-full ${status?.enabled ? "bg-emerald-400 animate-pulse" : "bg-red-400"}`} />
                {status?.enabled ? "LIVE" : "OFFLINE"}
              </div>
              <FireAllButton onDone={fetchStatus} />
              <button onClick={resetCooldowns}
                className="px-3 py-1.5 rounded-xl text-[11px] font-medium text-white/30 border border-white/[0.07] hover:bg-white/[0.06] hover:text-white/60 transition-all duration-200">
                Reset
              </button>
            </div>
          </div>
        </div>

        {/* ── Status bar: local poster + last error ── */}
        <div className="space-y-2">
          {localOnline === false && (
            <div className="rounded-xl border px-4 py-3 flex items-center gap-3"
              style={{ background: "rgba(251,191,36,0.05)", borderColor: "rgba(251,191,36,0.2)" }}>
              <span className="w-1.5 h-1.5 rounded-full bg-yellow-400 shrink-0" />
              <div className="flex-1 text-[12px]">
                <span className="text-yellow-300 font-medium">local_poster.py offline</span>
                <span className="text-yellow-400/50"> — tweets will queue but not send until you run </span>
                <code className="text-yellow-300 font-mono text-[11px]">python local_poster.py</code>
              </div>
            </div>
          )}
          {localOnline === true && (
            <div className="rounded-xl border px-4 py-2.5 flex items-center gap-3"
              style={{ background: "rgba(34,197,94,0.04)", borderColor: "rgba(34,197,94,0.12)" }}>
              <span className="w-1.5 h-1.5 rounded-full bg-green-400 animate-pulse shrink-0" />
              <span className="text-[12px] text-green-400 font-medium">local_poster.py running — posts send instantly from your PC</span>
            </div>
          )}
          {showError && (
            <div className="rounded-xl border px-4 py-3 flex items-start gap-3"
              style={{ background: "rgba(239,68,68,0.05)", borderColor: "rgba(239,68,68,0.2)" }}>
              <span className="text-red-400 text-sm shrink-0 mt-0.5">✗</span>
              <div>
                <div className="text-[11px] font-semibold text-red-300 mb-1">Last error</div>
                <code className="text-[10px] text-red-400/70 font-mono">{status?.last_error}</code>
              </div>
            </div>
          )}
        </div>

        {/* ── Stats row ── */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          {/* Daily budget with progress bar */}
          <div className="col-span-2 rounded-2xl bg-white/[0.03] border border-white/[0.07] p-4">
            <div className="flex items-end justify-between mb-3">
              <div>
                <div className="text-2xl font-bold text-white tabular-nums">{todayPosts}<span className="text-sm text-white/25 font-normal ml-1">/ {dailyBudget}</span></div>
                <div className="text-[11px] text-white/40 mt-0.5">Posts today</div>
              </div>
              <div className="text-right">
                <div className={`text-xl font-bold tabular-nums ${budgetLeft === 0 ? "text-red-400" : budgetLeft <= 1 ? "text-amber-400" : "text-emerald-400"}`}>{budgetLeft}</div>
                <div className="text-[11px] text-white/30">remaining</div>
              </div>
            </div>
            <div className="h-1.5 rounded-full bg-white/[0.06] overflow-hidden">
              <div className="h-full rounded-full transition-all duration-700"
                style={{ width: `${budgetPct * 100}%`, background: budgetPct >= 1 ? "rgb(239,68,68)" : budgetPct >= 0.8 ? "rgb(251,191,36)" : "rgb(52,211,153)" }} />
            </div>
          </div>

          <div className="rounded-2xl bg-white/[0.03] border border-white/[0.07] p-4">
            <div className="text-2xl font-bold text-white tabular-nums">{totalPosts}</div>
            <div className="text-[11px] text-white/40 mt-0.5">Total posts</div>
            <div className="text-[10px] text-white/20 mt-0.5">{status?.posts_per_hour?.toFixed(1) ?? "—"}/hr avg</div>
          </div>

          <div className="rounded-2xl bg-white/[0.03] border border-white/[0.07] p-4">
            <div className="flex items-center gap-1.5 mb-1">
              <span className="text-[10px] font-bold px-1.5 py-0.5 rounded"
                style={{ background: "rgba(99,102,241,0.2)", color: "rgba(165,180,252,0.9)" }}>
                {status?.ai_brain?.toUpperCase() ?? "—"}
              </span>
            </div>
            <div className="text-[11px] text-white/40">AI brain</div>
            <div className="text-[10px] text-white/20 mt-0.5">
              {lastPost ? `last: ${timeAgo(lastPost.ts)}` : "no posts yet"}
            </div>
          </div>
        </div>

        {/* ── Grok Intelligence card ── */}
        <GrokCard grok={status?.grok_intelligence} lastFired={grokNextIn} />

        {/* ── Pending Approvals ── */}
        <PendingApprovals pending={status?.pending_approvals ?? []} onAction={fetchStatus} />

        {/* ── Content Triggers ── */}
        <div>
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-[10px] font-bold text-white/25 uppercase tracking-[0.15em]">Content Triggers</h2>
            <span className="text-[10px] text-white/20">auto · {dailyBudget} posts/day max · no spam</span>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            {triggers.map((t) => (
              <TriggerCard key={t.endpoint} {...t} onTriggered={fetchStatus} />
            ))}
          </div>
        </div>

        {/* ── Compose + Feed ── */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">

          {/* Compose */}
          <div className="rounded-2xl bg-white/[0.03] border border-white/[0.07] p-5 flex flex-col gap-4">
            <div>
              <h2 className="text-[10px] font-bold text-white/25 uppercase tracking-[0.15em] mb-0.5">Compose</h2>
              <p className="text-[11px] text-white/20">Post manually as @tradeous</p>
            </div>
            <div className="relative flex-1">
              <textarea
                value={manualText}
                onChange={(e) => setManualText(e.target.value)}
                placeholder="Cold. Confident. Data-backed."
                maxLength={280}
                rows={5}
                className="w-full bg-white/[0.03] border border-white/[0.07] rounded-xl p-4 text-[13px] text-white placeholder-white/15 resize-none focus:outline-none focus:border-white/20 focus:bg-white/[0.05] transition-all duration-200 leading-relaxed"
              />
              <div className="absolute bottom-3 right-3 flex items-center gap-1.5">
                <svg className="w-5 h-5 -rotate-90" viewBox="0 0 20 20">
                  <circle cx="10" cy="10" r="7" fill="none" stroke="rgba(255,255,255,0.08)" strokeWidth="2" />
                  <circle cx="10" cy="10" r="7" fill="none"
                    stroke={charCount > 260 ? "rgb(239,68,68)" : charCount > 200 ? "rgb(251,191,36)" : "rgba(255,255,255,0.35)"}
                    strokeWidth="2"
                    strokeDasharray={`${2 * Math.PI * 7}`}
                    strokeDashoffset={`${2 * Math.PI * 7 * (1 - charCount / 280)}`}
                    strokeLinecap="round"
                    className="transition-all duration-150" />
                </svg>
                <span className={`text-[10px] font-mono ${charCount > 260 ? "text-red-400" : "text-white/20"}`}>
                  {280 - charCount}
                </span>
              </div>
            </div>
            {postResult && (
              <div className={`text-[12px] text-center py-2 rounded-xl border ${
                postResult.ok ? "bg-emerald-500/10 text-emerald-400 border-emerald-500/15" : "bg-red-500/10 text-red-400 border-red-500/15"
              }`}>{postResult.msg}</div>
            )}
            <button onClick={postManual} disabled={posting || !manualText.trim() || charCount > 280}
              className={`py-3 rounded-xl text-[13px] font-semibold tracking-tight transition-all duration-200 border ${
                posting || !manualText.trim() || charCount > 280
                  ? "bg-white/[0.02] text-white/15 border-white/[0.04] cursor-not-allowed"
                  : "bg-white text-black border-white hover:bg-white/90 cursor-pointer active:scale-[0.98]"
              }`}>
              {posting ? (
                <span className="flex items-center justify-center gap-2">
                  <span className="w-3 h-3 rounded-full border border-black/20 border-t-black/60 animate-spin" />
                  Sending…
                </span>
              ) : "Post as @tradeous"}
            </button>
          </div>

          {/* Activity Feed */}
          <div className="rounded-2xl bg-white/[0.03] border border-white/[0.07] p-5 flex flex-col gap-3">
            <div>
              <h2 className="text-[10px] font-bold text-white/25 uppercase tracking-[0.15em] mb-0.5">Activity</h2>
              <p className="text-[11px] text-white/20">Recent @tradeous posts</p>
            </div>
            {!recentSorted.length ? (
              <div className="flex-1 flex flex-col items-center justify-center gap-2 py-10 text-center">
                <div className="w-10 h-10 rounded-2xl bg-white/[0.04] border border-white/[0.06] flex items-center justify-center text-xl">𝕏</div>
                <div className="text-[12px] text-white/25">No posts yet this session</div>
                <div className="text-[10px] text-white/15">Use a trigger or wait for the scheduler</div>
              </div>
            ) : (
              <div className="space-y-2 overflow-y-auto max-h-[400px] -mr-1 pr-1">
                {recentSorted.map((post, i) => {
                  const meta = TYPE_META[post.type] || TYPE_META.manual;
                  const isViaLocalPoster = post.text?.startsWith("Posted via local poster");
                  return (
                    <div key={`${post.id}-${i}`}
                      className="rounded-xl border border-white/[0.05] p-3 hover:bg-white/[0.04] transition-all duration-200 group"
                      style={{ background: "rgba(255,255,255,0.02)" }}>
                      <div className="flex items-center justify-between mb-1.5">
                        <div className="flex items-center gap-1.5">
                          <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${meta.dot}`} />
                          <span className={`text-[10px] font-semibold uppercase tracking-wide ${meta.accent}`}>{meta.label}</span>
                        </div>
                        <div className="flex items-center gap-2">
                          <span className="text-[10px] text-white/20">{timeAgo(post.ts)}</span>
                          {post.url && (
                            <a href={post.url} target="_blank" rel="noopener noreferrer"
                              className="text-[10px] text-white/20 hover:text-blue-400 transition-colors opacity-0 group-hover:opacity-100">
                              ↗ X
                            </a>
                          )}
                        </div>
                      </div>
                      <p className={`text-[12px] leading-relaxed whitespace-pre-wrap break-words line-clamp-3 ${
                        isViaLocalPoster ? "text-white/25 italic" : "text-white/55"
                      }`}>
                        {isViaLocalPoster ? `Sent via local poster (${post.type})` : post.text}
                      </p>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>

        {/* ── Diagnose (collapsed) ── */}
        <div className="rounded-xl border border-white/[0.06] overflow-hidden">
          <button onClick={() => { setShowDiag(!showDiag); if (!showDiag && !diagResult) runDiagnose(); }}
            className="w-full px-4 py-3 flex items-center justify-between text-left hover:bg-white/[0.03] transition-all duration-200">
            <span className="text-[11px] text-white/30">System diagnostics</span>
            <span className="text-[10px] text-white/20">{showDiag ? "▲ hide" : "▼ show"}</span>
          </button>
          {showDiag && (
            <div className="px-4 pb-4 space-y-3 border-t border-white/[0.06]">
              <div className="pt-3 flex items-center gap-2">
                <button onClick={runDiagnose} disabled={diagLoading}
                  className="px-3 py-1.5 rounded-lg text-[11px] font-medium border border-white/10 bg-white/[0.04] hover:bg-white/[0.08] text-white/50 hover:text-white transition-all disabled:opacity-40">
                  {diagLoading ? "Checking…" : "Refresh"}
                </button>
                <span className="text-[10px] text-white/20">Posted not working? Check credentials and cookie freshness.</span>
              </div>
              {diagResult && (
                <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
                  {Object.entries(diagResult).map(([k, v]) => (
                    <div key={k} className="rounded-lg bg-black/30 px-3 py-2">
                      <div className="text-[9px] text-white/25 uppercase tracking-wide mb-0.5">{k.replace(/_/g, " ")}</div>
                      <div className={`text-[11px] font-mono break-all ${
                        v === true ? "text-emerald-400" : v === false ? "text-red-400" :
                        String(v).includes("MISSING") ? "text-red-400" : "text-white/50"
                      }`}>{String(v)}</div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>

      </div>
    </div>
  );
}
