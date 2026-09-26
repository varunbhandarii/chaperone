// Catalog items, the shopper's cart, the read-back gate, and what the model is told after checkout.
// Pure functions and classes: no DOM or network, so they run under `node --test`.

import type { Lang } from "./lang.ts";

export interface CatalogItem {
  sku: string;
  name: string;
  brand?: string;
  category: string;
  /** grocery | pharmacy | gift_card | prepaid_card; what the mandate's category rules see */
  mandate_category?: string;
  price: number;
  size?: string;
  usual?: boolean;
  /** set when the shopper's profile resolved a personal phrase ("my blood pressure medicine") */
  profile_label?: string;
  note?: string;
  [extra: string]: unknown;
}

export interface PricedCartItem {
  sku: string;
  name: string;
  category: string;
  qty: number;
  price: number;
}

export interface PricedCart {
  merchant: string;
  items: PricedCartItem[];
  total: number;
}

export interface CheckoutBody {
  session_id: string;
  mandate_id: string;
  cart: PricedCart;
  transcript?: string;
  lang?: Lang;
  read_back: boolean;
}

/** Used when the catalog service cannot be reached: real catalog skus and prices. */
export const FALLBACK_ITEMS: CatalogItem[] = [
  { sku: "BAK-001", name: "Nature's Own Honey Wheat Bread", brand: "Nature's Own", category: "bakery", mandate_category: "grocery", price: 3.49, size: "20 oz", usual: true },
  { sku: "BAK-003", name: "Kroger Low Sodium Whole Wheat Bread", brand: "Kroger", category: "bakery", mandate_category: "grocery", price: 3.19, size: "16 oz", usual: false },
  { sku: "BAK-002", name: "Kroger Whole Wheat Bread", brand: "Kroger", category: "bakery", mandate_category: "grocery", price: 2.99, size: "20 oz", usual: false },
];

export function isCatalogItem(value: unknown): value is CatalogItem {
  if (!value || typeof value !== "object") return false;
  const v = value as Record<string, unknown>;
  return (
    typeof v.sku === "string" &&
    v.sku.length > 0 &&
    typeof v.name === "string" &&
    typeof v.category === "string" &&
    typeof v.price === "number" &&
    Number.isFinite(v.price) &&
    v.price >= 0
  );
}

/** Items from a catalog /search response: {"q","items":[...]} (older mocks answer "results"). */
export function parseSearchResponse(body: unknown): CatalogItem[] | null {
  if (!body || typeof body !== "object") return null;
  const b = body as { items?: unknown; results?: unknown };
  const list = Array.isArray(b.items) ? b.items : Array.isArray(b.results) ? b.results : null;
  return list ? list.filter(isCatalogItem) : null;
}

/** Profile matches from /resolve: {"q","matches":[{sku,label,note,item}]} -> items marked usual with the label. */
export function parseResolveResponse(body: unknown): CatalogItem[] {
  const matches = (body as { matches?: unknown } | null)?.matches;
  if (!Array.isArray(matches)) return [];
  const out: CatalogItem[] = [];
  for (const m of matches) {
    const item = (m as { item?: unknown })?.item;
    if (!isCatalogItem(item)) continue;
    const label = (m as { label?: unknown }).label;
    const note = (m as { note?: unknown }).note;
    out.push({
      ...item,
      usual: true,
      ...(typeof label === "string" && label ? { profile_label: label } : {}),
      ...(typeof note === "string" && note ? { note } : {}),
    });
  }
  return out;
}

/** Profile matches first, then search results, no duplicates, at most `limit`. */
export function mergeResults(profile: CatalogItem[], search: CatalogItem[], limit = 3): CatalogItem[] {
  const seen = new Set<string>();
  const out: CatalogItem[] = [];
  for (const item of [...profile, ...search]) {
    if (seen.has(item.sku)) continue;
    seen.add(item.sku);
    out.push(item);
    if (out.length >= limit) break;
  }
  return out;
}

