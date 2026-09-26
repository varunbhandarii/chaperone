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

const sessions = new Map();
const SESSION_MS = 30 * 60 * 1000;

export function openSession() {
  const id = randomBytes(32).toString("hex");
  sessions.set(id, { verifiedAt: Date.now() });
  return id;
}

export function sessionOpen(id) {
  if (!id) return false;
  const row = sessions.get(id);
  if (!row) return false;
  if (Date.now() - row.verifiedAt > SESSION_MS) {
    sessions.delete(id);
    return false;
  }
  return true;
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
