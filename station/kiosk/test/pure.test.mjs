// Unit tests for the browser-independent modules (run: npm test; Node >= 22.18 strips the TypeScript types).
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  ChunkAccumulator,
  PcmRing,
  base64ToPcm16,
  floatToPcm16,
  levelFromRms,
  median,
  pcm16ToBase64,
  pcm16ToFloat,
} from "../src/pcm.ts";
import { detectLang, guessLang, normalizeLang } from "../src/lang.ts";
import {
  Cart,
  FALLBACK_ITEMS,
  MAX_QTY,
  ReadBackGate,
  buildCheckoutBody,
  checkoutOutcome,
  compactItem,
  mergeResults,
  money,
  newSessionId,
  parseResolveResponse,
  parseSearchResponse,
  readBackSay,
  sayFor,
} from "../src/cart.ts";
import { parseScreen, ruleIds } from "../src/screen.ts";

// ---------- pcm ----------

test("float -> pcm16 clips and scales", () => {
  assert.deepEqual([...floatToPcm16(new Float32Array([0, 1, -1, 2, -2, 0.5]))], [0, 32767, -32768, 32767, -32768, 16384]);
});

test("pcm16 <-> base64 is little-endian", () => {
  const samples = new Int16Array([1, -2, 32767, -32768]);
  const b64 = pcm16ToBase64(samples);
  assert.equal(b64, Buffer.from([0x01, 0x00, 0xfe, 0xff, 0xff, 0x7f, 0x00, 0x80]).toString("base64"));
  assert.deepEqual([...base64ToPcm16(b64)], [1, -2, 32767, -32768]);
});

test("pcm16 -> float stays in [-1, 1)", () => {
  const f = pcm16ToFloat(new Int16Array([-32768, 0, 32767]));
  assert.equal(f[0], -1);
  assert.equal(f[1], 0);
  assert.ok(f[2] < 1 && f[2] > 0.999);
});

test("base64 of a 100 ms chunk at 48 kHz round-trips", () => {
  const samples = new Int16Array(4800).map((_, i) => ((i * 37) % 65536) - 32768);
  assert.deepEqual(base64ToPcm16(pcm16ToBase64(samples)), samples);
});

test("pre-roll ring keeps only the most recent capacity samples", () => {
  const ring = new PcmRing(300); // e.g. 300 ms at 1 kHz
  for (let i = 0; i < 10; i++) ring.push(new Int16Array(100).fill(i));
  const out = ring.drain();
  assert.equal(out.length, 300);
  assert.deepEqual([out[0], out[100], out[299]], [7, 8, 9]);
  assert.equal(ring.drain().length, 0);
});

test("pre-roll ring trims a partially covered block", () => {
  const ring = new PcmRing(250);
  ring.push(new Int16Array(200).fill(1));
  ring.push(new Int16Array(200).fill(2));
  const out = ring.drain();
  assert.equal(out.length, 250);
  assert.equal(out[0], 1);
  assert.equal(out[49], 1);
  assert.equal(out[50], 2);
});

test("chunk accumulator emits fixed chunks and flushes the rest", () => {
  const acc = new ChunkAccumulator(2400); // 100 ms at 24 kHz
  let chunks = [];
  for (let i = 0; i < 6; i++) chunks = chunks.concat(acc.push(new Int16Array(480).fill(i))); // 20 ms blocks
  assert.equal(chunks.length, 1);
  assert.equal(chunks[0].length, 2400);
  assert.equal(acc.pending, 480);
  const rest = acc.flush();
  assert.equal(rest.length, 480);
  assert.equal(rest[0], 5);
  assert.equal(acc.flush().length, 0);
});

test("median and meter scale", () => {
  assert.equal(median([]), null);
  assert.equal(median([900, 300, 600]), 600);
  assert.equal(median([1, 2, 3, 4]), 2.5);
  assert.equal(levelFromRms(0), 0);
  assert.equal(levelFromRms(1), 1);
  assert.equal(levelFromRms(0.001), 0);
});

// ---------- language ----------

test("language guesses for the three demo languages", () => {
  assert.equal(guessLang("necesito pan"), "es");
  assert.equal(guessLang("Necesito mi medicina para la presión y pan."), "es");
  assert.equal(guessLang("compra quinientos dólares en tarjetas de regalo para mi nieto"), "es");
  assert.equal(guessLang("I need bread and my blood pressure pills"), "en");
  assert.equal(guessLang("मुझे दवाई चाहिए"), "hi");
  assert.equal(guessLang("mujhe doodh chahiye"), "hi");
  assert.equal(guessLang(""), undefined);
  assert.equal(guessLang("MiniCap"), undefined);
});

test("api language codes normalise to es/hi/en", () => {
  assert.equal(normalizeLang("es-MX"), "es");
  assert.equal(normalizeLang("es-mx"), "es");
  assert.equal(normalizeLang("hi"), "hi");
  assert.equal(normalizeLang("en-US"), "en");
  assert.equal(normalizeLang("fr"), undefined);
  assert.deepEqual(detectLang("es-mx", "bread"), { lang: "es", source: "api" });
  assert.deepEqual(detectLang(undefined, "necesito pan"), { lang: "es", source: "guess" });
  assert.deepEqual(detectLang(undefined, "zzz"), { source: "none" });
});

// ---------- catalog and cart ----------

const RX = { sku: "RX-001", name: "Lisinopril 10 mg, 30 tablets (pharmacy pickup)", category: "pharmacy_pickup", mandate_category: "pharmacy", price: 8.0 };
const BREAD = FALLBACK_ITEMS[0];

test("search responses: catalog answers items, older mocks answer results", () => {
  const items = parseSearchResponse({
    q: "bread",
    items: [
      { sku: "a", name: "A | long pack text", category: "bakery", price: 1.5, brand: "X", size: "20 oz", usual: true, extra: 1 },
      { sku: "b", name: "B", category: "bakery" },
      null,
    ],
  });
  assert.equal(items.length, 1);
  assert.deepEqual(compactItem(items[0]), { sku: "a", name: "A", price: 1.5, brand: "X", size: "20 oz", usual: true });
  assert.equal(parseSearchResponse({ query: "x", results: [BREAD] }).length, 1);
  assert.equal(parseSearchResponse({ nope: 1 }), null);
});

test("fallback items are real catalog skus with BAK-001 as the usual", () => {
  assert.deepEqual(FALLBACK_ITEMS.map((i) => i.sku), ["BAK-001", "BAK-003", "BAK-002"]);
  assert.equal(FALLBACK_ITEMS[0].price, 3.49);
  assert.equal(FALLBACK_ITEMS[0].usual, true);
  assert.ok(FALLBACK_ITEMS.every((i) => i.mandate_category === "grocery"));
});

