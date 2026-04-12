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
    if (d.posted) return { ok: true, msg: "Posted ✓ on X" };
    if (d.text) {
      try {
        const lr = await fetch(`${LOCAL}/post`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text: d.text, type: d.type || "auto" }),
        });
        if (lr.ok) return { ok: true, msg: "Posted ✓ via local poster" };
      } catch {}
    }
    return { ok: true, msg: "Queued — start local_poster.py to send" };
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
  mood?: string;
  ai_brain?: string;
  daily_posts: number;
  daily_budget: number;
  last_signal: number;
  last_result: number;
  last_contrarian: number;
  last_psychology: number;
  last_poll: number;
  last_trending_hook?: number;
  last_viral_commentary?: number;
  last_bold_prediction?: number;
  recent_posts: Post[];
  posts_per_hour?: number;
  total_posts?: number;
  next_post_in_sec?: number;
  last_error?: string;
  queue_length?: number;
}

const TYPE_META: Record<string, { label: string; icon: string; accent: string }> = {
  signal:            { label: "Trade Signal",       icon: "⚡", accent: "text-red-400"     },
  result:            { label: "Trade Result",       icon: "📊", accent: "text-violet-400"  },
  contrarian:        { label: "Contrarian Take",    icon: "◆",  accent: "text-rose-400"    },
  psychology_thread: { label: "Psychology Thread",  icon: "◇",  accent: "text-cyan-400"    },
  poll:              { label: "Poll",               icon: "○",  accent: "text-emerald-400" },
  trade_breakdown:   { label: "Trade Breakdown",    icon: "◈",  accent: "text-amber-400"   },
  trending_hook:     { label: "Trending Hook",      icon: "📈", accent: "text-blue-400"    },
  viral_commentary:  { label: "Viral Commentary",   icon: "◉",  accent: "text-indigo-400"  },
  bold_prediction:   { label: "Bold Prediction",    icon: "◎",  accent: "text-pink-400"    },
  daily:             { label: "Daily Summary",      icon: "◉",  accent: "text-indigo-400"  },
  weekly:            { label: "Weekly Recap",       icon: "◈",  accent: "text-pink-400"    },
  intro:             { label: "Intro",              icon: "◎",  accent: "text-emerald-400" },
  manual:            { label: "Manual",             icon: "◌",  accent: "text-white/50"    },
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

  return (
    <div className="group relative rounded-2xl bg-white/[0.04] border border-white/[0.07] p-5 hover:bg-white/[0.07] hover:border-white/[0.12] transition-all duration-300 overflow-hidden">
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
          <div className={`text-[10px] font-medium px-2 py-0.5 rounded-full shrink-0 ml-2 border ${
            isReady
              ? "bg-emerald-500/15 text-emerald-400 border-emerald-500/20"
              : "bg-white/[0.05] text-white/30 border-white/[0.06]"
          }`}>
            {isReady ? "● Ready" : nextPost}
          </div>
        </div>
        <button onClick={trigger} disabled={loading}
          className={`w-full py-2.5 rounded-xl text-[13px] font-medium transition-all duration-200 border ${
            loading
              ? "bg-white/[0.04] text-white/30 border-white/[0.06] cursor-wait"
              : result
                ? result.ok
                  ? "bg-emerald-500/15 text-emerald-400 border-emerald-500/20"
                  : "bg-red-500/10 text-red-400 border-red-500/15"
                : "bg-white/[0.06] text-white/80 border-white/[0.08] hover:bg-white/[0.1] hover:text-white cursor-pointer active:scale-[0.98]"
          }`}>
          {loading ? (
            <span className="flex items-center justify-center gap-2">
              <span className="w-3 h-3 rounded-full border border-white/20 border-t-white/60 animate-spin" />
              Queuing…
            </span>
          ) : result ? result.msg : "Post Now"}
        </button>
      </div>
    </div>
  );
}

