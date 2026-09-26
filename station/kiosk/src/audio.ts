// Microphone capture through an AudioWorklet (PCM16 blocks of ~20 ms) and gapless playback of
// the model's PCM16 deltas. One AudioContext serves both, so input and output share its real rate.

import { pcm16ToFloat } from "./pcm.ts";

/** PCM rates the realtime API accepts for audio/pcm. */
export const SUPPORTED_RATES = [8000, 11025, 16000, 22050, 24000, 32000, 44100, 48000];

/** Must run inside a user gesture (click) so the context is allowed to start. */
export function createAudioContext(preferredRate: number | null): AudioContext {
  if (preferredRate) {
    try {
      return new AudioContext({ sampleRate: preferredRate, latencyHint: "interactive" });
    } catch {
      /* fall through to the device rate */
    }
  }
  const ctx = new AudioContext({ latencyHint: "interactive" });
  if (SUPPORTED_RATES.includes(ctx.sampleRate)) return ctx;
  void ctx.close();
  return new AudioContext({ sampleRate: 48000, latencyHint: "interactive" });
}

type SinkCapable = { setSinkId?: (id: string) => Promise<void> };

export function canSelectOutput(ctx: AudioContext): boolean {
  return typeof (ctx as unknown as SinkCapable).setSinkId === "function";
}

export async function setOutputDevice(ctx: AudioContext, deviceId: string): Promise<boolean> {
  const sinkable = ctx as unknown as SinkCapable;
  if (typeof sinkable.setSinkId !== "function") return false;
  await sinkable.setSinkId(deviceId);
  return true;
}

// ---------- capture ----------

const WORKLET_SOURCE = `
class ChaperoneCapture extends AudioWorkletProcessor {
  constructor(options) {
    super();
    this.block = new Int16Array(options.processorOptions.blockFrames);
    this.n = 0;
    this.sumSq = 0;
    this.port.onmessage = (e) => { if (e.data === "flush") this.emit(true); };
  }
  emit(flush) {
    const pcm = this.block.slice(0, this.n);
    const rms = this.n ? Math.sqrt(this.sumSq / this.n) : 0;
    this.port.postMessage({ pcm: pcm.buffer, rms, flush }, [pcm.buffer]);
    this.n = 0;
    this.sumSq = 0;
  }
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (ch) {
      for (let i = 0; i < ch.length; i++) {
        let s = ch[i];
        if (s > 1) s = 1; else if (s < -1) s = -1;
        this.block[this.n++] = s < 0 ? Math.round(s * 32768) : Math.round(s * 32767);
        this.sumSq += s * s;
        if (this.n === this.block.length) this.emit(false);
      }
    }
    return true;
  }
}
registerProcessor("chaperone-capture", ChaperoneCapture);
`;

export interface CaptureBlock {
  pcm: Int16Array;
  rms: number;
  flush: boolean;
}

export const MIC_CONSTRAINTS: MediaTrackConstraints = {
  channelCount: 1,
  echoCancellation: true,
  noiseSuppression: true,
  autoGainControl: true,
};

export class Capture {
  readonly ctx: AudioContext;
  private node: AudioWorkletNode | null = null;
  private source: MediaStreamAudioSourceNode | null = null;
  private stream: MediaStream | null = null;
  private flushWaiters: Array<() => void> = [];
  onBlock: (block: CaptureBlock) => void = () => {};
  deviceLabel = "";

  constructor(ctx: AudioContext) {
    this.ctx = ctx;
  }

  async init(blockMs: number): Promise<void> {
    const url = URL.createObjectURL(new Blob([WORKLET_SOURCE], { type: "application/javascript" }));
    try {
      await this.ctx.audioWorklet.addModule(url);
    } finally {
      URL.revokeObjectURL(url);
    }
    const blockFrames = Math.max(128, Math.round((this.ctx.sampleRate * blockMs) / 1000));
    this.node = new AudioWorkletNode(this.ctx, "chaperone-capture", {
      numberOfInputs: 1,
      numberOfOutputs: 1,
      channelCount: 1,
      channelCountMode: "explicit",
      channelInterpretation: "speakers",
      processorOptions: { blockFrames },
    });
    // Keep the node in the rendered graph without making the mic audible.
    const mute = this.ctx.createGain();
    mute.gain.value = 0;
    this.node.connect(mute).connect(this.ctx.destination);
    this.node.port.onmessage = (e: MessageEvent<{ pcm: ArrayBuffer; rms: number; flush: boolean }>) => {
      const block: CaptureBlock = { pcm: new Int16Array(e.data.pcm), rms: e.data.rms, flush: e.data.flush };
      this.onBlock(block);
      if (block.flush) this.flushWaiters.splice(0).forEach((resolve) => resolve());
    };
  }

