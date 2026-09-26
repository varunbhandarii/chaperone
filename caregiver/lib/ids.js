// Approval ids are minted as a_ plus 12 hex chars. Anything else, including "../", never reaches policy.
export const APPROVAL_ID = /^a_[0-9a-f]{12}$/;

// Same shape the relay uses for session ids.
export const SESSION_ID = /^[A-Za-z0-9_-]{1,64}$/;

export function escapeHtml(text) {
  return String(text).replace(/[&<>]/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[ch]));
}
