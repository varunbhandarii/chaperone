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

// "1 errand done", "2 errands done".
export function plural(count, one, many) {
  const number = Number(count) || 0;
  return `${number} ${number === 1 ? one : many}`;
}

// What a scam check found, as a headline. Unknown patterns read as words; "unknown" reads as nothing.
const PATTERNS = {
  grandparent_emergency: "Someone claimed a family emergency",
  refund_scam: "A refund scam",
  refund_overpayment: "A refund scam",
  refund_fee: "A refund scam",
  recovery_scam: "Someone offered to get lost money back for a fee",
  tech_support: "A fake tech-support call",
  utility_impersonation: "Someone pretended to be the power company",
  utility_shutoff: "Someone pretended to be the power company",
  bank_impersonation: "Someone pretended to be the bank",
  safe_account: "Someone asked to move money to a \"safe\" account",
  government_impersonation: "Someone pretended to be the government",
  digital_arrest: "Someone pretended to be the police",
  gift_card_codes: "Someone asked for gift card numbers",
  gift_card_demand: "Someone asked to be paid in gift cards",
  crypto_atm: "Someone asked for cash at a crypto machine",
  courier_pickup: "Someone sent a courier to collect money",
  parcel_customs: "A fake parcel or customs fee",
  fake_delivery: "A fake delivery fee",
  fake_renewal: "A fake subscription renewal",
};

export function patternWords(pattern) {
  if (PATTERNS[pattern]) return PATTERNS[pattern];
  return pattern && pattern !== "unknown" ? humanize(pattern) : "";
}

// "1:14 AM" in Priyank's own time zone; "" for a missing or broken time.
export function clockTime(iso) {
  const time = new Date(iso || "");
  if (!iso || Number.isNaN(time.getTime())) return "";
  return time.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
}

// "Today", "Yesterday", "Tue, Sep 22".
export function dayLabel(iso, now = Date.now()) {
  const time = new Date(iso || "");
  if (!iso || Number.isNaN(time.getTime())) return "Earlier";
  const start = new Date(now);
  start.setHours(0, 0, 0, 0);
  const day = new Date(time);
  day.setHours(0, 0, 0, 0);
  const days = Math.round((start.getTime() - day.getTime()) / 86400000);
  if (days === 0) return "Today";
  if (days === 1) return "Yesterday";
  return time.toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" });
}

// "1:14 AM" today, "1:14 AM tomorrow", or "Sep 30, 1:14 AM".
export function whenWords(iso, now = Date.now()) {
  const time = new Date(iso || "");
  if (!iso || Number.isNaN(time.getTime())) return "";
  const label = dayLabel(iso, now);
  const clock = clockTime(iso);
  if (label === "Today") return clock;
  const tomorrow = new Date(now);
  tomorrow.setHours(0, 0, 0, 0);
  tomorrow.setDate(tomorrow.getDate() + 1);
  const day = new Date(time);
  day.setHours(0, 0, 0, 0);
  if (day.getTime() === tomorrow.getTime()) return `${clock} tomorrow`;
  return `${time.toLocaleDateString("en-US", { month: "short", day: "numeric" })}, ${clock}`;
}

// Seconds left as "1:12".
export function countdown(seconds) {
  const whole = Math.max(0, Math.ceil(Number(seconds) || 0));
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
}

// Where Ruth was when she asked.
export function channelWords(channel) {
  if (channel === "station") return "at home";
  if (channel === "line") return "on the phone line";
  return "";
}
