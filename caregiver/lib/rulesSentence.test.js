import assert from "node:assert/strict";
import test from "node:test";
import { rulesSentence } from "./rulesSentence.js";

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
