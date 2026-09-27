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
