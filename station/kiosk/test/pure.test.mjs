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