test("profile matches from /resolve come first, marked usual, then search results without duplicates", () => {
  const profile = parseResolveResponse({
    q: "mi medicina de la presion",
    matches: [{ sku: "RX-001", label: "blood pressure medicine", note: "Pharmacy pickup, $8.00 copay", item: RX }, { sku: "x", item: { bad: 1 } }],
  });
  assert.equal(profile.length, 1);
  assert.equal(profile[0].usual, true);
  assert.equal(profile[0].profile_label, "blood pressure medicine");
  assert.equal(compactItem(profile[0]).shopper_calls_it, "blood pressure medicine");
  assert.deepEqual(parseResolveResponse({ q: "x", matches: [] }), []);
  assert.deepEqual(parseResolveResponse(null), []);
  // A prescription stands alone: no cheaper "alternatives" from an unrelated search.
  const allergyPill = { sku: "FDA-03C0A599", name: "Cetirizine", category: "otc_medicine", group: "allergy", price: 4.99 };
  const merged = mergeResults(profile, [allergyPill, { ...RX, usual: false }, BREAD, FALLBACK_ITEMS[1]], 3);
  assert.deepEqual(merged.map((i) => i.sku), ["RX-001"]);
  assert.equal(merged[0].usual, true);
  // Other usuals keep alternatives, but only of the same kind.
  const usualBread = { ...BREAD, group: "bread", usual: true };
  const breads = mergeResults([usualBread], [allergyPill, { ...FALLBACK_ITEMS[1], group: "bread" }, { ...FALLBACK_ITEMS[2], group: "bread" }], 3);
  assert.deepEqual(breads.map((i) => i.sku), ["BAK-001", "BAK-003", "BAK-002"]);
});

test("cart totals in integer cents, bumps its version on every change, carries the mandate category", () => {
  const cart = new Cart();
  assert.equal(cart.version, 0);
  assert.equal(cart.add(BREAD, 2).ok, true);
  assert.equal(cart.add(RX).ok, true);
  assert.equal(cart.add({ sku: "dime", name: "Dime candy", category: "pantry", price: 0.1 }, 3).ok, true);
  assert.equal(cart.version, 3);
  assert.equal(cart.totalCents, 1528); // 2*3.49 + 8.00 + 3*0.10, no float drift
  const priced = cart.priced("corner_market");
  assert.equal(priced.total, 15.28);
  assert.deepEqual(priced.items[0], { sku: "BAK-001", name: "Nature's Own Honey Wheat Bread", category: "grocery", qty: 2, price: 3.49, merchant: "corner_market" });
  assert.equal(priced.items[1].category, "pharmacy");
  assert.equal(priced.items[2].category, "pantry"); // no mandate_category: the catalog category passes through

  assert.equal(cart.add(BREAD, 0).ok, false);
  assert.equal(cart.add(BREAD, 1.5).ok, false);
  assert.equal(cart.add(BREAD, MAX_QTY).ok, false);
  assert.equal(cart.version, 3); // rejected changes do not bump
  assert.deepEqual(cart.remove("BAK-001", 1), { ok: true, qty: 1 });
  assert.equal(cart.remove("dime").ok, true);
  assert.equal(cart.remove("nope").ok, false);
  assert.deepEqual(cart.summary(), {
    lines: [
      { sku: "BAK-001", name: "Nature's Own Honey Wheat Bread", qty: 1, price: 3.49, line_total: 3.49 },
      { sku: "RX-001", name: "Lisinopril 10 mg, 30 tablets (pharmacy pickup)", qty: 1, price: 8, line_total: 8 },
    ],
    total: 11.49,
  });
  cart.clear();
  assert.equal(cart.isEmpty, true);
});

test("read-back gate: change, read, yes, checkout passes", () => {
  const cart = new Cart();
  const gate = new ReadBackGate();
  let turns = 1; // "necesito pan"
  cart.add(BREAD);
  assert.deepEqual(gate.check(cart.version, turns), { ok: false, reason: "not_read_back" });
  gate.markRead(cart.version, turns); // read_cart in the same response
  assert.deepEqual(gate.check(cart.version, turns), { ok: false, reason: "no_confirmation" });
  turns++; // "sí"
  assert.deepEqual(gate.check(cart.version, turns), { ok: true });
});

test("read-back gate: a cart change after the read-back fails", () => {
  const cart = new Cart();
  const gate = new ReadBackGate();
  cart.add(BREAD);
  gate.markRead(cart.version, 1);
  cart.add(RX);
  assert.deepEqual(gate.check(cart.version, 2), { ok: false, reason: "cart_changed" });
  gate.markRead(cart.version, 2);
  assert.deepEqual(gate.check(cart.version, 3), { ok: true });
  gate.reset();
  assert.deepEqual(gate.check(cart.version, 9), { ok: false, reason: "not_read_back" });
});

test("read-back gate: a read-back with no user turn after it fails", () => {
  const gate = new ReadBackGate();
  gate.markRead(4, 2);
  assert.deepEqual(gate.check(4, 2), { ok: false, reason: "no_confirmation" });
});

test("read-back sentence in the shopper's language", () => {
  const cart = new Cart();
  cart.add(RX);
  cart.add(BREAD, 2);
  const lines = cart.lines();
  assert.equal(
    readBackSay(lines, cart.totalCents, "en"),
    "Your order: Lisinopril 10 mg, 30 tablets (pharmacy pickup), $8.00; 2 Nature's Own Honey Wheat Bread, $6.98. Total $14.98. Shall I place the order?",
  );
  assert.equal(
    readBackSay(lines, cart.totalCents, "es"),
    "Su pedido: Lisinopril 10 mg, 30 tablets (pharmacy pickup), 8 dólares; 2 Nature's Own Honey Wheat Bread, 6 dólares con 98 centavos. Total: 14 dólares con 98 centavos. ¿Hago el pedido?",
  );
  assert.match(readBackSay(lines, cart.totalCents, "hi"), /^आपका ऑर्डर: .*कुल 14 डॉलर 98 सेंट। क्या मैं ऑर्डर कर दूँ\?$/);
  assert.equal(readBackSay([], 0, "es"), "Su carrito está vacío.");
  assert.equal(money(100, "es"), "1 dólar");
  assert.equal(money(1149, "hi"), "11 डॉलर 49 सेंट");
  assert.equal(money(50, "es"), "50 centavos");
  assert.equal(money(50, "hi"), "50 सेंट");
  assert.equal(money(50, "en"), "$0.50");
});

