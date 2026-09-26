import { createHash } from "crypto";
import fs from "fs";
import path from "path";
import canonicalize from "canonicalize";

const dataDir = path.join(process.cwd(), "data");
const credPath = path.join(dataDir, "credentials.json");
const codePath = path.join(dataDir, "codes.json");

export function rpID() {
  return process.env.TUNNEL_HOST || "localhost";
}

export function origin() {
  if (process.env.ORIGIN) return process.env.ORIGIN;
  const host = rpID();
  if (host === "localhost" || host === "127.0.0.1") return "http://localhost:5175";
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

export function loadCredential() {
  return readJson(credPath, null);
}

export function saveCredential(credential) {
  writeJson(credPath, credential);
}

export function mandateHash(mandate) {
  const body = { ...mandate };
  delete body.passkey;
  return createHash("sha256").update(canonicalize(body)).digest();
}

export function issueCode() {
  const code = String(Math.floor(100000 + Math.random() * 900000));
  const record = {
    hash: createHash("sha256").update(code).digest("hex"),
    expires_at: Date.now() + 10 * 60 * 1000,
  };
  writeJson(codePath, record);
  return code;
}

export function verifyCode(code) {
  const record = readJson(codePath, null);
  if (!record) return false;
  if (Date.now() > record.expires_at) return false;
  const hash = createHash("sha256").update(String(code)).digest("hex");
  if (hash !== record.hash) return false;
  fs.rmSync(codePath, { force: true });
  return true;
}