/** The part of a product name a person would say ("Ensure Original ... | Small Meal ..." -> first segment). */
export function spokenName(item: Pick<CatalogItem, "name">): string {
  return item.name.split(" | ")[0].trim();
}

/** What the model sees for each item: enough to speak and to add it to the cart, nothing more. */
export function compactItem(item: CatalogItem): Record<string, unknown> {
  const out: Record<string, unknown> = { sku: item.sku, name: spokenName(item), price: item.price };
  if (item.brand) out.brand = item.brand;
  if (item.size) out.size = item.size;
  if (typeof item.usual === "boolean") out.usual = item.usual;
  if (item.profile_label) out.shopper_calls_it = item.profile_label;
  if (item.note) out.note = item.note;
  return out;
}

export class ItemCache {
  private items = new Map<string, CatalogItem>();

  add(items: CatalogItem[]): void {
    for (const item of items) this.items.set(item.sku, item);
  }

  get(sku: string): CatalogItem | undefined {
    return this.items.get(sku);
  }

  get size(): number {
    return this.items.size;
  }
}

export function toCents(price: number): number {
  return Math.round(price * 100);
}

export function fromCents(cents: number): number {
  return Math.round(cents) / 100;
}

// ---------------------------------------------------------------- cart

export const MAX_QTY = 20;

export interface CartLineView {
  sku: string;
  name: string;
  qty: number;
  price: number;
  line_total: number;
}

type Result<T> = ({ ok: true } & T) | { ok: false; error: string };

/** The shopper's cart in integer cents. `version` bumps on every change; the read-back gate compares it. */
export class Cart {
  private entries = new Map<string, { item: CatalogItem; qty: number }>();
  version = 0;

  add(item: CatalogItem, qty = 1): Result<{ qty: number }> {
    if (!Number.isInteger(qty) || qty < 1) return { ok: false, error: "qty must be a whole number of at least 1" };
    const next = (this.entries.get(item.sku)?.qty ?? 0) + qty;
    if (next > MAX_QTY) return { ok: false, error: `at most ${MAX_QTY} of one item per order` };
    this.entries.set(item.sku, { item, qty: next });
    this.version++;
    return { ok: true, qty: next };
  }

  /** Removes `qty` of a sku, or the whole line when qty is omitted. */
  remove(sku: string, qty?: number): Result<{ qty: number }> {
    const entry = this.entries.get(sku);
    if (!entry) return { ok: false, error: `${sku} is not in the cart` };
    if (qty !== undefined && (!Number.isInteger(qty) || qty < 1)) return { ok: false, error: "qty must be a whole number of at least 1" };
    const left = qty === undefined ? 0 : Math.max(0, entry.qty - qty);
    if (left === 0) this.entries.delete(sku);
    else entry.qty = left;
    this.version++;
    return { ok: true, qty: left };
  }

  clear(): void {
    if (this.entries.size === 0) return;
    this.entries.clear();
    this.version++;
  }

  get isEmpty(): boolean {
    return this.entries.size === 0;
  }

  get totalCents(): number {
    let total = 0;
    for (const { item, qty } of this.entries.values()) total += toCents(item.price) * qty;
    return total;
  }

  lines(): CartLineView[] {
    return [...this.entries.values()].map(({ item, qty }) => ({
      sku: item.sku,
      name: spokenName(item),
      qty,
      price: fromCents(toCents(item.price)),
      line_total: fromCents(toCents(item.price) * qty),
    }));
  }

  /** The priced cart for /checkout; category carries the mandate category when the catalog provides it. */
  priced(merchant: string): PricedCart {
    const items = [...this.entries.values()].map(({ item, qty }) => ({
      sku: item.sku,
      name: item.name,
      category: item.mandate_category ?? item.category,
      qty,
      price: fromCents(toCents(item.price)),
    }));
    return { merchant, items, total: fromCents(this.totalCents) };
  }

  /** What the model gets back after a cart change. */
  summary(): { lines: CartLineView[]; total: number } {
    return { lines: this.lines(), total: fromCents(this.totalCents) };
  }
}

// ---------------------------------------------------------------- read-back gate

