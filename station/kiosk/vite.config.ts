import { readFileSync } from "node:fs";
import type { IncomingMessage, ServerResponse } from "node:http";
import { fileURLToPath } from "node:url";
import { defineConfig, loadEnv, type ProxyOptions } from "vite";

// Repo root: the page imports ../../station/config/voice.json and reads SERVICES_HOST from the root .env.
const repoRoot = fileURLToPath(new URL("../..", import.meta.url));
const voice = JSON.parse(readFileSync(new URL("../../station/config/voice.json", import.meta.url), "utf8")) as {
  ports: Record<"relay" | "policy" | "merchant" | "catalog" | "printer", number>;
};

/**
 * The page calls the services through this server (/svc/relay, /svc/policy, /svc/catalog), so every call is
 * same-origin and the services need no CORS headers. An unreachable service answers 502 with
 * X-Station-Proxy: unreachable, which the page treats as "down" instead of waiting on it.
 */
function proxyTo(name: "relay" | "policy" | "merchant" | "catalog" | "printer", host: string): ProxyOptions {
  return {
    target: `http://${host}:${voice.ports[name]}`,
    changeOrigin: true,
    xfwd: false, // policy's LAN-only routes refuse requests that carry X-Forwarded-* headers
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
  // The receipt's QR code opens https://<TUNNEL_HOST>/s/<session_id>; the host name is public, not a secret.
  const tunnelHost = env.TUNNEL_HOST || "";
  const proxy = {
    "/svc/relay": proxyTo("relay", servicesHost),
    "/svc/policy": proxyTo("policy", servicesHost),
    "/svc/merchant": proxyTo("merchant", servicesHost),
    "/svc/catalog": proxyTo("catalog", servicesHost),
    // The print helper runs on the station laptop itself, next to the printer. The dev server listens on the
    // LAN (for the tablet), so only this laptop may reach the printer and the cached sessions through it.
    "/svc/printer": {
      ...proxyTo("printer", "127.0.0.1"),
      bypass: (req: IncomingMessage, res: ServerResponse) => {
        const from = req.socket.remoteAddress ?? "";
        if (from === "127.0.0.1" || from === "::1" || from === "::ffff:127.0.0.1") return undefined;
        res.statusCode = 403;
        res.end("printer is local to the station laptop");
        return false;
      },
    },
  };
  return {
    define: {
      "import.meta.env.VITE_SERVICES_HOST": JSON.stringify(servicesHost),
      "import.meta.env.VITE_TUNNEL_HOST": JSON.stringify(tunnelHost),
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
