// Receipts and caregiver approvals: parsing the services' replies and building a receipt locally.
// Pure functions: no DOM or network, so they run under `node --test`.

import { fromCents, type CartLineView } from "./cart.ts";
import type { Lang } from "./lang.ts";

/** GET {merchant}/orders/{id}/receipt, and what the print helper and the on-screen receipt take. */
export interface Receipt {
  merchant: string;
  items: Array<{ name: string; qty: number; price: number }>;
  total: number;
  pickup: string;
  order_id: string;
  decision_id?: string;
  paid_at?: string | number;
  session_url?: string;
  lang: Lang;
}

/** What the station remembers about the order it placed, for a receipt when the merchant's is unavailable. */
export interface PlacedOrder {
  order_id: string;
  decision_id?: string;
  lines: CartLineView[];
  totalCents: number;
  lang: Lang;
}

function isLang(v: unknown): v is Lang {
  return v === "es" || v === "hi" || v === "en";
}

/** Money arrives as a number or as a decimal string ("3.49", the merchant's format). */
function num(v: unknown): number | null {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string" && /^-?\d+(\.\d+)?$/.test(v.trim())) return Number(v);
  return null;
}

/**
 * GET {merchant}/orders/{id}/receipt. Items come back with `price` as the line total and `unit_price` beside it;
 * the station keeps unit prices (a line shows qty x price). Without `unit_price`, prices that add up to the total
 * are taken as line totals.
 */
export function parseReceipt(body: unknown, fallbackLang: Lang = "en"): Receipt | null {
  if (!body || typeof body !== "object") return null;
  const b = body as Record<string, unknown>;
  const total = num(b.total);
  if (typeof b.order_id !== "string" || total === null || !Array.isArray(b.items)) return null;
  const raw = b.items
    .filter((i): i is Record<string, unknown> => !!i && typeof i === "object" && typeof i.name === "string")
    .map((i) => ({ name: String(i.name), qty: Number.isInteger(Number(i.qty)) && Number(i.qty) > 0 ? Number(i.qty) : 1, price: num(i.price), unit: num(i.unit_price) }))
    .filter((i) => i.price !== null || i.unit !== null);
  const cents = (x: number) => Math.round(x * 100);
  const sumAsLines = raw.reduce((t, i) => t + cents(i.price ?? 0), 0);
  const sumAsUnits = raw.reduce((t, i) => t + cents(i.price ?? 0) * i.qty, 0);
  const pricesAreLineTotals = sumAsLines === cents(total) && sumAsUnits !== cents(total);
  const items = raw.map((i) => ({
    name: i.name,
    qty: i.qty,
    price: i.unit ?? (pricesAreLineTotals ? Math.round((cents(i.price ?? 0) / i.qty)) / 100 : (i.price ?? 0)),
  }));
  return {
    merchant: typeof b.merchant === "string" && b.merchant ? b.merchant : "Corner Market",
    items,
    total,
    pickup: typeof b.pickup === "string" && b.pickup ? b.pickup : "after 3 pm",
    order_id: b.order_id,
    ...(typeof b.decision_id === "string" ? { decision_id: b.decision_id } : {}),
    ...(typeof b.paid_at === "string" || typeof b.paid_at === "number" ? { paid_at: b.paid_at } : {}),
    ...(typeof b.session_url === "string" && b.session_url ? { session_url: b.session_url } : {}),
    lang: isLang(b.lang) ? b.lang : fallbackLang,
  };
}

/** The same receipt from the station's own record of the order (the merchant's endpoint is the better source). */
export function localReceipt(order: PlacedOrder, sessionUrl: string | undefined, paidAt: number | string | undefined): Receipt {
  return {
    merchant: "Corner Market",
    items: order.lines.map((l) => ({ name: l.name, qty: l.qty, price: l.price })),
    total: fromCents(order.totalCents),
    pickup: "after 3 pm",
    order_id: order.order_id,
    ...(order.decision_id ? { decision_id: order.decision_id } : {}),
    ...(paidAt !== undefined ? { paid_at: paidAt } : {}),
    ...(sessionUrl ? { session_url: sessionUrl } : {}),
    lang: order.lang,
  };
}

