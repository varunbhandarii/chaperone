// HTTP calls to the relay, catalog and policy services. Every call degrades gracefully:
// a missing or unreachable service produces a logged warning and a safe fallback, never a crash.

import { URLS, VOICE } from "./config.ts";
import { FALLBACK_ITEMS, parseSearchResponse, type CatalogItem, type CheckoutBody } from "./cart.ts";
import type { Lang } from "./lang.ts";
import { parseScreen, type ScreenResult } from "./screen.ts";

export type Warn = (msg: string) => void;

async function readJson(res: Response): Promise<unknown> {
  const text = await res.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return { raw: text.slice(0, 300) };
  }
}

function describe(err: unknown): string {
  if (err instanceof DOMException && (err.name === "TimeoutError" || err.name === "AbortError")) return "timed out";
  return err instanceof Error ? err.message : String(err);
}

// ---------- reachability ----------
// A service that is not running can cost a full timeout per call (on Windows a refused localhost
// connection often hangs instead of failing fast). Probe once at Start, skip services that are down,
// and re-probe the down ones in the background so they come back on their own.

export type ServiceName = "relay" | "policy" | "catalog";
export type ServiceState = "unknown" | "up" | "down";
const SERVICE_NAMES: ServiceName[] = ["relay", "policy", "catalog"];
const PROBE_TIMEOUT_MS = 1000;
const RECHECK_MS = 30_000;

class ServiceHealth {
  private state: Record<ServiceName, ServiceState> = { relay: "unknown", policy: "unknown", catalog: "unknown" };
  private timer: ReturnType<typeof setInterval> | undefined;
  private listeners = new Set<() => void>();

  isDown(name: ServiceName): boolean {
    return this.state[name] === "down";
  }

  snapshot(): Record<ServiceName, ServiceState> {
    return { ...this.state };
  }

  onChange(fn: () => void): void {
    this.listeners.add(fn);
  }

  /** Any HTTP answer (even a 404) means something is listening; only a network error or timeout means down. */
  async probe(name: ServiceName): Promise<ServiceState> {
    try {
      await fetch(`${URLS[name]}/`, { mode: "no-cors", cache: "no-store", signal: AbortSignal.timeout(PROBE_TIMEOUT_MS) });
      this.mark(name, "up");
    } catch {
      this.mark(name, "down");
    }
    return this.state[name];
  }

  /** Probes every service now and keeps re-probing the ones that are not up. */
  start(): Promise<ServiceState[]> {
    if (this.timer === undefined) {
      this.timer = setInterval(() => {
        for (const name of SERVICE_NAMES) if (this.state[name] !== "up") void this.probe(name);
      }, RECHECK_MS);
    }
    return Promise.all(SERVICE_NAMES.map((n) => this.probe(n)));
  }

  stop(): void {
    if (this.timer !== undefined) clearInterval(this.timer);
    this.timer = undefined;
  }

  mark(name: ServiceName, next: ServiceState): void {
    if (this.state[name] === next) return;
    this.state[name] = next;
    console.info(`[health] ${name} ${next} (${URLS[name]})`);
    for (const fn of this.listeners) fn();
  }
}

export const health = new ServiceHealth();

/** Network failures (not HTTP errors) mean the service is gone until the next probe says otherwise. */
function isNetworkFailure(err: unknown): boolean {
  return err instanceof TypeError || (err instanceof DOMException && (err.name === "TimeoutError" || err.name === "AbortError"));
}

// ---------- relay: ephemeral token ----------

export interface ClientSecret {
  value: string;
  expires_at: number;
}

export async function fetchToken(): Promise<ClientSecret> {
  const url = `${URLS.relay}${VOICE.token_path}`;
  let res: Response;
  try {
    res = await fetch(url, { method: "POST", signal: AbortSignal.timeout(10000) });
  } catch (err) {
    throw new Error(`relay unreachable at ${URLS.relay} (${describe(err)}). Start it: .venv/Scripts/python -m uvicorn relay.main:app --host 0.0.0.0 --port 8000`);
  }
  const body = (await readJson(res)) as Partial<ClientSecret> & { error?: string };
  if (!res.ok || !body || typeof body.value !== "string") {
    throw new Error(`token request failed (${res.status}): ${body?.error ?? "no token in response"}`);
  }
  return { value: body.value, expires_at: Number(body.expires_at) };
}

// ---------- catalog ----------

export interface SearchResult {
  query: string;
  items: CatalogItem[];
  source: "catalog" | "fallback";
  took_ms?: number;
  error?: string;
}