export type GateResult = { ok: true } | { ok: false; reason: "not_read_back" | "cart_changed" | "no_confirmation" };

/**
 * checkout is allowed only when read_cart ran on the current cart version AND the shopper spoke
 * (a committed user turn) after that read-back: that turn is the "yes".
 */
export class ReadBackGate {
  private readVersion = -1;
  private readAtTurn = -1;

  markRead(cartVersion: number, userTurns: number): void {
    this.readVersion = cartVersion;
    this.readAtTurn = userTurns;
  }

  check(cartVersion: number, userTurns: number): GateResult {
    if (this.readVersion < 0) return { ok: false, reason: "not_read_back" };
    if (this.readVersion !== cartVersion) return { ok: false, reason: "cart_changed" };
    if (userTurns <= this.readAtTurn) return { ok: false, reason: "no_confirmation" };
    return { ok: true };
  }

  reset(): void {
    this.readVersion = -1;
    this.readAtTurn = -1;
  }
}

// ---------------------------------------------------------------- spoken lines

/** Money the way the voice should read it in each language. */
export function money(cents: number, lang: Lang = "en"): string {
  const dollars = Math.floor(cents / 100);
  const rest = cents % 100;
  if (lang === "es") {
    const d = `${dollars} ${dollars === 1 ? "dólar" : "dólares"}`;
    return rest ? `${d} con ${rest} centavos` : d;
  }
  if (lang === "hi") return rest ? `${dollars} डॉलर ${rest} सेंट` : `${dollars} डॉलर`;
  return `$${(cents / 100).toFixed(2)}`;
}

/** The exact read-back sentence for the current cart, in the shopper's language. */
export function readBackSay(lines: CartLineView[], totalCents: number, lang: Lang = "en"): string {
  if (!lines.length) {
    return { es: "Su carrito está vacío.", hi: "आपकी कार्ट खाली है।", en: "Your cart is empty." }[lang];
  }
  const parts = lines.map((l) => {
    const cents = toCents(l.line_total);
    const qty = l.qty > 1 ? `${l.qty} ` : "";
    return `${qty}${l.name}, ${money(cents, lang)}`;
  });
  const list = parts.join("; ");
  const total = money(totalCents, lang);
  if (lang === "es") return `Su pedido: ${list}. Total: ${total}. ¿Hago el pedido?`;
  if (lang === "hi") return `आपका ऑर्डर: ${list}। कुल ${total}। क्या मैं ऑर्डर कर दूँ?`;
  return `Your order: ${list}. Total ${total}. Shall I place the order?`;
}

const SAY: Record<string, Record<Lang, string>> = {
  ordering_now: {
    en: "Ordering now. Total {total}.",
    es: "Hago el pedido ahora. Total: {total}.",
    hi: "ऑर्डर हो रहा है। कुल {total}।",
  },
  asking_priya: {
    en: "That is more than your limit for one purchase, so I have asked Priyank. One moment, please.",
    es: "Eso pasa de su límite para una compra, así que le pregunté a Priyank. Un momento, por favor.",
    hi: "यह एक खरीद की सीमा से ज़्यादा है, इसलिए प्रियंक से पूछा है। एक पल रुकिए।",
  },
  declined: {
    en: "I can't buy that on this account. Would you like something else?",
    es: "No puedo comprar eso con esta cuenta. ¿Quiere otra cosa?",
    hi: "इस खाते से यह खरीदारी नहीं हो सकती। क्या आपको कुछ और चाहिए?",
  },
  over_monthly_cap: {
    en: "That would go over this month's budget, so I can't order it.",
    es: "Eso pasaría el presupuesto de este mes, así que no puedo pedirlo.",
    hi: "यह इस महीने के बजट से ज़्यादा हो जाएगा, इसलिए यह ऑर्डर नहीं हो सकता।",
  },
  checkout_unavailable: {
    en: "I couldn't place the order just now. Nothing was bought.",
    es: "No pude hacer el pedido ahora mismo. No se compró nada.",
    hi: "अभी ऑर्डर नहीं हो पाया। कुछ भी नहीं खरीदा गया।",
  },
  cart_empty: {
    en: "The cart is empty. What would you like?",
    es: "El carrito está vacío. ¿Qué le gustaría?",
    hi: "कार्ट खाली है। आपको क्या चाहिए?",
  },
  budget_left: {
    en: "You have {left} left this month.",
    es: "Le quedan {left} este mes.",
    hi: "इस महीने आपके पास {left} बाकी हैं।",
  },
};

