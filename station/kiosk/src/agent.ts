// Grok Voice session for the shopper station: push-to-talk capture, gapless playback, barge-in,
// rule screening in front of every tool, the search_catalog and checkout tools, refusals and the ledger.

import { Capture, MIC_CONSTRAINTS, Player, createAudioContext, type CaptureBlock } from "./audio.ts";
import { VOICE, buildSession, realtimeUrl } from "./config.ts";
import {
  ItemCache,
  buildCart,
  buildCheckoutBody,
  compactItem,
  newSessionId,
  parseCartLines,
  type CatalogItem,
} from "./cart.ts";
import { detectLang, guessLang, type Lang } from "./lang.ts";
import { ChunkAccumulator, PcmRing, base64ToPcm16, median, pcm16ToBase64 } from "./pcm.ts";
import { ruleIds, type ScreenResult } from "./screen.ts";
import { Ledger, fetchToken, health, loadClip, postCheckout, screenText, searchCatalog } from "./services.ts";

export type AgentState = "off" | "connecting" | "ready" | "listening" | "thinking" | "speaking";
export type NoteKind = "info" | "tool" | "warn" | "error" | "rule";

export interface AgentUI {
  state(state: AgentState): void;
  status(text: string, kind?: NoteKind): void;
  transcript(role: "shopper" | "agent", key: string, text: string, final: boolean, lang?: string): void;
  note(text: string, kind?: NoteKind): void;
  /** stats covers voice turns answered by the model only; `label` is set for typed or refusal-clip timings. */
  latency(ms: number, stats: { min: number; median: number; count: number } | null, label: string): void;
  rules(ids: string[], action: string, say?: string): void;
  items(items: CatalogItem[], source: string): void;
  decision(result: Record<string, unknown>): void;
  level(rms: number): void;
  micDevice(label: string): void;
}

type ServerEvent = { type: string; [k: string]: any };
type ClientEvent = { type: string; [k: string]: unknown };

interface Deferred<T> {
  promise: Promise<T>;
  resolve: (value: T) => void;
}

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => (resolve = r));
  return { promise, resolve };
}

interface Turn {
  n: number;
  kind: "voice" | "text";
  itemId?: string;
  text?: string;
  lang?: Lang;
  screen: Deferred<ScreenResult | null>;
  screenSettled: boolean;
  screenResult: ScreenResult | null;
  pressed: boolean;
  responseRequested: boolean;
  pendingToolCalls: number;
  refusal: "none" | "tool" | "out_of_band";
  partialInFlight: boolean;
  partialQueued?: string;
  lastPartialScreened?: string;
}

const DEFAULT_SAY = "I can't help with that purchase on this account.";
const SHORT_AUDIO_MS = 100;
const MAX_OUTBOX = 1200;

function ts(): string {
  return new Date().toISOString().slice(11, 23);
}

function safeParse(text: unknown): Record<string, unknown> {
  if (typeof text !== "string" || !text) return {};
  try {
    const v = JSON.parse(text);
    return v && typeof v === "object" ? v : {};
  } catch {
    return {};
  }
}

export class StationAgent {
  readonly sessionId = newSessionId();
  readonly ledger: Ledger;
  private ui: AgentUI;

  ctx: AudioContext | null = null;
  capture: Capture | null = null;
  player: Player | null = null;
  private rate = 0;
  private rateReady: Promise<number> | null = null;

  private ws: WebSocket | null = null;
  private configured = false;
  private manualTurnRetried = false;
  private sessionUpdateSent = false;
  private setupRetried = false;
  private outbox: ClientEvent[] = [];
  private started = false;

  private pressed = false;
  private flushing = false;
  private pressStart = 0;
  private appendedSamples = 0;
  private ring = new PcmRing(0);
  private acc = new ChunkAccumulator(1);

  private responseActive = false;
  private currentResponseId: string | null = null;
  private cancelled = new Set<string>();
  private generation = 0;
  private continuing = false;
  private toolBatches = new Map<string, Promise<void>[]>();
  private agentText = new Map<string, string>();
  private agentKeyByResponse = new Map<string, string>();

  private awaitingFirstAudio = false;
  private tRelease = 0;
  private latencyKind: "voice" | "typed" = "voice";
  private latencies: number[] = [];

  private turns: Turn[] = [];
  private shopperTexts: string[] = [];
  private lastLang: Lang | undefined;
  private cache = new ItemCache();
  private pendingForce: { text: string; at: number } | null = null;
  private deferredRefusal: { turn: Turn; result: ScreenResult } | null = null;
  private warned = new Set<string>();

