"use client";

import { useState, FormEvent } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/context/AuthContext";
import { Logo } from "@/components/ui/Logo";

export default function LoginPage() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const { login } = useAuth();
  const router = useRouter();

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      await login(username, password);
      router.replace("/dashboard");
    } catch {
      setError("Incorrect username or password.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div
      className="min-h-screen flex items-center justify-center p-6"
      style={{ background: "#08090f" }}
    >
      {/* Ambient glow */}
      <div
        className="fixed inset-0 pointer-events-none"
        style={{
          background: "radial-gradient(ellipse 60% 45% at 50% 0%, rgba(10,132,255,0.07) 0%, transparent 65%)",
        }}
      />
      {/* Dot grid */}
      <div
        className="fixed inset-0 pointer-events-none"
        style={{
          backgroundImage: "radial-gradient(circle, rgba(255,255,255,0.03) 1px, transparent 1px)",
          backgroundSize: "28px 28px",
        }}
      />

      <div className="relative w-full max-w-[360px]">
        {/* Logo + wordmark */}
        <div className="flex flex-col items-center mb-10">
          <div className="mb-4" style={{ filter: "drop-shadow(0 8px 32px rgba(10,132,255,0.5))" }}>
            <Logo size={72} />
          </div>
          <h1
            className="text-[28px] font-bold tracking-tight text-white"
            style={{ letterSpacing: "-0.03em" }}
          >
            TradeOS
          </h1>
          <p className="text-[14px] mt-1.5 font-medium" style={{ color: "#3a3a52" }}>
            Crypto Trading Operating System
          </p>
        </div>

        {/* Form card */}
        <div
          className="rounded-[20px] p-8"
          style={{
            background: "linear-gradient(145deg, #0e1019 0%, #0b0d16 100%)",
            border: "1px solid rgba(255,255,255,0.07)",
            boxShadow: "0 32px 64px rgba(0,0,0,0.7), 0 1px 0 rgba(255,255,255,0.05) inset",
          }}
        >
          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className="block text-[12px] font-semibold mb-2" style={{ color: "#4a4a65" }}>
                Username
              </label>
              <input
                className="input"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="Enter username"
                autoComplete="username"
                autoFocus
                required
              />
            </div>

            <div>
              <label className="block text-[12px] font-semibold mb-2" style={{ color: "#4a4a65" }}>
                Password
              </label>
              <input
                className="input"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="••••••••"
                autoComplete="current-password"
                required
              />
            </div>

            {error && (
              <div
                className="flex items-center gap-2 px-4 py-3 rounded-xl text-[13px]"
                style={{
                  background: "rgba(255,69,58,0.08)",
                  border: "1px solid rgba(255,69,58,0.18)",
                  color: "#ff453a",
                }}
              >
                <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
                  <circle cx="7" cy="7" r="6" stroke="#ff453a" strokeWidth="1.2" />
                  <path d="M7 4.5v3" stroke="#ff453a" strokeWidth="1.4" strokeLinecap="round" />
                  <circle cx="7" cy="9.5" r="0.7" fill="#ff453a" />
                </svg>
                {error}
              </div>
            )}

            <button
              type="submit"
              disabled={loading}
              className="w-full flex items-center justify-center gap-2 py-3 rounded-xl text-[15px] font-semibold text-white transition-all duration-200 mt-2"
              style={{
                background: loading ? "rgba(10,132,255,0.4)" : "linear-gradient(135deg, #1a8fff 0%, #0071d4 100%)",
                boxShadow: loading ? "none" : "0 4px 20px rgba(10,132,255,0.4), 0 1px 0 rgba(255,255,255,0.15) inset",
                cursor: loading ? "not-allowed" : "pointer",
                letterSpacing: "-0.01em",
              }}
            >
              {loading ? (
                <div
                  className="w-5 h-5 rounded-full border-2 border-t-transparent animate-spin"
                  style={{ borderColor: "rgba(255,255,255,0.3) rgba(255,255,255,0.3) rgba(255,255,255,0.3) white" }}
                />
              ) : "Sign In →"}
            </button>
          </form>

          <div
            className="mt-6 pt-5 text-center"
            style={{ borderTop: "1px solid rgba(255,255,255,0.05)" }}
          >
            <p className="text-[12px]" style={{ color: "#2a2a3e" }}>
              Paper mode · No real funds at risk
            </p>
          </div>
        </div>

        <p className="text-center mt-5 text-[12px]" style={{ color: "#1e1e2e" }}>
          TradeOS v1.0 · Secure session
        </p>
      </div>
    </div>
  );
}
