import type { NextConfig } from "next";

const config: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  // The Docker image runs the self-contained server (`node server.js`); `next start` locally.
  output: process.env.NEXT_OUTPUT === "standalone" ? "standalone" : undefined,
};

export default config;
