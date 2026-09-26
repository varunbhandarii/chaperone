// After payment: order status, cancel, refunds with a confirm gate, purchase history, and "repeat that".
// Pure functions and classes: no DOM or network, so they run under `node --test`.

import type { Lang } from "./lang.ts";

// ---------------------------------------------------------------- refund confirm gate

export interface RefundTarget {
  order_id: string;
  sku?: string;
  qty?: number;
}

function sameTarget(a: RefundTarget, b: RefundTarget): boolean {
  return a.order_id === b.order_id && (a.sku ?? "") === (b.sku ?? "") && (a.qty ?? 0) === (b.qty ?? 0);
}

export type RefundGateResult = { ok: true } | { ok: false; reason: "no_preview" | "target_changed" | "no_confirmation" };

/**
 * request_refund {confirmed: true} goes to policy only after a {confirmed: false} preview for the same order and
 * item, and a shopper turn with words after that preview (the "yes"), the same rule as the checkout read-back.
 */
export class RefundGate {
  private preview: { target: RefundTarget; atTurn: number; shopperIndex: number } | null = null;

  markPreview(target: RefundTarget, userTurns: number, shopperIndex: number): void {
    this.preview = { target: { ...target }, atTurn: userTurns, shopperIndex };
  }

  check(target: RefundTarget, userTurns: number): RefundGateResult {
    if (!this.preview) return { ok: false, reason: "no_preview" };
    if (!sameTarget(this.preview.target, target)) return { ok: false, reason: "target_changed" };
    if (userTurns <= this.preview.atTurn) return { ok: false, reason: "no_confirmation" };
    return { ok: true };
  }

  /** Where the shopper's words since the preview start, so an earlier line does not ride along to policy. */
  get sinceShopperIndex(): number {
    return this.preview?.shopperIndex ?? 0;
  }

  reset(): void {
    this.preview = null;
  }
}

// ---------------------------------------------------------------- "repeat that"