function FireAllButton({ onDone }: { onDone: () => void }) {
  const [loading, setLoading] = useState(false);
  const [result, setResult]   = useState<string | null>(null);

  const fireAll = async () => {
    setLoading(true);
    setResult(null);
    try {
      const token = typeof window !== "undefined" ? localStorage.getItem("tradeos_token") : null;
      const r = await fetch(`${API}/api/x-agent/fire-all`, {
        method: "POST",
        headers: token ? { "Authorization": `Bearer ${token}` } : {},
      });
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
    <button onClick={fireAll} disabled={loading}
      className={`flex items-center gap-1.5 px-4 py-2 rounded-xl text-[13px] font-medium transition-all duration-200 border ${
        loading
          ? "bg-white/[0.04] text-white/30 border-white/[0.06] cursor-wait"
          : "bg-white/[0.07] text-white/80 border-white/[0.1] hover:bg-white/[0.12] hover:text-white cursor-pointer"
      }`}>
      {loading
        ? <span className="w-3 h-3 rounded-full border border-white/20 border-t-white/60 animate-spin" />
        : <span className="text-base">⚡</span>}
      {result || "Post All"}
    </button>
  );
}

export default function XAgentPage() {
  const [status, setStatus]       = useState<Status | null>(null);
  const [manualText, setManualText] = useState("");
  const [posting, setPosting]     = useState(false);
  const [postResult, setPostResult] = useState<{ ok: boolean; msg: string } | null>(null);
  const [localOnline, setLocalOnline] = useState<boolean | null>(null);
  const charCount = manualText.length;

  useEffect(() => {
    const check = async () => {
      try {
        await fetch(`${LOCAL}/post`, { method: "OPTIONS", signal: AbortSignal.timeout(1500) });
        setLocalOnline(true);
      } catch {
        setLocalOnline(false);
      }
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
      if (d.ok) {
        setPostResult({ ok: true, msg: "Posted ✓" });
        setManualText("");
        fetchStatus();
      } else {
        setPostResult({ ok: false, msg: d.error || "Failed" });
      }
    } catch {
      setPostResult({ ok: false, msg: "Cannot reach backend" });
    }

    setPosting(false);
    setTimeout(() => setPostResult(null), 7000);
  };

  const [diagResult, setDiagResult] = useState<Record<string, unknown> | null>(null);
  const [diagLoading, setDiagLoading] = useState(false);

  const runDiagnose = async () => {
    setDiagLoading(true);
    setDiagResult(null);
    try {
      const r = await fetch(`${API}/api/x-agent/diagnose`);
      setDiagResult(await r.json());
    } catch {
      setDiagResult({ error: "Cannot reach backend" });
    }
    setDiagLoading(false);
  };

  const todayPosts   = status?.daily_posts ?? status?.recent_posts.filter(p => Date.now() / 1000 - p.ts < 86400).length ?? 0;
  const dailyBudget  = status?.daily_budget ?? 5;
  const totalPosts   = status?.total_posts ?? status?.recent_posts.length ?? 0;

  // cooldowns (seconds)
  const CONTRARIAN_CD  = 21600;
  const PSYCHOLOGY_CD  = 43200;
  const POLL_CD        = 43200;
  const BREAKDOWN_CD   = 43200;

  const triggers = [
    {
      icon: "◆", label: "Contrarian Take",
      description: "Cold data-backed opinion against the crowd narrative",
      nextPost: nextIn(status?.last_contrarian || 0, CONTRARIAN_CD),
      endpoint: "/api/x-agent/trigger/contrarian",
    },
    {
      icon: "◇", label: "Psychology Thread",
      description: "2-part educational thread on trading psychology",
      nextPost: nextIn(status?.last_psychology || 0, PSYCHOLOGY_CD),
      endpoint: "/api/x-agent/trigger/psychology",
    },
    {
      icon: "○", label: "Poll",
      description: "Structured market poll with algo's answer to follow",
      nextPost: nextIn(status?.last_poll || 0, POLL_CD),
      endpoint: "/api/x-agent/trigger/poll",
    },
    {
      icon: "◈", label: "Trade Breakdown",
      description: "Technical thread explaining the algo's decision logic",
      nextPost: nextIn(status?.last_result || 0, BREAKDOWN_CD),
      endpoint: "/api/x-agent/trigger/breakdown",
    },
  ];

  return (
    <div className="min-h-screen text-white">
      <div className="max-w-5xl mx-auto px-1 py-2 space-y-8">

        {/* local_poster.py status */}
        {localOnline === false && (
          <div className="rounded-2xl border p-4 flex items-start gap-4"
            style={{ background: "rgba(251,191,36,0.06)", borderColor: "rgba(251,191,36,0.2)" }}>
            <div className="w-8 h-8 rounded-full flex items-center justify-center shrink-0 mt-0.5"
              style={{ background: "rgba(251,191,36,0.12)" }}>
              <span className="text-sm">⚡</span>
            </div>
            <div className="flex-1 min-w-0">
              <div className="text-[13px] font-semibold text-yellow-300">local_poster.py not running — tweets will queue but not send</div>
              <div className="text-[11px] text-yellow-400/60 mt-1 leading-relaxed">
                X blocks server IPs, so posts are sent from your PC&apos;s residential IP via curl_cffi. Open a terminal and run:
              </div>
              <code className="block mt-2 text-[11px] font-mono bg-black/40 border border-white/10 rounded-lg px-3 py-2 text-yellow-300 select-all">
                python local_poster.py
              </code>
            </div>
            <div className="flex items-center gap-1.5 shrink-0 text-[10px] text-yellow-400/50">
              <span className="w-1.5 h-1.5 rounded-full bg-yellow-500/50" />OFFLINE
            </div>
          </div>
        )}
        {localOnline === true && (
          <div className="rounded-2xl border p-3 flex items-center gap-3"
            style={{ background: "rgba(34,197,94,0.04)", borderColor: "rgba(34,197,94,0.15)" }}>
            <span className="w-2 h-2 rounded-full bg-green-400 animate-pulse shrink-0" />
            <span className="text-[12px] text-green-400 font-medium">local_poster.py running — posts go out from your PC instantly</span>
          </div>
        )}

        {/* Offline banner */}
        {status && !status.enabled && (
          <div className="rounded-2xl bg-red-500/[0.08] border border-red-500/20 p-4 flex items-center gap-4">
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
          <div className="absolute inset-0 bg-gradient-to-br from-white/[0.05] via-transparent to-white/[0.02] rounded-3xl" />
          <div className="absolute inset-0 border border-white/[0.08] rounded-3xl pointer-events-none" />
          <div className="relative px-8 py-8 flex items-center justify-between gap-4">
            <div className="flex items-center gap-5">
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
                  {status?.mood && (
                    <span className="text-[10px] text-white/30 font-mono px-2 py-0.5 rounded-full border border-white/10">{status.mood}</span>
                  )}
                </div>
                <p className="text-[13px] text-white/40 mt-1 font-light">
                  Cold, receipt-heavy · max {dailyBudget} posts/day · signals + high-value content only
                </p>
              </div>
            </div>
            <div className="flex items-center gap-2 shrink-0">
              <div className={`flex items-center gap-2 px-3.5 py-2 rounded-xl text-[12px] font-semibold border backdrop-blur-sm ${
                status?.enabled
                  ? "bg-emerald-500/10 text-emerald-400 border-emerald-500/20"
                  : "bg-red-500/10 text-red-400 border-red-500/20"
              }`}>
                <span className={`w-1.5 h-1.5 rounded-full ${status?.enabled ? "bg-emerald-400 animate-pulse" : "bg-red-400"}`} />
                {status?.enabled ? "LIVE" : "OFFLINE"}
              </div>
              <FireAllButton onDone={fetchStatus} />
              <button onClick={resetCooldowns}
                className="px-3.5 py-2 rounded-xl text-[12px] font-medium text-white/40 border border-white/[0.07] hover:bg-white/[0.06] hover:text-white/70 transition-all duration-200">
                Reset
              </button>
            </div>
          </div>
        </div>

        {/* Last error banner */}
        {status?.last_error && (
          <div className="rounded-2xl border border-red-500/20 bg-red-500/5 p-4 flex items-start gap-3">
            <span className="text-red-400 text-sm shrink-0 mt-0.5">✗</span>
            <div className="flex-1 min-w-0">
              <div className="text-[12px] font-semibold text-red-300 mb-0.5">Last posting error</div>
              <code className="text-[11px] text-red-400/70 font-mono break-all">{status.last_error}</code>
              {(status.last_error.includes("ghost") || status.last_error.includes("Queued for local")) ? (
                <div className="text-[11px] text-amber-400/70 mt-1.5">
                  Railway&apos;s datacenter IP is blocked by X. Run <code className="bg-black/30 px-1 rounded">python local_poster.py</code> on your PC — tweets are queued and will send the moment it starts.
                </div>
              ) : status.last_error.includes("403") || status.last_error.includes("expired") ? (
                <div className="text-[11px] text-amber-400/70 mt-1.5">
                  Cookies expired — run <code className="bg-black/30 px-1 rounded">python grab_cookies_and_tweet.py</code>, update <code className="bg-black/30 px-1 rounded">X_AUTH_TOKEN</code> + <code className="bg-black/30 px-1 rounded">X_CT0</code> in Railway Variables, then redeploy.
                </div>
              ) : status.last_error.includes("not configured") ? (
                <div className="text-[11px] text-amber-400/70 mt-1.5">
                  X cookies missing in Railway. Go to Railway → Variables and add <code className="bg-black/30 px-1 rounded">X_AUTH_TOKEN</code> and <code className="bg-black/30 px-1 rounded">X_CT0</code>.
                </div>
              ) : null}
            </div>
          </div>
        )}

        {/* Diagnose panel */}
        <div className="rounded-2xl bg-white/[0.03] border border-white/[0.06] p-4 flex items-center gap-3 flex-wrap">
          <span className="text-[11px] text-white/30">Posting not working?</span>
          <button onClick={runDiagnose} disabled={diagLoading}
            className="px-3 py-1.5 rounded-lg text-[11px] font-medium border border-white/10 bg-white/[0.05] hover:bg-white/[0.1] text-white/60 hover:text-white transition-all disabled:opacity-40">
            {diagLoading ? "Checking…" : "Run Diagnose"}
          </button>
          {diagResult && (
            <div className="w-full mt-2 grid grid-cols-2 sm:grid-cols-3 gap-2">
              {Object.entries(diagResult).map(([k, v]) => (
                <div key={k} className="rounded-lg bg-black/30 px-3 py-2">
                  <div className="text-[9px] text-white/30 uppercase tracking-wide mb-0.5">{k.replace(/_/g, " ")}</div>
                  <div className={`text-[11px] font-mono break-all ${
                    v === true ? "text-green-400" : v === false ? "text-red-400" :
                    String(v).includes("MISSING") ? "text-red-400" : "text-white/60"
                  }`}>{String(v)}</div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Stats row */}
        <div className="grid grid-cols-4 gap-3">
          {[
            { value: todayPosts,             label: "Posts today",    sub: `${dailyBudget} max daily budget` },
            { value: totalPosts,             label: "Total posts",    sub: "this session"                    },
            { value: status?.ai_brain ?? "—", label: "AI brain",     sub: "content generator"               },
            { value: dailyBudget - todayPosts >= 0 ? `${dailyBudget - todayPosts}` : "0", label: "Budget left", sub: "posts remaining today" },
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
            <span className="text-[11px] text-white/20">High-value posts · receipt-heavy · no spam</span>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-2 gap-3">
            {triggers.map((t) => (
              <TriggerCard key={t.endpoint} {...t} onTriggered={fetchStatus} />
            ))}
          </div>
        </div>

        {/* Compose + Recent Posts */}
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
                placeholder="Cold. Confident. Data-backed."
                maxLength={280}
                rows={5}
                className="w-full bg-white/[0.04] border border-white/[0.08] rounded-xl p-4 text-[13px] text-white placeholder-white/20 resize-none focus:outline-none focus:border-white/20 focus:bg-white/[0.06] transition-all duration-200 leading-relaxed"
              />
              <div className="absolute bottom-3 right-3 flex items-center gap-1.5">
                <svg className="w-5 h-5 -rotate-90" viewBox="0 0 20 20">
                  <circle cx="10" cy="10" r="7" fill="none" stroke="rgba(255,255,255,0.1)" strokeWidth="2" />
                  <circle cx="10" cy="10" r="7" fill="none"
                    stroke={charCount > 260 ? "rgb(239 68 68)" : charCount > 200 ? "rgb(251 191 36)" : "rgba(255,255,255,0.4)"}
                    strokeWidth="2"
                    strokeDasharray={`${2 * Math.PI * 7}`}
                    strokeDashoffset={`${2 * Math.PI * 7 * (1 - charCount / 280)}`}
                    strokeLinecap="round"
                    className="transition-all duration-150" />
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
              }`}>{postResult.msg}</div>
            )}
            <button onClick={postManual} disabled={posting || !manualText.trim() || charCount > 280}
              className={`py-3 rounded-xl text-[13px] font-semibold tracking-tight transition-all duration-200 border ${
                posting || !manualText.trim() || charCount > 280
                  ? "bg-white/[0.03] text-white/20 border-white/[0.05] cursor-not-allowed"
                  : "bg-white text-black border-white hover:bg-white/90 cursor-pointer active:scale-[0.98]"
              }`}>
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
                <div className="w-10 h-10 rounded-2xl bg-white/[0.04] border border-white/[0.07] flex items-center justify-center text-xl">𝕏</div>
                <div className="text-[12px] text-white/30">No posts this session</div>
                <div className="text-[11px] text-white/15">Trigger a post or wait for the scheduler</div>
              </div>
            ) : (
              <div className="flex-1 overflow-y-auto space-y-2 -mr-1 pr-1 max-h-[380px]">
                {[...status.recent_posts].reverse().map((post, i) => {
                  const meta = TYPE_META[post.type] || TYPE_META.manual;
                  return (
                    <div key={`${post.id}-${i}`}
                      className="rounded-xl bg-white/[0.03] border border-white/[0.06] p-3.5 hover:bg-white/[0.06] transition-all duration-200">
                      <div className="flex items-center justify-between mb-2">
                        <span className={`text-[11px] font-semibold ${meta.accent}`}>
                          {meta.icon} {meta.label}
                        </span>
                        <div className="flex items-center gap-2">
                          <span className="text-[10px] text-white/25">{timeAgo(post.ts)}</span>
                          {post.url && (
                            <a href={post.url} target="_blank" rel="noopener noreferrer"
                              className="text-[10px] text-white/25 hover:text-blue-400 transition-colors">↗</a>
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
