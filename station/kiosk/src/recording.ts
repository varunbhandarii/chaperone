// Recorded sessions for replay. Pure functions: no DOM or network, so they run under `node --test`.

export interface RecordedEvent {
  kind: string;
  /** the shopper turn a transcript belongs to; Grok can complete one turn several times */
  turn?: number;
}

/**
 * Keeps only the last transcript of each shopper turn, at the time of that last version, so a replay screens
 * and counts every turn once (the read-back "yes" is a turn, not a transcript update).
 */
export function collapseShopperTurns<T extends RecordedEvent>(events: T[]): T[] {
  const last = new Map<number, number>();
  events.forEach((e, i) => {
    if (e.kind === "shopper" && typeof e.turn === "number") last.set(e.turn, i);
  });
  return events.filter((e, i) => e.kind !== "shopper" || typeof e.turn !== "number" || last.get(e.turn) === i);
}