function norm(text: string): string {
  return text
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[¿?¡!.,;:"'।]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

const REPEAT_PHRASES: string[] = [
  // en
  "repeat that", "repeat", "repeat please", "please repeat", "say that again", "say it again", "can you repeat that",
  "could you repeat that", "what did you say", "come again", "pardon", "pardon me", "sorry what", "one more time", "again please",
  // es
  "otra vez", "repita", "repitalo", "repitelo", "repita por favor", "puede repetir", "me lo repite", "como", "perdon",
  "no le entendi", "no entendi", "que dijo", "digalo otra vez", "otra vez por favor",
  // hi (Latin and Devanagari)
  "phir se boliye", "phir se bolo", "fir se boliye", "fir se bolo", "dobara boliye", "dobara bolo", "kya kaha",
  // not a bare "phir se": "phir se bread dalo" starts that way, and Grok may complete the turn early
  "ek baar aur", "फिर से बोलिए", "फिर से बोलो", "दोबारा बोलिए", "दोबारा बोलो", "क्या कहा", "एक बार और",
].map(norm);

const extraRepeat = new Set<string>();

/** The line files can add trigger phrases ("repeat_phrases"). */
export function registerRepeatPhrases(phrases: string[]): void {
  for (const p of phrases) if (typeof p === "string" && p.trim()) extraRepeat.add(norm(p));
}

/** True when the whole turn only asks to hear the last line again (not "repeat my order of bread"). */
export function isRepeatRequest(text: string): boolean {
  const t = norm(text);
  if (!t || t.split(" ").length > 6) return false;
  const bare = t.replace(/^(please|por favor|kripya|कृपया|ji|जी)\s+/, "").replace(/\s+(please|por favor|ji|जी)$/, "");
  return REPEAT_PHRASES.includes(t) || REPEAT_PHRASES.includes(bare) || extraRepeat.has(t) || extraRepeat.has(bare);
}

// ---------------------------------------------------------------- order status

export type OrderStatus =
  | "awaiting_payment"
  | "paid"
  | "preparing"
  | "ready_for_pickup"
  | "picked_up"
  | "cancelled"
  | "partially_refunded"
  | "refunded";

const STATUS_WORDS: Record<OrderStatus, Record<Lang, string>> = {
  awaiting_payment: { en: "waiting for payment", es: "esperando el pago", hi: "भुगतान का इंतज़ार कर रहा है" },
  paid: { en: "paid", es: "pagado", hi: "भुगतान हो गया है" },
  preparing: { en: "being prepared", es: "en preparación", hi: "तैयार हो रहा है" },
  ready_for_pickup: { en: "ready for pickup", es: "listo para recoger", hi: "ले जाने के लिए तैयार है" },
  picked_up: { en: "picked up", es: "recogido", hi: "ले जाया जा चुका है" },
  cancelled: { en: "cancelled", es: "cancelado", hi: "रद्द हो गया है" },
  partially_refunded: { en: "partly refunded", es: "reembolsado en parte", hi: "कुछ पैसे वापस हो गए हैं" },
  refunded: { en: "refunded", es: "reembolsado", hi: "पैसे वापस हो गए हैं" },
};

export function statusWords(status: string, lang: Lang): string {
  return (STATUS_WORDS as Record<string, Record<Lang, string>>)[status]?.[lang] ?? status.replace(/_/g, " ");
}

/** "472" -> "4-7-2", so the voice reads the pickup code digit by digit. */
export function spokenCode(code: string | undefined): string {
  return (code ?? "").replace(/\D/g, "").split("").join("-");
}

export interface OrderView {
  order_id: string;
  status: string;
  /** The pickup progress, which goes on after a partial refund while `status` says partially_refunded. */
  fulfilment?: string;
  pickup_code?: string;
  total?: number;
  timeline: Array<{ status: string; at: string | number }>;
}

function num(v: unknown): number | undefined {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string" && /^-?\d+(\.\d+)?$/.test(v.trim())) return Number(v);
  return undefined;
}

/** GET {merchant}/orders/{id}: {order_id, status, pickup_code?, amount?, timeline?: [{status, at}]}. */
export function parseOrder(body: unknown): OrderView | null {
  if (!body || typeof body !== "object") return null;
  const b = body as Record<string, unknown>;
  if (typeof b.order_id !== "string" || typeof b.status !== "string") return null;
  const timeline = Array.isArray(b.timeline)
    ? (b.timeline as unknown[])
        .filter((e): e is Record<string, unknown> => !!e && typeof e === "object" && typeof (e as Record<string, unknown>).status === "string")
        .map((e) => ({ status: String(e.status), at: (typeof e.at === "number" || typeof e.at === "string" ? e.at : "") as string | number }))
    : [];
  const code = b.pickup_code;
  const total = num(b.amount) ?? num(b.total);
  return {
    order_id: b.order_id,
    status: b.status,
    ...(typeof b.fulfilment === "string" ? { fulfilment: b.fulfilment } : {}),
    ...(typeof code === "string" || typeof code === "number" ? { pickup_code: String(code) } : {}),
    ...(total !== undefined ? { total } : {}),
    timeline,
  };
}

// ---------------------------------------------------------------- refund replies

export type RefundReply =
  | { kind: "preview"; amount: number; card_last4?: string; items: Array<{ name: string; qty: number; amount?: number }>; say?: string }
  | { kind: "done"; amount?: number; status: string; refund_id?: string; reconciliation_id?: string; source?: string; say_key?: string }
  | { kind: "declined"; say_key: string; rules_failed: string[] }
  | { kind: "error"; error: string };

/**
 * POST {policy}/refunds. A preview answers {preview: {amount, card_last4, items}, say}; a confirmed refund carries the
 * processor's shape ({id, status: "PENDING", reconciliationId, refundAmountDetails: {refundAmount}}) directly or
 * under `refund`; a refusal is a deny decision with a say key (refund_not_allowed_rx, refund_scam, ...).
 */
export function parseRefundReply(body: unknown): RefundReply {
  if (!body || typeof body !== "object") return { kind: "error", error: "no reply from the refund service" };
  const b = body as Record<string, unknown>;
  const preview = b.preview as Record<string, unknown> | undefined;
  if (preview && typeof preview === "object") {
    const amount = num(preview.amount);
    if (amount !== undefined) {
      const items = Array.isArray(preview.items)
        ? (preview.items as Array<Record<string, unknown>>).filter((i) => i && typeof i.name === "string").map((i) => ({
            name: String(i.name),
            qty: Number(i.qty) || 1,
            ...(num(i.amount) !== undefined ? { amount: num(i.amount) } : {}),
          }))
        : [];
      const last4 = preview.card_last4;
      return {
        kind: "preview",
        amount,
        ...(typeof last4 === "string" || typeof last4 === "number" ? { card_last4: String(last4) } : {}),
        items,
        ...(typeof b.say === "string" ? { say: b.say } : {}),
      };
    }
  }
  const refund = (b.refund && typeof b.refund === "object" ? b.refund : b) as Record<string, unknown>;
  if (typeof refund.status === "string" && /^(PENDING|TRANSMITTED|SETTLED|COMPLETED)$/i.test(refund.status)) {
    const details = refund.refundAmountDetails as Record<string, unknown> | undefined;
    return {
      kind: "done",
      status: refund.status,
      ...(num(details?.refundAmount) !== undefined ? { amount: num(details?.refundAmount) } : num(b.amount) !== undefined ? { amount: num(b.amount) } : {}),
      ...(typeof refund.id === "string" ? { refund_id: refund.id } : {}),
      ...(typeof refund.reconciliationId === "string" ? { reconciliation_id: refund.reconciliationId } : {}),
      ...(typeof refund.source === "string" ? { source: refund.source } : {}),
      ...(typeof b.say_key === "string" ? { say_key: b.say_key } : {}),
    };
  }
  if (b.decision === "deny" || typeof b.say_key === "string" || typeof b.reason_key === "string") {
    const rules = Array.isArray(b.rules) ? (b.rules as Array<{ id?: unknown; passed?: unknown }>) : [];
    return {
      kind: "declined",
      say_key: String(b.say_key ?? b.reason_key ?? "declined"),
      rules_failed: rules.filter((r) => r && r.passed === false).map((r) => String(r.id)),
    };
  }
  return { kind: "error", error: typeof b.error === "string" ? b.error : typeof b.detail === "string" ? b.detail : "unexpected refund reply" };
}

// ---------------------------------------------------------------- cancel replies

export type CancelReply = { kind: "cancelled"; link_status?: string } | { kind: "too_late" } | { kind: "error"; error: string };

/** POST {policy}/orders/{id}/cancel: {status: "cancelled", link_status}; a paid order is refused (409, cancel_too_late). */
export function parseCancelReply(httpStatus: number, body: unknown): CancelReply {
  const b = (body && typeof body === "object" ? body : {}) as Record<string, unknown>;
  if (httpStatus >= 200 && httpStatus < 300 && (b.status === "cancelled" || b.cancelled === true)) {
    return { kind: "cancelled", ...(typeof b.link_status === "string" ? { link_status: b.link_status } : {}) };
  }
  const text = JSON.stringify(b).toLowerCase();
  if (httpStatus === 409 || b.say_key === "cancel_too_late" || /too_late|already paid|not awaiting|is paid/.test(text)) return { kind: "too_late" };
  return { kind: "error", error: typeof b.error === "string" ? b.error : typeof b.detail === "string" ? b.detail : `HTTP ${httpStatus}` };
}

// ---------------------------------------------------------------- history

export interface HistorySummary {
  orders: Array<{ order_id: string; when: string; total: number; status: string; items: string[] }>;
  refunds: Array<{ order_id?: string; when: string; amount: number; status: string }>;
  refusals: number;
  spent?: number;
}

function when(v: unknown): string {
  const ms = typeof v === "number" ? (v < 1e12 ? v * 1000 : v) : typeof v === "string" ? Date.parse(v) : NaN;
  return Number.isFinite(ms) ? new Date(ms).toISOString().slice(0, 10) : "";
}

/** An order line as words: policy sends "2 x Bread"; objects {qty, name} work too. "1 x Bread" -> "Bread". */
function itemWords(line: unknown): string {
  if (typeof line === "string") {
    const m = /^\s*(\d+)\s*x\s+(.+)$/i.exec(line);
    return (m ? (Number(m[1]) > 1 ? `${m[1]} ${m[2]}` : m[2]) : line).trim();
  }
  if (!line || typeof line !== "object") return "";
  const l = line as Record<string, unknown>;
  return `${Number(l.qty) > 1 ? `${l.qty} ` : ""}${String(l.name ?? l.sku ?? "")}`.trim();
}

/** GET {policy}/history: orders, refunds and refusals, compacted for the model to speak from. */
export function parseHistory(body: unknown): HistorySummary | null {
  if (!body || typeof body !== "object") return null;
  const b = body as Record<string, unknown>;
  const orders = Array.isArray(b.orders) ? (b.orders as Array<Record<string, unknown>>) : [];
  const refunds = Array.isArray(b.refunds) ? (b.refunds as Array<Record<string, unknown>>) : [];
  const refusals = Array.isArray(b.refusals) ? b.refusals.length : typeof b.refusals === "number" ? b.refusals : 0;
  const totals = (b.totals ?? {}) as Record<string, unknown>;
  return {
    orders: orders.slice(0, 10).map((o) => {
      const lines = (Array.isArray(o.items) ? o.items : Array.isArray(o.lines) ? o.lines : []) as Array<Record<string, unknown>>;
      return {
        order_id: String(o.order_id ?? ""),
        when: when(o.at ?? o.created_at ?? o.paid_at ?? o.t),
        total: num(o.total) ?? num(o.amount) ?? 0,
        status: String(o.status ?? ""),
        items: (lines as unknown[]).map(itemWords).filter(Boolean),
      };
    }),
    refunds: refunds.slice(0, 10).map((r) => ({
      ...(typeof r.order_id === "string" ? { order_id: r.order_id } : {}),
      when: when(r.at ?? r.t),
      amount: num(r.amount) ?? 0,
      status: String(r.status ?? ""),
    })),
    refusals,
    ...((num(totals.spent) ?? num(totals.orders)) !== undefined ? { spent: num(totals.spent) ?? num(totals.orders) } : {}),
  };
}

/** What purchase_history says: how many orders, what they came to, and what the latest one had (newest first). */
export function historySummary(h: HistorySummary): { count: number; spentCents: number; lastItems: string[] } {
  const kept = h.orders.filter((o) => o.status !== "cancelled");
  const latest = [...kept].sort((a, b) => (a.when < b.when ? 1 : a.when > b.when ? -1 : 0))[0];
  return {
    count: kept.length,
    spentCents: kept.reduce((t, o) => t + Math.round(o.total * 100), 0),
    lastItems: latest?.items.slice(0, 3) ?? [],
  };
}