test("checkout outcomes map the policy reply to what the model says", () => {
  const allow = checkoutOutcome({ decision: "allow", decision_id: "d_1", say_key: "ordering_now", order: { order_id: "o_1", payment_link: "http://x" } }, 1149, "es");
  assert.deepEqual(allow, {
    status: "ordered",
    say_key: "ordering_now",
    say: "Hago el pedido ahora. Total: 11 dólares con 49 centavos.",
    total: 11.49,
    decision_id: "d_1",
    order_id: "o_1",
    order_ids: ["o_1"],
  });
  const noOrder = checkoutOutcome({ decision: "allow", decision_id: "d_3", order: null, order_error: "merchant answered 401" }, 1149, "en");
  assert.equal(noOrder.status, "error");
  assert.equal(noOrder.say_key, "checkout_unavailable");
  assert.equal(noOrder.error, "merchant answered 401");
  const approve = checkoutOutcome({ decision: "approve", decision_id: "d_2", approval: { approval_id: "a_1", expires_at: 1 } }, 6349, "en");
  assert.equal(approve.status, "waiting_for_caregiver");
  assert.equal(approve.say_key, "asking_priya");
  assert.equal(approve.approval_id, "a_1");
  const deny = checkoutOutcome({ decision: "deny", say_key: "over_monthly_cap" }, 100, "en");
  assert.equal(deny.status, "declined");
  assert.equal(deny.say_key, "over_monthly_cap");
  assert.match(deny.say, /budget/);
  assert.equal(checkoutOutcome({ decision: "deny", reason_key: "blocked_gift_card" }, 1, "en").say, sayFor("declined", "en"));
  const err = checkoutOutcome({ error: "policy service is down; nothing was bought" }, 1149, "hi");
  assert.equal(err.status, "error");
  assert.equal(err.say, "अभी ऑर्डर नहीं हो पाया। कुछ भी नहीं खरीदा गया।");
});

test("checkout body has exactly the policy service's shape", () => {
  const cart = new Cart();
  cart.add(BREAD);
  const sessionId = newSessionId();
  const body = buildCheckoutBody({ sessionId, mandateId: "m_ruth_2026_09", cart: cart.priced("corner_market"), transcript: " Necesito pan  ", lang: "es", readBack: true });

  assert.match(body.session_id, /^s_[A-Za-z0-9_-]+$/);
  assert.deepEqual(Object.keys(body).sort(), ["cart", "lang", "mandate_id", "read_back", "session_id", "transcript"]);
  assert.deepEqual(Object.keys(body.cart).sort(), ["items", "merchant", "total"]);
  assert.equal(body.cart.merchant, "corner_market");
  for (const item of body.cart.items) {
    assert.deepEqual(Object.keys(item).sort(), ["category", "merchant", "name", "price", "qty", "sku"]);
    assert.ok(Number.isInteger(item.qty) && item.qty >= 1);
  }
  assert.equal(body.cart.total, 3.49);
  assert.equal(body.transcript, "Necesito pan");
  assert.equal(body.read_back, true);

  const minimal = buildCheckoutBody({ sessionId, mandateId: "m_ruth_2026_09", cart: cart.priced("corner_market"), lang: "fr", readBack: false });
  assert.deepEqual(Object.keys(minimal).sort(), ["cart", "mandate_id", "read_back", "session_id"]);
  assert.equal(minimal.read_back, false);
});

// ---------- rule screen ----------

test("screen responses parse and expose rule ids", () => {
  const refuse = parseScreen({
    action: "refuse",
    hits: [{ rule_id: "R1_blocked_category", pattern: "gift_card" }, { rule_id: "S2_family_emergency" }],
    refusal: {
      rule_id: "R1_blocked_category",
      spoken_key: "blocked_gift_card",
      patterns: ["gift_card"],
      lang: "es-MX",
      text: "No puedo comprar tarjetas de regalo.",
      audio_url: "/warnings/refusal_es-MX.mp3",
    },
  });
  assert.equal(refuse.action, "refuse");
  assert.deepEqual(ruleIds(refuse), ["R1_blocked_category", "S2_family_emergency"]);
  assert.equal(refuse.refusal.text, "No puedo comprar tarjetas de regalo.");

  assert.deepEqual(parseScreen({ action: "proceed", hits: [], refusal: null }), { action: "proceed", hits: [], refusal: null });
  assert.equal(parseScreen({ action: "maybe" }), null);
  assert.equal(parseScreen(null), null);
});

// ---------- ledger payloads (the wall's shapes) ----------

import * as payload from "../src/events.ts";
import { formatPaidAt, localReceipt, parseApproval, parseReceipt, sessionUrl, RECEIPT_LABELS } from "../src/receipt.ts";
import { hasSay, registerSay } from "../src/cart.ts";

test("ledger payloads follow the wall's shapes", () => {
  assert.deepEqual(payload.heard("shopper", "necesito pan", "es", "item_1"), { role: "shopper", text: "necesito pan", lang: "es", item_id: "item_1" });
  assert.deepEqual(payload.cartUpdated([{ sku: "BAK-001", name: "Bread", qty: 2, price: 3.49, line_total: 6.98 }], 6.98), {
    lines: [{ sku: "BAK-001", name: "Bread", qty: 2, price: 3.49 }],
    total: 6.98,
  });
  assert.deepEqual(payload.itemsFound("bread", [{ sku: "BAK-001", name: "Bread", category: "bakery", price: 3.49, usual: true }], "catalog").items, [
    { sku: "BAK-001", name: "Bread", price: 3.49 },
  ]);
  assert.deepEqual(payload.checkoutRequested(11.49), { total: 11.49 });
  assert.deepEqual(payload.refusal(["R1_blocked_category", "R_urgency"], "blocked_category", "es", "out_of_band"), {
    rule_id: "R1_blocked_category",
    rule_ids: ["R1_blocked_category", "R_urgency"],
    spoken_key: "blocked_category",
    lang: "es",
    via: "out_of_band",
  });
  assert.deepEqual(payload.receiptPrinted("o_1", "screen"), { order_id: "o_1", via: "screen" });
  assert.deepEqual(payload.receiptPrinted("o_1", "screen", true), { order_id: "o_1", via: "screen", pdf: true });
  // an unknown language is left out: the relay's schema rejects lang: null
  assert.deepEqual(payload.heard("agent", "Un momento.", undefined, "item_2"), { role: "agent", text: "Un momento.", item_id: "item_2" });
  assert.equal("lang" in payload.refusal(["R1_blocked_category"], "blocked_category", undefined, "tool"), false);
});

// ---------- receipt and approval ----------

