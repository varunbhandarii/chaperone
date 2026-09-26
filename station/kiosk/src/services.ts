// HTTP calls to the relay, catalog and policy services. Every call degrades gracefully:
// a missing or unreachable service produces a logged warning and a safe fallback, never a crash.

import { URLS, VOICE } from "./config.ts";
import { parseApproval, parseReceipt, type ApprovalStatus, type Receipt } from "./receipt.ts";
import {
  parseCancelReply,
  parseHistory,
  parseOrder,
  parseRefundReply,
  type CancelReply,
  type HistorySummary,
  type OrderView,
  type RefundReply,
} from "./postpurchase.ts";
import { FALLBACK_ITEMS, mergeResults, parseResolveResponse, parseSearchResponse, type CatalogItem, type CheckoutBody } from "./cart.ts";
import type { Lang } from "./lang.ts";
import { parseScreen, type ScreenResult } from "./screen.ts";
import { parseBill, parseScamReply, type BillView, type ScamVerdict } from "./guards.ts";

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

export type ServiceName = "relay" | "policy" | "merchant" | "catalog" | "printer";
export type ServiceState = "unknown" | "up" | "down";
const SERVICE_NAMES: ServiceName[] = ["relay", "policy", "merchant", "catalog", "printer"];
const PROBE_TIMEOUT_MS = 1000;
const RECHECK_MS = 30_000;

class ServiceHealth {
  private state: Record<ServiceName, ServiceState> = { relay: "unknown", policy: "unknown", merchant: "unknown", catalog: "unknown", printer: "unknown" };
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
      await call(`${URLS[name]}/`, { mode: "no-cors", cache: "no-store", signal: AbortSignal.timeout(PROBE_TIMEOUT_MS) });
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
  return (
    err instanceof ProxyUnreachable ||
    err instanceof TypeError ||
    (err instanceof DOMException && (err.name === "TimeoutError" || err.name === "AbortError"))
  );
}

/** The dev-server proxy could not reach the service (vite.config.ts marks those 502s). */
class ProxyUnreachable extends Error {}

function proxyUnreachable(res: Response): boolean {
  return res.status === 502 && res.headers.get("x-station-proxy") === "unreachable";
}

