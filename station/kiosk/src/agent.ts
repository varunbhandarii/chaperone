// Grok Voice session for the shopper station: push-to-talk capture, gapless playback, barge-in,
// rule screening in front of every tool, the search_catalog and checkout tools, refusals and the ledger.

import { Capture, MIC_CONSTRAINTS, Player, createAudioContext, type CaptureBlock } from "./audio.ts";
import { VOICE, VOICE_NAME, buildSession, languageHint, realtimeUrl, voiceFor } from "./config.ts";
import {
  Cart,
  ItemCache,
  ReadBackGate,
  buildCheckoutBody,
  checkoutOutcome,
  compactItem,
  money,
  newSessionId,
  readBackSay,
  sayFor,
  type CartLineView,
  type CatalogItem,
  type CheckoutOutcome,
} from "./cart.ts";
import { detectLang, guessLang, type Lang } from "./lang.ts";
import { ChunkAccumulator, PcmRing, base64ToPcm16, median, pcm16ToBase64 } from "./pcm.ts";
import { ruleIds, type ScreenResult } from "./screen.ts";
import { Ledger, fetchToken, getBudget, health, loadClip, postCheckout, screenText, searchCatalog } from "./services.ts";

export type AgentState = "off" | "connecting" | "ready" | "listening" | "thinking" | "speaking" | "waiting";
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
  cart(lines: CartLineView[], total: number): void;
  outcome(outcome: CheckoutOutcome): void;
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
const RECONNECT_DELAYS_MS = [500, 1000, 2000];
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
  /** false after the server rejected audio.input.transcription.model; later session updates leave it out */
  private transcriptionModel = true;
  private outbox: ClientEvent[] = [];
  private started = false;
  /** From conversation.created; reconnecting with it resumes the conversation (history kept 30 min). */
  private conversationId: string | null = null;
  private reconnecting = false;
  private resumed = false;
  /** Bumps on every new socket; tool outputs computed for an older socket are dropped. */
  private socketGen = 0;

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
  private cart = new Cart();
  private gate = new ReadBackGate();
  /** Committed shopper turns (voice commits and typed messages); a turn after read_cart is the "yes". */
  private userTurns = 0;
  private waitingForCaregiver = false;
  private sessionLang: Lang | undefined;
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

  private openSocket(secret: string, conversationId?: string): void {
    const url = realtimeUrl(conversationId);
    const ws = new WebSocket(url, [VOICE.subprotocol_prefix + secret]);
    this.ws = ws;
    this.socketGen++;
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
      if (this.started && this.conversationId && VOICE.session.resumption) {
        void this.reconnect(`socket closed (code ${e.code})`);
        return;
      }
      this.ui.status(`Voice session closed (code ${e.code}${e.reason ? `: ${e.reason}` : ""}). Press Start to reconnect.`, e.code === 1000 ? "info" : "error");
      void this.stop();
    };
  }

  private async sendSessionUpdate(withTranscriptionModel = true): Promise<void> {
    if (this.sessionUpdateSent && withTranscriptionModel) return;
    if (!withTranscriptionModel) this.transcriptionModel = false;
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
    this.userTurns++;
    this.waitingForCaregiver = false;
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
    this.userTurns++;
    this.waitingForCaregiver = false;
    if (turn.lang) this.applyLanguage(turn.lang);
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
    void screenText(this.sessionId, text, guessLang(text) ?? this.lastLang, (m) => this.warn("screen", m), true).then((result) => {
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
        if (ev.type === "conversation.created" && typeof ev.conversation?.id === "string") this.conversationId = ev.conversation.id;
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
            const session = buildSession(rate ?? 24000, { withTranscriptionModel: this.transcriptionModel }) as unknown as Record<string, unknown>;
            session.turn_detection = null;
            this.rawSend({ type: "session.update", session } as unknown as ClientEvent);
          });
        }
        if (!this.configured) {
          this.configured = true;
          this.flushOutbox();
          const mic = this.capture?.hasStream;
          this.ui.status(
            `Session ready (${this.rate} Hz, voice ${s.voice ?? VOICE_NAME}). ${mic ? "Hold the button and speak." : "Microphone unavailable: use typed input, or allow the mic and press Start again."}`,
            mic ? "info" : "warn",
          );
          this.ledger.post("session_started", { model: VOICE.model, voice: s.voice, rate: this.rate, resumed: this.resumed });
          if (this.resumed) this.afterResume();
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
        if (turn.lang && detected.source !== "none") this.applyLanguage(turn.lang);
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
    const gen = this.socketGen;
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
      } else {
        output = await this.dispatchTool(name, args);
      }
    }
    console.info(`[${ts()}] TOOL RESULT ${name}:`, output);
    if (gen !== this.socketGen) {
      console.warn(`[${ts()}] ${name} finished after a reconnect; its output is dropped (the cart state was re-sent instead)`);
      return;
    }
    if (callId) {
      this.send({ type: "conversation.item.create", item: { type: "function_call_output", call_id: callId, output: JSON.stringify(output) } });
    }
  }

  private async dispatchTool(name: string, args: Record<string, unknown>): Promise<Record<string, unknown>> {
    switch (name) {
      case "search_catalog":
        return this.toolSearch(args);
      case "add_to_cart":
        return this.toolAdd(args);
      case "remove_from_cart":
        return this.toolRemove(args);
      case "read_cart":
        return this.toolReadCart();
      case "budget_left":
        return this.toolBudget();
      case "checkout":
        return this.toolCheckout();
      default:
        return { error: `unknown tool ${name}` };
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

  private get lang(): Lang {
    return this.lastLang ?? "en";
  }

  private async toolSearch(args: Record<string, unknown>): Promise<Record<string, unknown>> {
    const query = String(args.query ?? "").trim();
    if (!query) return { error: "query is required" };
    const result = await searchCatalog(query, (m) => this.warn("catalog", m));
    this.cache.add(result.items);
    this.ui.items(result.items, result.source);
    this.ledger.post("items_found", { query, skus: result.items.map((i) => i.sku), catalog_source: result.source });
    return { query, source: result.source, items: result.items.map(compactItem) };
  }

  /** Every cart change: the version bumps (voiding an earlier read-back), cart_updated is posted, the screen redraws. */
  private cartChanged(): { lines: CartLineView[]; total: number } {
    const summary = this.cart.summary();
    this.ledger.post("cart_updated", { cart: this.cart.priced(VOICE.merchant), version: this.cart.version });
    this.ui.cart(summary.lines, summary.total);
    return summary;
  }

  private toolAdd(args: Record<string, unknown>): Record<string, unknown> {
    const sku = String(args.sku ?? "");
    const qty = args.qty === undefined || args.qty === null ? 1 : Number(args.qty);
    const item = this.cache.get(sku);
    if (!item) return { error: `unknown sku ${sku || "(none)"}; call search_catalog and use a sku from its items` };
    const added = this.cart.add(item, qty);
    if (!added.ok) return { error: added.error };
    return { ok: true, added: { sku, qty, name: compactItem(item).name }, cart: this.cartChanged() };
  }

  private toolRemove(args: Record<string, unknown>): Record<string, unknown> {
    const sku = String(args.sku ?? "");
    const qty = args.qty === undefined || args.qty === null ? undefined : Number(args.qty);
    const removed = this.cart.remove(sku, qty);
    if (!removed.ok) return { error: removed.error, cart: this.cart.summary() };
    return { ok: true, removed: sku, qty_left: removed.qty, cart: this.cartChanged() };
  }

  /** Reads the cart back and arms the checkout gate for this cart version. */
  private toolReadCart(): Record<string, unknown> {
    const { lines, total } = this.cart.summary();
    const say = readBackSay(lines, this.cart.totalCents, this.lang);
    if (lines.length) this.gate.markRead(this.cart.version, this.userTurns);
    console.info(`[${ts()}] read-back armed for cart v${this.cart.version} at user turn ${this.userTurns}`);
    return { lines, total, say };
  }

  private async toolBudget(): Promise<Record<string, unknown>> {
    const budget = await getBudget(VOICE.mandate_id, (m) => this.warn("budget", m));
    if ("error" in budget) return { error: budget.error };
    return { ...budget, say: sayFor("budget_left", this.lang, { left: money(Math.round(budget.left * 100), this.lang) }) };
  }

  /** Checks out the cart that was read back; the model cannot pass its own list. */
  private async toolCheckout(): Promise<Record<string, unknown>> {
    if (this.cart.isEmpty) return { error: "cart_empty", say: sayFor("cart_empty", this.lang) };
    const gate = this.gate.check(this.cart.version, this.userTurns);
    if (!gate.ok) {
      console.warn(`[${ts()}] checkout held by the read-back gate: ${gate.reason}`);
      this.ui.note(`Checkout held: read-back required (${gate.reason})`, "warn");
      return {
        error: "read_back_required",
        reason: gate.reason,
        say: readBackSay(this.cart.lines(), this.cart.totalCents, this.lang),
        instruction: "Call read_cart, read its say text to the shopper, and wait for their yes before calling checkout again.",
      };
    }
    const cart = this.cart.priced(VOICE.merchant);
    const totalCents = this.cart.totalCents;
    const body = buildCheckoutBody({
      sessionId: this.sessionId,
      mandateId: VOICE.mandate_id,
      cart,
      transcript: this.shopperTexts.join(" "),
      lang: this.lastLang,
      readBack: true,
    });
    this.ledger.post("checkout_requested", { cart, read_back: true });
    this.ui.note(`Checkout: ${cart.items.map((i) => `${i.qty} x ${i.name} $${i.price.toFixed(2)}`).join(", ")} = $${cart.total.toFixed(2)}`, "tool");
    const reply = await postCheckout(body, (m) => this.warn("policy", m));
    if (typeof reply.decision === "string") {
      this.ledger.post("policy_decision", {
        decision_id: reply.decision_id,
        decision: reply.decision,
        rules: reply.rules ?? [],
        monthly_total_after: reply.monthly_total_after,
      });
      const failed = Array.isArray(reply.rules)
        ? (reply.rules as Array<{ id?: string; passed?: boolean }>).filter((r) => r && r.passed === false).map((r) => String(r.id))
        : [];
      if (failed.length) this.ui.rules(failed, String(reply.decision));
    }
    this.ui.decision(reply);
    const outcome = checkoutOutcome(reply, totalCents, this.lang);
    this.ui.outcome(outcome);
    if (outcome.status === "ordered") {
      this.cart.clear();
      this.gate.reset();
      this.cartChanged();
    } else if (outcome.status === "waiting_for_caregiver") {
      this.waitingForCaregiver = true;
    }
    return { ...outcome };
  }

  // ---------------------------------------------------------------- session resumption

  /** The socket dropped: rejoin the same conversation with a fresh token; the cart and gate live in the page. */
  private async reconnect(reason: string): Promise<void> {
    if (this.reconnecting || !this.conversationId) return;
    this.reconnecting = true;
    this.ws = null;
    this.configured = false;
    this.sessionUpdateSent = false;
    this.responseActive = false;
    this.currentResponseId = null;
    this.toolBatches.clear();
    this.outbox = this.outbox.filter((m) => m.type === "input_audio_buffer.append");
    this.generation++;
    this.player?.stop();
    this.awaitingFirstAudio = false;
    this.ui.state("connecting");
    this.ui.status(`Voice connection lost (${reason}); reconnecting and keeping the cart...`, "warn");
    for (const [attempt, delay] of RECONNECT_DELAYS_MS.entries()) {
      await new Promise((r) => setTimeout(r, delay));
      if (!this.started) break;
      try {
        const token = await fetchToken();
        this.resumed = true;
        this.sessionLang = undefined; // the language hint is re-sent after the next detected turn
        this.openSocket(token.value, this.conversationId);
        console.info(`[${ts()}] reconnect attempt ${attempt + 1}: resuming conversation ${this.conversationId}`);
        this.reconnecting = false;
        return;
      } catch (err) {
        console.warn(`[${ts()}] reconnect attempt ${attempt + 1} failed:`, err);
      }
    }
    this.reconnecting = false;
    this.ui.status("Could not reconnect the voice session. Press Start to begin again; typed input still needs a session.", "error");
    await this.stop();
  }

  /** After a resume: tell the model what the page knows (cart, read-back state) without a response. */
  private afterResume(): void {
    this.resumed = false;
    const { lines, total } = this.cart.summary();
    const state = { cart: lines, total, read_back_done: this.gate.check(this.cart.version, this.userTurns + 1).ok };
    this.send({
      type: "conversation.item.create",
      item: {
        type: "message",
        role: "system",
        content: [{ type: "text", text: `The connection dropped and was restored. Current state: ${JSON.stringify(state)}. Continue where you left off; do not greet again.` }],
      },
    });
    this.ui.status("Reconnected; the cart was kept.", "info");
    this.ui.note(`Resumed conversation ${this.conversationId} with ${lines.length} cart line(s)`, "info");
  }

  // ---------------------------------------------------------------- language

  /** First detected language (or a change): send language_hint, and the voice for that language when configured. */
  private applyLanguage(lang: Lang): void {
    if (lang === this.sessionLang || !this.configured) return;
    this.sessionLang = lang;
    void this.rateReady?.then((rate) => {
      // Always the full session: a partial session.update may not keep the tools and instructions.
      const session = buildSession(rate ?? 24000, {
        withTranscriptionModel: this.transcriptionModel,
        languageHint: languageHint(lang),
        voice: voiceFor(lang),
      });
      this.rawSend({ type: "session.update", session });
      console.info(`[${ts()}] language ${lang}: language_hint ${languageHint(lang)}, voice ${session.voice}`);
    });
  }

  // ---------------------------------------------------------------- state and warnings

  private refreshState(force?: AgentState): void {
    if (!this.started) return this.ui.state("off");
    if (force) return this.ui.state(force);
    if (!this.configured) return this.ui.state(this.pressed ? "listening" : "connecting");
    if (this.pressed) return this.ui.state("listening");
    if (this.player?.active) return this.ui.state("speaking");
    if (this.responseActive || this.continuing || this.awaitingFirstAudio) return this.ui.state("thinking");
    this.ui.state(this.waitingForCaregiver ? "waiting" : "ready");
  }

  private warn(key: string, msg: string): void {
    console.warn(`[${ts()}] ${msg}`);
    if (this.warned.has(key)) return;
    this.warned.add(key);
    this.ui.note(msg, "warn");
  }
}