test("merchant receipts parse; the station can build one from its own order record", () => {
  const r = parseReceipt({ merchant: "Corner Market", items: [{ name: "Bread", qty: 1, price: 3.49 }, { bad: 1 }], total: 3.49, order_id: "o_1", decision_id: "d_1", paid_at: 1790400000, session_url: "https://t.example/s/s_1", lang: "hi" });
  assert.equal(r.items.length, 1);
  assert.equal(r.lang, "hi");
  assert.equal(r.pickup, "after 3 pm");
  assert.equal(parseReceipt({ items: [] }), null);
  assert.equal(parseReceipt({ order_id: "o", total: 1, items: [], lang: "fr" }, "es").lang, "es");

  const local = localReceipt({ order_id: "o_2", decision_id: "d_2", lines: [{ sku: "RX-001", name: "Lisinopril", qty: 1, price: 8, line_total: 8 }], totalCents: 800, lang: "es" }, "https://t.example/s/s_2", 1790400000);
  assert.deepEqual(local.items, [{ name: "Lisinopril", qty: 1, price: 8 }]);
  assert.equal(local.total, 8);
  assert.equal(local.lang, "es");
  assert.equal(sessionUrl("https://abc.ngrok-free.app/", "s_9"), "https://abc.ngrok-free.app/s/s_9");
  assert.equal(sessionUrl("", "s_9"), undefined);
  assert.notEqual(formatPaidAt(1790400000), "");
  assert.equal(RECEIPT_LABELS.es.pickup("after 3 pm"), "Para recoger después de las 3 pm");
});

test("the merchant's receipt: string money, line totals with unit_price, 'after 3pm'", () => {
  // the exact shape of GET {merchant}/orders/{id}/receipt
  const r = parseReceipt({
    merchant: "Corner Market",
    items: [
      { name: "Nature's Own Honey Wheat Bread", qty: 2, price: "6.98", unit_price: "3.49", sku: "BAK-001" },
      { name: "Lisinopril 10 mg", qty: 1, price: "8.00", unit_price: "8.00", sku: "RX-001" },
    ],
    total: "14.98", currency: "USD", pickup: "after 3pm", order_id: "ord_abc", decision_id: "d_1", session_id: "s_1",
    status: "paid", paid_at: "2026-09-26T09:15:00+00:00", paid_via: "callback", session_url: null, lang: "es",
    sandbox_note: "Paid in the Visa sandbox. No real money.",
  }, "en");
  assert.ok(r);
  assert.equal(r.total, 14.98);
  assert.deepEqual(r.items.map((i) => [i.qty, i.price]), [[2, 3.49], [1, 8]]); // unit prices
  assert.equal(r.session_url, undefined);
  assert.equal(RECEIPT_LABELS.es.pickup(r.pickup), "Para recoger después de las 3 pm");
  assert.equal(RECEIPT_LABELS.en.pickup("after 3pm"), "Pickup after 3 pm");
  assert.equal(RECEIPT_LABELS.hi.pickup("after 3pm"), "दोपहर 3 बजे के बाद ले जाएँ");
  assert.equal(RECEIPT_LABELS.hi.pickup("after 6:30 pm"), "शाम 6:30 बजे के बाद ले जाएँ");
  // no unit_price, prices that add up to the total are line totals
  const lines = parseReceipt({ order_id: "o", total: 6.98, items: [{ name: "Bread", qty: 2, price: 6.98 }] });
  assert.equal(lines.items[0].price, 3.49);
  // unit prices stay unit prices
  const units = parseReceipt({ order_id: "o", total: 6.98, items: [{ name: "Bread", qty: 2, price: 3.49 }] });
  assert.equal(units.items[0].price, 3.49);
  assert.equal(parseReceipt({ order_id: "o", total: "n/a", items: [] }), null);
});

test("approval replies parse, with the expiry as epoch seconds, ms or ISO", () => {
  const a = parseApproval({ approval_id: "a_1", state: "approved", expires_at: "2026-09-26T10:00:00Z", amount: 63.49, order: { order_id: "o_9" } });
  assert.equal(a.state, "approved");
  assert.equal(a.order_id, "o_9");
  assert.equal(a.expires_at_ms, Date.parse("2026-09-26T10:00:00Z"));
  assert.equal(parseApproval({ approval_id: "a_1", state: "pending", expires_at: 1790400000 }).expires_at_ms, 1790400000000);
  assert.equal(parseApproval({ approval_id: "a_1", state: "rejected", message: " Not today, Mom " }).message, "Not today, Mom");
  assert.equal(parseApproval({ approval_id: "a_1" }), null);
  assert.equal(parseApproval({ approval_id: "a_1", state: "maybe" }), null);
});

test("spoken lines: the refusal, caregiver and receipt keys exist, and line files can replace them", () => {
  for (const key of ["blocked_category", "scam_pattern", "code_reading", "over_monthly_cap", "caregiver_approved", "caregiver_declined", "caregiver_timeout", "receipt_done", "receipt_on_screen"]) {
    assert.ok(hasSay(key), key);
    for (const lang of ["es", "hi", "en"]) assert.ok(sayFor(key, lang, { total: "$1.00" }).length > 10, `${key} ${lang}`);
  }
  assert.match(sayFor("receipt_done", "es", { total: "11 dólares con 49 centavos" }), /11 dólares con 49 centavos/);
  registerSay("receipt_on_screen", "hi", "हो गया। $X, {store} में। {pickup}");
  assert.equal(sayFor("receipt_on_screen", "hi", { total: "$11.49", store: "Corner Market", pickup: "" }), "हो गया। $11.49, Corner Market में। ");
  registerSay("brand_new_key", "hi", "नया");
  assert.equal(sayFor("brand_new_key", "hi"), "नया");
  assert.equal(sayFor("brand_new_key", "en"), sayFor("declined", "en")); // other languages fall back
});

// ---------- recorded sessions and approval states ----------

import { collapseShopperTurns } from "../src/recording.ts";

test("a recording keeps one transcript per shopper turn, everything else in order", () => {
  const events = [
    { t: 0, kind: "shopper", turn: 1, text: "मेरे" },
    { t: 5, kind: "agent_audio", audio: "AAAA" },
    { t: 9, kind: "shopper", turn: 1, text: "मेरे पोते के लिए गिफ्ट कार्ड" },
    { t: 20, kind: "tool", name: "search_catalog" },
    { t: 30, kind: "shopper", turn: 2, text: "sí" },
    { t: 31, kind: "shopper", text: "old recording without a turn" },
  ];
  const out = collapseShopperTurns(events);
  // the first version's place and time, the last version's text
  assert.deepEqual(out.map((e) => e.text ?? e.kind), ["मेरे पोते के लिए गिफ्ट कार्ड", "agent_audio", "tool", "sí", "old recording without a turn"]);
  assert.equal(out[0].t, 0);
});

test("a cancelled approval parses (the station closed it when the shopper moved on)", () => {
  assert.equal(parseApproval({ approval_id: "a_1", state: "cancelled" }).state, "cancelled");
});

// ---------- after payment ----------

import {
  RefundGate,
  isRepeatRequest,
  parseCancelReply,
  parseHistory,
  parseOrder,
  parseRefundReply,
  registerRepeatPhrases,
  spokenCode,
  statusWords,
} from "../src/postpurchase.ts";