/** The public session page behind the receipt's QR code: https://<tunnel-host>/s/<session_id>. */
export function sessionUrl(tunnelHost: string | undefined, sessionId: string): string | undefined {
  const host = (tunnelHost ?? "").trim().replace(/^https?:\/\//, "").replace(/\/+$/, "");
  return host ? `https://${host}/s/${encodeURIComponent(sessionId)}` : undefined;
}

/** "after 3pm", "after 3 pm", "after 11:30 am" -> {h, m, pm}; anything else is shown as given. */
function parsePickup(when: string): { h: number; m?: string; pm: boolean } | null {
  const m = when.trim().match(/^after\s+(\d{1,2})(?::(\d{2}))?\s*([ap])\.?\s*m\.?$/i);
  return m ? { h: Number(m[1]), m: m[2], pm: m[3].toLowerCase() === "p" } : null;
}

function pickupEn(when: string): string {
  const p = parsePickup(when);
  return p ? `Pickup after ${p.h}${p.m ? `:${p.m}` : ""} ${p.pm ? "pm" : "am"}` : `Pickup ${when}`;
}

function pickupEs(when: string): string {
  const p = parsePickup(when);
  return p ? `Para recoger después de las ${p.h}${p.m ? `:${p.m}` : ""} ${p.pm ? "pm" : "am"}` : `Para recoger: ${when}`;
}

function pickupHi(when: string): string {
  const p = parsePickup(when);
  if (!p) return `ले जाएँ: ${when}`;
  const part = !p.pm ? "सुबह" : p.h === 12 || p.h < 5 ? "दोपहर" : p.h < 8 ? "शाम" : "रात";
  return `${part} ${p.h}${p.m ? `:${p.m}` : ""} बजे के बाद ले जाएँ`;
}

/** Labels for the on-screen receipt, in the session's language. */
export const RECEIPT_LABELS: Record<Lang, { title: string; total: string; pickup: (when: string) => string; sandbox: string; scan: string; order: string; decision: string; paid: string }> = {
  en: {
    title: "Receipt",
    total: "Total",
    pickup: pickupEn,
    sandbox: "Paid in the Visa sandbox. No real money.",
    scan: "Scan for your session",
    order: "Order",
    decision: "Decision",
    paid: "Paid",
  },
  es: {
    title: "Recibo",
    total: "Total",
    pickup: pickupEs,
    sandbox: "Pagado en el entorno de pruebas de Visa. Sin dinero real.",
    scan: "Escanee para ver su sesión",
    order: "Pedido",
    decision: "Decisión",
    paid: "Pagado",
  },
  hi: {
    title: "रसीद",
    total: "कुल",
    pickup: pickupHi,
    sandbox: "Visa सैंडबॉक्स में भुगतान। असली पैसा नहीं।",
    scan: "अपना सत्र देखने के लिए स्कैन करें",
    order: "ऑर्डर",
    decision: "निर्णय",
    paid: "भुगतान",
  },
};

export function formatPaidAt(paidAt: string | number | undefined): string {
  if (paidAt === undefined) return "";
  const ms = typeof paidAt === "number" ? (paidAt < 1e12 ? paidAt * 1000 : paidAt) : Date.parse(paidAt);
  if (!Number.isFinite(ms)) return String(paidAt);
  return new Date(ms).toLocaleString([], { hour12: false, month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

// ---------------------------------------------------------------- caregiver approval

export type ApprovalState = "pending" | "approved" | "rejected" | "expired" | "cancelled";

export interface ApprovalStatus {
  approval_id: string;
  state: ApprovalState;
  expires_at_ms?: number;
  amount?: number;
  order_id?: string;
  message?: string;
}

/** GET {policy}/approvals/{id}: {approval_id, state, expires_at, amount, merchant, excerpt, rule, decision_id, order}. */
export function parseApproval(body: unknown): ApprovalStatus | null {
  if (!body || typeof body !== "object") return null;
  const b = body as Record<string, unknown>;
  const state = b.state;
  if (typeof b.approval_id !== "string" || (state !== "pending" && state !== "approved" && state !== "rejected" && state !== "expired" && state !== "cancelled")) {
    return null;
  }
  const expires =
    typeof b.expires_at === "number" ? (b.expires_at < 1e12 ? b.expires_at * 1000 : b.expires_at) : typeof b.expires_at === "string" ? Date.parse(b.expires_at) : NaN;
  const order = b.order as { order_id?: unknown } | null | undefined;
  return {
    approval_id: b.approval_id,
    state,
    ...(Number.isFinite(expires) ? { expires_at_ms: expires } : {}),
    ...(typeof b.amount === "number" ? { amount: b.amount } : {}),
    ...(order && typeof order.order_id === "string" ? { order_id: order.order_id } : {}),
    ...(typeof b.message === "string" && b.message.trim() ? { message: b.message.trim() } : {}),
  };
}
