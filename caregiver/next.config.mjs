import { existsSync, readFileSync } from "fs";
import path from "path";
import { fileURLToPath } from "url";

const envPath = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", ".env");
if (existsSync(envPath)) {
  for (const line of readFileSync(envPath, "utf8").split("\n")) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#") || !trimmed.includes("=")) continue;
    const index = trimmed.indexOf("=");
    const key = trimmed.slice(0, index).trim();
    const value = trimmed.slice(index + 1).trim().replace(/^["']|["']$/g, "");
    if (!process.env[key]) process.env[key] = value;
  }
}

const merchant = process.env.MERCHANT_PUBLIC_URL || "http://127.0.0.1:8002";
const policy = (process.env.POLICY_URL || "http://127.0.0.1:8001").replace(/\/$/, "");

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  compress: false,
  async rewrites() {
    return [
      { source: "/merchant/webhooks/cybersource", destination: `${merchant.replace(/\/$/, "")}/webhooks/cybersource` },
      { source: "/merchant/webhooks/cybersource/:path*", destination: `${merchant.replace(/\/$/, "")}/webhooks/cybersource/:path*` },
      { source: "/card/asa", destination: `${policy}/card/asa` },
    ];
  },
};

export default nextConfig;