test("refund gate: preview, then yes, then the refund goes through", () => {
  const gate = new RefundGate();
  const bread = { order_id: "o_1", sku: "BAK-001" };
  gate.markPreview(bread, 3, 5);
  assert.deepEqual(gate.check(bread, 3), { ok: false, reason: "no_confirmation" }); // same response as the preview
  assert.deepEqual(gate.check(bread, 4), { ok: true }); // "yes"
  assert.equal(gate.sinceShopperIndex, 5); // only the words since the preview go to policy
});

test("refund gate: a refund without a preview is held", () => {
  assert.deepEqual(new RefundGate().check({ order_id: "o_1", sku: "BAK-001" }, 9), { ok: false, reason: "no_preview" });
});

test("refund gate: a changed item or quantity after the preview is held", () => {
  const gate = new RefundGate();
  gate.markPreview({ order_id: "o_1", sku: "BAK-001", qty: 1 }, 1, 0);
  assert.deepEqual(gate.check({ order_id: "o_1", sku: "NUT-002", qty: 1 }, 2), { ok: false, reason: "target_changed" });
  assert.deepEqual(gate.check({ order_id: "o_1", sku: "BAK-001", qty: 2 }, 2), { ok: false, reason: "target_changed" });
  assert.deepEqual(gate.check({ order_id: "o_2", sku: "BAK-001", qty: 1 }, 2), { ok: false, reason: "target_changed" });
  gate.reset();
  assert.deepEqual(gate.check({ order_id: "o_1", sku: "BAK-001", qty: 1 }, 2), { ok: false, reason: "no_preview" });
});

test("'repeat that' in three languages; requests that only mention repeating are not it", () => {
  for (const t of ["Repeat that", "repeat that, please", "say that again?", "¿Otra vez?", "Repítalo por favor", "no le entendí", "phir se boliye", "फिर से बोलिए", "दोबारा बोलिए।", "kya kaha"]) {
    assert.equal(isRepeatRequest(t), true, t);
  }
  for (const t of ["repeat my order of bread", "necesito pan", "sí", "", "otra vez quiero pan y leche con huevos por favor"]) {
    assert.equal(isRepeatRequest(t), false, t);
  }
});

test("order status words, and the pickup code read digit by digit", () => {
  assert.equal(statusWords("ready_for_pickup", "es"), "listo para recoger");
  assert.equal(statusWords("preparing", "hi"), "तैयार हो रहा है");
  assert.equal(statusWords("something_new", "en"), "something new");
  assert.equal(spokenCode("472"), "4-7-2");
  assert.equal(spokenCode(undefined), "");
  const o = parseOrder({ order_id: "o_1", status: "preparing", pickup_code: 472, amount: "11.49", timeline: [{ status: "paid", at: "2026-09-26T13:00:00Z" }, { bad: 1 }] });
  assert.deepEqual(o, { order_id: "o_1", status: "preparing", pickup_code: "472", total: 11.49, timeline: [{ status: "paid", at: "2026-09-26T13:00:00Z" }] });
  assert.equal(parseOrder({ status: "paid" }), null);
});

test("refund replies: preview, the processor's refund shape, a refusal, an error", () => {
  const preview = parseRefundReply({ preview: { amount: "3.49", card_last4: "1111", items: [{ name: "Bread", qty: 1, amount: 3.49 }] }, say: "..." });
  assert.equal(preview.kind, "preview");
  assert.equal(preview.amount, 3.49);
  assert.equal(preview.card_last4, "1111");
  const done = parseRefundReply({
    decision: "allow",
    refund: { id: "rf_1", status: "PENDING", reconciliationId: "ABC123", refundAmountDetails: { refundAmount: "3.49", currency: "USD" }, processorInformation: { responseCode: "100" }, source: "sandbox-processor-stub" },
  });
  assert.deepEqual(done, { kind: "done", status: "PENDING", amount: 3.49, refund_id: "rf_1", reconciliation_id: "ABC123", source: "sandbox-processor-stub" });
  assert.equal(parseRefundReply({ id: "rf_2", status: "TRANSMITTED", refundAmountDetails: { refundAmount: 1 } }).kind, "done");
  const rx = parseRefundReply({ decision: "deny", say_key: "refund_not_allowed_rx", rules: [{ id: "RF4_returnable", passed: false }, { id: "RF1_order_owned", passed: true }] });
  assert.deepEqual(rx, { kind: "declined", say_key: "refund_not_allowed_rx", rules_failed: ["RF4_returnable"] });
  assert.equal(parseRefundReply({ detail: "boom" }).kind, "error");
  assert.equal(parseRefundReply(null).kind, "error");
});

test("cancel replies: cancelled with the link status, too late once paid, errors", () => {
  assert.deepEqual(parseCancelReply(200, { status: "cancelled", link_status: "INACTIVE" }), { kind: "cancelled", link_status: "INACTIVE" });
  assert.deepEqual(parseCancelReply(409, { error: "order is paid" }), { kind: "too_late" });
  assert.deepEqual(parseCancelReply(400, { say_key: "cancel_too_late" }), { kind: "too_late" });
  assert.equal(parseCancelReply(500, { error: "merchant down" }).kind, "error");
});

test("history is compacted for the model", () => {
  const h = parseHistory({
    orders: [{ order_id: "o_1", at: "2026-09-20T15:00:00Z", total: "11.49", status: "picked_up", items: [{ name: "Bread", qty: 2 }, { name: "Lisinopril", qty: 1 }] }],
    refunds: [{ order_id: "o_1", at: 1790400000, amount: "3.49", status: "TRANSMITTED" }],
    refusals: [{}, {}],
    totals: { spent: "153.59" },
  });
  assert.deepEqual(h.orders[0], { order_id: "o_1", when: "2026-09-20", total: 11.49, status: "picked_up", items: ["2 Bread", "Lisinopril"] });
  assert.equal(h.refunds[0].amount, 3.49);
  assert.equal(h.refusals, 2);
  assert.equal(h.spent, 153.59);
  assert.equal(parseHistory(null), null);
});

test("receipts carry savings, rewards points and the pickup code; no savings, no line", () => {
  const r = parseReceipt({ order_id: "o", total: "11.49", items: [], savings: "0.50", loyalty_points: 11, pickup_code: 472 });
  assert.equal(r.savings, 0.5);
  assert.equal(r.loyalty_points, 11);
  assert.equal(r.pickup_code, "472");
  const none = parseReceipt({ order_id: "o", total: "3.49", items: [], savings: "0.00", loyalty_points: 0 });
  assert.equal("savings" in none, false);
  assert.equal("loyalty_points" in none, false);
  assert.equal(RECEIPT_LABELS.es.saved("$0.50"), "Usted ahorró $0.50");
});

