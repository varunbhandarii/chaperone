import { fileURLToPath } from "node:url";
import { defineConfig, loadEnv } from "vite";

// Repo root: the page imports ../../station/config/voice.json and reads SERVICES_HOST from the root .env.
const repoRoot = fileURLToPath(new URL("../..", import.meta.url));

export default defineConfig(({ mode }) => {
  // Only the services host is exposed to the page; nothing else from .env reaches the browser.
  const env = loadEnv(mode, repoRoot, "");
  const servicesHost = env.VITE_SERVICES_HOST || env.SERVICES_HOST || "localhost";
  return {
    define: {
      "import.meta.env.VITE_SERVICES_HOST": JSON.stringify(servicesHost),
    },
    server: {
      port: 5173,
      strictPort: true,
      host: "0.0.0.0",
      fs: { allow: [repoRoot] },
    },
    build: { target: "es2022" },
  };
});
