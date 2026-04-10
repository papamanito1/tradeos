/** @type {import('next').NextConfig} */
const nextConfig = {
  // "standalone" is only needed for Docker builds — Vercel handles its own bundling
  ...(process.env.BUILD_STANDALONE === "1" ? { output: "standalone" } : {}),

  env: {
    NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000",
    NEXT_PUBLIC_WS_URL:  process.env.NEXT_PUBLIC_WS_URL  || "ws://localhost:8000",
  },

  generateBuildId: async () => `build-${Date.now()}`,

  headers: async () => [
    {
      source: "/(.*)",
      headers: [
        { key: "Cache-Control", value: "no-store, must-revalidate" },
      ],
    },
  ],
};

module.exports = nextConfig;
