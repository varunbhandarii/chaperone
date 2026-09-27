import registry from "./merchants.json";

const merchants = registry.merchants || [];

export function storeName(id) {
  const found = merchants.find((entry) => entry.id === id);
  return found ? found.name : id || "";
}

export function storefronts() {
  return merchants.filter((entry) => entry.kind === "store" || entry.kind === "biller");
}

export function payeeName(approval) {
  const ids = (approval && approval.stores && approval.stores.length)
    ? approval.stores
    : (approval && approval.merchant ? [approval.merchant] : []);
  const names = ids.map(storeName).filter(Boolean);
  return names.join(" and ") || "Chaperone";
}

export const storeNames = Object.fromEntries(merchants.map((entry) => [entry.id, entry.name]));
