// Ledger event payloads in the shapes the wall renders (one builder per event type the station posts).
// Pure functions: no DOM or network, so they run under `node --test`.

import type { CartLineView, CatalogItem } from "./cart.ts";
import type { Lang } from "./lang.ts";

export function heard(role: "shopper" | "agent", text: string, lang: Lang | undefined, itemId: string): Record<string, unknown> {
  // The wall keeps one line per item_id, so a longer transcript of the same turn replaces the shorter one.
  return { role, text, lang: lang ?? null, item_id: itemId };
}

export function itemsFound(query: string, items: CatalogItem[], source: string): Record<string, unknown> {
  return { query, items: items.map((i) => ({ sku: i.sku, name: i.name, price: i.price })), catalog_source: source };
}

export function cartUpdated(lines: CartLineView[], total: number): Record<string, unknown> {
  return { lines: lines.map((l) => ({ sku: l.sku, name: l.name, qty: l.qty, price: l.price })), total };
}

export function checkoutRequested(total: number): Record<string, unknown> {
  return { total };
}

export function refusal(ruleIds: string[], spokenKey: string, lang: string | undefined, via: string): Record<string, unknown> {
  return { rule_id: ruleIds[0] ?? "unknown", rule_ids: ruleIds, spoken_key: spokenKey, lang: lang ?? null, via };
}

export function receiptPrinted(orderId: string, via: "printer" | "screen", pdf = false): Record<string, unknown> {
  // via stays "printer" or "screen" (the wall's values); pdf marks a screen receipt that also has a PDF.
  return pdf ? { order_id: orderId, via, pdf: true } : { order_id: orderId, via };
}
