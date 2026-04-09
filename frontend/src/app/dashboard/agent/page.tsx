"use client";

import dynamic from "next/dynamic";

// Wallet adapter uses browser APIs — must be client-only, no SSR
const AgentPageInner = dynamic(() => import("./AgentPageInner"), { ssr: false });

export default function AgentPage() {
  return <AgentPageInner />;
}