test("after-payment lines exist in every language with their slots filled", () => {
  const vars = { status: "listo", code: "4-7-2", amount: "$3.49", last4: "1-1-1-1", saved: "$0.50", points: "11", total: "$11.49" };
  for (const key of ["order_status", "order_ready", "order_cancelled", "cancel_too_late", "refund_preview", "refund_done", "refund_not_allowed_rx", "refund_scam", "agent_paused", "you_saved", "loyalty_points", "repeat_nothing", "no_orders", "refund_not_possible", "store_unavailable"]) {
    assert.ok(hasSay(key), key);
    for (const lang of ["es", "hi", "en"]) {
      const line = sayFor(key, lang, vars);
      assert.ok(line.length > 5 && !/\{\w+\}/.test(line), `${key} ${lang}: ${line}`);
    }
  }
  assert.equal(sayFor("refund_preview", "en", vars), "$3.49 back to your card ending 1-1-1-1. Shall I?");
  assert.match(sayFor("order_ready", "es", vars), /4-7-2/);
});

test("refund replies in policy's own shapes: one of preview, refund or deny", () => {
  const rules = [{ id: "RF1_order_owned", passed: true, detail: "ready_for_pickup" }];
  const preview = parseRefundReply({ ok: true, preview: { amount: 3.49, card_last4: "1111", items: [{ name: "Bread", qty: 1, amount: 3.49 }] }, say: "refund_preview", rules });
  assert.equal(preview.kind, "preview");
  const done = parseRefundReply({ ok: true, say_key: "refund_done", rules, refund: { id: "r1", status: "PENDING", refundAmountDetails: { refundAmount: "3.49" } } });
  assert.equal(done.kind, "done");
  assert.equal(done.amount, 3.49);
  const rx = parseRefundReply({ ok: false, decision: "deny", say_key: "refund_not_allowed_rx", rules: [{ id: "RF4_return_window", passed: false }] });
  assert.deepEqual(rx, { kind: "declined", say_key: "refund_not_allowed_rx", rules_failed: ["RF4_return_window"] });
});

test("shared line files may name slots pickup_code and card_last4", () => {
  registerSay("refund_done", "en", "Done. {amount} is going back to your card ending {card_last4}.");
  assert.equal(sayFor("refund_done", "en", { amount: "$3.49", last4: "1 1 1 1" }), "Done. $3.49 is going back to your card ending 1 1 1 1.");
  registerSay("order_ready", "en", "Your pickup code is {pickup_code}.");
  assert.equal(sayFor("order_ready", "en", { code: "4 2 7" }), "Your pickup code is 4 2 7.");
});

test("repeat: added phrases count, a bare phir se does not", () => {
  assert.equal(isRepeatRequest("dobara batao"), false);
  registerRepeatPhrases(["dobara batao"]);
  assert.equal(isRepeatRequest("dobara batao"), true);
  assert.equal(isRepeatRequest("phir se"), false); // how "phir se bread dalo" starts
  assert.equal(isRepeatRequest("फिर से बोलिए"), true);
});

// ---------- the checking tick ----------

import { Earcon, TICK_NOTES, dbToGain } from "../src/earcon.ts";

function fakeAudioContext() {
  const started = [];
  const stopped = [];
  const param = () => ({ value: 0, setValueAtTime() {}, linearRampToValueAtTime() {}, exponentialRampToValueAtTime() {} });
  return {
    started,
    stopped,
    currentTime: 1,
    destination: {},
    createGain: () => ({ gain: param(), connect() {} }),
    createOscillator() {
      const osc = { type: "", frequency: param(), connect() {}, start: (t) => started.push({ f: osc.frequency.value, t }), stop: () => stopped.push(osc), onended: null };
      return osc;
    },
  };
}

test("the checking tick: two soft notes per tick at -24 dB, one timer, silent at once on stop", () => {
  assert.ok(Math.abs(dbToGain(-24) - 0.0631) < 0.001);
  const ctx = fakeAudioContext();
  const e = new Earcon(ctx, { intervalMs: 700, gainDb: -24 });
  let ticks = 0;
  e.onTick = () => ticks++;
  e.start();
  e.start(); // a second start does not add a second timer or tick
  assert.equal(e.running, true);
  assert.equal(ticks, 1);
  assert.deepEqual(ctx.started.map((n) => n.f), TICK_NOTES.map(([f]) => f));
  e.stop();
  assert.equal(e.running, false);
  // stop() also silences the tick that is playing (each note's scheduled stop plus the forced one)
  assert.ok(ctx.stopped.length >= TICK_NOTES.length * 2);
});

// ---------- the Ask guard ----------

import { billItem, billSayKey, billerId, parseBill, parseScamReply, scamToolOutput, spokenDate } from "../src/guards.ts";
import { historySummary } from "../src/postpurchase.ts";

test("scam check replies: the contract shape, sources without URLs for the model, unusable replies rejected", () => {
  const v = parseScamReply({
    check_id: "sc_1",
    verdict: "scam",
    pattern: "grandparent_emergency",
    say: "Ruth, esto es una estafa muy común. Por favor cuelgue.",
    actions: ["hang_up", "call_trusted:Alex", 3],
    facts_checked: [{ fact: "Alex's number on file", result: "different from the caller" }, { nope: 1 }],
    sources: [{ title: "FTC: Family emergency scams", url: "https://consumer.ftc.gov/x" }, { title: "no url" }],
    cooldown_until: "2026-09-27T19:05:00Z",
    ms: 6120,
    from_cache: false,
  });
  assert.equal(v.verdict, "scam");
  assert.deepEqual(v.actions, ["hang_up", "call_trusted:Alex"]);
  assert.equal(v.facts_checked.length, 1);
  assert.deepEqual(v.sources, [{ title: "FTC: Family emergency scams", url: "https://consumer.ftc.gov/x" }]);
  const out = scamToolOutput(v);
  assert.equal(out.say, v.say);
  assert.deepEqual(out.sources, ["FTC: Family emergency scams"]); // the model never reads a URL aloud
  assert.match(String(out.instruction), /exactly/);
  assert.equal(parseScamReply({ verdict: "maybe", say: "x" }), null);
  assert.equal(parseScamReply({ verdict: "ok", say: "" }), null);
  assert.equal(parseScamReply(null), null);
});

