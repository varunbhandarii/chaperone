import assert from "node:assert/strict";
import test from "node:test";
import { cardSentence, rulesSentence } from "./rulesSentence.js";

test("the rules sentence names the caps and the blocked list", () => {
  const sentence = rulesSentence({
    per_purchase_cap: 60,
    monthly_cap: 300,
    approval_threshold: 40,
    blocked_categories: ["gift_card", "wire"],
  });
  assert.match(sentence, /\$60/);
  assert.match(sentence, /\$300/);
  assert.match(sentence, /\$40/);
  assert.match(sentence, /Gift cards and wire transfers are always refused/);
});

test("the rules sentence names categories in plain words", () => {
  const sentence = rulesSentence({
    per_purchase_cap: 60,
    monthly_cap: 300,
    approval_threshold: 40,
    allowed_merchants: ["corner_market", "peachtree_power"],
    allowed_categories: ["grocery", "pharmacy", "household", "utility_bill"],
    blocked_categories: ["wire", "gift_card", "lottery"],
  }, { corner_market: "Corner Market", peachtree_power: "Peachtree Power" });
  assert.doesNotMatch(sentence, /_/);
  assert.match(sentence, /at Corner Market, Peachtree Power on groceries, pharmacy, household and utility bills\./);
  assert.match(sentence, /Wire transfers, gift cards and lottery are always refused\./);
});

test("with nothing blocked the sentence says so", () => {
  const sentence = rulesSentence({ per_purchase_cap: 60, monthly_cap: 300, approval_threshold: 40, blocked_categories: [] });
  assert.match(sentence, /Nothing is blocked\./);
});

test("the card sentence reads the drugstore cap from the mandate", () => {
  const card = { category_caps: { 5912: 80 }, default_cap: 60, cooldown: { hours: 24 } };
  assert.match(cardSentence({ card }), /A drugstore swipe can be up to \$80\./);
  assert.match(cardSentence({ card }), /tighter for a day\./);
  const edited = { card: { category_caps: { "5912": 45.5 }, cooldown: { hours: 12 } } };
  assert.match(cardSentence(edited), /up to \$45\.50\./);
  assert.match(cardSentence(edited), /tighter for 12 hours\./);
});

test("without a drugstore cap the card sentence falls back to the default cap", () => {
  assert.match(cardSentence({ card: { default_cap: 60 } }), /up to \$60\./);
  assert.doesNotMatch(cardSentence({}), /drugstore/);
});
