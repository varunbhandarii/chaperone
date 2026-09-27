export function money(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "";
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(number);
}
