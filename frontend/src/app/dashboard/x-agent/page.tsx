"use client";
import { useState, useEffect, useCallback } from "react";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

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
}

const TYPE_META: Record<string, { label: string; icon: string; color: string }> = {
  signal:     { label: "Trade Signal",    icon: "🚨", color: "text-red-400" },
  result:     { label: "Trade Result",    icon: "📊", color: "text-purple-400" },
  hourly:     { label: "Hourly Update",  icon: "🤖", color: "text-blue-400" },
  news:       { label: "Crypto News",    icon: "📰", color: "text-yellow-400" },
  fear_greed: { label: "Fear & Greed",   icon: "😱", color: "text-orange-400" },
  hot_take:   { label: "Hot Take",       icon: "🔥", color: "text-red-300" },
  philosophy: { label: "Philosophy",     icon: "🧠", color: "text-cyan-400" },
  engagement: { label: "Engagement",     icon: "💬", color: "text-green-400" },
  daily:      { label: "Daily Summary",  icon: "📅", color: "text-indigo-400" },
  weekly:     { label: "Weekly Recap",   icon: "📈", color: "text-pink-400" },
  manual:     { label: "Manual",         icon: "✍️", color: "text-gray-400" },
  intro:      { label: "Intro",          icon: "🎉", color: "text-emerald-400" },
};

