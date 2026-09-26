// Recorded sessions for replay. Pure functions: no DOM or network, so they run under `node --test`.

export interface RecordedEvent {
  t: number;
  kind: string;
  /** the shopper turn a transcript belongs to; Grok can complete one turn several times */
  turn?: number;
  text?: string;
}

/**
 * One transcript per shopper turn: the first version's place and time with the last version's text. A replay then
 * screens and counts every turn once (the read-back "yes" is a turn, not a transcript update), and the "yes" plays
 * before the checkout it led to, even when a longer transcript of it arrived after the checkout started.
 */
export function collapseShopperTurns<T extends RecordedEvent>(events: T[]): T[] {
  const lastText = new Map<number, string | undefined>();
  for (const e of events) if (e.kind === "shopper" && typeof e.turn === "number") lastText.set(e.turn, e.text);
  const kept = new Set<number>();
  const out: T[] = [];
  for (const e of events) {
    if (e.kind !== "shopper" || typeof e.turn !== "number") {
      out.push(e);
    } else if (!kept.has(e.turn)) {
      kept.add(e.turn);
      out.push({ ...e, text: lastText.get(e.turn) });
    }
  }
  return out;
}
