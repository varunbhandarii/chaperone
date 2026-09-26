/**
 * Caregiver passkey server without Next.
 * The App Router handlers under app/api/passkeys/ are the same flow.
 * This file is what runs while `next` is not installed.
 */
import { createServer } from "http";
import { existsSync, readFileSync } from "fs";
import { randomBytes } from "crypto";
import path from "path";
import { fileURLToPath } from "url";
import {
  generateAuthenticationOptions,
  generateRegistrationOptions,
  verifyAuthenticationResponse,
  verifyRegistrationResponse,
} from "@simplewebauthn/server";
import { isoBase64URL } from "@simplewebauthn/server/helpers";
import { issueCode, loadCredential, mandateHash, origin, requireUV, rpID, saveCredential, verifyCode } from "./lib/passkeys.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

function loadEnv() {
  const envPath = path.join(__dirname, "..", ".env");
  if (!existsSync(envPath)) return;
  for (const line of readFileSync(envPath, "utf8").split("\n")) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#") || !trimmed.includes("=")) continue;
    const index = trimmed.indexOf("=");
    const key = trimmed.slice(0, index).trim();
    const value = trimmed.slice(index + 1).trim().replace(/^["']|["']$/g, "");
    if (!process.env[key]) process.env[key] = value;
  }
}
loadEnv();
const bundle = readFileSync(
  path.join(__dirname, "node_modules/@simplewebauthn/browser/dist/bundle/index.umd.min.js"),
);
const page = readFileSync(path.join(__dirname, "static/index.html"));
const challenges = new Map();

function cookieJar(req) {
  const header = req.headers.cookie || "";
  const out = {};
  for (const part of header.split(";")) {
    const [key, ...rest] = part.trim().split("=");
    if (key) out[key] = decodeURIComponent(rest.join("="));
  }
  return out;
}

function send(res, status, body, extra = {}) {
  const payload = typeof body === "string" || Buffer.isBuffer(body) ? body : JSON.stringify(body);
  res.writeHead(status, {
    "Content-Type": typeof body === "string" ? "text/html; charset=utf-8" : Buffer.isBuffer(body) ? "text/javascript" : "application/json",
    ...extra,
  });
  res.end(payload);
}

async function readJson(req) {
  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  const raw = Buffer.concat(chunks).toString() || "{}";
  return JSON.parse(raw);
}

function setChallenge(res, id, challenge) {
  const headers = { "Set-Cookie": `sid=${id}; HttpOnly; SameSite=Lax; Path=/` };
  challenges.set(id, challenge);
  return headers;
}

const server = createServer(async (req, res) => {
  const url = new URL(req.url, origin());
  try {
    if (req.method === "GET" && url.pathname === "/") {
      res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
      res.end(page);
      return;
    }
    if (req.method === "GET" && url.pathname === "/vendor/webauthn.js") {
      res.writeHead(200, { "Content-Type": "text/javascript; charset=utf-8" });
      res.end(bundle);
      return;
    }
    if (req.method === "GET" && url.pathname === "/api/config") return send(res, 200, { rpID: rpID(), origin: origin() });

    if (req.method === "POST" && url.pathname === "/api/passkeys/generate-registration-options") {
      const options = await generateRegistrationOptions({
        rpName: "Chaperone",
        rpID: rpID(),
        userName: "priyank",
        attestationType: "none",
        authenticatorSelection: { residentKey: "preferred", userVerification: "preferred" },
      });
      const sid = randomBytes(16).toString("hex");
      return send(res, 200, options, setChallenge(res, sid, options.challenge));
    }

    if (req.method === "POST" && url.pathname === "/api/passkeys/verify-registration") {
      const response = await readJson(req);
      const sid = cookieJar(req).sid;
      const expectedChallenge = challenges.get(sid);
      if (!expectedChallenge) return send(res, 400, { error: "missing challenge cookie" });
      const verified = await verifyRegistrationResponse({
        response,
        expectedChallenge,
        expectedOrigin: origin(),
        expectedRPID: rpID(),
        requireUserVerification: requireUV(),
      });
      if (!verified.verified) return send(res, 400, { verified: false });
      const credential = verified.registrationInfo.credential;
      saveCredential({
        id: credential.id,
        publicKey: isoBase64URL.fromBuffer(credential.publicKey),
        counter: credential.counter,
        transports: credential.transports || [],
      });
      return send(res, 200, { verified: true, id: credential.id });
    }

    if (req.method === "POST" && url.pathname === "/api/passkeys/generate-authentication-options") {
      const { mandate } = await readJson(req);
      const stored = loadCredential();
      if (!stored) return send(res, 400, { error: "register a passkey first" });
      const hash = mandateHash(mandate);
      const options = await generateAuthenticationOptions({
        rpID: rpID(),
        challenge: hash,
        allowCredentials: [{ id: stored.id, transports: stored.transports }],
      });
      const sid = cookieJar(req).sid || randomBytes(16).toString("hex");
      return send(res, 200, options, setChallenge(res, sid, options.challenge));
    }

    if (req.method === "POST" && url.pathname === "/api/passkeys/verify-authentication") {
      const { mandate, response } = await readJson(req);
      const stored = loadCredential();
      if (!stored) return send(res, 400, { error: "register a passkey first" });
      const verified = await verifyAuthenticationResponse({
        response,
        expectedChallenge: isoBase64URL.fromBuffer(mandateHash(mandate)),
        expectedOrigin: origin(),
        expectedRPID: rpID(),
        requireUserVerification: requireUV(),
        credential: {
          id: stored.id,
          publicKey: isoBase64URL.toBuffer(stored.publicKey),
          counter: stored.counter,
          transports: stored.transports,
        },
      });
      if (!verified.verified) return send(res, 400, { verified: false });
      stored.counter = verified.authenticationInfo.newCounter;
      saveCredential(stored);
      return send(res, 200, { verified: true, counter: stored.counter });
    }

    if (req.method === "POST" && url.pathname === "/api/code/issue") return send(res, 200, { code: issueCode() });
    if (req.method === "POST" && url.pathname === "/api/code/verify") {
      const { code } = await readJson(req);
      const verified = verifyCode(code);
      return send(res, verified ? 200 : 400, { verified });
    }

    send(res, 404, { error: "not found" });
  } catch (error) {
    send(res, 400, { error: String(error) });
  }
});

server.listen(5175, "0.0.0.0", () => {
  console.log(`caregiver http://localhost:5175 rpID=${rpID()} origin=${origin()}`);
});
