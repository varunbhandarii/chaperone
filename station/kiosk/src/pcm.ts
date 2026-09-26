// PCM16 little-endian helpers, base64 transport, pre-roll ring buffer and chunking.
// Pure functions: no DOM, so they run under `node --test` as well as in the browser.

export const IS_LITTLE_ENDIAN = new Uint8Array(new Uint16Array([1]).buffer)[0] === 1;

export function floatToPcm16(input: Float32Array): Int16Array {
  const out = new Int16Array(input.length);
  for (let i = 0; i < input.length; i++) {
    const s = input[i] > 1 ? 1 : input[i] < -1 ? -1 : input[i];
    out[i] = s < 0 ? Math.round(s * 0x8000) : Math.round(s * 0x7fff);
  }
  return out;
}

export function pcm16ToFloat(input: Int16Array): Float32Array {
  const out = new Float32Array(input.length);
  for (let i = 0; i < input.length; i++) out[i] = input[i] / 0x8000;
  return out;
}

type Base64Uint8 = Uint8Array & { toBase64?: () => string };
type Base64Ctor = typeof Uint8Array & { fromBase64?: (s: string) => Uint8Array };

export function bytesToBase64(bytes: Uint8Array): string {
  const native = (bytes as Base64Uint8).toBase64;
  if (typeof native === "function") return native.call(bytes);
  let binary = "";
  const step = 0x8000;
  for (let i = 0; i < bytes.length; i += step) {
    binary += String.fromCharCode.apply(null, bytes.subarray(i, i + step) as unknown as number[]);
  }
  return btoa(binary);
}

export function base64ToBytes(b64: string): Uint8Array {
  const native = (Uint8Array as Base64Ctor).fromBase64;
  if (typeof native === "function") return native(b64);
  const binary = atob(b64);
  const out = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) out[i] = binary.charCodeAt(i);
  return out;
}

/** Int16 samples -> base64 of little-endian bytes (the wire format for audio/pcm). */
export function pcm16ToBase64(samples: Int16Array): string {
  if (IS_LITTLE_ENDIAN) return bytesToBase64(new Uint8Array(samples.buffer, samples.byteOffset, samples.byteLength));
  const bytes = new Uint8Array(samples.length * 2);
  const view = new DataView(bytes.buffer);
  for (let i = 0; i < samples.length; i++) view.setInt16(i * 2, samples[i], true);
  return bytesToBase64(bytes);
}

/** base64 little-endian bytes -> Int16 samples. A trailing odd byte is dropped. */
export function base64ToPcm16(b64: string): Int16Array {
  const bytes = base64ToBytes(b64);
  const n = bytes.length >> 1;
  if (IS_LITTLE_ENDIAN && bytes.byteOffset % 2 === 0) return new Int16Array(bytes.buffer, bytes.byteOffset, n);
  const out = new Int16Array(n);
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  for (let i = 0; i < n; i++) out[i] = view.getInt16(i * 2, true);
  return out;
}

export function concatPcm16(blocks: Int16Array[]): Int16Array {
  let total = 0;
  for (const b of blocks) total += b.length;
  const out = new Int16Array(total);
  let offset = 0;
  for (const b of blocks) {
    out.set(b, offset);
    offset += b.length;
  }
  return out;
}

/** Keeps roughly the most recent `capacity` samples while the button is up. */
export class PcmRing {
  private blocks: Int16Array[] = [];
  private size = 0;
  readonly capacity: number;

  constructor(capacity: number) {
    this.capacity = Math.max(0, Math.floor(capacity));
  }

  push(block: Int16Array): void {
    if (this.capacity === 0 || block.length === 0) return;
    this.blocks.push(block);
    this.size += block.length;
    while (this.blocks.length > 1 && this.size - this.blocks[0].length >= this.capacity) {
      this.size -= this.blocks.shift()!.length;
    }
  }

  /** Returns the buffered audio, trimmed to at most `capacity` samples, and empties the ring. */
  drain(): Int16Array {
    let all = concatPcm16(this.blocks);
    if (all.length > this.capacity) all = all.slice(all.length - this.capacity);
    this.blocks = [];
    this.size = 0;
    return all;
  }

  get samples(): number {
    return Math.min(this.size, this.capacity);
  }
}

/** Groups small capture blocks into fixed-size chunks (about 100 ms) for input_audio_buffer.append. */
export class ChunkAccumulator {
  private blocks: Int16Array[] = [];
  private size = 0;
  readonly chunkSamples: number;

  constructor(chunkSamples: number) {
    this.chunkSamples = Math.max(1, Math.floor(chunkSamples));
  }

  push(block: Int16Array): Int16Array[] {
    if (block.length === 0) return [];
    this.blocks.push(block);
    this.size += block.length;
    if (this.size < this.chunkSamples) return [];
    const all = concatPcm16(this.blocks);
    const out: Int16Array[] = [];
    let offset = 0;
    while (all.length - offset >= this.chunkSamples) {
      out.push(all.slice(offset, offset + this.chunkSamples));
      offset += this.chunkSamples;
    }
    const rest = all.slice(offset);
    this.blocks = rest.length ? [rest] : [];
    this.size = rest.length;
    return out;
  }

  /** Whatever is left (possibly empty), then reset. */
  flush(): Int16Array {
    const all = concatPcm16(this.blocks);
    this.blocks = [];
    this.size = 0;
    return all;
  }

  get pending(): number {
    return this.size;
  }
}

/** RMS level of float samples mapped to 0..1 on a -60..0 dBFS scale, for the mic meter. */
export function levelFromRms(rms: number): number {
  if (!(rms > 0)) return 0;
  const db = 20 * Math.log10(rms);
  return Math.max(0, Math.min(1, (db + 60) / 60));
}

export function median(values: number[]): number | null {
  if (values.length === 0) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const mid = sorted.length >> 1;
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}
