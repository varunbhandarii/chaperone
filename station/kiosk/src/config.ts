// Station voice settings come from station/config/voice.json (the single source of truth);
// Service URLs: by default the same-origin paths /svc/<name>, which the dev server proxies to
// SERVICES_HOST (repo-root .env) so the services need no CORS. ?host= or ?relay= / ?policy= / ?catalog=
// call a service directly instead (tests and debugging).

import voiceJson from "../../../station/config/voice.json";
import merchantsJson from "../../../contracts/merchants.json";
import type { StoreRegistry } from "./receipt.ts";

export interface VoiceConfig {
  model: string;
  ws_url: string;
  subprotocol_prefix: string;
  token_path: string;
  ports: { relay: number; policy: number; merchant: number; catalog: number; printer: number };
  mandate_id: string;
  merchant: string;
  capture: { preferred_sample_rate: number | null; chunk_ms: number; block_ms: number; preroll_ms: number; min_press_ms: number };
  screen: { timeout_ms: number; screen_partials: boolean };
  refusal: { fallback: "force_message" | "instruct"; clip_timeout_ms: number };
  barge_in: { truncate: boolean };
  session: SessionConfig;
  /** voice per shopper language, e.g. {"hi": "naksh"}; ?voice= overrides it for the whole session */
  voice_by_lang?: Partial<Record<"es" | "hi" | "en", string>>;
  /** the soft tick while the station checks: after_ms from release with no reply yet, then every interval_ms */
  earcon: { after_ms: number; interval_ms: number; gain_db: number };
  /** Ruth's billers (the signed mandate's billers[] wins when it has one) */
  billers: Record<string, { name: string; account_ref: string }>;
  measured: { release_to_first_audio_ms: number | null; [k: string]: unknown };
}

export interface SessionConfig {
  instructions: string;
  voice: string;
  audio: {
    input: { format: { type: string; rate: number | null }; transcription?: Record<string, unknown>; [k: string]: unknown };
    output: { format: { type: string; rate: number | null }; [k: string]: unknown };
  };
  tools: Array<{ type: string; name: string; [k: string]: unknown }>;
  [k: string]: unknown;
}

export const VOICE = voiceJson as unknown as VoiceConfig;

const params = new URLSearchParams(typeof location === "undefined" ? "" : location.search);

const DIRECT_HOST = params.get("host");
export const SERVICES_HOST = DIRECT_HOST || import.meta.env.VITE_SERVICES_HOST || "localhost";

export type ServiceKey = "relay" | "policy" | "merchant" | "catalog" | "printer";

function serviceUrl(name: ServiceKey): string {
  const override = params.get(name);
  if (override) return override.replace(/\/+$/, "");
  if (DIRECT_HOST || typeof location === "undefined") {
    // The print helper always runs on the station laptop itself.
    return `http://${name === "printer" ? "127.0.0.1" : SERVICES_HOST}:${VOICE.ports[name]}`;
  }
  return `${location.origin}/svc/${name}`;
}

/** Public tunnel host for the receipt's QR code (?tunnel= overrides TUNNEL_HOST from the root .env). */
export const TUNNEL_HOST = params.get("tunnel") || import.meta.env.VITE_TUNNEL_HOST || "";

export const URLS = {
  relay: serviceUrl("relay"),
  policy: serviceUrl("policy"),
  merchant: serviceUrl("merchant"),
  catalog: serviceUrl("catalog"),
  printer: serviceUrl("printer"),
};

const VOICE_OVERRIDE = params.get("voice");

/** ?voice=... forces one voice; otherwise the voice in voice.json until the shopper's language is known. */
export const VOICE_NAME = VOICE_OVERRIDE || VOICE.session.voice;

/** The voice for a shopper language (voice_by_lang), unless ?voice= forced one. */
export function voiceFor(lang: "es" | "hi" | "en"): string {
  return VOICE_OVERRIDE || VOICE.voice_by_lang?.[lang] || VOICE.session.voice;
}

/** transcription.language_hint values: Spanish needs a region (bare "es" is rejected). */
export function languageHint(lang: "es" | "hi" | "en"): string {
  return { es: "es-MX", hi: "hi", en: "en" }[lang];
}

/** ?ws= points the page at a local mock of the realtime API; only loopback hosts are accepted. */
function wsOverride(): string | null {
  const raw = params.get("ws");
  if (!raw) return null;
  try {
    const u = new URL(raw);
    const loopback = ["localhost", "127.0.0.1", "[::1]"].includes(u.hostname);
    return loopback && (u.protocol === "ws:" || u.protocol === "wss:") ? u.toString() : null;
  } catch {
    return null;
  }
}

export const WS_OVERRIDE = wsOverride();

/** The realtime URL; with a conversation id the server resumes that conversation (session resumption). */
export function realtimeUrl(conversationId?: string): string {
  const base = WS_OVERRIDE ?? VOICE.ws_url;
  const url = `${base}${base.includes("?") ? "&" : "?"}model=${encodeURIComponent(VOICE.model)}`;
  return conversationId ? `${url}&conversation_id=${encodeURIComponent(conversationId)}` : url;
}

/** The session object for session.update, with both PCM rates set to the AudioContext's real rate. */
export function buildSession(
  rate: number,
  opts: { withTranscriptionModel?: boolean; languageHint?: string; voice?: string } = {},
): SessionConfig {
  const session = structuredClone(VOICE.session);
  session.voice = opts.voice ?? VOICE_NAME;
  if (opts.languageHint) session.audio.input.transcription = { ...(session.audio.input.transcription ?? {}), language_hint: opts.languageHint };
  session.audio.input.format.rate = rate;
  session.audio.output.format.rate = rate;
  if (opts.withTranscriptionModel === false && session.audio.input.transcription) {
    delete session.audio.input.transcription.model;
  }
  return session;
}

export const TOOL_NAMES = VOICE.session.tools.map((t) => t.name);

// ---------- stores: one registry for every name (contracts/merchants.json) ----------

interface MerchantEntry {
  id: string;
  name: string;
  kind: "store" | "biller" | "blocked";
  mcc: string;
  categories: string[];
}

export const MERCHANTS: MerchantEntry[] = (merchantsJson as { merchants: MerchantEntry[] }).merchants;

/** A store's name for Ruth ("Parkside Pharmacy"); a card terminal's name for a swipe from a store we don't sell at. */
export function storeName(id: string | undefined): string | undefined {
  if (!id) return undefined;
  return MERCHANTS.find((m) => m.id === id)?.name ?? (merchantsJson as { card_terminal_stores?: Array<{ acceptor_id: string; name: string }> })
    .card_terminal_stores?.find((s) => s.acceptor_id === id)?.name;
}

export const STORES: StoreRegistry = {
  name: storeName,
  isBiller: (id) => MERCHANTS.some((m) => m.id === id && m.kind === "biller"),
  account: (id) => VOICE.billers?.[id]?.account_ref,
};
