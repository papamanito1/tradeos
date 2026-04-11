"use client";
import { useState, useEffect, useCallback } from "react";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const LOCAL = "http://localhost:4242";  // local_poster.py — posts via Edge browser instantly

async function postViaLocal(text: string, type: string): Promise<{ ok: boolean; msg: string }> {
  try {
    const r = await fetch(`${LOCAL}/post`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, type }),
    });
    if (r.ok) {
      const d = await r.json();
      return { ok: true, msg: "Posting now…" };
    }
  } catch {}
  return { ok: false, msg: "local_poster.py not running" };
}

async function queueViaRailway(endpoint: string): Promise<{ ok: boolean; msg: string; queued?: boolean }> {
  try {
    const r = await fetch(`${API}${endpoint}`, { method: "POST" });
    const d = await r.json();
    if (d.ok) return { ok: true, msg: d.queued ? "Queued — posts within 60s" : "Posted!", queued: true };
    return { ok: false, msg: d.error || "Failed" };
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

interface Status {
  enabled: boolean;
  intro_posted: boolean;
  recent_posts: Post[];
  last_news: number;
  last_fear_greed: number;
  last_hot_take: number;
  last_philosophy: number;
  last_engagement: number;
  last_hourly: number;
  posts_per_hour?: number;
  total_posts?: number;
  next_post_in_sec?: number;
}

const TYPE_META: Record<string, { label: string; icon: string; accent: string; glow: string }> = {
  signal:       { label: "Trade Signal",   icon: "⚡", accent: "text-red-400",    glow: "shadow-red-500/20" },
  result:       { label: "Trade Result",   icon: "📊", accent: "text-violet-400", glow: "shadow-violet-500/20" },
  hourly:       { label: "25min Update",   icon: "◉",  accent: "text-blue-400",   glow: "shadow-blue-500/20" },
  news:         { label: "Crypto News",    icon: "◈",  accent: "text-amber-400",  glow: "shadow-amber-500/20" },
  fear_greed:   { label: "Fear & Greed",   icon: "◐",  accent: "text-orange-400", glow: "shadow-orange-500/20" },
  hot_take:     { label: "Hot Take",       icon: "◆",  accent: "text-rose-400",   glow: "shadow-rose-500/20" },
  philosophy:   { label: "Philosophy",     icon: "◇",  accent: "text-cyan-400",   glow: "shadow-cyan-500/20" },
  engagement:   { label: "Engagement",     icon: "○",  accent: "text-emerald-400",glow: "shadow-emerald-500/20" },
  algo_insight: { label: "Algo Insight",   icon: "🧠", accent: "text-purple-400", glow: "shadow-purple-500/20" },
  btc_move:     { label: "BTC Move",       icon: "📈", accent: "text-yellow-400", glow: "shadow-yellow-500/20" },
  daily:        { label: "Daily Summary",  icon: "◉",  accent: "text-indigo-400", glow: "shadow-indigo-500/20" },
  weekly:       { label: "Weekly Recap",   icon: "◈",  accent: "text-pink-400",   glow: "shadow-pink-500/20" },
  manual:       { label: "Manual",         icon: "◌",  accent: "text-white/50",   glow: "" },
  intro:        { label: "Intro",          icon: "◎",  accent: "text-emerald-400",glow: "shadow-emerald-500/20" },
};

function timeAgo(ts: number): string {
  const diff = Math.floor((Date.now() / 1000) - ts);
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

function nextIn(last: number, cooldown: number): string {
  if (!last) return "Ready";
  const remaining = cooldown - (Date.now() / 1000 - last);
  if (remaining <= 0) return "Ready";
  const h = Math.floor(remaining / 3600);
  const m = Math.floor((remaining % 3600) / 60);
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

interface TriggerCardProps {
  icon: string;
  label: string;
  description: string;
  nextPost: string;
  endpoint: string;
  onTriggered: () => void;
}

function TriggerCard({ icon, label, description, nextPost, endpoint, onTriggered }: TriggerCardProps) {
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; msg: string } | null>(null);
  const isReady = nextPost === "Ready";

  const trigger = async () => {
    setLoading(true);
    setResult(null);
    const res = await queueViaRailway(endpoint);
    if (res.ok) {
      setResult({ ok: true, msg: res.queued ? "Queued ✓" : "Posted ✓ on X" });
      onTriggered();
    } else {
      setResult({ ok: false, msg: res.msg });
    }
    setLoading(false);
    setTimeout(() => setResult(null), 6000);
  };

  return (
    <div className="group relative rounded-2xl bg-white/[0.04] border border-white/[0.07] p-5 hover:bg-white/[0.07] hover:border-white/[0.12] transition-all duration-300 overflow-hidden">
      {/* Subtle shimmer on hover */}
      <div className="absolute inset-0 opacity-0 group-hover:opacity-100 transition-opacity duration-500 pointer-events-none bg-gradient-to-br from-white/[0.04] via-transparent to-transparent rounded-2xl" />

      <div className="relative flex flex-col gap-4">
        <div className="flex items-start justify-between">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-xl bg-white/[0.06] flex items-center justify-center text-lg shrink-0 border border-white/[0.08]">
              {icon}
            </div>
            <div>
              <div className="text-[13px] font-semibold text-white tracking-tight">{label}</div>
              <div className="text-[11px] text-white/40 mt-0.5 leading-snug">{description}</div>
            </div>
          </div>
          <div className={`text-[10px] font-medium px-2 py-0.5 rounded-full shrink-0 ml-2 ${
            isReady
              ? "bg-emerald-500/15 text-emerald-400 border border-emerald-500/20"
              : "bg-white/[0.05] text-white/30 border border-white/[0.06]"
          }`}>
            {isReady ? "● Ready" : nextPost}
          </div>
        </div>

        <button
          onClick={trigger}
          disabled={loading}
          className={`w-full py-2.5 rounded-xl text-[13px] font-medium transition-all duration-200 border ${
            loading
              ? "bg-white/[0.04] text-white/30 border-white/[0.06] cursor-wait"
              : result
                ? result.ok
                  ? "bg-emerald-500/15 text-emerald-400 border-emerald-500/20"
                  : "bg-red-500/10 text-red-400 border-red-500/15"
                : "bg-white/[0.06] text-white/80 border-white/[0.08] hover:bg-white/[0.1] hover:text-white cursor-pointer active:scale-[0.98]"
          }`}
        >
          {loading ? (
            <span className="flex items-center justify-center gap-2">
              <span className="w-3 h-3 rounded-full border border-white/20 border-t-white/60 animate-spin" />
              Queuing…
            </span>
          ) : result ? (
            result.msg
          ) : (
            "Post Now"
          )}
        </button>
      </div>
    </div>
  );
}

function FireAllButton({ onDone }: { onDone: () => void }) {
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<string | null>(null);

  const fireAll = async () => {
    setLoading(true);
    setResult(null);
    try {
      const r = await fetch(`${API}/api/x-agent/fire-all`, { method: "POST" });
      const d = await r.json();
      if (d.ok) {
        const n = Object.values(d.results || {}).filter((v) => v !== "failed").length;
        setResult(`${n} queued`);
        onDone();
      } else {
        setResult("Failed");
      }
    } catch {
      setResult("Error");
    }
    setLoading(false);
    setTimeout(() => setResult(null), 4000);
  };

  return (
    <button
      onClick={fireAll}
      disabled={loading}
      className={`flex items-center gap-1.5 px-4 py-2 rounded-xl text-[13px] font-medium transition-all duration-200 border ${
        loading
          ? "bg-white/[0.04] text-white/30 border-white/[0.06] cursor-wait"
          : "bg-white/[0.07] text-white/80 border-white/[0.1] hover:bg-white/[0.12] hover:text-white cursor-pointer"
      }`}
    >
      {loading ? (
        <span className="w-3 h-3 rounded-full border border-white/20 border-t-white/60 animate-spin" />
      ) : (
        <span className="text-base">⚡</span>
      )}
      {result || "Post All"}
    </button>
  );
}

export default function XAgentPage() {
  const [status, setStatus] = useState<Status | null>(null);
  const [manualText, setManualText] = useState("");
  const [posting, setPosting] = useState(false);
  const [postResult, setPostResult] = useState<{ ok: boolean; msg: string } | null>(null);
  const charCount = manualText.length;

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
    await fetch(`${API}/api/x-agent/reset-cooldowns`, { method: "POST" });
    fetchStatus();
  };

  const postManual = async () => {
    if (!manualText.trim() || charCount > 280) return;
    setPosting(true);
    setPostResult(null);

    // Try local poster first (instant), then Railway queue as fallback
    const localRes = await postViaLocal(manualText.trim(), "manual");
    if (localRes.ok) {
      setPostResult({ ok: true, msg: "Posting now via local browser…" });
      setManualText("");
      fetchStatus();
    } else {
      // Fallback: Railway queue
      const railRes = await queueViaRailway(`/api/x-agent/post`);
      // Railway /post needs body — call directly
      try {
        const r = await fetch(`${API}/api/x-agent/post`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text: manualText.trim() }),
        });
        const d = await r.json();
        if (d.ok) {
          setPostResult({ ok: true, msg: "Queued — local_poster.py will send it" });
          setManualText("");
          fetchStatus();
        } else {
          setPostResult({ ok: false, msg: d.error || "Failed — start local_poster.py" });
        }
      } catch {
        setPostResult({ ok: false, msg: "Start local_poster.py to enable posting" });
      }
    }

    setPosting(false);
    setTimeout(() => setPostResult(null), 7000);
  };

  const todayPosts = status?.recent_posts.filter(p => Date.now() / 1000 - p.ts < 86400).length ?? 0;
  const totalPosts = status?.total_posts ?? status?.recent_posts.length ?? 0;
  const nextHourly = nextIn(status?.last_hourly || 0, 1500);
  const nextPostSec = status?.next_post_in_sec ?? 0;
  const nextPostLabel = nextPostSec <= 0 ? "Now" : nextPostSec < 60 ? `${Math.round(nextPostSec)}s` : `${Math.round(nextPostSec / 60)}m`;
  const postsPerHour = status?.posts_per_hour ?? 0;

  const triggers = [
    {
      icon: "◈", label: "Crypto News",
      description: "CoinDesk / CoinTelegraph headline with sharp commentary",
      nextPost: nextIn(status?.last_news || 0, 7200),
      endpoint: "/api/x-agent/trigger/news",
    },
    {
      icon: "◐", label: "Fear & Greed",
      description: "Alternative.me index with market psychology take",
      nextPost: nextIn(status?.last_fear_greed || 0, 14400),
      endpoint: "/api/x-agent/trigger/fear-greed",
    },
    {
      icon: "◆", label: "Hot Take",
      description: "Spicy market opinion engineered for engagement",
      nextPost: nextIn(status?.last_hot_take || 0, 28800),
      endpoint: "/api/x-agent/trigger/hot-take",
    },
    {
      icon: "◇", label: "Philosophy",
      description: "Trading wisdom from legends, twisted by algorithm",
      nextPost: nextIn(status?.last_philosophy || 0, 43200),
      endpoint: "/api/x-agent/trigger/philosophy",
    },
    {
      icon: "○", label: "Engagement",
      description: "Audience question designed to drive replies",
      nextPost: nextIn(status?.last_engagement || 0, 43200),
      endpoint: "/api/x-agent/trigger/engagement",
    },
    {
      icon: "◉", label: "25-min Update",
      description: "Live BTC price, regime, move %, and witty commentary",
      nextPost: nextIn(status?.last_hourly || 0, 1500),
      endpoint: "/api/x-agent/trigger/hourly",
    },
    {
      icon: "🧠", label: "Algo Insight",
      description: "Transparency post: how the system works, risk params, strategy logic",
      nextPost: nextIn(status?.last_hourly || 0, 10800),
      endpoint: "/api/x-agent/trigger/algo-insight",
    },
  ];

  return (
    <div className="min-h-screen text-white">
      <div className="max-w-5xl mx-auto px-1 py-2 space-y-8">

        {/* Offline banner */}
        {status && !status.enabled && (
          <div className="rounded-2xl bg-red-500/[0.08] border border-red-500/20 p-4 flex items-center gap-4 backdrop-blur-sm">
            <div className="w-8 h-8 rounded-full bg-red-500/15 flex items-center justify-center shrink-0">
              <span className="text-red-400 text-sm">!</span>
            </div>
            <div className="flex-1 min-w-0">
              <div className="text-[13px] font-semibold text-red-300">X Agent offline — credentials missing</div>
              <div className="text-[12px] text-red-400/70 mt-0.5">
                Add <code className="font-mono bg-red-500/10 px-1.5 py-0.5 rounded text-red-300">X_AUTH_TOKEN</code> and{" "}
                <code className="font-mono bg-red-500/10 px-1.5 py-0.5 rounded text-red-300">X_CT0</code>{" "}
                to Railway environment variables
              </div>
            </div>
          </div>
        )}

        {/* Hero Header */}
        <div className="relative rounded-3xl overflow-hidden">
          {/* Background gradient */}
          <div className="absolute inset-0 bg-gradient-to-br from-white/[0.05] via-transparent to-white/[0.02] rounded-3xl" />
          <div className="absolute inset-0 border border-white/[0.08] rounded-3xl pointer-events-none" />

          <div className="relative px-8 py-8 flex items-center justify-between gap-4">
            <div className="flex items-center gap-5">
              {/* X logo area */}
              <div className="relative w-14 h-14 rounded-2xl bg-white/[0.07] border border-white/[0.1] flex items-center justify-center shrink-0">
                <span className="text-2xl font-bold tracking-tighter text-white">𝕏</span>
                {status?.enabled && (
                  <span className="absolute -top-1 -right-1 w-3 h-3 rounded-full bg-emerald-500 border-2 border-[#0d0d0d] animate-pulse" />
                )}
              </div>
              <div>
                <div className="flex items-center gap-3">
                  <h1 className="text-2xl font-bold tracking-tight text-white">X Agent</h1>
                  <span className="text-[13px] text-white/40 font-medium">@tradeous</span>
                </div>
                <p className="text-[13px] text-white/40 mt-1 font-light">
                  AI content engine · Posts every 25 min · Memory-aware · Never repeats
                </p>
              </div>
            </div>

            <div className="flex items-center gap-2 shrink-0">
              {/* Status pill */}
              <div className={`flex items-center gap-2 px-3.5 py-2 rounded-xl text-[12px] font-semibold border backdrop-blur-sm ${
                status?.enabled
                  ? "bg-emerald-500/10 text-emerald-400 border-emerald-500/20"
                  : "bg-red-500/10 text-red-400 border-red-500/20"
              }`}>
                <span className={`w-1.5 h-1.5 rounded-full ${status?.enabled ? "bg-emerald-400 animate-pulse" : "bg-red-400"}`} />
                {status?.enabled ? "LIVE" : "OFFLINE"}
              </div>

              <FireAllButton onDone={fetchStatus} />

              <button
                onClick={resetCooldowns}
                className="px-3.5 py-2 rounded-xl text-[12px] font-medium text-white/40 border border-white/[0.07] hover:bg-white/[0.06] hover:text-white/70 transition-all duration-200"
              >
                Reset
              </button>
            </div>
          </div>
        </div>

        {/* Stats row */}
        <div className="grid grid-cols-4 gap-3">
          {[
            { value: todayPosts, label: "Posts today", sub: "last 24 hours" },
            { value: totalPosts, label: "Total posts", sub: "this session" },
            { value: nextHourly === "Ready" ? "Now" : nextHourly, label: "Next post", sub: "25-min cadence" },
            { value: postsPerHour > 0 ? `${postsPerHour}/h` : "—", label: "Post rate", sub: "live average" },
          ].map((s) => (
            <div key={s.label} className="rounded-2xl bg-white/[0.04] border border-white/[0.07] p-5 relative overflow-hidden group hover:bg-white/[0.06] transition-all duration-300">
              <div className="absolute inset-0 bg-gradient-to-br from-white/[0.03] to-transparent opacity-0 group-hover:opacity-100 transition-opacity duration-500 pointer-events-none rounded-2xl" />
              <div className="relative">
                <div className="text-3xl font-bold tracking-tight text-white tabular-nums">{s.value}</div>
                <div className="text-[12px] font-medium text-white/60 mt-1">{s.label}</div>
                <div className="text-[11px] text-white/25 mt-0.5">{s.sub}</div>
              </div>
            </div>
          ))}
        </div>

        {/* Content Triggers */}
        <div>
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-[11px] font-semibold text-white/30 uppercase tracking-[0.12em]">Content Triggers</h2>
            <span className="text-[11px] text-white/20">Queued posts sent via local poster</span>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {triggers.map((t) => (
              <TriggerCard key={t.endpoint} {...t} onTriggered={fetchStatus} />
            ))}
          </div>
        </div>

        {/* Compose + Recent Posts side by side on large screens */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">

          {/* Manual Compose */}
          <div className="rounded-2xl bg-white/[0.04] border border-white/[0.07] p-6 flex flex-col gap-4">
            <div>
              <h2 className="text-[11px] font-semibold text-white/30 uppercase tracking-[0.12em] mb-0.5">Compose</h2>
              <p className="text-[11px] text-white/20">Post manually as @tradeous</p>
            </div>

            <div className="relative flex-1">
              <textarea
                value={manualText}
                onChange={(e) => setManualText(e.target.value)}
                placeholder="What's happening in the market…"
                maxLength={280}
                rows={5}
                className="w-full bg-white/[0.04] border border-white/[0.08] rounded-xl p-4 text-[13px] text-white placeholder-white/20 resize-none focus:outline-none focus:border-white/20 focus:bg-white/[0.06] transition-all duration-200 leading-relaxed"
              />
              {/* Char ring */}
              <div className="absolute bottom-3 right-3 flex items-center gap-1.5">
                <svg className="w-5 h-5 -rotate-90" viewBox="0 0 20 20">
                  <circle cx="10" cy="10" r="7" fill="none" stroke="rgba(255,255,255,0.1)" strokeWidth="2" />
                  <circle
                    cx="10" cy="10" r="7" fill="none"
                    stroke={charCount > 260 ? "rgb(239 68 68)" : charCount > 200 ? "rgb(251 191 36)" : "rgba(255,255,255,0.4)"}
                    strokeWidth="2"
                    strokeDasharray={`${2 * Math.PI * 7}`}
                    strokeDashoffset={`${2 * Math.PI * 7 * (1 - charCount / 280)}`}
                    strokeLinecap="round"
                    className="transition-all duration-150"
                  />
                </svg>
                <span className={`text-[10px] font-mono ${charCount > 260 ? "text-red-400" : "text-white/25"}`}>
                  {280 - charCount}
                </span>
              </div>
            </div>

            {postResult && (
              <div className={`text-[12px] text-center py-2.5 rounded-xl border ${
                postResult.ok
                  ? "bg-emerald-500/10 text-emerald-400 border-emerald-500/15"
                  : "bg-red-500/10 text-red-400 border-red-500/15"
              }`}>
                {postResult.msg}
              </div>
            )}

            <button
              onClick={postManual}
              disabled={posting || !manualText.trim() || charCount > 280}
              className={`py-3 rounded-xl text-[13px] font-semibold tracking-tight transition-all duration-200 border ${
                posting || !manualText.trim() || charCount > 280
                  ? "bg-white/[0.03] text-white/20 border-white/[0.05] cursor-not-allowed"
                  : "bg-white text-black border-white hover:bg-white/90 cursor-pointer active:scale-[0.98]"
              }`}
            >
              {posting ? (
                <span className="flex items-center justify-center gap-2">
                  <span className="w-3 h-3 rounded-full border border-black/20 border-t-black/70 animate-spin" />
                  Queuing…
                </span>
              ) : "Post Tweet"}
            </button>
          </div>

          {/* Recent Posts */}
          <div className="rounded-2xl bg-white/[0.04] border border-white/[0.07] p-6 flex flex-col gap-4 min-h-[300px]">
            <div>
              <h2 className="text-[11px] font-semibold text-white/30 uppercase tracking-[0.12em] mb-0.5">Activity</h2>
              <p className="text-[11px] text-white/20">Recent posts from @tradeous</p>
            </div>

            {!status?.recent_posts.length ? (
              <div className="flex-1 flex flex-col items-center justify-center gap-2 text-center py-8">
                <div className="w-10 h-10 rounded-2xl bg-white/[0.04] border border-white/[0.07] flex items-center justify-center text-xl">
                  𝕏
                </div>
                <div className="text-[12px] text-white/30">No posts this session</div>
                <div className="text-[11px] text-white/15">Trigger a post or wait for the scheduler</div>
              </div>
            ) : (
              <div className="flex-1 overflow-y-auto space-y-2 -mr-1 pr-1 max-h-[380px] scrollbar-thin">
                {[...status.recent_posts].reverse().map((post, i) => {
                  const meta = TYPE_META[post.type] || TYPE_META.manual;
                  return (
                    <div
                      key={`${post.id}-${i}`}
                      className="rounded-xl bg-white/[0.03] border border-white/[0.06] p-3.5 hover:bg-white/[0.06] transition-all duration-200 group"
                    >
                      <div className="flex items-center justify-between mb-2">
                        <div className="flex items-center gap-2">
                          <span className={`text-[11px] font-semibold ${meta.accent}`}>
                            {meta.icon} {meta.label}
                          </span>
                        </div>
                        <div className="flex items-center gap-2">
                          <span className="text-[10px] text-white/25">{timeAgo(post.ts)}</span>
                          {post.url && (
                            <a
                              href={post.url}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="text-[10px] text-white/25 hover:text-blue-400 transition-colors"
                            >
                              ↗
                            </a>
                          )}
                        </div>
                      </div>
                      <p className="text-[12px] text-white/50 leading-relaxed whitespace-pre-wrap break-words line-clamp-3">
                        {post.text}
                      </p>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>

      </div>
    </div>
  );
}
