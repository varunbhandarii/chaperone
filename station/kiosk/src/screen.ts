// Rule-screen results from the policy service's POST /screen:
//   {"action":"refuse"|"judge"|"slow"|"proceed","hits":[{"rule_id",...}],
//    "refusal":{"rule_id","spoken_key","patterns","lang","text","audio_url"}|null}
// Pure functions: no DOM or network, so they run under `node --test`.

export type ScreenAction = "refuse" | "judge" | "slow" | "proceed";

export interface Refusal {
  rule_id: string;
  spoken_key?: string;
  patterns?: string[];
  lang?: string;
  text: string;
  audio_url?: string;
}

export interface ScreenResult {
  action: ScreenAction;
  hits: Array<{ rule_id: string; [k: string]: unknown }>;
  refusal: Refusal | null;
}

const ACTIONS = new Set<ScreenAction>(["refuse", "judge", "slow", "proceed"]);

export function parseScreen(body: unknown): ScreenResult | null {
  if (!body || typeof body !== "object") return null;
  const b = body as Record<string, unknown>;
  if (typeof b.action !== "string" || !ACTIONS.has(b.action as ScreenAction)) return null;
  const hits = Array.isArray(b.hits) ? (b.hits as ScreenResult["hits"]).filter((h) => h && typeof h.rule_id === "string") : [];
  const r = b.refusal as Record<string, unknown> | null | undefined;
  const refusal =
    r && typeof r === "object" && typeof r.text === "string"
      ? ({ ...r, rule_id: String(r.rule_id ?? hits[0]?.rule_id ?? "unknown") } as Refusal)
      : null;
  return { action: b.action as ScreenAction, hits, refusal };
}

export function ruleIds(result: ScreenResult): string[] {
  const ids = result.hits.map((h) => h.rule_id);
  if (result.refusal?.rule_id) ids.push(result.refusal.rule_id);
  return [...new Set(ids)];
}
