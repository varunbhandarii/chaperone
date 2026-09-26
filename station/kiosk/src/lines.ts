// Spoken lines from the shared files in ai/prompts, registered over the built-in defaults in cart.ts:
// refusal.<key>.<lang>.txt (the rule screen's refusal texts) and lines.<lang>.json ({key: text}) when present.
// Vite bundles them at build time; a missing file simply leaves the default in place.

import { registerSay } from "./cart.ts";
import { registerRepeatPhrases } from "./postpurchase.ts";
import type { Lang } from "./lang.ts";

const refusals = import.meta.glob("../../../ai/prompts/refusal.*.txt", { query: "?raw", import: "default", eager: true }) as Record<string, string>;
const lineFiles = import.meta.glob("../../../ai/prompts/lines.*.json", { import: "default", eager: true }) as Record<string, unknown>;
const repeatFiles = import.meta.glob("../../../ai/prompts/repeat_triggers.json", { import: "default", eager: true }) as Record<string, unknown>;

let loaded = 0;
for (const [path, text] of Object.entries(refusals)) {
  const m = path.match(/refusal\.([a-z_]+)\.(es|hi|en)\.txt$/);
  if (m && typeof text === "string") {
    registerSay(m[1], m[2] as Lang, text);
    loaded++;
  }
}
for (const [path, table] of Object.entries(lineFiles)) {
  const m = path.match(/lines\.(es|hi|en)\.json$/);
  if (!m || !table || typeof table !== "object") continue;
  for (const [key, text] of Object.entries(table as Record<string, unknown>)) {
    if (key === "repeat_phrases" && Array.isArray(text)) {
      registerRepeatPhrases(text.filter((p): p is string => typeof p === "string"));
      continue;
    }
    if (typeof text === "string") {
      registerSay(key, m[1] as Lang, text);
      loaded++;
    }
  }
}

// repeat_triggers.json: {lang: [phrase, ...]}
for (const table of Object.values(repeatFiles)) {
  if (!table || typeof table !== "object") continue;
  for (const phrases of Object.values(table as Record<string, unknown>)) {
    if (Array.isArray(phrases)) registerRepeatPhrases(phrases.filter((p): p is string => typeof p === "string"));
  }
}

export const LINES_LOADED = loaded;
