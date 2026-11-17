import type { NextConfig } from "next";

const hosted = process.env.VERCEL_ENV === "production" || process.env.VERCEL_ENV === "preview";
const config: NextConfig = {
  devIndicators: false,
  agentRules: false,
  env: { NEXT_PUBLIC_SAME_ORIGIN_API: hosted ? "1" : "0" },
};
export default config;
