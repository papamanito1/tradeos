"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";
import {
  LayoutDashboard, Zap, TrendingUp, FileText, ShieldAlert,
  BarChart2, Activity, BookOpen, Settings, LogOut, Bot,
} from "lucide-react";
import { useAuth } from "@/context/AuthContext";
import { Logo } from "@/components/ui/Logo";

const NAV_SECTIONS = [
  {
    label: "Trading",
    items: [
      { label: "Overview",   href: "/dashboard",            icon: LayoutDashboard },
      { label: "Live Agent", href: "/dashboard/agent",     icon: Bot },
      { label: "Strategies", href: "/dashboard/strategies", icon: Zap },
      { label: "Positions",  href: "/dashboard/positions",  icon: TrendingUp },
      { label: "Orders",     href: "/dashboard/orders",     icon: FileText },
    ],
  },
  {
    label: "Tools",
    items: [
      { label: "Risk Control", href: "/dashboard/risk",     icon: ShieldAlert },
      { label: "Backtest",     href: "/dashboard/backtest", icon: BarChart2 },
    ],
  },
  {
    label: "Monitor",
    items: [
      { label: "Market",   href: "/dashboard/market",   icon: Activity },
      { label: "Journal",  href: "/dashboard/journal",  icon: BookOpen },
      { label: "Settings", href: "/dashboard/settings", icon: Settings },
    ],
  },
];

export function Sidebar() {
  const pathname = usePathname();
  const { user, logout } = useAuth();

  const isActive = (href: string) =>
    href === "/dashboard" ? pathname === "/dashboard" : pathname.startsWith(href);

  return (
    <aside
      className="w-56 min-h-screen flex flex-col flex-shrink-0"
      style={{
        background: "linear-gradient(180deg, #06070e 0%, #050608 100%)",
        borderRight: "1px solid rgba(255,255,255,0.05)",
      }}
    >
      {/* Logo */}
      <div className="px-4 pt-5 pb-4">
        <div className="flex items-center gap-3">
          <div className="flex-shrink-0" style={{ filter: "drop-shadow(0 2px 10px rgba(10,132,255,0.55))" }}>
            <Logo size={30} />
          </div>
          <div>
            <div
              className="font-semibold text-[15px] leading-none text-white"
              style={{ letterSpacing: "-0.02em" }}
            >
              TradeOS
            </div>
            <div className="text-[10px] mt-0.5" style={{ color: "#2a2a3e" }}>
              v1.0 · Paper
            </div>
          </div>
        </div>
      </div>

      {/* Separator */}
      <div className="mx-4 mb-3" style={{ height: 1, background: "rgba(255,255,255,0.04)" }} />

      {/* Navigation */}
      <nav className="flex-1 px-2.5 pb-4 space-y-4">
        {NAV_SECTIONS.map((section, si) => (
          <div key={si}>
            <div
              className="px-3 mb-1.5 text-[9px] font-bold uppercase tracking-[0.12em]"
              style={{ color: "#252535" }}
            >
              {section.label}
            </div>
            <div className="space-y-0.5">
              {section.items.map(({ label, href, icon: Icon }) => {
                const active = isActive(href);
                return (
                  <Link
                    key={href}
                    href={href}
                    className="group flex items-center gap-2.5 px-3 py-[7px] rounded-[10px] text-[13px] font-medium transition-all duration-150"
                    style={
                      active
                        ? {
                            background: "rgba(10,132,255,0.12)",
                            color: "#60aaff",
                            boxShadow: "inset 0 0 0 1px rgba(10,132,255,0.2)",
                          }
                        : { color: "#3d3d58" }
                    }
                    onMouseEnter={(e) => {
                      if (!active) {
                        (e.currentTarget as HTMLElement).style.background = "rgba(255,255,255,0.035)";
                        (e.currentTarget as HTMLElement).style.color = "#a0a0c0";
                      }
                    }}
                    onMouseLeave={(e) => {
                      if (!active) {
                        (e.currentTarget as HTMLElement).style.background = "";
                        (e.currentTarget as HTMLElement).style.color = "#3d3d58";
                      }
                    }}
                  >
                    <Icon size={14} className="flex-shrink-0" style={{ color: active ? "#60aaff" : "inherit" }} />
                    {label}
                    {active && (
                      <div className="ml-auto w-1.5 h-1.5 rounded-full" style={{ background: "#0a84ff" }} />
                    )}
                  </Link>
                );
              })}
            </div>
          </div>
        ))}
      </nav>

      {/* User */}
      <div
        className="px-2.5 pb-4 pt-3"
        style={{ borderTop: "1px solid rgba(255,255,255,0.04)" }}
      >
        <div className="flex items-center gap-2.5 px-3 py-2 mb-0.5">
          <div
            className="w-6 h-6 rounded-full flex items-center justify-center text-[11px] font-bold text-white flex-shrink-0"
            style={{
              background: "linear-gradient(135deg, #0a84ff 0%, #0055cc 100%)",
              boxShadow: "0 1px 4px rgba(10,132,255,0.4)",
            }}
          >
            {(user?.username?.[0] || "A").toUpperCase()}
          </div>
          <span className="text-[12px] font-medium truncate" style={{ color: "#5a5a7a" }}>
            {user?.username}
          </span>
        </div>
        <button
          onClick={logout}
          className="w-full flex items-center gap-2.5 px-3 py-2 rounded-[10px] text-[12px] font-medium transition-all duration-150"
          style={{ color: "#2a2a40" }}
          onMouseEnter={(e) => {
            const el = e.currentTarget as HTMLElement;
            el.style.background = "rgba(255,69,58,0.08)";
            el.style.color = "#ff453a";
          }}
          onMouseLeave={(e) => {
            const el = e.currentTarget as HTMLElement;
            el.style.background = "";
            el.style.color = "#2a2a40";
          }}
        >
          <LogOut size={13} className="flex-shrink-0" />
          Sign out
        </button>
      </div>
    </aside>
  );
}
