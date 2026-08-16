import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  typescript: {
    ignoreBuildErrors: true,
  },
  reactStrictMode: false,
  // FIX: Proxy /api/* and /ws/* to the FastAPI backend on port 8000.
  // This makes the frontend work from any browser (not just localhost:81)
  // because the browser calls same-origin relative paths, and Next.js
  // server-side rewrites them to the backend.
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: "http://localhost:8000/api/:path*",
      },
    ];
  },
};

export default nextConfig;
