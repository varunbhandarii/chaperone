import { isBiller } from "./stores.js";

// Order statuses where the money has left Ruth's card (policy/postpurchase.py PAID_OR_LATER, plus refunded).
export const PAID = new Set(["paid", "preparing", "ready_for_pickup", "picked_up", "partially_refunded", "refunded"]);
const REFUND_FAILED = new Set(["FAILED", "DECLINED", "REJECTED", "VOIDED", "CANCELLED"]);

const cents = (value) => {
  const number = Number(value);
  return Number.isFinite(number) ? Math.round(number * 100) : 0;
};

// What left the card at each store: paid orders less what came back. Largest first; an order with no
// store is counted under "".
export function spendByStore(history) {
  const orders = (history && history.orders) || [];
  const refunds = (history && history.refunds) || [];
  const storeOf = new Map();
  const totals = new Map();
  for (const order of orders) {
    if (!PAID.has(order.status)) continue;
    const store = order.store || order.merchant || "";
    storeOf.set(order.order_id, store);
    totals.set(store, (totals.get(store) || 0) + cents(order.total));
  }
  for (const refund of refunds) {
    if (!storeOf.has(refund.order_id) || REFUND_FAILED.has(String(refund.status || "").toUpperCase())) continue;
    const store = storeOf.get(refund.order_id);
    totals.set(store, totals.get(store) - cents(refund.amount));
  }
  return [...totals]
    .filter(([, total]) => total > 0)
    .sort((a, b) => b[1] - a[1])
    .map(([store, total]) => ({ store, amount: total / 100 }));
}

// Errands done, bills paid and attempts stopped over the last seven days.
export function weekSummary(history, declines, now = Date.now()) {
  const start = now - 7 * 24 * 60 * 60 * 1000;
  const recent = (list) => (list || []).filter((row) => !row.at || new Date(row.at).getTime() >= start);
  const orders = recent(history && history.orders).filter((order) => PAID.has(order.status));
  const bill = (order) => isBiller(order.store) || (order.items || []).some((line) => String(line).toLowerCase().includes("bill"));
  const bills = orders.filter(bill).length;
  const stopped = recent(history && history.refusals).length + recent(declines).length;
  return { errands: orders.length - bills, bills, stopped };
}
