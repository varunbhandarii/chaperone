import assert from "node:assert/strict";
import test from "node:test";
import { shortMoney } from "./money.js";
import { approvalWords, plainError, RETRY } from "./status.js";
import { cardWords, categoryName, humanize, joinWords } from "./words.js";

test("every category id in Ruth's rules reads as plain words", () => {
  const ids = ["grocery", "pharmacy", "pharmacy_pickup", "household", "utility_bill", "gift_card", "prepaid_card", "wire", "crypto", "lottery"];
  for (const id of ids) {
    assert.doesNotMatch(categoryName(id), /_/, id);
  }
  assert.equal(categoryName("utility_bill"), "Utility bills");
  assert.equal(categoryName("grocery"), "Groceries");
  assert.equal(categoryName("gift_card"), "Gift cards");
});

test("an unknown id still reads as words", () => {
  assert.equal(categoryName("garden_supplies"), "Garden supplies");
  assert.equal(humanize(""), "");
  assert.equal(humanize(null), "");
});

test("lists join with commas and a final and", () => {
  assert.equal(joinWords([]), "");
  assert.equal(joinWords(["a"]), "a");
  assert.equal(joinWords(["a", "b"]), "a and b");
  assert.equal(joinWords(["a", "b", "c"]), "a, b and c");
});

test("a refund names the card only by a real last four", () => {
  assert.equal(cardWords("1111"), "the card ending 1111");
  assert.equal(cardWords(null), "the card that paid");
  assert.equal(cardWords("sandbox"), "the card that paid");
});

test("whole dollars drop the cents", () => {
  assert.equal(shortMoney(80), "$80");
  assert.equal(shortMoney("25"), "$25");
  assert.equal(shortMoney(1200), "$1,200");
  assert.equal(shortMoney(12.5), "$12.50");
  assert.equal(shortMoney(undefined), "");
});

test("errors reach Priyank as plain words", () => {
  const cancelled = new Error("The operation either timed out or was not allowed.");
  cancelled.name = "NotAllowedError";
  assert.equal(plainError(cancelled), "Cancelled");
  assert.equal(plainError(new Error("code rejected")), "That code didn't work.");
  assert.equal(plainError(new Error("sign in required")), "Please sign in again.");
  assert.equal(plainError(new Error("bad response 500 <html>")), RETRY);
  assert.equal(plainError(new TypeError("Failed to fetch")), RETRY);
  assert.equal(plainError(undefined), RETRY);
});

test("approval results read as plain words", () => {
  assert.equal(approvalWords("approved"), "Approved");
  assert.equal(approvalWords("rejected"), "Declined");
  assert.doesNotMatch(approvalWords("expired"), /expired/);
  assert.equal(approvalWords("pending"), RETRY);
});