  constructor(ui: AgentUI) {
    this.ui = ui;
    this.ledger = new Ledger(this.sessionId, VOICE.mandate_id, (m) => this.warn("ledger", m));
  }

  get isStarted(): boolean {
    return this.started;
  }

  // ---------------------------------------------------------------- start / stop

  /** Must be called from a click handler (user gesture). Token, mic and socket are set up in parallel. */
  async start(micDeviceId?: string): Promise<void> {
    if (this.started) return;
    this.started = true;
    this.ui.state("connecting");

    const ctx = createAudioContext(VOICE.capture.preferred_sample_rate);
    this.ctx = ctx;
    void ctx.resume();
    this.player = new Player(ctx);
    this.player.onIdle = () => this.refreshState();

    this.rateReady = this.setupCapture(ctx, micDeviceId);
    void health.start();
    const tokenPromise = fetchToken();

    let token;
    try {
      token = await tokenPromise;
    } catch (err) {
      this.ui.status(String(err instanceof Error ? err.message : err), "error");
      console.error(`[${ts()}] token:`, err);
      await this.stop();
      return;
    }
    const expiresIn = token.expires_at ? Math.round(token.expires_at - Date.now() / 1000) : NaN;
    this.ui.status(`Token received${Number.isFinite(expiresIn) ? ` (valid ${expiresIn} s)` : ""}; connecting to Grok Voice...`);
    this.openSocket(token.value);
  }

