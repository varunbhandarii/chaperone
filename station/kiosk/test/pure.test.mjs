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
  FALLBACK_ITEMS,
  ItemCache,
  buildCart,
  buildCheckoutBody,
  compactItem,
  newSessionId,
  parseCartLines,
  parseSearchResponse,
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

test("search response parsing keeps valid items only", () => {
  const items = parseSearchResponse({
    query: "bread",
    results: [
      { sku: "a", name: "A", category: "grocery", price: 1.5, brand: "X", size: "20 oz", usual: true, extra: 1 },
      { sku: "b", name: "B", category: "grocery" },
      null,
    ],
    took_ms: 3,
  });
  assert.equal(items.length, 1);
  assert.deepEqual(compactItem(items[0]), { sku: "a", name: "A", category: "grocery", price: 1.5, brand: "X", size: "20 oz", usual: true });
  assert.equal(parseSearchResponse({ nope: 1 }), null);
});

test("checkout args are validated and duplicate skus merged", () => {
  assert.deepEqual(parseCartLines({ items: [{ sku: "a", qty: 1 }, { sku: "a", qty: 2 }, { sku: "b", qty: 1 }] }), {
    ok: true,
    lines: [{ sku: "a", qty: 3 }, { sku: "b", qty: 1 }],
  });
  assert.equal(parseCartLines({ items: [] }).ok, false);
  assert.equal(parseCartLines({ items: [{ sku: "a", qty: 0 }] }).ok, false);
  assert.equal(parseCartLines({ items: [{ sku: "a", qty: 1.5 }] }).ok, false);
  assert.equal(parseCartLines({}).ok, false);
});

test("priced cart totals in integer cents", () => {
  const cache = new ItemCache();
  cache.add(FALLBACK_ITEMS);
  cache.add([{ sku: "dime", name: "Dime candy", category: "grocery", price: 0.1 }]);
  const r = buildCart([{ sku: "bread_ww_20oz", qty: 2 }, { sku: "bread_white_20oz", qty: 1 }, { sku: "dime", qty: 3 }], cache, "corner_market");
  assert.equal(r.ok, true);
  assert.equal(r.cart.total, 9.57); // 2*3.49 + 2.29 + 3*0.10, no float drift
  assert.deepEqual(r.cart.items[0], { sku: "bread_ww_20oz", name: "Whole wheat bread", category: "grocery", qty: 2, price: 3.49 });
  const bad = buildCart([{ sku: "gift_card_500", qty: 1 }], cache, "corner_market");
  assert.equal(bad.ok, false);
  assert.deepEqual(bad.unknown_skus, ["gift_card_500"]);
});

test("checkout body has exactly the policy service's shape", () => {
  const cache = new ItemCache();
  cache.add(FALLBACK_ITEMS);
  const { cart } = buildCart([{ sku: "bread_ww_20oz", qty: 1 }], cache, "corner_market");
  const sessionId = newSessionId();
  const body = buildCheckoutBody({ sessionId, mandateId: "m_ruth_2026_09", cart, transcript: " Necesito pan  ", lang: "es", readBack: true });

  assert.match(body.session_id, /^s_[A-Za-z0-9_-]+$/);
  assert.deepEqual(Object.keys(body).sort(), ["cart", "lang", "mandate_id", "read_back", "session_id", "transcript"]);
  assert.deepEqual(Object.keys(body.cart).sort(), ["items", "merchant", "total"]);
  assert.equal(body.cart.merchant, "corner_market");
  assert.ok(body.cart.items.length >= 1);
  for (const item of body.cart.items) {
    assert.deepEqual(Object.keys(item).sort(), ["category", "name", "price", "qty", "sku"]);
    assert.ok(Number.isInteger(item.qty) && item.qty >= 1);
    assert.ok(typeof item.price === "number" && item.price >= 0);
  }
  assert.equal(body.cart.total, 3.49);
  assert.equal(body.transcript, "Necesito pan");
  assert.equal(body.lang, "es");
  assert.equal(body.read_back, true);

  const minimal = buildCheckoutBody({ sessionId, mandateId: "m_ruth_2026_09", cart, lang: "fr" });
  assert.deepEqual(Object.keys(minimal).sort(), ["cart", "mandate_id", "read_back", "session_id"]);
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
