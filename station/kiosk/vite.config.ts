import { readFileSync } from "node:fs";
import type { ServerResponse } from "node:http";
import { fileURLToPath } from "node:url";
import { defineConfig, loadEnv, type ProxyOptions } from "vite";

// Repo root: the page imports ../../station/config/voice.json and reads SERVICES_HOST from the root .env.
const repoRoot = fileURLToPath(new URL("../..", import.meta.url));
const voice = JSON.parse(readFileSync(new URL("../../station/config/voice.json", import.meta.url), "utf8")) as {
  ports: Record<"relay" | "policy" | "catalog", number>;
};

/**
 * The page calls the services through this server (/svc/relay, /svc/policy, /svc/catalog), so every call is
 * same-origin and the services need no CORS headers. An unreachable service answers 502 with
 * X-Station-Proxy: unreachable, which the page treats as "down" instead of waiting on it.
 */
function proxyTo(name: "relay" | "policy" | "catalog", host: string): ProxyOptions {
  return {
    target: `http://${host}:${voice.ports[name]}`,
    changeOrigin: true,
    rewrite: (path) => path.replace(new RegExp(`^/svc/${name}`), "") || "/",
    proxyTimeout: 20000, // checkout signs the order and waits for the merchant
    configure: (proxy) => {
      proxy.on("error", (err, _req, res) => {
        const out = res as ServerResponse;
        if (typeof out.writeHead !== "function" || out.headersSent) return;
        out.writeHead(502, { "Content-Type": "application/json", "X-Station-Proxy": "unreachable" });
        out.end(JSON.stringify({ error: `${name} unreachable at ${host}:${voice.ports[name]} (${err.message})` }));
      });
    },
  };
}

export default defineConfig(({ mode }) => {
  // Only the services host is exposed to the page; nothing else from .env reaches the browser.
  const env = loadEnv(mode, repoRoot, "");
  const servicesHost = env.VITE_SERVICES_HOST || env.SERVICES_HOST || "localhost";
  const proxy = {
    "/svc/relay": proxyTo("relay", servicesHost),
    "/svc/policy": proxyTo("policy", servicesHost),
    "/svc/catalog": proxyTo("catalog", servicesHost),
  };
  return {
    define: {
      "import.meta.env.VITE_SERVICES_HOST": JSON.stringify(servicesHost),
    },
    server: {
      port: 5173,
      strictPort: true,
      host: "0.0.0.0",
      fs: { allow: [repoRoot] },
      proxy,
    },
    preview: { proxy },
    build: { target: "es2022" },
  };
});
