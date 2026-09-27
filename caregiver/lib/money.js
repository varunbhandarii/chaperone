export function money(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "";
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(number);
}

// "$80" for whole dollars, "$80.50" otherwise.
export function shortMoney(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "";
  if (!Number.isInteger(number)) return money(number);
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 }).format(number);
}
