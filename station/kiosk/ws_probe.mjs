#!/usr/bin/env node
// Command-line probe for the Grok Voice realtime protocol and the search_catalog tool round trip.
//
//   node station/kiosk/ws_probe.mjs [--text "Necesito pan"] [--timeout 20] [--wav reply.wav]
//                                  [--voice ara] [--relay http://localhost:8000] [--direct] [--catalog]
//                                  [--ws-url ws://127.0.0.1:8010/v1/realtime]   (local mock of the realtime API)
//
// Mints an ephemeral client secret through the relay (or directly with XAI_API_KEY from the repo-root
// .env when the relay is down or --direct is given), opens the realtime WebSocket with the same
// subprotocol the browser uses, sends session.update from station/config/voice.json at 24000 Hz,
// sends one text turn, answers search_catalog with the fallback items (or the catalog with --catalog),
// and prints every event type, both transcripts, the tool call and the time to first audio delta.
// The token and the API key are never printed.

import { readFileSync, writeFileSync, existsSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(HERE, "../..");
const RATE = 24000;
const CLIENT_SECRETS_URL = "https://api.x.ai/v1/realtime/client_secrets";
const FALLBACK_ITEMS = [
  { sku: "BAK-001", name: "Nature's Own Honey Wheat Bread", price: 3.49, usual: true },
  { sku: "BAK-003", name: "Kroger Low Sodium Whole Wheat Bread", price: 3.19, usual: false },
  { sku: "BAK-002", name: "Kroger Whole Wheat Bread", price: 2.99, usual: false },
];

const { values: args } = parseArgs({
  options: {
    text: { type: "string", default: "Necesito pan" },
    timeout: { type: "string", default: "20" },
    wav: { type: "string" },
    voice: { type: "string" },
    relay: { type: "string" },
    direct: { type: "boolean", default: false },
    catalog: { type: "boolean", default: false },
    "ws-url": { type: "string" },
  },
});

function loadDotEnv(path) {
  const out = {};
  if (!existsSync(path)) return out;
  for (const line of readFileSync(path, "utf8").split(/\r?\n/)) {
    const m = line.match(/^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$/);
    if (!m) continue;
    let v = m[2].trim();
    if ((v.startsWith('"') && v.endsWith('"')) || (v.startsWith("'") && v.endsWith("'"))) v = v.slice(1, -1);
    else v = v.replace(/\s+#.*$/, "");
    out[m[1]] = v;
  }
  return out;
}

const dotenv = loadDotEnv(resolve(ROOT, ".env"));
const env = (name) => process.env[name] || dotenv[name] || "";
const voice = JSON.parse(readFileSync(resolve(ROOT, "station/config/voice.json"), "utf8"));
const host = env("SERVICES_HOST") || "localhost";
const relayUrl = (args.relay || env("RELAY_URL") || `http://${host}:${voice.ports.relay}`).replace(/\/+$/, "");
const catalogUrl = (env("CATALOG_URL") || `http://${host}:${voice.ports.catalog}`).replace(/\/+$/, "");

const t0 = performance.now();
const stamp = () => `+${String(Math.round(performance.now() - t0)).padStart(6)} ms`;
const log = (...parts) => console.log(stamp(), ...parts);

// ---------------------------------------------------------------- token

async function mintViaRelay() {
  const res = await fetch(`${relayUrl}${voice.token_path}`, { method: "POST", signal: AbortSignal.timeout(10000) });
  const body = await res.json().catch(() => ({}));
  if (res.ok && typeof body.value === "string") return body;
  throw new Error(`relay answered ${res.status}: ${body.error ?? "no token"}`);
}

async function mintDirect(key) {
  const res = await fetch(CLIENT_SECRETS_URL, {
    method: "POST",
    headers: { Authorization: `Bearer ${key}`, "Content-Type": "application/json" },
    body: JSON.stringify({ expires_after: { seconds: 300 } }),
    signal: AbortSignal.timeout(10000),
  });
  const body = await res.json().catch(() => ({}));
  if (res.ok && typeof body.value === "string") return body;
  const msg = typeof body.error === "string" ? body.error : body.error?.message ?? JSON.stringify(body).slice(0, 200);
  throw new Error(`api.x.ai answered ${res.status}: ${msg}`);
}

async function mintToken() {
  if (!args.direct) {
    try {
      const t = await mintViaRelay();
      log(`token minted via relay ${relayUrl} (expires in ${Math.round(t.expires_at - Date.now() / 1000)} s)`);
      return t.value;
    } catch (err) {
      log(`relay token failed: ${err.message}`);
    }
  }
  const key = env("XAI_API_KEY");
  if (!key) {
    console.error(
      "\nNo token: the relay could not mint one and XAI_API_KEY is not set in the environment or the repo-root .env.\n" +
        "Add XAI_API_KEY to .env, then run the relay or this probe again.",
    );
    process.exit(2);
  }
  const t = await mintDirect(key);
  log(`token minted directly with XAI_API_KEY (expires in ${Math.round(t.expires_at - Date.now() / 1000)} s)`);
  return t.value;
}

// ---------------------------------------------------------------- tools

async function searchCatalog(query) {
  if (args.catalog) {
    try {
      const res = await fetch(`${catalogUrl}/search?q=${encodeURIComponent(query)}&limit=3`, { signal: AbortSignal.timeout(2500) });
      const body = await res.json();
      const items = Array.isArray(body.items) ? body.items : body.results;
      if (res.ok && Array.isArray(items)) return { query, source: "catalog", items: items.slice(0, 3) };
    } catch (err) {
      log(`catalog unavailable (${err.message}); using fallback items`);
    }
  }
  return { query, source: "fallback", items: FALLBACK_ITEMS };
}

// A minimal cart so the model can run its whole flow from the command line (nothing is bought here).
const cart = new Map();
const known = new Map(FALLBACK_ITEMS.map((i) => [i.sku, i]));

function cartView() {
  const lines = [...cart.entries()].map(([sku, qty]) => ({ sku, name: known.get(sku)?.name ?? sku, qty, price: known.get(sku)?.price ?? 0 }));
  const total = Math.round(lines.reduce((t, l) => t + Math.round(l.price * 100) * l.qty, 0)) / 100;
  return { lines, total };
}

async function runTool(name, argsJson) {
  let a = {};
  try {
    a = JSON.parse(argsJson || "{}");
  } catch {
    /* keep empty */
  }
  if (name === "search_catalog") {
    const out = await searchCatalog(String(a.query ?? ""));
    for (const item of out.items) known.set(item.sku, item);
    return out;
  }
  if (name === "add_to_cart") {
    if (!known.has(a.sku)) return { error: `unknown sku ${a.sku}; call search_catalog first` };
    cart.set(a.sku, (cart.get(a.sku) ?? 0) + (Number(a.qty) || 1));
    return { ok: true, added: { sku: a.sku, qty: Number(a.qty) || 1 }, cart: cartView() };
  }
  if (name === "remove_from_cart") {
    cart.delete(a.sku);
    return { ok: true, removed: a.sku, cart: cartView() };
  }
  if (name === "read_cart") {
    const v = cartView();
    return { ...v, say: `Your order: ${v.lines.map((l) => `${l.name}, $${l.price.toFixed(2)}`).join("; ")}. Total $${v.total.toFixed(2)}. Shall I place the order?` };
  }
  if (name === "budget_left") return { monthly_cap: 300, spent: 142.1, left: 157.9, say: "You have $157.90 left this month." };
  if (name === "checkout") return { status: "error", say_key: "checkout_unavailable", say: "This probe does not place orders; nothing was bought." };
  return { error: `unknown tool ${name}` };
}

// ---------------------------------------------------------------- session

function sessionFor(withTranscriptionModel) {
  const session = structuredClone(voice.session);
  if (args.voice) session.voice = args.voice;
  session.audio.input.format.rate = RATE;
  session.audio.output.format.rate = RATE;
  if (!withTranscriptionModel && session.audio.input.transcription) delete session.audio.input.transcription.model;
  return session;
}

function writeWav(path, chunks) {
  const pcm = Buffer.concat(chunks);
  const header = Buffer.alloc(44);
  header.write("RIFF", 0);
  header.writeUInt32LE(36 + pcm.length, 4);
  header.write("WAVE", 8);
  header.write("fmt ", 12);
  header.writeUInt32LE(16, 16);
  header.writeUInt16LE(1, 20);
  header.writeUInt16LE(1, 22);
  header.writeUInt32LE(RATE, 24);
  header.writeUInt32LE(RATE * 2, 28);
  header.writeUInt16LE(2, 32);
  header.writeUInt16LE(16, 34);
  header.write("data", 36);
  header.writeUInt32LE(pcm.length, 40);
  writeFileSync(path, Buffer.concat([header, pcm]));
}

async function main() {
  if (typeof WebSocket !== "function") {
    console.error("This Node has no global WebSocket; use Node 22 or newer.");
    process.exit(2);
  }
  const token = await mintToken();
  const base = args["ws-url"] || voice.ws_url;
  const url = `${base}${base.includes("?") ? "&" : "?"}model=${encodeURIComponent(voice.model)}`;
  log(`connecting ${url} (subprotocol ${voice.subprotocol_prefix}<token>)`);
  const ws = new WebSocket(url, [voice.subprotocol_prefix + token]);

  const counts = new Map();
  const transcripts = [];
  const toolCalls = [];
  const firstAudio = [];
  const audio = [];
  let sessionSent = false;
  let configured = false;
  let retried = false;
  let requestAt = 0;
  let awaitingAudio = false;
  let responsesDone = 0;
  const toolJobs = [];
  let agentText = "";
  let finished = false;

  const send = (msg) => ws.send(JSON.stringify(msg));
  const sendSession = (withModel = true) => {
    sessionSent = true;
    const session = sessionFor(withModel);
    send({ type: "session.update", session });
    log(`-> session.update (voice ${session.voice}, ${RATE} Hz, turn_detection ${JSON.stringify(session.turn_detection)}, tools ${session.tools.map((t) => t.name).join(",")})`);
  };
  const requestResponse = (label) => {
    send({ type: "response.create" });
    requestAt = performance.now();
    awaitingAudio = true;
    log(`-> response.create (${label})`);
  };

  const finish = (code, reason) => {
    if (finished) return;
    finished = true;
    console.log("\n==== summary", reason ? `(${reason})` : "");
    console.log("events:", Object.fromEntries(counts));
    for (const t of transcripts) console.log(`${t.role.padEnd(7)} ${t.text}`);
    for (const c of toolCalls) console.log(`tool    ${c.name}(${c.arguments}) -> ${c.source}`);
    firstAudio.forEach((ms, i) => console.log(`first audio delta after response.create #${i + 1}: ${ms} ms`));
    if (args.wav && audio.length) {
      writeWav(resolve(process.cwd(), args.wav), audio);
      console.log(`agent audio written to ${args.wav} (${RATE} Hz mono)`);
    }
    const ok = configured && toolCalls.some((c) => c.name === "search_catalog") && firstAudio.length > 0;
    console.log(ok ? "RESULT: PASS (session configured, search_catalog called, audio received)" : "RESULT: INCOMPLETE");
    try {
      ws.close(1000, "probe done");
    } catch {
      /* already closed */
    }
    setTimeout(() => process.exit(code ?? (ok ? 0 : 1)), 250);
  };

  const timer = setTimeout(() => finish(1, `timeout after ${args.timeout} s`), Number(args.timeout) * 1000);
  timer.unref?.();

  ws.onopen = () => {
    log(`websocket open (protocol "${ws.protocol ? "xai-client-secret.<token>" : ""}")`);
    setTimeout(() => {
      if (!sessionSent) sendSession();
    }, 1000);
  };
  ws.onerror = (e) => log(`websocket error: ${e?.message ?? e?.error?.message ?? "unknown"} (a rejected or expired token also shows up here)`);
  ws.onclose = (e) => {
    log(`websocket closed code=${e.code} reason=${e.reason || "-"}`);
    finish(finished ? 0 : 1, "socket closed");
  };

  ws.onmessage = async (msg) => {
    if (typeof msg.data !== "string") {
      counts.set("(binary)", (counts.get("(binary)") ?? 0) + 1);
      return;
    }
    const ev = JSON.parse(msg.data);
    const n = (counts.get(ev.type) ?? 0) + 1;
    counts.set(ev.type, n);

    switch (ev.type) {
      case "session.created":
      case "conversation.created":
        log(ev.type);
        if (!sessionSent) sendSession();
        break;

      case "session.updated": {
        const s = ev.session ?? {};
        log(`session.updated (voice ${s.voice}, turn_detection ${JSON.stringify(s.turn_detection)}, tools ${(s.tools ?? []).map((t) => t.name ?? t.function?.name ?? t.type).join(",") || "none echoed"})`);
        if (!configured) {
          configured = true;
          send({ type: "conversation.item.create", item: { type: "message", role: "user", content: [{ type: "input_text", text: args.text }] } });
          log(`-> conversation.item.create input_text "${args.text}"`);
          transcripts.push({ role: "shopper", text: args.text });
          requestResponse("text turn");
        }
        break;
      }

      case "response.output_audio.delta": {
        if (typeof ev.delta === "string") audio.push(Buffer.from(ev.delta, "base64"));
        if (awaitingAudio) {
          awaitingAudio = false;
          const ms = Math.round(performance.now() - requestAt);
          firstAudio.push(ms);
          log(`response.output_audio.delta  FIRST AUDIO ${ms} ms after response.create`);
        }
        break;
      }

      case "response.output_audio_transcript.delta":
        agentText += ev.delta ?? "";
        break;

      case "response.output_audio_transcript.done": {
        const text = (ev.transcript ?? agentText).trim();
        agentText = "";
        log(`AGENT: ${text}`);
        if (text) transcripts.push({ role: "agent", text });
        break;
      }

      case "conversation.item.input_audio_transcription.completed":
        log(`SHOPPER (asr${ev.language ? `, ${ev.language}` : ""}): ${ev.transcript}`);
        break;

      case "response.function_call_arguments.done": {
        log(`TOOL CALL ${ev.name}(${ev.arguments}) call_id=${ev.call_id}`);
        toolCalls.push({ name: ev.name, arguments: ev.arguments, source: "pending" });
        const call = toolCalls[toolCalls.length - 1];
        toolJobs.push(
          runTool(ev.name, ev.arguments).then((output) => {
            call.source = output.source ?? "error";
            return { call_id: ev.call_id, output };
          }),
        );
        break;
      }

      case "response.done": {
        responsesDone++;
        log(`response.done #${responsesDone} status=${ev.response?.status ?? "?"}${ev.response?.usage ? ` usage=${JSON.stringify(ev.response.usage)}` : ""}`);
        if (responsesDone >= 2) return finish(undefined, "second response.done");
        const outputs = await Promise.all(toolJobs.splice(0));
        if (outputs.length) {
          for (const p of outputs) {
            send({ type: "conversation.item.create", item: { type: "function_call_output", call_id: p.call_id, output: JSON.stringify(p.output) } });
            log(`-> function_call_output ${p.call_id} (${p.output.source ?? p.output.error ?? "ok"}, ${p.output.items?.length ?? p.output.lines?.length ?? 0} items)`);
          }
          requestResponse("after tool output");
        } else {
          finish(undefined, "response without a tool call");
        }
        break;
      }

      case "error": {
        const err = ev.error ?? {};
        log(`ERROR ${err.type ?? ""} ${err.code ?? ""} ${err.param ? `(${err.param}) ` : ""}${err.message ?? JSON.stringify(err)}`);
        if (!configured && !retried && /transcri|model/i.test(`${err.param ?? ""} ${err.message ?? ""}`)) {
          retried = true;
          log("retrying session.update without audio.input.transcription.model");
          sendSession(false);
        }
        break;
      }

      default:
        if (ev.type.endsWith(".delta")) {
          if (n === 1) log(`${ev.type} (further deltas counted, not printed)`);
        } else {
          log(ev.type);
        }
    }
  };
}

main().catch((err) => {
  console.error(`probe failed: ${err.message}`);
  process.exit(1);
});
