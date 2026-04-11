"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/context/AuthContext";
import { Sidebar } from "@/components/layout/Sidebar";
import { TopBar } from "@/components/layout/TopBar";
import { useWebSocket } from "@/hooks/useWebSocket";
import { usePathname } from "next/navigation";
import { WSMessage } from "@/types";
import { Logo } from "@/components/ui/Logo";
import { ServerAgentProvider } from "@/context/ServerAgentContext";

const PAGE_TITLES: Record<string, string> = {
  "/dashboard":          "Overview",
  "/dashboard/settings": "Settings",
  "/dashboard/agent":    "Live Agent",
  "/dashboard/x-agent":  "X Agent",
};

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  const { user, isLoading } = useAuth();
  const router = useRouter();
  const pathname = usePathname();
  const [killSwitchActive, setKillSwitchActive] = useState(false);
  const [mode] = useState("live");

  useEffect(() => {
    if (!isLoading && !user) router.replace("/login");
  }, [user, isLoading, router]);

  const handleWsMessage = (msg: WSMessage) => {
    if (msg.event === "risk:kill_switch") setKillSwitchActive(true);
  };
  const { connected } = useWebSocket({ onMessage: handleWsMessage });

  if (isLoading || !user) {
    return (
      <div className="flex items-center justify-center h-screen" style={{ background: "#08090f" }}>
        <div className="flex flex-col items-center gap-5">
          <div style={{ filter: "drop-shadow(0 4px 20px rgba(10,132,255,0.55))" }}>
            <Logo size={52} />
          </div>
          <div
            className="w-5 h-5 rounded-full border-2 border-t-transparent animate-spin"
            style={{ borderColor: "rgba(10,132,255,0.2) rgba(10,132,255,0.2) rgba(10,132,255,0.2) #0a84ff" }}
          />
        </div>
      </div>
    );
  }

  const title = PAGE_TITLES[pathname] || "TradeOS";

  return (
    <ServerAgentProvider>
      <div className="flex h-screen overflow-hidden" style={{ background: "#08090f" }}>
        <Sidebar />
        <div className="flex-1 flex flex-col overflow-hidden min-w-0">
          <TopBar
            title={title}
            wsConnected={connected}
            mode={mode}
            killSwitchActive={killSwitchActive}
            onKillSwitchChange={setKillSwitchActive}
          />
          <main className="flex-1 overflow-y-auto" style={{ padding: "24px 28px" }}>
            {children}
          </main>
        </div>
      </div>
    </ServerAgentProvider>
  );
}
