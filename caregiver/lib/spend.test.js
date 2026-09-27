import assert from "node:assert/strict";
import test from "node:test";
import { spendByStore, weekSummary } from "./spend.js";

const history = {
  orders: [
    { order_id: "o1", store: "corner_market", status: "picked_up", total: 12.5 },
    { order_id: "o2", store: "corner_market", status: "paid", total: 7.25 },
    { order_id: "o3", store: "parkside_pharmacy", status: "cancelled", total: 30 },
    { order_id: "o4", store: "parkside_pharmacy", status: "awaiting_payment", total: 18 },
    { order_id: "o5", store: "main_street_home", status: "partially_refunded", total: 20 },
    { order_id: "o6", store: "peachtree_power", status: "refunded", total: 40 },
    { order_id: "o7", status: "paid", total: 3 },
  ],
  refunds: [
    { order_id: "o5", amount: 5, status: "PENDING" },
    { order_id: "o6", amount: 40, status: "PENDING" },
    { order_id: "o3", amount: 30, status: "PENDING" },
    { order_id: "o1", amount: 2, status: "FAILED" },
  ],
};

test("spend by store counts only money that left the card, less refunds", () => {
  assert.deepEqual(spendByStore(history), [
    { store: "corner_market", amount: 19.75 },
    { store: "main_street_home", amount: 15 },
    { store: "", amount: 3 },
  ]);
});

test("cancelled and unpaid orders count nothing", () => {
  const rows = spendByStore(history);
  assert.equal(rows.some((row) => row.store === "parkside_pharmacy"), false);
});

test("a fully refunded store drops off", () => {
  assert.equal(spendByStore(history).some((row) => row.store === "peachtree_power"), false);
});

test("spend by store adds cents exactly", () => {
  const rows = spendByStore({ orders: [
    { order_id: "a", store: "corner_market", status: "paid", total: 0.1 },
    { order_id: "b", store: "corner_market", status: "paid", total: "0.2" },
  ] });
  assert.deepEqual(rows, [{ store: "corner_market", amount: 0.3 }]);
});

test("spend by store is empty without history", () => {
  assert.deepEqual(spendByStore(null), []);
  assert.deepEqual(spendByStore({}), []);
});

test("the week counts paid errands, bills and stops from the last seven days", () => {
  const now = Date.parse("2026-09-26T12:00:00Z");
  const week = weekSummary({
    orders: [
      { order_id: "o1", store: "corner_market", status: "paid", at: "2026-09-25T10:00:00Z" },
      { order_id: "o2", store: "peachtree_power", status: "paid", at: "2026-09-24T10:00:00Z" },
      { order_id: "o3", store: "corner_market", status: "cancelled", at: "2026-09-24T10:00:00Z" },
      { order_id: "o4", store: "corner_market", status: "awaiting_payment", at: "2026-09-24T10:00:00Z" },
      { order_id: "o5", store: "corner_market", status: "paid", at: "2026-09-01T10:00:00Z" },
    ],
    refusals: [{ decision_id: "d1", at: "2026-09-25T09:00:00Z" }, { decision_id: "d0", at: "2026-09-02T09:00:00Z" }],
  }, [{ token: "t1", at: "2026-09-26T08:00:00Z" }], now);
  assert.deepEqual(week, { errands: 1, bills: 1, stopped: 2 });
});
