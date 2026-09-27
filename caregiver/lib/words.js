// Plain words for ids and fields Priyank would otherwise see raw.
const CATEGORIES = {
  grocery: "Groceries",
  pharmacy: "Pharmacy",
  pharmacy_pickup: "Pharmacy pickup",
  household: "Household",
  utility_bill: "Utility bills",
  gift_card: "Gift cards",
  prepaid_card: "Prepaid cards",
  wire: "Wire transfers",
  crypto: "Crypto",
  lottery: "Lottery",
};

export function capitalize(text) {
  const value = String(text || "");
  return value ? value[0].toUpperCase() + value.slice(1) : "";
}

// An id nobody named yet reads as words: "garden_center" -> "Garden center".
export function humanize(id) {
  return capitalize(String(id || "").replace(/_+/g, " ").trim());
}

export function categoryName(id) {
  return CATEGORIES[id] || humanize(id);
}

// "a", "a and b", "a, b and c".
export function joinWords(words) {
  const list = words.filter(Boolean);
  if (list.length < 2) return list[0] || "";
  return `${list.slice(0, -1).join(", ")} and ${list[list.length - 1]}`;
}

// Only a real last four is read out; anything else ("sandbox", null) is "the card that paid".
export function cardWords(last4) {
  const digits = String(last4 || "");
  return /^\d{4}$/.test(digits) ? `the card ending ${digits}` : "the card that paid";
}
