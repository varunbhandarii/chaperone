import assert from "node:assert/strict";
import test from "node:test";
import { shortMoney } from "./money.js";
import { approvalReason, approvalWords, declineWords, orderStatus, plainError, refundStatus, RETRY } from "./status.js";
import { cardWords, categoryName, channelWords, countdown, humanize, joinWords, patternWords, plural } from "./words.js";

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

test("counts read with the right plural", () => {
  assert.equal(plural(1, "errand done", "errands done"), "1 errand done");
  assert.equal(plural(0, "bill paid", "bills paid"), "0 bills paid");
  assert.equal(plural(3, "attempt stopped", "attempts stopped"), "3 attempts stopped");
});

test("time left reads as minutes and seconds", () => {
  assert.equal(countdown(72), "1:12");
  assert.equal(countdown(9.2), "0:10");
  assert.equal(countdown(-4), "0:00");
});

test("scam patterns and channels read as plain words", () => {
  assert.equal(patternWords("grandparent_emergency"), "Someone claimed a family emergency");
  assert.equal(patternWords("garden_scam"), "Garden scam");
  assert.equal(patternWords("unknown"), "");
  assert.equal(channelWords("station"), "at home");
  assert.equal(channelWords("line"), "on the phone line");
  assert.equal(channelWords(undefined), "");
});

test("an approval's reason is written to Priyank, never about him", () => {
  const judgeDown = { rule: "R7_scam_judge", reason: "the safety check was unavailable, so I asked Priyank" };
  assert.equal(approvalReason(judgeDown, 40), "The safety check couldn't finish, so it came to you.");
  assert.equal(approvalReason({ rule: "R6_approval_threshold", reason: "This is over the amount you set." }, 40), "Over your $40 ask-me line.");
  assert.equal(approvalReason({ reason: "over the cap" }), "Over the cap.");
  assert.equal(approvalReason({}), "");
});

test("declines, orders and refunds read as plain words", () => {
  assert.equal(declineWords("card_blocked_category"), "This kind of store stays blocked on Ruth's card.");
  assert.equal(declineWords("new_reason", "From the card."), "From the card.");
  assert.deepEqual(orderStatus("paid", true), { label: "Bill paid", tone: "ok" });
  assert.deepEqual(orderStatus("awaiting_payment"), { label: "Waiting for payment", tone: "neutral" });
  assert.equal(refundStatus("FAILED").tone, "err");
  assert.equal(refundStatus(undefined).label, "On its way");
});
