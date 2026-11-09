import type { NextConfig } from "next"

/**
 * Built as static files (`out/`) that `healer panel` serves together with /api/snapshot,
 * so nothing runs on Node once built. In development (`next dev`) the API is proxied to
 * the panel server given by PANEL_API.
 */
const development = process.env.NODE_ENV === "development"

const nextConfig: NextConfig = {
  ...(development
    ? {
        async rewrites() {
          const target = process.env.PANEL_API ?? "http://localhost:8765"
          return [{ source: "/api/:path*", destination: `${target}/api/:path*` }]
        },
      }
    : { output: "export" }),
  images: { unoptimized: true },
  poweredByHeader: false,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
}

export default nextConfig
