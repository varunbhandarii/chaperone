import { randomBytes } from "crypto";

const challenges = new Map();

export function saveChallenge(purpose, challenge) {
  const id = randomBytes(16).toString("hex");
  challenges.set(id, { challenge, purpose, expires: Date.now() + 5 * 60 * 1000 });
  return id;
}

export function takeChallenge(id, purpose) {
  const row = challenges.get(id);
  if (!row) return null;
  challenges.delete(id);
  if (row.purpose !== purpose || row.expires < Date.now()) return null;
  return row.challenge;
}

const mandateReady = new Set();

export function markMandateReady(id) {
  mandateReady.add(id);
}

export function consumeMandateReady(id) {
  if (!mandateReady.has(id)) return false;
  mandateReady.delete(id);
  return true;
}
