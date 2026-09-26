// The Ask guard at the station: policy's scam check and the biller's real balance, and how they are spoken.
// Pure functions: no DOM or network, so they run under `node --test`.

import type { Lang } from "./lang.ts";

// ---------------------------------------------------------------- scam check (POST {policy}/scam-check)

export type Verdict = "scam" | "unsure" | "ok";

export interface ScamVerdict {
  check_id?: string;
  verdict: Verdict;
  pattern?: string;
  /** in the shopper's language, one clear action; spoken exactly */
  say: string;
  actions: string[];
  facts_checked: Array<{ fact: string; result: string }>;
  sources: Array<{ title: string; url: string }>;
  cooldown_until?: string;
  from_cache?: boolean;
  ms?: number;
}

function str(v: unknown): string | undefined {
  return typeof v === "string" && v.trim() ? v : undefined;
}

/** {check_id, verdict, pattern, say, actions, facts_checked, sources, cooldown_until, ms, from_cache}; null if unusable. */
export function parseScamReply(body: unknown): ScamVerdict | null {
  if (!body || typeof body !== "object") return null;
  const b = body as Record<string, unknown>;
  const verdict = b.verdict;
  const say = str(b.say);
  if ((verdict !== "scam" && verdict !== "unsure" && verdict !== "ok") || !say) return null;
  const list = (v: unknown) => (Array.isArray(v) ? (v as unknown[]) : []);
  return {
    ...(str(b.check_id) ? { check_id: String(b.check_id) } : {}),
    verdict,
    ...(str(b.pattern) ? { pattern: String(b.pattern) } : {}),
    say,
    actions: list(b.actions).filter((a): a is string => typeof a === "string"),
    facts_checked: list(b.facts_checked)
      .filter((f): f is Record<string, unknown> => !!f && typeof f === "object")
      .map((f) => ({ fact: String(f.fact ?? ""), result: String(f.result ?? "") }))
      .filter((f) => f.fact),
    sources: list(b.sources)
      .filter((s): s is Record<string, unknown> => !!s && typeof s === "object" && typeof (s as Record<string, unknown>).url === "string")
      .map((s) => ({ title: String(s.title ?? s.url), url: String(s.url) })),
    ...(str(b.cooldown_until) ? { cooldown_until: String(b.cooldown_until) } : {}),
    ...(typeof b.from_cache === "boolean" ? { from_cache: b.from_cache } : {}),
    ...(typeof b.ms === "number" ? { ms: b.ms } : {}),
  };
}

/** What the model gets back: the verdict and the words to say, not the raw facts or URLs to read aloud. */
export function scamToolOutput(v: ScamVerdict): Record<string, unknown> {
  return {
    verdict: v.verdict,
    ...(v.pattern ? { pattern: v.pattern } : {}),
    say: v.say,
    actions: v.actions,
    ...(v.facts_checked.length ? { facts_checked: v.facts_checked } : {}),
    ...(v.sources.length ? { sources: v.sources.map((s) => s.title) } : {}),
    instruction: "Say the say text exactly, calmly, and offer its one action. Do not add a warning of your own.",
  };
}

// ---------------------------------------------------------------- billers (GET {merchant}/billers/{id}/accounts/{ref})

export const BILL_SKU_PREFIX = "BILL-";

export interface BillView {
  biller: string;
  account_ref: string;
  balance_due: number;
  due_date?: string;
  past_due: boolean;
  autopay: boolean;
  disconnect_notice: boolean;
  last_payment?: { amount: number; at: string };
}

function num(v: unknown): number | undefined {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string" && /^-?\d+(\.\d+)?$/.test(v.trim())) return Number(v);
  return undefined;
}

/** The merchant's biller answer: {biller, account_ref, balance_due, due_date, past_due, autopay, last_payment, disconnect_notice}. */
export function parseBill(body: unknown): BillView | null {
  if (!body || typeof body !== "object") return null;
  const b = body as Record<string, unknown>;
  const balance = num(b.balance_due);
  if (!str(b.biller) || balance === undefined) return null;
  const last = b.last_payment as Record<string, unknown> | undefined;
  const lastAmount = last && typeof last === "object" ? num(last.amount) : undefined;
  return {
    biller: String(b.biller),
    account_ref: String(b.account_ref ?? ""),
    balance_due: balance,
    ...(str(b.due_date) ? { due_date: String(b.due_date) } : {}),
    past_due: b.past_due === true,
    autopay: b.autopay === true,
    disconnect_notice: b.disconnect_notice === true,
    ...(lastAmount !== undefined && last ? { last_payment: { amount: lastAmount, at: String(last.at ?? "") } } : {}),
  };
}

/** Ruth's only biller for now; the model may say "the power company", "la luz" or "बिजली". */
export function billerId(said: unknown, known: string[]): string | null {
  const s = String(said ?? "").toLowerCase().trim();
  if (!s) return known[0] ?? null;
  const id = s.replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "");
  if (known.includes(id)) return id;
  if (/peachtree|power|electric|light|luz|electricidad|बिजली|bijli/.test(s)) return known.find((k) => k.includes("power")) ?? null;
  return known.length === 1 ? known[0] : null;
}

const MONTH_LOCALE: Record<Lang, string> = { en: "en-US", es: "es-MX", hi: "hi-IN" };

/** "2026-10-15" -> "October 15" / "15 de octubre" / "15 अक्टूबर" (a date, so no time zone shift). */
export function spokenDate(iso: string | undefined, lang: Lang): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso ?? "");
  if (!m) return iso ?? "";
  const d = new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3])));
  return new Intl.DateTimeFormat(MONTH_LOCALE[lang], { month: "long", day: "numeric", timeZone: "UTC" }).format(d);
}

/** Which line fits the bill: nothing owed, past due, or owed and on time. */
export function billSayKey(bill: BillView): "bill_paid" | "bill_past_due" | "bill_due" {
  if (bill.balance_due <= 0) return "bill_paid";
  return bill.past_due ? "bill_past_due" : "bill_due";
}

/** The bill as a cart item, so "pay it" goes through the same read-back and checkout; policy prices it from the biller. */
export function billItem(id: string, name: string, bill: BillView): Record<string, unknown> & { sku: string; name: string; category: string; price: number } {
  const ref = bill.account_ref;
  return {
    sku: `${BILL_SKU_PREFIX}${id}`,
    name: `${name} bill${ref ? ` ${ref.length > 4 ? `…${ref.slice(-4)}` : ref}` : ""}`,
    category: "utility_bill",
    mandate_category: "utility_bill",
    price: bill.balance_due,
    merchant: id,
    store: name,
  };
}
