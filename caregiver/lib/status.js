// What Priyank reads on the status line. The technical detail stays in the console.
export const RETRY = "That didn't go through. Please try again.";

const KNOWN = [
  ["every limit needs an amount", "Every limit needs an amount."],
  ["sign in required", "Please sign in again."],
  ["setup code required", "That setup code didn't work."],
  ["registration is closed", "A passkey is already set up. Sign in instead."],
  ["register a passkey first", "Create your passkey first."],
  ["policy unavailable", "Chaperone can't be reached right now. Please try again."],
  ["code rejected", "That code didn't work."],
  ["too many attempts", "Too many tries. That request is closed."],
  ["approval is closed", "That request has already closed."],
  ["unknown hold", "That hold was already handled."],
  ["no explanation", "No explanation for this one yet."],
  ["unknown decision", "No explanation for this one yet."],
];

export function plainError(error) {
  const name = error && error.name;
  if (name === "NotAllowedError" || name === "AbortError") return "Cancelled";
  if (name === "InvalidStateError") return "This phone already has a passkey. Sign in instead.";
  const message = String((error && error.message) || error || "").toLowerCase();
  const known = KNOWN.find(([key]) => message.includes(key));
  return known ? known[1] : RETRY;
}

const STATES = {
  approved: "Approved",
  rejected: "Declined",
  expired: "That request ran out of time.",
  cancelled: "That request was cancelled.",
};

export function approvalWords(state) {
  return STATES[state] || RETRY;
}

// Why an approval came to Priyank, in his words. The policy's own reason is the fallback.
export function approvalReason(approval, threshold) {
  const rule = String((approval && approval.rule) || "");
  const reason = String((approval && approval.reason) || "").trim();
  if (rule.startsWith("R7") || /safety check was unavailable/i.test(reason)) {
    return "The safety check couldn't finish, so it came to you.";
  }
  if (rule.startsWith("R6")) {
    const line = Number(threshold);
    return Number.isFinite(line) && String(threshold ?? "").trim() !== "" ? `Over your $${line % 1 ? line.toFixed(2) : line} ask-me line.` : "Over the amount you set.";
  }
  if (!reason) return "";
  const sentence = reason[0].toUpperCase() + reason.slice(1);
  return /[.!?]$/.test(sentence) ? sentence : `${sentence}.`;
}

// A declined swipe, in plain words.
const DECLINES = {
  card_blocked_category: "This kind of store stays blocked on Ruth's card.",
  card_cooldown: "Declined during extra care, after a scam check.",
  card_over_cap: "Over the limit for this kind of store.",
  card_unusual_amount: "Much more than Ruth usually spends at this kind of store.",
  card_atm_cap: "Over Ruth's daily cash limit.",
};

export function declineWords(reasonKey, fallback) {
  return DECLINES[reasonKey] || fallback || "Declined by Ruth's card rules.";
}

// What a refusal was about, from the line Ruth heard.
const REFUSALS = {
  blocked_category: "Asked for gift cards, a money transfer or crypto",
  scam_pattern: "A rushed or secret purchase",
  code_reading: "Someone asked for card numbers or codes",
  over_monthly_cap: "Over this month's budget",
  declined: "Something outside Ruth's rules",
  refund_scam: "A refund that looked like a scam",
  refund_not_allowed_rx: "A return of prescription medicine",
  refund_not_allowed_bill: "A return of a bill payment",
  refund_not_possible: "A return that can't be made",
  agent_paused: "A request while shopping was paused",
};

export function refusalTitle(sayKey) {
  return REFUSALS[sayKey] || "Chaperone stopped a request";
}

// An order's status as a badge: tone "ok" once the money has gone as planned, "neutral" otherwise.
const ORDER_STATUS = {
  awaiting_payment: ["Waiting for payment", "neutral"],
  paid: ["Paid", "ok"],
  preparing: ["Being prepared", "ok"],
  ready_for_pickup: ["Ready for pickup", "ok"],
  picked_up: ["Picked up", "ok"],
  cancelled: ["Cancelled", "neutral"],
  partially_refunded: ["Partly refunded", "neutral"],
  refunded: ["Refunded", "neutral"],
};

export function orderStatus(status, bill = false) {
  const [label, tone] = ORDER_STATUS[status] || ["In progress", "neutral"];
  if (bill && status === "paid") return { label: "Bill paid", tone };
  return { label, tone };
}

// A refund's status: on its way, sent, or failed (the only red one: something broke).
export function refundStatus(status) {
  const value = String(status || "PENDING").toUpperCase();
  if (["FAILED", "DECLINED", "REJECTED", "VOIDED", "CANCELLED"].includes(value)) return { label: "Didn't go through", tone: "err" };
  if (["PENDING", "TRANSMITTED", "PROCESSING"].includes(value)) return { label: "On its way", tone: "info" };
  return { label: "Sent", tone: "ok" };
}