/** fetch that turns "the proxy could not reach the service" into a network failure. */
async function call(url: string, init?: RequestInit): Promise<Response> {
  const res = await fetch(url, init);
  if (proxyUnreachable(res)) throw new ProxyUnreachable(`${new URL(url).pathname.split("/")[2] ?? "service"} unreachable through the proxy`);
  return res;
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
    res = await call(url, { method: "POST", signal: AbortSignal.timeout(10000) });
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

/** Profile phrases first ("my blood pressure medicine" -> the saved pickup, marked usual), then catalog search. */
export async function searchCatalog(query: string, warn: Warn, store?: string): Promise<SearchResult> {
  const fallback = (why: string): SearchResult => ({ query, items: FALLBACK_ITEMS.map((i) => ({ ...i })), source: "fallback", error: why });
  if (health.isDown("catalog")) {
    warn(`catalog down at ${URLS.catalog}; using fallback items (rechecked every ${RECHECK_MS / 1000} s)`);
    return fallback("catalog down");
  }
  const q = encodeURIComponent(query);
  try {
    const t0 = performance.now();
    const [resolved, searched] = await Promise.all([
      call(`${URLS.catalog}/resolve?q=${q}`, { signal: AbortSignal.timeout(2500) }).then(
        async (res) => (res.ok ? parseResolveResponse(await readJson(res)) : []),
        () => [] as CatalogItem[],
      ),
      call(`${URLS.catalog}/search?q=${q}&limit=3${store ? `&store=${encodeURIComponent(store)}` : ""}`, { signal: AbortSignal.timeout(2500) }),
    ]);
    health.mark("catalog", "up");
    if (!searched.ok) throw new Error(`HTTP ${searched.status}`);
    const items = parseSearchResponse(await readJson(searched));
    if (!items) throw new Error("response has no items list");
    return { query, items: mergeResults(resolved, items, 3), source: "catalog", took_ms: Math.round(performance.now() - t0) };
  } catch (err) {
    if (isNetworkFailure(err)) health.mark("catalog", "down");
    warn(`catalog unavailable at ${URLS.catalog} (${describe(err)}); using fallback items`);
    return fallback(describe(err));
  }
}

// ---------- policy: budget ----------

export interface Budget {
  monthly_cap: number;
  spent: number;
  left: number;
}

export async function getBudget(mandateId: string, warn: Warn): Promise<Budget | { error: string }> {
  if (health.isDown("policy") && (await health.probe("policy")) === "down") return { error: "policy service is down" };
  try {
    const res = await call(`${URLS.policy}/budget?mandate_id=${encodeURIComponent(mandateId)}`, { signal: AbortSignal.timeout(2500) });
    health.mark("policy", "up");
    const body = (await readJson(res)) as Partial<Budget> | null;
    if (!res.ok || !body || typeof body.left !== "number") return { error: `budget unavailable (HTTP ${res.status})` };
    return { monthly_cap: Number(body.monthly_cap), spent: Number(body.spent), left: body.left };
  } catch (err) {
    if (isNetworkFailure(err)) health.mark("policy", "down");
    warn(`budget unavailable at ${URLS.policy} (${describe(err)})`);
    return { error: `budget unavailable (${describe(err)})` };
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
    const res = await call(url, {
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

/** partial: true for live (cumulative) transcripts, false for the final one, so the policy counts each turn once. */
export async function screenText(sessionId: string, text: string, lang: Lang | undefined, warn: Warn, partial = false): Promise<ScreenResult | null> {
  const url = `${URLS.policy}/screen`;
  if (health.isDown("policy")) {
    warn(`rule screen skipped: policy down at ${URLS.policy} (rechecked every ${RECHECK_MS / 1000} s)`);
    return null;
  }
  try {
    const res = await call(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, text, ...(lang ? { lang } : {}), partial }),
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
  /** Set while a cached session replays, so the wall labels those events. */
  replay = false;

  constructor(sessionId: string, mandateId: string, warn: Warn) {
    this.sessionId = sessionId;
    this.mandateId = mandateId;
    this.warn = warn;
  }

  post(type: string, payload: Record<string, unknown> = {}): void {
    const event = {
      ...payload,
      ...(this.replay ? { replay: true } : {}),
      type,
      session_id: this.sessionId,
      mandate_id: this.mandateId,
      t: Date.now(),
      source: "station",
    };
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
  // "/audio/x.mp3" must keep the relay's path prefix (the dev proxy serves the relay under /svc/relay).
  const url = audioUrl.startsWith("/") ? URLS.relay.replace(/\/+$/, "") + audioUrl : new URL(audioUrl, URLS.relay + "/").toString();
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

// ---------- caregiver approval ----------

/** GET {policy}/approvals/{id}; null when the policy is unreachable or the reply has no state. */
export async function getApproval(approvalId: string): Promise<ApprovalStatus | null> {
  try {
    const res = await call(`${URLS.policy}/approvals/${encodeURIComponent(approvalId)}`, { signal: AbortSignal.timeout(2500), cache: "no-store" });
    health.mark("policy", "up");
    return res.ok ? parseApproval(await readJson(res)) : null;
  } catch (err) {
    if (isNetworkFailure(err)) health.mark("policy", "down");
    return null;
  }
}

/**
 * POST {policy}/approvals/{id}/cancel: the shopper moved on, so policy closes the approval and a late tap on the
 * phone cannot place the order. LAN-only at policy (the dev proxy adds no forwarding headers). False when policy
 * refused (the approval had already closed) or could not be reached.
 */
export async function cancelApproval(approvalId: string): Promise<boolean> {
  try {
    const res = await call(`${URLS.policy}/approvals/${encodeURIComponent(approvalId)}/cancel`, {
      method: "POST",
      headers: { "X-Chaperone-Host": "1" },
      signal: AbortSignal.timeout(2500),
    });
    console.info(`[approval] cancel ${approvalId}: HTTP ${res.status}`);
    return res.ok;
  } catch (err) {
    console.warn(`[approval] cancel ${approvalId} failed: ${describe(err)}`);
    return false;
  }
}

// ---------- after payment: order status, cancel, refunds, history ----------

/** GET {merchant}/orders/{id}: status, pickup code and timeline. */
export async function getOrder(orderId: string): Promise<OrderView | null> {
  try {
    const res = await call(`${URLS.merchant}/orders/${encodeURIComponent(orderId)}`, { signal: AbortSignal.timeout(2500), cache: "no-store" });
    health.mark("merchant", "up");
    return res.ok ? parseOrder(await readJson(res)) : null;
  } catch (err) {
    if (isNetworkFailure(err)) health.mark("merchant", "down");
    return null;
  }
}

/** POST {policy}/orders/{id}/cancel: policy checks the order is this mandate's and unpaid, then signs the merchant call. */
export async function cancelOrder(orderId: string, body: Record<string, unknown>): Promise<CancelReply> {
  try {
    const res = await call(`${URLS.policy}/orders/${encodeURIComponent(orderId)}/cancel`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(15000),
    });
    health.mark("policy", "up");
    return parseCancelReply(res.status, await readJson(res));
  } catch (err) {
    if (isNetworkFailure(err)) health.mark("policy", "down");
    return { kind: "error", error: `policy unreachable (${describe(err)})` };
  }
}

/** POST {policy}/refunds: preview (confirmed false) or refund (confirmed true). No amount or destination is ever sent. */
export async function postRefund(body: Record<string, unknown>): Promise<RefundReply> {
  try {
    const res = await call(`${URLS.policy}/refunds`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(15000),
    });
    health.mark("policy", "up");
    return parseRefundReply(await readJson(res));
  } catch (err) {
    if (isNetworkFailure(err)) health.mark("policy", "down");
    return { kind: "error", error: `policy unreachable (${describe(err)})` };
  }
}

/** GET {policy}/history?mandate_id=&days= */
export async function getHistory(mandateId: string, days: number): Promise<HistorySummary | null> {
  try {
    const res = await call(`${URLS.policy}/history?mandate_id=${encodeURIComponent(mandateId)}&days=${days}`, { signal: AbortSignal.timeout(4000) });
    health.mark("policy", "up");
    return res.ok ? parseHistory(await readJson(res)) : null;
  } catch (err) {
    if (isNetworkFailure(err)) health.mark("policy", "down");
    return null;
  }
}

// ---------- the Ask guard: scam check and billers ----------

export type ScamReply = { kind: "ok"; verdict: ScamVerdict } | { kind: "not_ready"; status: number } | { kind: "error"; error: string };

/** POST {policy}/scam-check: rules first, then the facts, then Grok with search (policy keeps it under 12 s). */
export async function scamCheck(body: Record<string, unknown>): Promise<ScamReply> {
  try {
    const res = await call(`${URLS.policy}/scam-check`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(14000),
    });
    health.mark("policy", "up");
    if (res.status === 404 || res.status === 405 || res.status === 501) return { kind: "not_ready", status: res.status };
    const verdict = parseScamReply(await readJson(res));
    return verdict ? { kind: "ok", verdict } : { kind: "error", error: `unexpected scam-check reply (HTTP ${res.status})` };
  } catch (err) {
    if (isNetworkFailure(err)) health.mark("policy", "down");
    return { kind: "error", error: `policy unreachable (${describe(err)})` };
  }
}

/** GET {merchant}/billers/{id}/accounts/{ref}: what Ruth really owes. */
export async function getBill(billerId: string, accountRef: string): Promise<BillView | null> {
  try {
    const res = await call(`${URLS.merchant}/billers/${encodeURIComponent(billerId)}/accounts/${encodeURIComponent(accountRef)}`, {
      signal: AbortSignal.timeout(3000),
      cache: "no-store",
    });
    health.mark("merchant", "up");
    return res.ok ? parseBill(await readJson(res)) : null;
  } catch (err) {
    if (isNetworkFailure(err)) health.mark("merchant", "down");
    return null;
  }
}

/** Ruth's billers from the signed mandate (v2 `billers[]`), e.g. [{merchant_id, account_ref, monthly_cap}]. */
export async function getMandateBillers(): Promise<Array<{ merchant_id: string; account_ref: string }> | null> {
  try {
    const res = await call(`${URLS.policy}/mandate`, { signal: AbortSignal.timeout(2500) });
    if (!res.ok) return null;
    const body = (await readJson(res)) as { mandate?: { billers?: unknown } } | null;
    const list = body?.mandate?.billers;
    if (!Array.isArray(list)) return null;
    return list
      .filter((b): b is Record<string, unknown> => !!b && typeof b === "object")
      .filter((b) => typeof b.merchant_id === "string" && typeof b.account_ref === "string")
      .map((b) => ({ merchant_id: String(b.merchant_id), account_ref: String(b.account_ref) }));
  } catch {
    return null;
  }
}

// ---------- receipt ----------

/** GET {merchant}/orders/{id}/receipt; null when unavailable (the station then builds the receipt itself). */
export async function getReceipt(orderId: string, lang: Lang): Promise<Receipt | null> {
  try {
    const res = await call(`${URLS.merchant}/orders/${encodeURIComponent(orderId)}/receipt?lang=${lang}`, { signal: AbortSignal.timeout(2000) });
    health.mark("merchant", "up");
    return res.ok ? parseReceipt(await readJson(res), lang) : null;
  } catch (err) {
    if (isNetworkFailure(err)) health.mark("merchant", "down");
    return null;
  }
}

export interface PrintResult {
  ok: boolean;
  /** "printer": on paper; "pdf": no printer, the saved PDF (and its image) is the receipt */
  via?: "printer" | "pdf";
  reason?: string;
  ms: number;
  /** absolute URLs (through the proxy) of the rendered receipt */
  pngUrl?: string;
  pdfUrl?: string;
}

/** POST {printer}/print; anything but {ok: true} within 5 s counts as a failed print. */
export async function printReceipt(receipt: Receipt): Promise<PrintResult> {
  const t0 = performance.now();
  if (health.isDown("printer")) return { ok: false, reason: "print helper is not running", ms: 0 };
  try {
    const res = await call(`${URLS.printer}/print`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(receipt),
      signal: AbortSignal.timeout(5000),
    });
    health.mark("printer", "up");
    const body = (await readJson(res)) as { ok?: unknown; via?: unknown; reason?: unknown; png_url?: unknown; pdf_url?: unknown } | null;
    const ms = Math.round(performance.now() - t0);
    const files = {
      ...(typeof body?.png_url === "string" ? { pngUrl: `${URLS.printer}${body.png_url}` } : {}),
      ...(typeof body?.pdf_url === "string" ? { pdfUrl: `${URLS.printer}${body.pdf_url}` } : {}),
    };
    const reason = typeof body?.reason === "string" ? body.reason : undefined;
    if (res.ok && body?.ok === true) return { ok: true, via: body.via === "pdf" ? "pdf" : "printer", ms, ...(reason ? { reason } : {}), ...files };
    return { ok: false, reason: reason ?? `HTTP ${res.status}`, ms, ...files };
  } catch (err) {
    if (isNetworkFailure(err)) health.mark("printer", "down");
    return { ok: false, reason: describe(err), ms: Math.round(performance.now() - t0) };
  }
}

// ---------- cached sessions: the print helper's files, else this browser's IndexedDB ----------

function idb<T>(mode: IDBTransactionMode, run: (store: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    const open = indexedDB.open("chaperone-station", 1);
    open.onupgradeneeded = () => open.result.createObjectStore("cached");
    open.onerror = () => reject(open.error);
    open.onsuccess = () => {
      const db = open.result;
      const req = run(db.transaction("cached", mode).objectStore("cached"));
      req.onsuccess = () => {
        resolve(req.result);
        db.close();
      };
      req.onerror = () => {
        reject(req.error);
        db.close();
      };
    };
  });
}

/** Saves to the print helper (sessions/cached/<lang>.json) and, as a copy, to this browser's IndexedDB. */
export async function saveCachedSession(lang: Lang, session: unknown): Promise<{ ok: boolean; where: string; error?: string }> {
  let helper = "";
  try {
    const res = await call(`${URLS.printer}/cached/${lang}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(session),
      signal: AbortSignal.timeout(20000),
    });
    if (!res.ok) helper = `helper answered HTTP ${res.status}`;
  } catch (err) {
    helper = `helper unavailable (${describe(err)})`;
  }
  try {
    await idb("readwrite", (store) => store.put(session, lang));
  } catch (err) {
    return helper ? { ok: false, where: "nowhere", error: `${helper}; browser storage failed (${describe(err)})` } : { ok: true, where: "the print helper" };
  }
  return { ok: true, where: helper ? `this browser only (${helper})` : "the print helper and this browser" };
}

/** The print helper's copy first (it survives a cleared browser), else the browser's. */
export async function loadCachedSession(lang: Lang): Promise<unknown | null> {
  try {
    const res = await call(`${URLS.printer}/cached/${lang}`, { signal: AbortSignal.timeout(20000) });
    if (res.ok) return await res.json();
  } catch {
    /* fall through to the browser copy */
  }
  try {
    return (await idb("readonly", (store) => store.get(lang))) ?? null;
  } catch {
    return null;
  }
}

// ---------- reset ----------

/** POST {relay}/reset: the relay resets policy, merchant and the live ledger, then posts a reset event. */
export async function requestReset(): Promise<boolean> {
  try {
    // The header marks a deliberate Host action; a cross-site form or no-CORS fetch cannot set it.
    const res = await call(`${URLS.relay}/reset`, { method: "POST", headers: { "X-Chaperone-Host": "1" }, signal: AbortSignal.timeout(15000) });
    if (!res.ok) return false;
    // The relay answers 200 with {ok: false, failed: [...]} when policy or the merchant did not reset.
    const body = (await res.json().catch(() => ({}))) as { ok?: boolean };
    return body.ok !== false;
  } catch {
    return false;
  }
}

// ---------- relay event stream ----------

export type StreamEvent = { type: string; session_id?: string; seq?: number; [k: string]: unknown };

/**
 * Follows the relay's event stream for the given types. The stream first replays the live ledger, so the
 * backlog is skipped: the current last sequence number is read once, and only newer events are delivered.
 */
export class RelayStream {
  private source: EventSource | null = null;
  private lastSeq = 0;
  private closed = false;
  private readonly types: string[];
  private readonly onEvent: (ev: StreamEvent) => void;
  private readonly warn: Warn;

  constructor(types: string[], onEvent: (ev: StreamEvent) => void, warn: Warn) {
    this.types = types;
    this.onEvent = onEvent;
    this.warn = warn;
  }

  async open(): Promise<void> {
    const url = `${URLS.relay}/events/stream?types=${encodeURIComponent(this.types.join(","))}`;
    try {
      const res = await call(`${url}&once=true`, { signal: AbortSignal.timeout(5000), cache: "no-store" });
      const text = await res.text();
      for (const m of text.matchAll(/^id: (\d+)$/gm)) this.lastSeq = Math.max(this.lastSeq, Number(m[1]));
    } catch (err) {
      this.warn(`relay event stream unavailable (${describe(err)}); paid and reset events will not arrive until it is up`);
    }
    if (this.closed) return;
    const source = new EventSource(`${url}&last_event_id=${this.lastSeq}`);
    this.source = source;
    source.onmessage = (e) => {
      let ev: StreamEvent;
      try {
        ev = JSON.parse(e.data);
      } catch {
        return;
      }
      const seq = typeof ev.seq === "number" ? ev.seq : Number(e.lastEventId);
      if (Number.isFinite(seq) && seq <= this.lastSeq) return; // replayed on reconnect
      if (Number.isFinite(seq)) this.lastSeq = seq;
      this.onEvent(ev);
    };
    source.onerror = () => {
      // EventSource reconnects on its own and sends Last-Event-ID, so nothing is missed or repeated.
      console.warn("[stream] relay event stream interrupted; reconnecting");
    };
  }

  close(): void {
    this.closed = true;
    this.source?.close();
    this.source = null;
  }
}