function timeAgo(ts: number): string {
  const diff = Math.floor((Date.now() / 1000) - ts);
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

function nextIn(last: number, cooldown: number): string {
  if (!last) return "Ready now";
  const remaining = cooldown - (Date.now() / 1000 - last);
  if (remaining <= 0) return "Ready now";
  const h = Math.floor(remaining / 3600);
  const m = Math.floor((remaining % 3600) / 60);
  if (h > 0) return `in ${h}h ${m}m`;
  return `in ${m}m`;
}

interface TriggerCardProps {
  icon: string;
  label: string;
  description: string;
  nextPost: string;
  endpoint: string;
  color: string;
  onTriggered: () => void;
}

function TriggerCard({ icon, label, description, nextPost, endpoint, color, onTriggered }: TriggerCardProps) {
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<string | null>(null);

  const trigger = async () => {
    setLoading(true);
    setResult(null);
    try {
      const r = await fetch(`${API}${endpoint}`, { method: "POST" });
      const d = await r.json();
      setResult(d.ok ? "✅ Posted!" : `❌ ${d.error || "Failed"}`);
      if (d.ok) onTriggered();
    } catch {
      setResult("❌ Network error");
    }
    setLoading(false);
    setTimeout(() => setResult(null), 4000);
  };

  const isReady = nextPost === "Ready now";

  return (
    <div className="bg-gray-800 border border-gray-700 rounded-xl p-4 flex flex-col gap-3 hover:border-gray-600 transition-colors">
      <div className="flex items-start justify-between">
        <div className="flex items-center gap-2">
          <span className="text-2xl">{icon}</span>
          <div>
            <div className={`font-semibold text-sm ${color}`}>{label}</div>
            <div className="text-gray-400 text-xs mt-0.5">{description}</div>
          </div>
        </div>
        <div className={`text-xs px-2 py-1 rounded-full border ${
          isReady
            ? "border-green-600 text-green-400 bg-green-950"
            : "border-gray-600 text-gray-500"
        }`}>
          {nextPost}
        </div>
      </div>
      <button
        onClick={trigger}
        disabled={loading}
        className={`w-full py-2 rounded-lg text-sm font-medium transition-all ${
          loading
            ? "bg-gray-700 text-gray-500 cursor-wait"
            : "bg-gray-700 hover:bg-gray-600 text-white cursor-pointer"
        }`}
      >
        {loading ? "Posting…" : result || "Post Now"}
      </button>
    </div>
  );
}

export default function XAgentPage() {
  const [status, setStatus] = useState<Status | null>(null);
  const [manualText, setManualText] = useState("");
  const [posting, setPosting] = useState(false);
  const [postResult, setPostResult] = useState<string | null>(null);
  const [charCount, setCharCount] = useState(0);

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
    if (!manualText.trim() || manualText.length > 280) return;
    setPosting(true);
    setPostResult(null);
    try {
      const r = await fetch(`${API}/api/x-agent/post`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: manualText }),
      });
      const d = await r.json();
      if (d.ok) {
        setPostResult("✅ Tweet posted!");
        setManualText("");
        setCharCount(0);
        fetchStatus();
      } else {
        setPostResult(`❌ ${d.error || "Failed"}`);
      }
    } catch {
      setPostResult("❌ Network error");
    }
    setPosting(false);
    setTimeout(() => setPostResult(null), 5000);
  };

  const triggers = [
    {
      icon: "📰", label: "Crypto News", color: "text-yellow-400",
      description: "Latest headline from CoinDesk / CoinTelegraph with sharp take",
      nextPost: nextIn(status?.last_news || 0, 7200),
      endpoint: "/api/x-agent/trigger/news",
    },
    {
      icon: "😱", label: "Fear & Greed", color: "text-orange-400",
      description: "Alternative.me index with market commentary",
      nextPost: nextIn(status?.last_fear_greed || 0, 14400),
      endpoint: "/api/x-agent/trigger/fear-greed",
    },
    {
      icon: "🔥", label: "Hot Take", color: "text-red-300",
      description: "Spicy market opinion — engagement magnet",
      nextPost: nextIn(status?.last_hot_take || 0, 28800),
      endpoint: "/api/x-agent/trigger/hot-take",
    },
    {
      icon: "🧠", label: "Philosophy", color: "text-cyan-400",
      description: "Trading wisdom from legends + algo twist",
      nextPost: nextIn(status?.last_philosophy || 0, 43200),
      endpoint: "/api/x-agent/trigger/philosophy",
    },
    {
      icon: "💬", label: "Engagement", color: "text-green-400",
      description: "Question to the audience — boosts replies",
      nextPost: nextIn(status?.last_engagement || 0, 43200),
      endpoint: "/api/x-agent/trigger/engagement",
    },
    {
      icon: "🤖", label: "Hourly Update", color: "text-blue-400",
      description: "BTC price + regime + witty commentary",
      nextPost: nextIn(status?.last_hourly || 0, 3300),
      endpoint: "/api/x-agent/trigger/hourly",
    },
  ];

  return (
    <div className="min-h-screen bg-gray-900 text-white p-6">
      <div className="max-w-5xl mx-auto space-y-6">

        {/* Header */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold flex items-center gap-2">
              <span>🐦</span> X Agent
              <span className="text-gray-400 font-normal text-lg">@tradeous</span>
            </h1>
            <p className="text-gray-400 text-sm mt-1">
              AI-powered viral content engine — news, hot takes, philosophy, engagement
            </p>
          </div>
          <div className="flex items-center gap-3">
            <div className={`flex items-center gap-2 px-3 py-1.5 rounded-full text-sm font-medium border ${
              status?.enabled
                ? "border-green-600 text-green-400 bg-green-950"
                : "border-red-600 text-red-400 bg-red-950"
            }`}>
              <span className={`w-2 h-2 rounded-full ${status?.enabled ? "bg-green-400 animate-pulse" : "bg-red-400"}`} />
              {status?.enabled ? "LIVE" : "OFFLINE"}
            </div>
            <button
              onClick={resetCooldowns}
              className="px-3 py-1.5 text-xs text-gray-400 border border-gray-600 rounded-lg hover:border-gray-400 hover:text-gray-200 transition-colors"
            >
              Reset Cooldowns
            </button>
          </div>
        </div>

        {/* Stats row */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          {[
            { label: "Posts Today", value: status?.recent_posts.filter(p => Date.now()/1000 - p.ts < 86400).length ?? "–" },
            { label: "Total Posts", value: status?.recent_posts.length ?? "–" },
            { label: "Intro Posted", value: status?.intro_posted ? "Yes ✅" : "No" },
            { label: "Next Hourly", value: nextIn(status?.last_hourly || 0, 3300) },
          ].map((s) => (
            <div key={s.label} className="bg-gray-800 border border-gray-700 rounded-xl p-4 text-center">
              <div className="text-2xl font-bold text-white">{s.value}</div>
              <div className="text-gray-400 text-xs mt-1">{s.label}</div>
            </div>
          ))}
        </div>

        {/* Content type triggers */}
        <div>
          <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-3">
            Content Triggers
          </h2>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {triggers.map((t) => (
              <TriggerCard key={t.endpoint} {...t} onTriggered={fetchStatus} />
            ))}
          </div>
        </div>

        {/* Manual compose */}
        <div className="bg-gray-800 border border-gray-700 rounded-xl p-5">
          <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-4">
            ✍️ Manual Compose
          </h2>
          <div className="relative">
            <textarea
              value={manualText}
              onChange={(e) => {
                setManualText(e.target.value);
                setCharCount(e.target.value.length);
              }}
              placeholder="Write a tweet as @tradeous…"
              maxLength={280}
              rows={4}
              className="w-full bg-gray-900 border border-gray-600 rounded-lg p-3 text-sm text-white placeholder-gray-500 resize-none focus:outline-none focus:border-blue-500 transition-colors"
            />
            <div className={`absolute bottom-3 right-3 text-xs ${charCount > 260 ? "text-red-400" : "text-gray-500"}`}>
              {charCount}/280
            </div>
          </div>
          <div className="flex items-center justify-between mt-3">
            <div className="text-xs text-gray-500">
              Tip: keep it under 200 chars for better engagement
            </div>
            <button
              onClick={postManual}
              disabled={posting || !manualText.trim() || charCount > 280}
              className={`px-5 py-2 rounded-lg text-sm font-medium transition-all ${
                posting || !manualText.trim() || charCount > 280
                  ? "bg-gray-700 text-gray-500 cursor-not-allowed"
                  : "bg-blue-600 hover:bg-blue-500 text-white cursor-pointer"
              }`}
            >
              {posting ? "Posting…" : "Post Tweet"}
            </button>
          </div>
          {postResult && (
            <div className={`mt-3 text-sm text-center py-2 rounded-lg ${
              postResult.startsWith("✅") ? "bg-green-950 text-green-400" : "bg-red-950 text-red-400"
            }`}>
              {postResult}
            </div>
          )}
        </div>

        {/* Recent posts feed */}
        <div>
          <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-3">
            Recent Posts
          </h2>
          {!status?.recent_posts.length ? (
            <div className="bg-gray-800 border border-gray-700 rounded-xl p-8 text-center text-gray-500">
              No posts yet. Use the triggers above or the agent will post automatically.
            </div>
          ) : (
            <div className="space-y-2">
              {[...status.recent_posts].reverse().map((post, i) => {
                const meta = TYPE_META[post.type] || TYPE_META.manual;
                return (
                  <div key={`${post.id}-${i}`} className="bg-gray-800 border border-gray-700 rounded-xl p-4 flex gap-3 hover:border-gray-600 transition-colors">
                    <span className="text-xl mt-0.5 shrink-0">{meta.icon}</span>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-1">
                        <span className={`text-xs font-semibold ${meta.color}`}>{meta.label}</span>
                        <span className="text-gray-600 text-xs">·</span>
                        <span className="text-gray-500 text-xs">{timeAgo(post.ts)}</span>
                        {post.url && (
                          <>
                            <span className="text-gray-600 text-xs">·</span>
                            <a
                              href={post.url}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="text-blue-400 text-xs hover:underline"
                            >
                              View on X ↗
                            </a>
                          </>
                        )}
                      </div>
                      <p className="text-gray-300 text-sm leading-relaxed whitespace-pre-wrap break-words">
                        {post.text}
                      </p>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>

      </div>
    </div>
  );
}