export function sayFor(key: string, lang: Lang = "en", vars: Record<string, string> = {}): string {
  const table = SAY[key] ?? SAY.declined;
  return table[lang].replace(/\{(\w+)\}/g, (_, name: string) => vars[name] ?? "");
}

// ---------------------------------------------------------------- checkout outcome

export interface CheckoutOutcome {
  status: "ordered" | "waiting_for_caregiver" | "declined" | "error";
  say_key: string;
  say: string;
  total?: number;
  decision_id?: string;
  order_id?: string;
  approval_id?: string;
  error?: string;
}

/** Maps the policy's /checkout reply to what the model is told (the model speaks `say`). */
export function checkoutOutcome(reply: Record<string, unknown>, totalCents: number, lang: Lang = "en"): CheckoutOutcome {
  const decision = reply.decision;
  const decisionId = typeof reply.decision_id === "string" ? reply.decision_id : undefined;
  const total = money(totalCents, lang);
  if (decision === "allow") {
    const order = (reply.order ?? null) as { order_id?: unknown } | null;
    // Allowed but the merchant never took the order (signing or merchant failure): nothing was bought.
    if (!order || typeof order.order_id !== "string") {
      return {
        status: "error",
        say_key: "checkout_unavailable",
        say: sayFor("checkout_unavailable", lang),
        decision_id: decisionId,
        error: typeof reply.order_error === "string" ? reply.order_error : "the merchant did not take the order",
      };
    }
    return {
      status: "ordered",
      say_key: "ordering_now",
      say: sayFor("ordering_now", lang, { total }),
      total: fromCents(totalCents),
      decision_id: decisionId,
      order_id: order.order_id,
    };
  }
  if (decision === "approve") {
    const approval = (reply.approval ?? null) as { approval_id?: unknown } | null;
    return {
      status: "waiting_for_caregiver",
      say_key: "asking_priya",
      say: sayFor("asking_priya", lang),
      total: fromCents(totalCents),
      decision_id: decisionId,
      ...(approval && typeof approval.approval_id === "string" ? { approval_id: approval.approval_id } : {}),
    };
  }
  if (decision === "deny") {
    const key = typeof reply.say_key === "string" ? reply.say_key : typeof reply.reason_key === "string" ? reply.reason_key : "declined";
    return { status: "declined", say_key: key, say: sayFor(key in SAY ? key : "declined", lang), decision_id: decisionId };
  }
  return {
    status: "error",
    say_key: "checkout_unavailable",
    say: sayFor("checkout_unavailable", lang),
    error: typeof reply.error === "string" ? reply.error : "unexpected reply from the policy service",
  };
}

export function buildCheckoutBody(opts: {
  sessionId: string;
  mandateId: string;
  cart: PricedCart;
  transcript?: string;
  lang?: Lang;
  readBack: boolean;
}): CheckoutBody {
  const body: CheckoutBody = { session_id: opts.sessionId, mandate_id: opts.mandateId, cart: opts.cart, read_back: opts.readBack };
  const transcript = (opts.transcript ?? "").trim();
  if (transcript) body.transcript = transcript;
  if (opts.lang === "es" || opts.lang === "hi" || opts.lang === "en") body.lang = opts.lang;
  return body;
}

export function newSessionId(random: (n: number) => Uint8Array = defaultRandom): string {
  const bytes = random(6);
  return "s_" + [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("");
}

function defaultRandom(n: number): Uint8Array {
  const out = new Uint8Array(n);
  crypto.getRandomValues(out);
  return out;
}