  /** Opens (or re-opens) the microphone. Getting the stream may be slow the first time (permission prompt). */
  async open(deviceId?: string): Promise<void> {
    const audio: MediaTrackConstraints = { ...MIC_CONSTRAINTS };
    if (deviceId) audio.deviceId = { exact: deviceId };
    const stream = await navigator.mediaDevices.getUserMedia({ audio });
    this.attach(stream);
  }

  attach(stream: MediaStream): void {
    if (!this.node) throw new Error("capture not initialised");
    this.detach();
    this.stream = stream;
    this.source = this.ctx.createMediaStreamSource(stream);
    this.source.connect(this.node);
    this.deviceLabel = stream.getAudioTracks()[0]?.label ?? "";
  }

  get deviceId(): string | undefined {
    return this.stream?.getAudioTracks()[0]?.getSettings().deviceId;
  }

  get hasStream(): boolean {
    return !!this.stream;
  }

  /** Asks the worklet for its partial block, so the last syllable reaches the server before commit. */
  flush(timeoutMs = 60): Promise<void> {
    if (!this.node) return Promise.resolve();
    return new Promise((resolve) => {
      const timer = setTimeout(resolve, timeoutMs);
      this.flushWaiters.push(() => {
        clearTimeout(timer);
        resolve();
      });
      this.node!.port.postMessage("flush");
    });
  }

  private detach(): void {
    this.source?.disconnect();
    this.stream?.getTracks().forEach((t) => t.stop());
    this.source = null;
    this.stream = null;
  }

  close(): void {
    this.detach();
    this.node?.disconnect();
    this.node = null;
  }
}

// ---------- playback ----------

export class Player {
  readonly ctx: AudioContext;
  private out: GainNode;
  private nextTime = 0;
  private sources = new Map<AudioScheduledSourceNode, (() => void) | undefined>();
  private drainWaiters: Array<() => void> = [];
  private itemId: string | null = null;
  private itemStart = 0;
  onIdle: () => void = () => {};

  constructor(ctx: AudioContext) {
    this.ctx = ctx;
    this.out = ctx.createGain();
    this.out.connect(ctx.destination);
  }

  /** Schedules one PCM16 delta right after the previous one (gapless). */
  /** `rate` is the PCM's sample rate (a replayed recording may differ from the context; the buffer resamples). */
  enqueue(pcm: Int16Array, itemId?: string, rate: number = this.ctx.sampleRate): void {
    if (pcm.length === 0) return;
    const buf = this.ctx.createBuffer(1, pcm.length, rate);
    buf.getChannelData(0).set(pcm16ToFloat(pcm));
    const startAt = this.schedule(buf);
    if (itemId && itemId !== this.itemId) {
      this.itemId = itemId;
      this.itemStart = startAt;
    }
  }

  /** Plays a decoded clip (refusal audio) on the same output. Resolves when it ends or is stopped. */
  playClip(buf: AudioBuffer): Promise<void> {
    this.itemId = null;
    return new Promise((resolve) => {
      this.schedule(buf, resolve);
    });
  }

  private schedule(buf: AudioBuffer, onEnded?: () => void): number {
    const src = this.ctx.createBufferSource();
    src.buffer = buf;
    src.connect(this.out);
    const startAt = Math.max(this.ctx.currentTime, this.nextTime);
    src.start(startAt);
    this.nextTime = startAt + buf.duration;
    this.sources.set(src, onEnded);
    src.onended = () => {
      this.sources.delete(src);
      onEnded?.();
      if (this.sources.size === 0) this.settle();
    };
    return startAt;
  }

  get active(): boolean {
    return this.sources.size > 0;
  }

  /** Resolves once everything scheduled so far has finished playing. */
  drained(): Promise<void> {
    if (this.sources.size === 0) return Promise.resolve();
    return new Promise((resolve) => this.drainWaiters.push(resolve));
  }

  /** Milliseconds of the given item actually played (for conversation.item.truncate). */
  playedMs(itemId: string): number | null {
    if (itemId !== this.itemId) return null;
    const played = Math.min(this.ctx.currentTime, this.nextTime) - this.itemStart;
    return Math.max(0, Math.round(played * 1000));
  }

  get currentItemId(): string | null {
    return this.itemId;
  }

  /** Stops everything immediately (barge-in, refusal). */
  stop(): void {
    const stopped = [...this.sources];
    this.sources.clear();
    this.itemId = null;
    for (const [src, onEnded] of stopped) {
      src.onended = null;
      try {
        src.stop();
      } catch {
        /* already stopped */
      }
      onEnded?.();
    }
    this.nextTime = 0;
    this.settle();
  }

  private settle(): void {
    this.drainWaiters.splice(0).forEach((resolve) => resolve());
    this.onIdle();
  }
}
