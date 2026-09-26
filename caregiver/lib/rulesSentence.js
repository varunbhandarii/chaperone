const NAMES = {
  gift_card: "Gift cards",
  prepaid_card: "prepaid cards",
  wire: "wire transfers",
  crypto: "crypto",
  lottery: "lottery",
};

export function rulesSentence(mandate) {
  const blocked = (mandate.blocked_categories || []).map((item) => NAMES[item] || item);
  const last = blocked.length > 1 ? `${blocked.slice(0, -1).join(", ")} and ${blocked[blocked.length - 1]}` : (blocked[0] || "Nothing");
  return `Ruth can spend up to $${mandate.per_purchase_cap} at a time and $${mandate.monthly_cap} a month at Corner Market on groceries and pharmacy. Anything over $${mandate.approval_threshold} comes to you. ${last} are always refused.`;
}
