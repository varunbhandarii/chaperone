// A soft two-note tick while the station is checking (tools, the rule screen, a slow reply): the wait gets a
// sound instead of a spoken filler ("One moment"). It plays on its own output so it never counts as agent speech.

export interface EarconOptions {
  /** time between ticks */
  intervalMs: number;
  /** loudness of each note's peak, in dB below full scale */
  gainDb: number;
}

export function dbToGain(db: number): number {
  return Math.pow(10, db / 20);
}

/** The notes of one tick: [frequency Hz, start offset s, length s]. */
export const TICK_NOTES: ReadonlyArray<readonly [number, number, number]> = [
  [660, 0, 0.07],
  [880, 0.09, 0.07],
];

export class Earcon {
  private readonly ctx: BaseAudioContext;
  private readonly out: GainNode;
  private readonly intervalMs: number;
  private timer: ReturnType<typeof setInterval> | null = null;
  private notes = new Set<AudioScheduledSourceNode>();
  /** Called on every tick (the first one marks "first sound" for the latency meter). */
  onTick: () => void = () => {};

  constructor(ctx: BaseAudioContext, opts: EarconOptions) {
    this.ctx = ctx;
    this.intervalMs = opts.intervalMs;
    this.out = ctx.createGain();
    this.out.gain.value = dbToGain(opts.gainDb);
    this.out.connect(ctx.destination);
  }

  get running(): boolean {
    return this.timer !== null;
  }

  start(): void {
    if (this.timer !== null) return;
    this.tick();
    this.timer = setInterval(() => this.tick(), this.intervalMs);
  }

  /** Silences it at once, including a tick already playing. */
  stop(): void {
    if (this.timer !== null) {
      clearInterval(this.timer);
      this.timer = null;
    }
    for (const note of this.notes) {
      try {
        note.stop();
      } catch {
        /* already stopped */
      }
    }
    this.notes.clear();
  }

  private tick(): void {
    const t0 = this.ctx.currentTime + 0.01;
    for (const [freq, offset, length] of TICK_NOTES) this.note(freq, t0 + offset, length);
    this.onTick();
  }

  private note(freq: number, at: number, length: number): void {
    const osc = this.ctx.createOscillator();
    const env = this.ctx.createGain();
    osc.type = "sine";
    osc.frequency.value = freq;
    // A short attack and a quick decay: a soft "tick", no click.
    env.gain.setValueAtTime(0, at);
    env.gain.linearRampToValueAtTime(1, at + 0.01);
    env.gain.exponentialRampToValueAtTime(0.001, at + length);
    osc.connect(env);
    env.connect(this.out);
    osc.start(at);
    osc.stop(at + length + 0.02);
    this.notes.add(osc);
    osc.onended = () => this.notes.delete(osc);
  }
}