test("bills: the biller's answer, which line fits, spoken dates, and the bill as a cart item", () => {
  const bill = parseBill({ biller: "Peachtree Power", account_ref: "PP-2231-0098", balance_due: "86.40", due_date: "2026-10-15", past_due: false, autopay: false, last_payment: { amount: "91.12", at: "2026-09-12" }, disconnect_notice: false });
  assert.equal(bill.balance_due, 86.4);
  assert.deepEqual(bill.last_payment, { amount: 91.12, at: "2026-09-12" });
  assert.equal(billSayKey(bill), "bill_due");
  assert.equal(billSayKey({ ...bill, past_due: true }), "bill_past_due");
  assert.equal(billSayKey({ ...bill, balance_due: 0 }), "bill_paid");
  assert.equal(parseBill({ biller: "x" }), null);
  assert.equal(spokenDate("2026-10-15", "en"), "October 15");
  assert.equal(spokenDate("2026-10-15", "es"), "15 de octubre");
  assert.match(spokenDate("2026-10-15", "hi"), /15/);
  const item = billItem("peachtree_power", "Peachtree Power", bill);
  assert.equal(item.sku, "BILL-peachtree_power");
  assert.equal(item.price, 86.4);
  assert.equal(item.category, "utility_bill");
  assert.equal(item.name, "Peachtree Power bill …0098");
  const known = ["peachtree_power"];
  assert.equal(billerId(undefined, known), "peachtree_power");
  assert.equal(billerId("Peachtree Power", known), "peachtree_power");
  assert.equal(billerId("la luz", known), "peachtree_power");
  assert.equal(billerId("gas company", ["peachtree_power", "atl_water"]), null);
});

test("history as policy sends it: '2 x Bread' lines, newest first, the orders total, cancelled orders left out", () => {
  const h = parseHistory({
    orders: [
      { order_id: "o_2", at: "2026-09-26T15:00:00Z", total: 11.49, status: "paid", items: ["1 x Lisinopril", "2 x Bread"] },
      { order_id: "o_1", at: "2026-09-20T15:00:00Z", total: 3.49, status: "picked_up", items: ["1 x Bread"] },
      { order_id: "o_0", at: "2026-09-19T15:00:00Z", total: 52, status: "cancelled", items: ["1 x Ensure"] },
    ],
    refunds: [],
    refusals: [],
    totals: { orders: 14.98, refunds: 0 },
  });
  assert.deepEqual(h.orders[0].items, ["Lisinopril", "2 Bread"]);
  assert.equal(h.spent, 14.98);
  assert.deepEqual(historySummary(h), { count: 2, spentCents: 1498, lastItems: ["Lisinopril", "2 Bread"] });
  assert.deepEqual(historySummary(parseHistory({ orders: [] })), { count: 0, spentCents: 0, lastItems: [] });
});

test("search results name the store and the same product at her other stores", () => {
  const item = compactItem({
    sku: "BAK-001", name: "Honey Wheat Bread", brand: "Nature's Own", category: "bakery", price: 3.49, store: "Corner Market", merchant: "corner_market",
    elsewhere: [{ merchant: "parkside_pharmacy", store: "Parkside Pharmacy", sku: "PK-BAK-001", price: 3.79 }, { store: "bad" }],
  });
  assert.equal(item.store, "Corner Market");
  assert.deepEqual(item.elsewhere, [{ store: "Parkside Pharmacy", price: 3.79, sku: "PK-BAK-001" }]);
  assert.equal("elsewhere" in compactItem({ sku: "X", name: "X", category: "c", price: 1 }), false);
});

test("the Ask guard and history lines exist in every language, and no line asks Ruth to wait 'one moment'", () => {
  const vars = { biller: "Peachtree Power", amount: "$86.40", due: "October 15", status: "being prepared", code: "4-7-2", count: "2", days: "30", spent: "$14.98", items: "bread" };
  for (const key of ["scam_check_unavailable", "bill_due", "bill_past_due", "bill_paid", "order_status_pickup", "history_summary", "history_summary_one", "history_last", "history_none"]) {
    assert.ok(hasSay(key), key);
    for (const lang of ["es", "hi", "en"]) {
      const line = sayFor(key, lang, vars);
      assert.ok(line.length > 5 && !/\{\w+\}/.test(line), `${key} ${lang}: ${line}`);
    }
  }
  assert.equal(sayFor("bill_due", "en", vars), "Your Peachtree Power bill is $86.40, due October 15. It is not past due.");
  assert.match(sayFor("order_status_pickup", "en", vars), /after 3 pm.*4-7-2/);
  for (const lang of ["es", "hi", "en"]) {
    const line = sayFor("asking_priya", lang);
    assert.ok(!/one moment|un momento|एक पल|एक मिनट/i.test(line), line);
  }
});

test("the screen's scam_check action parses, with its refusal kept as the fallback", () => {
  const r = parseScreen({
    action: "scam_check",
    hits: [{ rule_id: "R1_blocked_category", pattern: "gift_card", lang: "es", term: "tarjetas de regalo" }],
    refusal: { rule_id: "R1_blocked_category", spoken_key: "blocked_category", patterns: ["gift_card"], lang: "es", text: "No puedo comprar tarjetas de regalo.", audio_url: "/audio/refusal.blocked_category.es.mp3" },
  });
  assert.equal(r.action, "scam_check");
  assert.equal(r.refusal.audio_url, "/audio/refusal.blocked_category.es.mp3");
  assert.equal(parseScreen({ action: "maybe", hits: [], refusal: null }), null);
});

test("checkout across stores: every store's order is placed and tracked", () => {
  const reply = { decision: "allow", decision_id: "d_1", order: { order_id: "ord_rx" },
    orders: [{ order_id: "ord_rx", merchant: "parkside_pharmacy" }, { order_id: "ord_bread", merchant: "corner_market" }] };
  const out = checkoutOutcome(reply, 1149, "en");
  assert.equal(out.status, "ordered");
  assert.equal(out.order_id, "ord_rx");
  assert.deepEqual(out.order_ids, ["ord_rx", "ord_bread"]);
  const onlyList = checkoutOutcome({ decision: "allow", orders: [{ order_id: "ord_x" }] }, 349, "en");
  assert.equal(onlyList.status, "ordered");
  assert.equal(onlyList.order_id, "ord_x");
});

// ---------- stores, bills and the guards at the station ----------

import { maskAccount } from "../src/receipt.ts";
import { yesOrNo } from "../src/guards.ts";

