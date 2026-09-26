import { createHash, randomInt } from "crypto";
import fs from "fs";
import path from "path";
import canonicalize from "canonicalize";

const dataDir = path.join(process.cwd(), "data");
const credPath = path.join(dataDir, "credentials.json");
const setupPath = path.join(dataDir, "setup.json");

export function rpID() {
  return process.env.TUNNEL_HOST || "localhost";
}

export function origin() {
  if (process.env.ORIGIN) return process.env.ORIGIN;
  const host = rpID();
  if (host === "localhost" || host === "127.0.0.1") return `http://${host}:5175`;
  return `https://${host}`;
}

export function requireUV() {
  return process.env.REQUIRE_UV !== "0";
}

function readJson(file, fallback) {
  if (!fs.existsSync(file)) return fallback;
  return JSON.parse(fs.readFileSync(file, "utf8"));
}

function writeJson(file, value) {
  fs.mkdirSync(dataDir, { recursive: true });
  fs.writeFileSync(file, JSON.stringify(value, null, 2));
}

export function loadCredentials() {
  const raw = readJson(credPath, null);
  if (!raw) return [];
  if (Array.isArray(raw.credentials)) return raw.credentials;
  if (raw.id) return [raw];
  return [];
}

export function saveCredentials(credentials) {
  writeJson(credPath, { credentials });
}

export function ensureSetupCode() {
  if (loadCredentials().length || process.env.CAREGIVER_SETUP_CODE_HASH) return;
  const existing = readJson(setupPath, null);
  if (existing && existing.expires_at > Date.now() && !existing.used) return;
  const code = String(randomInt(0, 1_000_000)).padStart(6, "0");
  writeJson(setupPath, {
    hash: createHash("sha256").update(code).digest("hex"),
    expires_at: Date.now() + 10 * 60 * 1000,
    used: false,
  });
  console.log(`CAREGIVER SETUP CODE: ${code}`);
}

export function consumeSetupCode(code) {
  const digest = createHash("sha256").update(String(code || "")).digest("hex");
  if (process.env.CAREGIVER_SETUP_CODE_HASH) {
    return digest === process.env.CAREGIVER_SETUP_CODE_HASH;
  }
  const record = readJson(setupPath, null);
  if (!record || record.used || Date.now() > record.expires_at) return false;
  if (digest !== record.hash) return false;
  record.used = true;
  writeJson(setupPath, record);
  return true;
}

export function mandateHash(mandate) {
  const body = { ...mandate };
  delete body.passkey;
  return createHash("sha256").update(canonicalize(body)).digest();
}