export async function searchCatalog(query: string, warn: Warn): Promise<SearchResult> {
  const fallback = (why: string): SearchResult => ({ query, items: FALLBACK_ITEMS.map((i) => ({ ...i })), source: "fallback", error: why });
  if (health.isDown("catalog")) {
    warn(`catalog down at ${URLS.catalog}; using fallback items (rechecked every ${RECHECK_MS / 1000} s)`);
    return fallback("catalog down");
  }
  const url = `${URLS.catalog}/search?q=${encodeURIComponent(query)}&limit=3`;
  try {
    const res = await fetch(url, { signal: AbortSignal.timeout(2500) });
    health.mark("catalog", "up");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const body = await readJson(res);
    const items = parseSearchResponse(body);
    if (!items) throw new Error("response has no results list");
    const took = (body as { took_ms?: unknown }).took_ms;
    return { query, items: items.slice(0, 3), source: "catalog", took_ms: typeof took === "number" ? took : undefined };
  } catch (err) {
    if (isNetworkFailure(err)) health.mark("catalog", "down");
    warn(`catalog unavailable at ${URLS.catalog} (${describe(err)}); using fallback items`);
    return fallback(describe(err));
  }
}

// ---------- policy: checkout ----------

export async function postCheckout(body: CheckoutBody, warn: Warn): Promise<Record<string, unknown>> {
  const url = `${URLS.policy}/checkout`;
  // Checkout matters more than a second of latency: re-probe a down policy service instead of skipping it.
  if (health.isDown("policy") && (await health.probe("policy")) === "down") {
    warn(`policy down at ${URLS.policy}`);
    return { error: "policy service is down; nothing was bought" };
  }
  try {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(15000),
    });
    health.mark("policy", "up");
    const json = await readJson(res);
    if (json && typeof json === "object" && !Array.isArray(json)) {
      if (!res.ok && !("error" in json) && !("decision" in json)) return { error: `policy answered ${res.status}`, detail: json };
      return json as Record<string, unknown>;
    }
    return { error: `policy answered ${res.status} without a JSON object` };
  } catch (err) {
    if (isNetworkFailure(err)) health.mark("policy", "down");
    warn(`policy unreachable at ${URLS.policy} (${describe(err)})`);
    return { error: `policy service unreachable (${describe(err)}); nothing was bought` };
  }
}

// ---------- policy: rule screen ----------

export async function screenText(sessionId: string, text: string, lang: Lang | undefined, warn: Warn): Promise<ScreenResult | null> {
  const url = `${URLS.policy}/screen`;
  if (health.isDown("policy")) {
    warn(`rule screen skipped: policy down at ${URLS.policy} (rechecked every ${RECHECK_MS / 1000} s)`);
    return null;
  }
  try {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(lang ? { session_id: sessionId, text, lang } : { session_id: sessionId, text }),
      signal: AbortSignal.timeout(1200),
    });
    health.mark("policy", "up");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const parsed = parseScreen(await readJson(res));
    if (!parsed) throw new Error("unexpected response shape");
    return parsed;
  } catch (err) {
    if (isNetworkFailure(err)) health.mark("policy", "down");
    warn(`rule screen unavailable at ${url} (${describe(err)}); proceeding without it`);
    return null;
  }
}

// ---------- relay: ledger events (fire and forget) ----------

export class Ledger {
  private warned = false;
  readonly sessionId: string;
  readonly mandateId: string;
  private readonly warn: Warn;

  constructor(sessionId: string, mandateId: string, warn: Warn) {
    this.sessionId = sessionId;
    this.mandateId = mandateId;
    this.warn = warn;
  }

  post(type: string, payload: Record<string, unknown> = {}): void {
    const event = { ...payload, type, session_id: this.sessionId, mandate_id: this.mandateId, t: Date.now(), source: "station" };
    fetch(`${URLS.relay}/events`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(event),
      keepalive: true,
    })
      .then((res) => {
        if (!res.ok) this.warnOnce(`ledger POST ${URLS.relay}/events answered ${res.status}; events are only logged locally`);
      })
      .catch((err) => this.warnOnce(`ledger unreachable at ${URLS.relay}/events (${describe(err)}); events are only logged locally`));
  }

  private warnOnce(msg: string): void {
    if (this.warned) return;
    this.warned = true;
    this.warn(msg);
  }
}

// ---------- refusal clips ----------

const clipCache = new Map<string, Promise<AudioBuffer | null>>();

/** Fetches and decodes a refusal clip (relative URLs resolve against the relay). Null when it cannot load in time. */
export function loadClip(ctx: AudioContext, audioUrl: string, timeoutMs: number, warn: Warn): Promise<AudioBuffer | null> {
  const url = new URL(audioUrl, URLS.relay + "/").toString();
  let pending = clipCache.get(url);
  if (!pending) {
    pending = (async () => {
      try {
        const res = await fetch(url, { signal: AbortSignal.timeout(Math.max(timeoutMs, 3000)) });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return await ctx.decodeAudioData(await res.arrayBuffer());
      } catch (err) {
        warn(`refusal clip ${url} unavailable (${describe(err)})`);
        clipCache.delete(url);
        return null;
      }
    })();
    clipCache.set(url, pending);
  }
  const timeout = new Promise<null>((resolve) => setTimeout(() => resolve(null), timeoutMs));
  return Promise.race([pending, timeout]);
}
