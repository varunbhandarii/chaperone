import { shortMoney } from "./money.js";
import { capitalize, categoryName, humanize, joinWords } from "./words.js";

const lower = (id) => categoryName(id).toLowerCase();

export function rulesSentence(mandate, storeNames = {}) {
  const blocked = joinWords((mandate.blocked_categories || []).map(lower));
  const refused = blocked ? `${capitalize(blocked)} are always refused.` : "Nothing is blocked.";
  const stores = (mandate.allowed_merchants || []).map((id) => storeNames[id] || humanize(id));
  const where = stores.length ? stores.join(", ") : "her stores";
  const categories = joinWords((mandate.allowed_categories || []).map(lower)) || "the categories you allowed";
  return `Ruth can spend up to $${mandate.per_purchase_cap} at a time and $${mandate.monthly_cap} a month at ${where} on ${categories}. Anything over $${mandate.approval_threshold} comes to you. ${refused}`;
}

// The card sentence reads the mandate being edited: the drugstore cap (MCC 5912) and the cool-down length.
export function cardSentence(mandate) {
  const card = mandate.card || {};
  const drugstore = (card.category_caps || {})["5912"] ?? card.default_cap;
  const hours = Number((card.cooldown || {}).hours);
  const cap = drugstore === undefined || drugstore === null ? "" : ` A drugstore swipe can be up to ${shortMoney(drugstore)}.`;
  const cooldown = hours > 0 ? ` After a scam check, risky spending is tighter for ${hours === 24 ? "a day" : `${hours} hours`}.` : "";
  return `Gift-card shops, crypto and wires stay blocked.${cap}${cooldown}`;
}
