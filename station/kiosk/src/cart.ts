// Catalog items, the sku cache, and the priced cart / checkout body sent to the policy service.
// Pure functions: no DOM or network, so they run under `node --test`.

import type { Lang } from "./lang.ts";

export interface CatalogItem {
  sku: string;
  name: string;
  brand?: string;
  category: string;
  price: number;
  size?: string;
  usual?: boolean;
  [extra: string]: unknown;
}

export interface CartLine {
  sku: string;
  qty: number;
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
  read_back?: boolean;
}

/** Used when the catalog service cannot be reached. */
export const FALLBACK_ITEMS: CatalogItem[] = [
  { sku: "bread_ww_20oz", name: "Whole wheat bread", category: "grocery", price: 3.49, usual: true },
  { sku: "bread_white_20oz", name: "White bread", category: "grocery", price: 2.29, usual: false },
  { sku: "bread_sourdough", name: "Sourdough loaf", category: "grocery", price: 4.99, usual: false },
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

/** Pulls valid items out of a catalog /search response ({"query","results":[...],"took_ms"}). */
export function parseSearchResponse(body: unknown): CatalogItem[] | null {
  if (!body || typeof body !== "object") return null;
  const results = (body as { results?: unknown }).results;
  if (!Array.isArray(results)) return null;
  return results.filter(isCatalogItem);
}

/** What the model sees for each item: enough to speak and to call checkout, nothing more. */
export function compactItem(item: CatalogItem): Record<string, unknown> {
  const out: Record<string, unknown> = { sku: item.sku, name: item.name, category: item.category, price: item.price };
  if (item.brand) out.brand = item.brand;
  if (item.size) out.size = item.size;
  if (typeof item.usual === "boolean") out.usual = item.usual;
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

/** Validates checkout tool arguments: {"items":[{"sku":string,"qty":integer>=1}]}. */
export function parseCartLines(args: unknown): { ok: true; lines: CartLine[] } | { ok: false; error: string } {
  const items = (args as { items?: unknown } | null)?.items;
  if (!Array.isArray(items) || items.length === 0) return { ok: false, error: "items must be a non-empty list of {sku, qty}" };
  const merged = new Map<string, number>();
  for (const raw of items) {
    const sku = (raw as { sku?: unknown })?.sku;
    const qty = Number((raw as { qty?: unknown })?.qty ?? 1);
    if (typeof sku !== "string" || !sku) return { ok: false, error: "every item needs a sku from search_catalog" };
    if (!Number.isInteger(qty) || qty < 1) return { ok: false, error: `qty for ${sku} must be a whole number of at least 1` };
    merged.set(sku, (merged.get(sku) ?? 0) + qty);
  }
  return { ok: true, lines: [...merged].map(([sku, qty]) => ({ sku, qty })) };
}

/** Prices each line from cached catalog items. Totals are computed in integer cents. */
export function buildCart(
  lines: CartLine[],
  cache: ItemCache,
  merchant: string,
): { ok: true; cart: PricedCart } | { ok: false; error: string; unknown_skus: string[] } {
  const unknown = lines.filter((l) => !cache.get(l.sku)).map((l) => l.sku);
  if (unknown.length) {
    return {
      ok: false,
      error: `unknown sku ${unknown.join(", ")}; call search_catalog first and use a sku from its results`,
      unknown_skus: unknown,
    };
  }
  let totalCents = 0;
  const items: PricedCartItem[] = lines.map((line) => {
    const item = cache.get(line.sku)!;
    const priceCents = toCents(item.price);
    totalCents += priceCents * line.qty;
    return { sku: item.sku, name: item.name, category: item.category, qty: line.qty, price: fromCents(priceCents) };
  });
  return { ok: true, cart: { merchant, items, total: fromCents(totalCents) } };
}

export function buildCheckoutBody(opts: {
  sessionId: string;
  mandateId: string;
  cart: PricedCart;
  transcript?: string;
  lang?: Lang;
  readBack?: boolean;
}): CheckoutBody {
  const body: CheckoutBody = { session_id: opts.sessionId, mandate_id: opts.mandateId, cart: opts.cart };
  const transcript = (opts.transcript ?? "").trim();
  if (transcript) body.transcript = transcript;
  if (opts.lang === "es" || opts.lang === "hi" || opts.lang === "en") body.lang = opts.lang;
  body.read_back = opts.readBack ?? true;
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
