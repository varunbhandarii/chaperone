const NAMES = {
  gift_card: "Gift cards",
  prepaid_card: "prepaid cards",
  wire: "wire transfers",
  crypto: "crypto",
  lottery: "lottery",
};

export function rulesSentence(mandate, storeNames = {}) {
  const blocked = (mandate.blocked_categories || []).map((item) => NAMES[item] || item);
  const last = blocked.length > 1 ? `${blocked.slice(0, -1).join(", ")} and ${blocked[blocked.length - 1]}` : (blocked[0] || "Nothing");
  const stores = (mandate.allowed_merchants || []).map((id) => storeNames[id] || id);
  const where = stores.length ? stores.join(", ") : "her stores";
  const categories = (mandate.allowed_categories || []).join(", ") || "the categories you allowed";
  return `Ruth can spend up to $${mandate.per_purchase_cap} at a time and $${mandate.monthly_cap} a month at ${where} on ${categories}. Anything over $${mandate.approval_threshold} comes to you. ${last} are always refused.`;
}