  private async setupCapture(ctx: AudioContext, micDeviceId?: string): Promise<number> {
    const cap = new Capture(ctx);
    this.capture = cap;
    cap.onBlock = (b) => this.onBlock(b);
    this.rate = ctx.sampleRate;
    const samplesPerMs = this.rate / 1000;
    this.ring = new PcmRing(VOICE.capture.preroll_ms * samplesPerMs);
    this.acc = new ChunkAccumulator(VOICE.capture.chunk_ms * samplesPerMs);
    console.info(`[${ts()}] audio context ${this.rate} Hz (input and output PCM rate)`);

    // Microphone permission and worklet load run in parallel.
    const audio: MediaTrackConstraints = micDeviceId ? { ...MIC_CONSTRAINTS, deviceId: { exact: micDeviceId } } : MIC_CONSTRAINTS;
    const init = cap.init(VOICE.capture.block_ms).then(
      () => null,
      (err: unknown) => err,
    );
    let stream: MediaStream | null = null;
    let failure: unknown = null;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio });
    } catch (err) {
      failure = err;
    }
    failure = failure ?? (await init);
    const stillOurs = this.capture === cap && ctx.state !== "closed";
    if (stream && (failure || !stillOurs)) stream.getTracks().forEach((t) => t.stop());
    if (!stillOurs) return this.rate;
    if (failure || !stream) {
      const msg = failure instanceof Error ? `${failure.name}: ${failure.message}` : String(failure);
      this.ui.status(`Microphone unavailable (${msg}). Typed input still works.`, "error");
      console.error(`[${ts()}] mic:`, failure);
      return this.rate;
    }
    cap.attach(stream);
    this.ui.micDevice(cap.deviceLabel);
    return this.rate;
  }

  async switchMic(deviceId: string): Promise<void> {
    if (!this.capture) return;
    try {
      await this.capture.open(deviceId || undefined);
      this.ui.micDevice(this.capture.deviceLabel);
      this.ui.note(`Microphone: ${this.capture.deviceLabel || deviceId}`);
    } catch (err) {
      this.ui.note(`Could not open that microphone: ${err instanceof Error ? err.message : err}`, "error");
    }
  }

  async stop(): Promise<void> {
    this.started = false;
    health.stop();
    this.configured = false;
    this.sessionUpdateSent = false;
    this.pressed = false;
    this.outbox = [];
    const ws = this.ws;
    this.ws = null;
    if (ws && ws.readyState <= WebSocket.OPEN) ws.close(1000, "station stopped");
    this.player?.stop();
    this.capture?.close();
    const ctx = this.ctx;
    this.ctx = null;
    this.capture = null;
    this.player = null;
    if (ctx && ctx.state !== "closed") await ctx.close().catch(() => {});
    this.ui.state("off");
  }

  // ---------------------------------------------------------------- socket

  private openSocket(secret: string): void {
    const url = realtimeUrl();
    const ws = new WebSocket(url, [VOICE.subprotocol_prefix + secret]);
    this.ws = ws;
    const openedAt = performance.now();

    ws.onopen = () => {
      console.info(`[${ts()}] websocket open (${Math.round(performance.now() - openedAt)} ms) ${url}`);
      // Normally session.update goes out on session.created; this covers a server that sends nothing first.
      setTimeout(() => void this.sendSessionUpdate(), 1000);
    };
    ws.onmessage = (e) => {
      if (typeof e.data !== "string") return;
      let ev: ServerEvent;
      try {
        ev = JSON.parse(e.data);
      } catch {
        return;
      }
      this.handle(ev);
    };
    ws.onerror = () => {
      this.ui.status("WebSocket error (see console). Check the token and network.", "error");
    };
    ws.onclose = (e) => {
      if (this.ws !== ws) return;
      console.warn(`[${ts()}] websocket closed code=${e.code} reason=${e.reason || "-"}`);
      this.ui.status(`Voice session closed (code ${e.code}${e.reason ? `: ${e.reason}` : ""}). Press Start to reconnect.`, e.code === 1000 ? "info" : "error");
      void this.stop();
    };
  }

  private async sendSessionUpdate(withTranscriptionModel = true): Promise<void> {
    if (this.sessionUpdateSent && withTranscriptionModel) return;
    this.sessionUpdateSent = true;
    const rate = await (this.rateReady ?? Promise.resolve(24000));
    const session = buildSession(rate, { withTranscriptionModel });
    this.rawSend({ type: "session.update", session });
    console.info(`[${ts()}] session.update sent (voice=${session.voice}, rate=${rate}, tools=${session.tools.map((t) => t.name).join(",")})`);
  }

  private rawSend(msg: ClientEvent): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(msg));
  }

  /** Sends now when the session is configured, otherwise queues (audio captured before the socket is ready is kept). */
  private send(msg: ClientEvent): void {
    if (this.configured && this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(msg));
      return;
    }
    this.outbox.push(msg);
    if (this.outbox.length > MAX_OUTBOX) {
      const i = this.outbox.findIndex((m) => m.type === "input_audio_buffer.append");
      this.outbox.splice(i >= 0 ? i : 0, 1);
    }
  }

  private flushOutbox(): void {
    const queued = this.outbox.splice(0);
    for (const msg of queued) this.rawSend(msg);
    if (queued.length) console.info(`[${ts()}] sent ${queued.length} queued message(s)`);
  }

  // ---------------------------------------------------------------- push-to-talk

  press(): void {
    if (!this.started || this.pressed) return;
    this.pressed = true;
    this.pressStart = performance.now();
    this.appendedSamples = 0;
    this.generation++;
    this.bargeIn("button pressed");

    const turn = this.newTurn("voice");
    turn.pressed = true;

    const preroll = this.ring.drain();
    if (preroll.length) this.sendAppend(preroll);
    this.refreshState();
  }

  async release(): Promise<void> {
    if (!this.pressed) return;
    const tRelease = performance.now();
    this.pressed = false;
    this.flushing = true;
    const turn = this.currentTurn();
    const heldMs = tRelease - this.pressStart;

    if (heldMs < VOICE.capture.min_press_ms) {
      this.flushing = false;
      this.acc.flush();
      this.send({ type: "input_audio_buffer.clear" });
      this.dropTurn(turn);
      this.ui.note(`Short tap (${Math.round(heldMs)} ms): nothing sent.`);
      this.deliverDeferredRefusal();
      this.refreshState();
      return;
    }

    await this.capture?.flush();
    const rest = this.acc.flush();
    if (rest.length) this.sendAppend(rest);
    this.flushing = false;

    if ((this.appendedSamples / (this.rate || 24000)) * 1000 < SHORT_AUDIO_MS) {
      this.send({ type: "input_audio_buffer.clear" });
      this.dropTurn(turn);
      this.ui.note("No audio was captured; check the microphone.", "warn");
      this.deliverDeferredRefusal();
      this.refreshState();
      return;
    }

    this.send({ type: "input_audio_buffer.commit" });
    this.tRelease = tRelease;
    this.latencyKind = "voice";
    this.awaitingFirstAudio = true;
    if (turn) {
      turn.pressed = false;
      // Placeholder keeps the shopper's line above the agent's reply; transcription events fill it in.
      if (!turn.text) this.ui.transcript("shopper", `voice-${turn.n}`, "...", false);
    }
    console.info(`[${ts()}] released after ${Math.round(heldMs)} ms; committed ${Math.round((this.appendedSamples / this.rate) * 1000)} ms of audio`);

    if (turn && turn.screenResult?.action === "refuse") {
      this.deferredRefusal = null;
      void this.refuseOutOfBand(turn, turn.screenResult);
    } else if (!this.deliverDeferredRefusal()) {
      this.send({ type: "response.create" });
      if (turn) turn.responseRequested = true;
    }
    this.refreshState();
  }

  /** A refusal that arrived while the button was held takes priority over answering the new turn. */
  private deliverDeferredRefusal(): boolean {
    const deferred = this.deferredRefusal;
    this.deferredRefusal = null;
    if (!deferred) return false;
    void this.refuseOutOfBand(deferred.turn, deferred.result);
    return true;
  }

  /** Stops the agent's audio now and cancels the in-flight response. */
  private bargeIn(reason: string): void {
    const player = this.player;
    const wasSpeaking = !!player?.active;
    const itemId = wasSpeaking ? player?.currentItemId ?? null : null;
    const played = itemId && player ? player.playedMs(itemId) : null;
    player?.stop();
    if (this.responseActive && this.currentResponseId) {
      this.cancelled.add(this.currentResponseId);
      this.send({ type: "response.cancel" });
    }
    if (VOICE.barge_in.truncate && itemId && played !== null) {
      this.send({ type: "conversation.item.truncate", item_id: itemId, content_index: 0, audio_end_ms: played });
    }
    this.awaitingFirstAudio = false;
    if (wasSpeaking) console.info(`[${ts()}] barge-in (${reason})`);
  }

  private onBlock(block: CaptureBlock): void {
    this.ui.level(block.rms);
    if (block.pcm.length === 0) return;
    if (this.pressed || this.flushing) {
      for (const chunk of this.acc.push(block.pcm)) this.sendAppend(chunk);
    } else {
      this.ring.push(block.pcm);
    }
  }

  private sendAppend(pcm: Int16Array): void {
    this.appendedSamples += pcm.length;
    this.send({ type: "input_audio_buffer.append", audio: pcm16ToBase64(pcm) });
  }

  // ---------------------------------------------------------------- typed input

  async sendText(text: string): Promise<void> {
    text = text.trim();
    if (!text || !this.started) return;
    this.generation++;
    this.bargeIn("typed message");
    const turn = this.newTurn("text");
    turn.text = text;
    turn.lang = guessLang(text) ?? this.lastLang;
    if (turn.lang) this.lastLang = turn.lang;
    this.recordShopper(turn, text, `text-${turn.n}`, "guess");
    this.send({ type: "conversation.item.create", item: { type: "message", role: "user", content: [{ type: "input_text", text }] } });
    this.tRelease = performance.now();
    this.latencyKind = "typed";
    this.awaitingFirstAudio = true;

    const result = await screenText(this.sessionId, text, turn.lang, (m) => this.warn("screen", m));
    this.settleScreen(turn, result);
    if (result?.action === "refuse") return; // settleScreen delivered the refusal
    this.send({ type: "response.create" });
    turn.responseRequested = true;
    this.refreshState();
  }

  // ---------------------------------------------------------------- turns and rule screen

  private newTurn(kind: Turn["kind"]): Turn {
    const turn: Turn = {
      n: this.turns.length + 1,
      kind,
      screen: deferred<ScreenResult | null>(),
      screenSettled: false,
      screenResult: null,
      pressed: false,
      responseRequested: false,
      pendingToolCalls: 0,
      refusal: "none",
      partialInFlight: false,
    };
    this.turns.push(turn);
    return turn;
  }

  private dropTurn(turn: Turn | undefined): void {
    if (!turn) return;
    const i = this.turns.indexOf(turn);
    if (i >= 0) this.turns.splice(i, 1);
    turn.screen.resolve(null);
  }

  private currentTurn(): Turn | undefined {
    return this.turns[this.turns.length - 1];
  }

  private turnForItem(itemId: unknown): Turn | undefined {
    if (typeof itemId === "string") {
      const hit = this.turns.find((t) => t.itemId === itemId);
      if (hit) return hit;
    }
    for (let i = this.turns.length - 1; i >= 0; i--) if (this.turns[i].kind === "voice") return this.turns[i];
    return undefined;
  }

  private recordShopper(turn: Turn, text: string, key: string, langSource: string): void {
    this.shopperTexts.push(text);
    const langLabel = turn.lang ? `${turn.lang}${langSource === "guess" ? "?" : ""}` : "?";
    console.log(`%c[${ts()}] SHOPPER (${langLabel}): ${text}`, "color:#0a7;font-weight:bold");
    this.ui.transcript("shopper", key, text, true, langLabel);
    this.ledger.post("heard", { role: "shopper", text, lang: turn.lang });
  }

  /** Screens partial transcripts while the button is held, so a blocked request is refused without waiting for the model. */
  private screenPartial(turn: Turn, text: string): void {
    if (!VOICE.screen.screen_partials || turn.screenSettled || !text.trim()) return;
    if (turn.partialInFlight) {
      turn.partialQueued = text;
      return;
    }
    if (text === turn.lastPartialScreened) return;
    turn.partialInFlight = true;
    turn.lastPartialScreened = text;
    void screenText(this.sessionId, text, guessLang(text) ?? this.lastLang, (m) => this.warn("screen", m)).then((result) => {
      turn.partialInFlight = false;
      if (result?.action === "refuse") this.settleScreen(turn, result);
      const queued = turn.partialQueued;
      turn.partialQueued = undefined;
      if (queued && queued !== text) this.screenPartial(turn, queued);
    });
  }

  private settleScreen(turn: Turn, result: ScreenResult | null): void {
    if (turn.screenSettled) return;
    turn.screenSettled = true;
    turn.screenResult = result;
    turn.screen.resolve(result);
    if (!result) return;
    const ids = ruleIds(result);
    if (result.action === "refuse") {
      console.warn(`%c[${ts()}] RULE REFUSE ${ids.join(", ")}`, "color:#fff;background:#c00;font-size:16px;padding:2px 6px");
      this.ui.rules(ids, "refuse", result.refusal?.text);
      if (turn.pendingToolCalls > 0) return; // a waiting tool call returns the refusal to the model
      if (turn.pressed) return; // still talking: delivered on release instead of asking the model
      if (this.pressed) {
        this.deferredRefusal = { turn, result }; // never talk over the shopper; delivered on release
        return;
      }
      void this.refuseOutOfBand(turn, result);
    } else if (result.action !== "proceed" || ids.length) {
      console.info(`[${ts()}] screen: ${result.action} ${ids.join(", ")}`);
      this.ui.rules(ids, result.action);
    }
  }

  private async awaitScreen(turn: Turn | undefined): Promise<ScreenResult | null> {
    if (!turn) return null;
    if (turn.screenSettled) return turn.screenResult;
    turn.pendingToolCalls++;
    const timeout = new Promise<"timeout">((r) => setTimeout(() => r("timeout"), VOICE.screen.timeout_ms));
    const result = await Promise.race([turn.screen.promise, timeout]);
    turn.pendingToolCalls--;
    if (result === "timeout") {
      this.warn("screen-timeout", `rule screen not ready after ${VOICE.screen.timeout_ms} ms; running the tool anyway`);
      return null;
    }
    return result;
  }

  // ---------------------------------------------------------------- refusals

  private logRefusal(turn: Turn, result: ScreenResult, via: string): void {
    const ids = ruleIds(result);
    this.ledger.post("refusal", {
      rule_ids: ids,
      spoken_key: result.refusal?.spoken_key ?? "refusal",
      patterns: result.refusal?.patterns ?? [],
      judge_score: null,
      lang: result.refusal?.lang ?? turn.lang,
      via,
    });
    this.ui.note(`Refused (${ids.join(", ")}) via ${via}`, "rule");
  }

  /** Refusal without the model: cancel what it is saying, then play the clip or speak the text verbatim. */
  private async refuseOutOfBand(turn: Turn, result: ScreenResult): Promise<void> {
    if (turn.refusal !== "none") return;
    turn.refusal = "out_of_band";
    this.generation++;
    const waitingForAudio = this.awaitingFirstAudio;
    this.bargeIn("rule refusal");
    this.awaitingFirstAudio = waitingForAudio;
    const say = result.refusal?.text || DEFAULT_SAY;
    this.logRefusal(turn, result, "out_of_band");

    let played = false;
    const url = result.refusal?.audio_url;
    if (url && this.ctx && this.player) {
      const gen = this.generation;
      const clip = await loadClip(this.ctx, url, VOICE.refusal.clip_timeout_ms, (m) => this.warn(`clip:${url}`, m));
      if (clip && gen === this.generation && this.player) {
        played = true;
        this.markFirstAudio("refusal clip");
        this.refreshState("speaking");
        this.ui.transcript("agent", `refusal-${turn.n}`, say, true);
        console.log(`%c[${ts()}] AGENT (clip): ${say}`, "color:#36c;font-weight:bold");
        // Keep the model's history consistent with what the shopper heard.
        this.send({ type: "conversation.item.create", item: { type: "message", role: "assistant", content: [{ type: "text", text: say }] } });
        this.ledger.post("heard", { role: "agent", text: say, lang: result.refusal?.lang ?? turn.lang, via: "clip" });
        await this.player.playClip(clip);
        this.refreshState();
      }
    }
    if (!played) this.speakVerbatim(say);
  }

  private speakVerbatim(text: string): void {
    if (VOICE.refusal.fallback === "force_message" && !this.warned.has("force-failed")) {
      this.pendingForce = { text, at: performance.now() };
      this.send({
        type: "conversation.item.create",
        item: { type: "force_message", role: "assistant", interruptible: true, content: [{ type: "output_text", text }] },
      });
    } else {
      this.send({ type: "response.create", response: { instructions: `Say exactly the following words and nothing else: ${text}` } });
    }
  }

  // ---------------------------------------------------------------- server events

  private handle(ev: ServerEvent): void {
    switch (ev.type) {
      case "session.created":
      case "conversation.created":
        console.info(`[${ts()}] ${ev.type}`);
        void this.sendSessionUpdate();
        break;

      case "session.updated": {
        const s = ev.session ?? {};
        console.info(`[${ts()}] session.updated`, { voice: s.voice, turn_detection: s.turn_detection, tools: (s.tools ?? []).length });
        // The docs show manual mode both as {"type": null} and as null; if the server kept server VAD, resend with the other form once.
        if (s.turn_detection?.type === "server_vad" && !this.manualTurnRetried) {
          this.manualTurnRetried = true;
          this.ui.note("Server kept automatic turn detection; resending session with turn_detection null.", "warn");
          void this.rateReady?.then((rate) => {
            const session = buildSession(rate ?? 24000) as unknown as Record<string, unknown>;
            session.turn_detection = null;
            this.rawSend({ type: "session.update", session } as unknown as ClientEvent);
          });
        }
        if (!this.configured) {
          this.configured = true;
          this.flushOutbox();
          const mic = this.capture?.hasStream;
          this.ui.status(
            `Session ready (${this.rate} Hz, voice ${s.voice ?? "?"}). ${mic ? "Hold the button and speak." : "Microphone unavailable: use typed input, or allow the mic and press Start again."}`,
            mic ? "info" : "warn",
          );
          this.ledger.post("session_started", { model: VOICE.model, voice: s.voice, rate: this.rate });
          this.refreshState();
        }
        break;
      }

      case "input_audio_buffer.committed": {
        const turn = [...this.turns].reverse().find((t) => t.kind === "voice" && !t.itemId);
        if (turn && typeof ev.item_id === "string") turn.itemId = ev.item_id;
        console.info(`[${ts()}] committed (+${Math.round(performance.now() - this.tRelease)} ms after release)`);
        break;
      }

      case "conversation.item.input_audio_transcription.updated": {
        const turn = this.turnForItem(ev.item_id);
        const text = String(ev.transcript ?? "");
        if (!turn || !text) break;
        this.ui.transcript("shopper", `voice-${turn.n}`, text, false);
        this.screenPartial(turn, text);
        break;
      }

      case "conversation.item.input_audio_transcription.completed": {
        const turn = this.turnForItem(ev.item_id);
        const text = String(ev.transcript ?? "").trim();
        if (!turn) break;
        if (!text) {
          this.settleScreen(turn, null);
          this.ui.transcript("shopper", `voice-${turn.n}`, "(nothing heard)", true);
          break;
        }
        turn.text = text;
        const detected = detectLang(ev.language, text);
        turn.lang = detected.lang ?? this.lastLang;
        if (turn.lang) this.lastLang = turn.lang;
        if (ev.language) console.info(`[${ts()}] detected language (api): ${ev.language}`);
        this.recordShopper(turn, text, `voice-${turn.n}`, detected.source);
        if (!turn.screenSettled) {
          void screenText(this.sessionId, text, turn.lang, (m) => this.warn("screen", m)).then((r) => this.settleScreen(turn, r));
        }
        break;
      }

      case "response.created":
        this.responseActive = true;
        this.currentResponseId = ev.response?.id ?? null;
        if (this.pressed && this.currentResponseId) {
          // Requested before the button went down; never speak over the shopper.
          this.cancelled.add(this.currentResponseId);
          this.send({ type: "response.cancel" });
        }
        this.refreshState();
        break;

      case "response.output_audio.delta": {
        if (this.cancelled.has(ev.response_id) || !this.player || typeof ev.delta !== "string") break;
        this.markFirstAudio(this.pendingForce ? "refusal speech" : "model");
        this.player.enqueue(base64ToPcm16(ev.delta), ev.item_id);
        this.refreshState();
        break;
      }

      case "response.output_audio_transcript.delta": {
        if (this.cancelled.has(ev.response_id)) break;
        const key = String(ev.item_id ?? ev.response_id);
        const text = (this.agentText.get(key) ?? "") + String(ev.delta ?? "");
        this.agentText.set(key, text);
        if (ev.response_id) this.agentKeyByResponse.set(String(ev.response_id), key);
        this.ui.transcript("agent", key, text, false);
        break;
      }

      case "response.output_audio_transcript.done": {
        const key = String(ev.item_id ?? ev.response_id);
        const text = String(ev.transcript ?? this.agentText.get(key) ?? "").trim();
        this.agentText.delete(key);
        if (this.cancelled.has(ev.response_id)) {
          this.ui.transcript("agent", key, text ? `${text} (interrupted)` : "(interrupted)", true);
          break;
        }
        if (!text) break;
        console.log(`%c[${ts()}] AGENT: ${text}`, "color:#36c;font-weight:bold");
        this.ui.transcript("agent", key, text, true, this.lastLang);
        this.ledger.post("heard", { role: "agent", text, lang: this.lastLang });
        if (this.pendingForce && text === this.pendingForce.text) this.pendingForce = null;
        break;
      }

      case "response.function_call_arguments.done": {
        const responseId = String(ev.response_id ?? this.currentResponseId ?? "unknown");
        const job = this.runTool(ev, responseId);
        const batch = this.toolBatches.get(responseId) ?? [];
        batch.push(job);
        this.toolBatches.set(responseId, batch);
        break;
      }

      case "response.done": {
        const id = String(ev.response?.id ?? this.currentResponseId ?? "unknown");
        const status = ev.response?.status;
        if (id === this.currentResponseId) {
          this.responseActive = false;
          this.currentResponseId = null;
        }
        if (status && status !== "completed") console.info(`[${ts()}] response ${status}`);
        const liveKey = this.agentKeyByResponse.get(id);
        this.agentKeyByResponse.delete(id);
        if (liveKey && this.agentText.has(liveKey)) {
          // No transcript.done arrived (cancelled or cut short): close the live line.
          const partial = this.agentText.get(liveKey) ?? "";
          this.agentText.delete(liveKey);
          this.ui.transcript("agent", liveKey, `${partial} (interrupted)`.trim(), true);
        }
        const batch = this.toolBatches.get(id);
        if (batch) {
          this.toolBatches.delete(id);
          void this.continueAfterTools(batch);
        }
        this.refreshState();
        break;
      }

      case "error": {
        const err = ev.error ?? {};
        const msg = `${err.type ?? "error"}${err.param ? ` (${err.param})` : ""}: ${err.message ?? JSON.stringify(err)}`;
        console.error(`[${ts()}] server error ${msg}`);
        if (!this.configured && !this.setupRetried && /transcri|model/i.test(`${err.param ?? ""} ${err.message ?? ""}`)) {
          this.setupRetried = true;
          this.ui.note("Session setup rejected the transcription model; retrying without it.", "warn");
          void this.sendSessionUpdate(false);
          break;
        }
        if (this.pendingForce && performance.now() - this.pendingForce.at < 3000) {
          const text = this.pendingForce.text;
          this.pendingForce = null;
          this.warned.add("force-failed");
          this.ui.note("force_message not accepted; asking the model to say the refusal instead.", "warn");
          this.speakVerbatim(text);
          break;
        }
        if (/truncat/i.test(msg)) break; // barge-in bookkeeping; harmless
        this.ui.note(`Server error ${msg}`, "error");
        break;
      }

      default:
        if (!ev.type.endsWith(".delta")) console.debug(`[${ts()}] ${ev.type}`);
    }
  }

  private markFirstAudio(via: string): void {
    if (!this.awaitingFirstAudio) return;
    this.awaitingFirstAudio = false;
    const ms = Math.round(performance.now() - this.tRelease);
    // Only button release -> model audio counts toward the release-to-first-audio target.
    const counted = this.latencyKind === "voice" && via === "model";
    if (counted) this.latencies.push(ms);
    const stats = this.latencies.length
      ? { min: Math.min(...this.latencies), median: Math.round(median(this.latencies) ?? ms), count: this.latencies.length }
      : null;
    const label = counted ? "" : `${this.latencyKind} turn, ${via}`;
    const summary = stats ? `min ${stats.min}, median ${stats.median}, n=${stats.count}` : "no voice turns yet";
    console.log(`%c[${ts()}] ${this.latencyKind === "voice" ? "release" : "send"} -> first audio: ${ms} ms (${via}); ${summary}`, "color:#b60;font-weight:bold");
    this.ui.latency(ms, stats, label);
  }

  // ---------------------------------------------------------------- tools

  private async runTool(ev: ServerEvent, responseId: string): Promise<void> {
    const name = String(ev.name ?? "");
    const callId = String(ev.call_id ?? "");
    const args = safeParse(ev.arguments);
    const turn = this.currentTurn();
    console.info(`%c[${ts()}] TOOL CALL ${name}(${JSON.stringify(args)})`, "color:#a0a;font-weight:bold");
    this.ui.note(`Tool call: ${name}(${JSON.stringify(args)})`, "tool");

    let output: Record<string, unknown>;
    if (this.cancelled.has(responseId)) {
      output = { cancelled: true, note: "The shopper interrupted before this ran. Nothing was done." };
    } else {
      const screen = await this.awaitScreen(turn);
      if (screen?.action === "refuse") {
        output = { refused: true, rule_id: screen.refusal?.rule_id ?? ruleIds(screen)[0] ?? "unknown", say: screen.refusal?.text || DEFAULT_SAY };
        if (turn && turn.refusal === "none") {
          turn.refusal = "tool";
          this.logRefusal(turn, screen, `tool ${name}`);
        }
      } else if (name === "search_catalog") {
        output = await this.toolSearch(args);
      } else if (name === "checkout") {
        output = await this.toolCheckout(args);
      } else {
        output = { error: `unknown tool ${name}` };
      }
    }
    console.info(`[${ts()}] TOOL RESULT ${name}:`, output);
    if (callId) {
      this.send({ type: "conversation.item.create", item: { type: "function_call_output", call_id: callId, output: JSON.stringify(output) } });
    }
  }

  /** After a response with tool calls: outputs are sent; ask for the next response once playback has drained. */
  private async continueAfterTools(batch: Promise<void>[]): Promise<void> {
    const gen = this.generation;
    this.continuing = true;
    this.refreshState();
    try {
      await Promise.all(batch);
      await this.player?.drained();
    } finally {
      this.continuing = false;
    }
    const turn = this.currentTurn();
    if (gen !== this.generation || turn?.refusal === "out_of_band") {
      this.refreshState();
      return;
    }
    this.send({ type: "response.create" });
    this.refreshState();
  }

  private async toolSearch(args: Record<string, unknown>): Promise<Record<string, unknown>> {
    const query = String(args.query ?? "").trim();
    if (!query) return { error: "query is required" };
    const result = await searchCatalog(query, (m) => this.warn("catalog", m));
    this.cache.add(result.items);
    this.ui.items(result.items, result.source);
    this.ledger.post("items_found", { query, skus: result.items.map((i) => i.sku), catalog_source: result.source });
    return { query, source: result.source, results: result.items.map(compactItem) };
  }

  private async toolCheckout(args: Record<string, unknown>): Promise<Record<string, unknown>> {
    const parsed = parseCartLines(args);
    if (!parsed.ok) return { error: parsed.error };
    const built = buildCart(parsed.lines, this.cache, VOICE.merchant);
    if (!built.ok) return { error: built.error };
    const body = buildCheckoutBody({
      sessionId: this.sessionId,
      mandateId: VOICE.mandate_id,
      cart: built.cart,
      transcript: this.shopperTexts.join(" "),
      lang: this.lastLang,
      readBack: true,
    });
    this.ledger.post("checkout_requested", { cart: body.cart, read_back: true });
    this.ui.note(`Checkout: ${body.cart.items.map((i) => `${i.qty} x ${i.name} $${i.price.toFixed(2)}`).join(", ")} = $${body.cart.total.toFixed(2)}`, "tool");
    const result = await postCheckout(body, (m) => this.warn("policy", m));
    if (typeof result.decision === "string") {
      this.ledger.post("policy_decision", {
        decision_id: result.decision_id,
        decision: result.decision,
        rules: result.rules ?? [],
        monthly_total_after: result.monthly_total_after,
      });
      const failed = Array.isArray(result.rules)
        ? (result.rules as Array<{ id?: string; passed?: boolean }>).filter((r) => r && r.passed === false).map((r) => String(r.id))
        : [];
      if (failed.length) this.ui.rules(failed, String(result.decision));
    }
    this.ui.decision(result);
    return result;
  }

  // ---------------------------------------------------------------- state and warnings

  private refreshState(force?: AgentState): void {
    if (!this.started) return this.ui.state("off");
    if (force) return this.ui.state(force);
    if (!this.configured) return this.ui.state(this.pressed ? "listening" : "connecting");
    if (this.pressed) return this.ui.state("listening");
    if (this.player?.active) return this.ui.state("speaking");
    if (this.responseActive || this.continuing || this.awaitingFirstAudio) return this.ui.state("thinking");
    this.ui.state("ready");
  }

  private warn(key: string, msg: string): void {
    console.warn(`[${ts()}] ${msg}`);
    if (this.warned.has(key)) return;
    this.warned.add(key);
    this.ui.note(msg, "warn");
  }
}
