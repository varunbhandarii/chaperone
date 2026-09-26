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
  assert.deepEqual(priced.items[0], { sku: "BAK-001", name: "Nature's Own Honey Wheat Bread", category: "grocery", qty: 2, price: 3.49 });
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
    assert.deepEqual(Object.keys(item).sort(), ["category", "name", "price", "qty", "sku"]);
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
  registerSay("receipt_done", "en", "Done. $X at Corner Market. I printed your receipt.");
  assert.equal(sayFor("receipt_done", "en", { total: "$11.49" }), "Done. $11.49 at Corner Market. I printed your receipt.");
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
