import type { NextConfig } from "next";
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";

const appRoot = fileURLToPath(new URL(".", import.meta.url));
const monorepoRoot = fileURLToPath(new URL("../..", import.meta.url));
const workspaceRoot = existsSync(`${monorepoRoot}/package.json`) ? monorepoRoot : appRoot;
const apiInternalUrl = (process.env.API_INTERNAL_URL ?? "http://127.0.0.1:8000").replace(
  /\/$/,
  "",
);

const nextConfig: NextConfig = {
  output: "standalone",
  poweredByHeader: false,
  reactStrictMode: true,
  devIndicators: false,
  turbopack: {
    root: workspaceRoot,
  },
  async rewrites() {
    return [
      {
        source: "/api/v1/:path*",
        destination: `${apiInternalUrl}/api/v1/:path*`,
      },
    ];
  },
};

export default nextConfig;
