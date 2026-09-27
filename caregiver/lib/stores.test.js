import assert from "node:assert/strict";
import test from "node:test";
import { isBiller, payeeName, storeName, storeNames, storefronts } from "./stores.js";

test("store ids read as the registry names", () => {
  assert.equal(storeName("parkside_pharmacy"), "Parkside Pharmacy");
  assert.equal(storeName("corner_market"), "Corner Market");
  assert.equal(storeNames.main_street_home, "Main Street Home");
});

test("an unknown store id reads as words, and a missing one stays empty", () => {
  assert.equal(storeName("garden_center"), "Garden center");
  assert.equal(storeName("Five Points Drug"), "Five Points Drug");
  assert.equal(storeName(undefined), "");
  assert.equal(storeName(""), "");
});

test("storefronts are the stores and the biller, never the blocked gift-card shop", () => {
  const ids = storefronts().map((store) => store.id);
  assert.deepEqual(ids, ["corner_market", "parkside_pharmacy", "main_street_home", "peachtree_power"]);
  assert.equal(ids.includes("quickgift_cards"), false);
});

test("only the power company is a biller", () => {
  assert.equal(isBiller("peachtree_power"), true);
  assert.equal(isBiller("corner_market"), false);
  assert.equal(isBiller(undefined), false);
});

test("a Parkside Pharmacy cart is paid to Parkside Pharmacy", () => {
  assert.equal(payeeName({ approval_id: "a_1", stores: ["parkside_pharmacy"], merchant: "corner_market" }), "Parkside Pharmacy");
  assert.equal(payeeName({ approval_id: "a_1", merchant: "parkside_pharmacy" }), "Parkside Pharmacy");
});

test("a cart across stores names every store", () => {
  assert.equal(payeeName({ stores: ["corner_market", "parkside_pharmacy"] }), "Corner Market and Parkside Pharmacy");
});

test("with no store the payee is Chaperone", () => {
  assert.equal(payeeName(null), "Chaperone");
  assert.equal(payeeName({}), "Chaperone");
  assert.equal(payeeName({ stores: [] }), "Chaperone");
});
