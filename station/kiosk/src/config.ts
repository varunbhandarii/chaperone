// Station voice settings come from station/config/voice.json (the single source of truth);
// service URLs come from ?host= / ?relay= / ?policy= / ?catalog=, then VITE_SERVICES_HOST
// (or SERVICES_HOST in the repo-root .env), then localhost.

import voiceJson from "../../../station/config/voice.json";

export interface VoiceConfig {
  model: string;
  ws_url: string;
  subprotocol_prefix: string;
  token_path: string;
  ports: { relay: number; policy: number; catalog: number };
  mandate_id: string;
  merchant: string;
  capture: { preferred_sample_rate: number | null; chunk_ms: number; block_ms: number; preroll_ms: number; min_press_ms: number };
  screen: { timeout_ms: number; screen_partials: boolean };
  refusal: { fallback: "force_message" | "instruct"; clip_timeout_ms: number };
  barge_in: { truncate: boolean };
  session: SessionConfig;
  measured: { release_to_first_audio_ms: number | null };
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

export const SERVICES_HOST = params.get("host") || import.meta.env.VITE_SERVICES_HOST || "localhost";

function serviceUrl(name: "relay" | "policy" | "catalog"): string {
  const override = params.get(name);
  return (override || `http://${SERVICES_HOST}:${VOICE.ports[name]}`).replace(/\/+$/, "");
}

export const URLS = {
  relay: serviceUrl("relay"),
  policy: serviceUrl("policy"),
  catalog: serviceUrl("catalog"),
};

/** ?voice=naksh for Hindi sessions; otherwise the voice in voice.json. */
export const VOICE_NAME = params.get("voice") || VOICE.session.voice;

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

export function realtimeUrl(): string {
  const base = WS_OVERRIDE ?? VOICE.ws_url;
  return `${base}${base.includes("?") ? "&" : "?"}model=${encodeURIComponent(VOICE.model)}`;
}

/** The session object for session.update, with both PCM rates set to the AudioContext's real rate. */
export function buildSession(rate: number, opts: { withTranscriptionModel?: boolean } = {}): SessionConfig {
  const session = structuredClone(VOICE.session);
  session.voice = VOICE_NAME;
  session.audio.input.format.rate = rate;
  session.audio.output.format.rate = rate;
  if (opts.withTranscriptionModel === false && session.audio.input.transcription) {
    delete session.audio.input.transcription.model;
  }
  return session;
}

export const TOOL_NAMES = VOICE.session.tools.map((t) => t.name);