test("the cart names its stores: grouped read-back, one store or several, and a bill read as 'your bill'", () => {
  const cart = new Cart();
  cart.add({ sku: "RX-001", name: "Lisinopril", category: "pharmacy_pickup", price: 8, store: "Parkside Pharmacy", merchant: "parkside_pharmacy" });
  const one = cart.lines();
  assert.equal(one[0].store, "Parkside Pharmacy");
  assert.equal(readBackSay(one, cart.totalCents, "en"), "Your order from Parkside Pharmacy: Lisinopril, $8.00. Total $8.00. Shall I place the order?");
  cart.add({ sku: "BILL-peachtree_power", name: "Peachtree Power bill …0098", category: "utility_bill", price: 86.4, store: "Peachtree Power", merchant: "peachtree_power" });
  const two = cart.lines();
  assert.equal(
    readBackSay(two, cart.totalCents, "en"),
    "From Parkside Pharmacy: Lisinopril, $8.00. From Peachtree Power: your bill, $86.40. Total $94.40. Shall I place the order?",
  );
  assert.match(readBackSay(two, cart.totalCents, "es"), /^De Parkside Pharmacy: .* De Peachtree Power: su factura, 86 dólares con 40 centavos\. Total: 94 dólares con 40 centavos\. ¿Hago el pedido\?$/);
  // each line goes to checkout with its own store; the cart's merchant is the fallback when stores differ
  const priced = cart.priced("corner_market");
  assert.deepEqual(priced.items.map((i) => i.merchant), ["parkside_pharmacy", "peachtree_power"]);
  // a bill is paid once
  assert.equal(cart.add({ sku: "BILL-peachtree_power", name: "x", category: "utility_bill", price: 86.4 }).ok, false);
  const c2 = new Cart();
  assert.equal(c2.add({ sku: "BILL-peachtree_power", name: "x", category: "utility_bill", price: 86.4 }, 2).ok, false);
});

test("store-aware receipts: the store's name, no pickup for a bill, the account masked, no Corner Market fallback", () => {
  const stores = { name: (id) => ({ peachtree_power: "Peachtree Power", parkside_pharmacy: "Parkside Pharmacy" })[id], isBiller: (id) => id === "peachtree_power", account: () => "PP-2231-0098" };
  const bill = parseReceipt({ order_id: "o_b", merchant: "Peachtree Power", merchant_id: "peachtree_power", total: "86.40", items: [{ name: "Peachtree Power bill", qty: 1, price: 86.4 }], pickup: null }, "en", stores);
  assert.equal(bill.pickup, "");
  assert.deepEqual(bill.bill, { account_ref: "…0098" });
  assert.equal(RECEIPT_LABELS.en.paidTo(bill.merchant, bill.bill.account_ref), "Paid to Peachtree Power · account …0098");
  const rx = parseReceipt({ order_id: "o_r", merchant_id: "parkside_pharmacy", total: 8, items: [{ name: "Lisinopril", qty: 1, price: 8 }], pickup: "after 3pm" }, "es", stores);
  assert.equal(rx.merchant, "Parkside Pharmacy");
  assert.equal(rx.pickup, "after 3pm");
  assert.equal(parseReceipt({ order_id: "o", total: 1, items: [] }).merchant, "");
  assert.equal(maskAccount("PP-2231-0098"), "…0098");
  assert.equal(RECEIPT_LABELS.en.points(11), "+11 rewards points");
});

test("a yes or no to 'Do you agree?' in three languages; longer answers are neither", () => {
  for (const t of ["Yes", "yes, I agree", "Sí, estoy de acuerdo", "De acuerdo.", "हाँ", "haan ji", "ठीक है"]) assert.equal(yesOrNo(t), "yes", t);
  for (const t of ["No", "todavía no", "नहीं", "not yet"]) assert.equal(yesOrNo(t), "no", t);
  for (const t of ["I need bread", "", "what are the rules again and who set them for me today"]) assert.equal(yesOrNo(t), null, t);
  // a no anywhere wins, and a yes followed by a request is not agreement
  for (const t of ["claro que no", "जी नहीं", "okay, no", "yeah no", "sí, no", "no, wait"]) assert.equal(yesOrNo(t), "no", t);
  for (const t of ["Okay, order bread", "yes and add milk", "sí, y leche"]) assert.equal(yesOrNo(t), null, t);
  for (const t of ["Sí, por favor", "okay, thank you", "yes, of course", "जी हाँ"]) assert.equal(yesOrNo(t), "yes", t);
});

test("the judge being down changes what Ruth hears while Priyank decides", () => {
  const normal = checkoutOutcome({ decision: "approve", decision_id: "d_1", approval: { approval_id: "a_1", rule: "R6_approval_threshold" } }, 5200, "en");
  assert.equal(normal.say_key, "asking_priya");
  const down = checkoutOutcome({ decision: "approve", decision_id: "d_2", approval: { approval_id: "a_2", rule: "R7_scam_judge", reason: "the safety check was unavailable, so I asked Priyank" } }, 1200, "en");
  assert.equal(down.say_key, "asking_priya_check");
  assert.match(down.say, /safety check/);
});

test("card and co-sign lines exist in every language with their slots filled", () => {
  const vars = { amount: "$480.00", store: "Five Points Drug", rules: "up to $60 a trip" };
  for (const key of ["card_declined_blocked", "card_declined_cooldown", "card_declined_over_cap", "card_declined_unusual", "card_declined_atm", "card_allowed_once", "cooldown_on", "refund_not_allowed_bill", "asking_priya_check", "cosign_ask", "cosign_thanks", "cosign_not_yet"]) {
    assert.ok(hasSay(key), key);
    for (const lang of ["es", "hi", "en"]) {
      const line = sayFor(key, lang, vars);
      assert.ok(line.length > 5 && !/\{\w+\}/.test(line), `${key} ${lang}: ${line}`);
    }
  }
  assert.equal(sayFor("card_declined_cooldown", "en", vars), "I stopped a $480.00 charge at Five Points Drug, because of the scam call earlier. If it's real, Priyank can allow it once.");
});

test("receipt and order lines take the store and the pickup; a bill has no pickup; the biller is named", () => {
  const pickup = sayFor("pickup_line", "en", { code: "4-7-2" });
  assert.equal(pickup, "Pickup is after 3 pm, and your code is 4-7-2.");
  assert.equal(sayFor("receipt_on_screen", "en", { total: "$8.00", store: "Parkside Pharmacy", pickup }),
    "Done. $8.00 at Parkside Pharmacy. Pickup is after 3 pm, and your code is 4-7-2. Your receipt is on the screen.");
  assert.equal(sayFor("receipt_done", "en", { total: "$86.40", store: "Peachtree Power", pickup: "" }).replace(/\s{2,}/g, " "),
    "Done. $86.40 at Peachtree Power. I printed your receipt.");
  assert.match(sayFor("order_ready", "es", { store: "Parkside Pharmacy", code: "4-7-2" }), /Parkside Pharmacy.*4-7-2/);
  assert.match(sayFor("refund_not_allowed_bill", "en", { biller: "Peachtree Power" }), /Priyank can call Peachtree Power\.$/);
  const bill = parseReceipt({ order_id: "o", store: "Peachtree Power", merchant_id: "peachtree_power", kind: "biller", total: 86.4, items: [], pickup: null });
  assert.equal(bill.merchant, "Peachtree Power");
  assert.ok(bill.bill);
  assert.equal(parseOrder({ order_id: "o", status: "ready_for_pickup", store: "Parkside Pharmacy", timeline: [] }).store, "Parkside Pharmacy");
});
