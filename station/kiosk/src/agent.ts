// Grok Voice session for the shopper station: push-to-talk capture, gapless playback, barge-in,
// rule screening in front of every tool, the search_catalog and checkout tools, refusals and the ledger.

import { Capture, MIC_CONSTRAINTS, Player, createAudioContext, type CaptureBlock } from "./audio.ts";
import { TUNNEL_HOST, VOICE, VOICE_NAME, buildSession, languageHint, realtimeUrl, voiceFor } from "./config.ts";
import * as payload from "./events.ts";
import { localReceipt, sessionUrl, type ApprovalStatus, type PlacedOrder, type Receipt } from "./receipt.ts";
import {
  Cart,
  ItemCache,
  ReadBackGate,
  buildCheckoutBody,
  checkoutOutcome,
  compactItem,
  fromCents,
  money,
  newSessionId,
  readBackSay,
  sayFor,
  toCents,
  type CartLineView,
  type CatalogItem,
  type CheckoutOutcome,
} from "./cart.ts";
import { detectLang, guessLang, type Lang } from "./lang.ts";
import { ChunkAccumulator, PcmRing, base64ToPcm16, median, pcm16ToBase64 } from "./pcm.ts";
import { ruleIds, type ScreenResult } from "./screen.ts";
import {
  Ledger,
  RelayStream,
  fetchToken,
  getApproval,
  getBudget,
  getReceipt,
  health,
  loadCachedSession,
  loadClip,
  postCheckout,
  printReceipt,
  requestReset,
  saveCachedSession,
  screenText,
  searchCatalog,
  type StreamEvent,
} from "./services.ts";

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
  /** seconds left while waiting for the caregiver, null when not waiting */
  waiting(secondsLeft: number | null): void;
  /** the receipt shown full-screen (always, even when it printed), null to close it */
  receipt(receipt: Receipt | null, note?: string, files?: { png?: string; pdf?: string }): void;
  replay(on: boolean, lang?: string): void;
  /** a reset: new session id, empty transcript, cart, outcome and banners */
  cleared(sessionId: string): void;
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
  /** Last completed transcript screened as final; Grok can send several .completed events per turn. */
  finalScreened?: string;
  /** This turn's slot in shopperTexts, so a longer transcript replaces the shorter one. */
  shopperIndex?: number;
  /** Counted toward userTurns (the read-back "yes") once it had a non-empty transcript. */
  counted?: boolean;
  /** The final transcript was sent to /screen after a partial refusal (for the policy's repeat memory). */
  finalAfterRefusal?: boolean;
}

const DEFAULT_SAY = "I can't help with that purchase on this account.";
const RECONNECT_DELAYS_MS = [500, 1000, 2000];
/** Poll the caregiver approval for up to 95 s (the policy expires it at 90 s). */
const APPROVAL_WAIT_MS = 95_000;
/** Policy records the approval before the merchant answers (the merchant checks it), so the order follows it. */
const ORDER_AFTER_APPROVAL_MS = 12_000;
/** Do not ask the relay to reset again for a reset event that our own key caused. */
const RESET_ECHO_MS = 10_000;

interface CachedEvent {
  t: number;
  kind: "shopper" | "agent_audio" | "agent_text" | "tool" | "clip";
  text?: string;
  lang?: string;
  audio?: string;
  item?: string;
  name?: string;
  args?: Record<string, unknown>;
  url?: string;
}

interface CachedSession {
  version: 1;
  lang: string;
  rate: number;
  recorded_at: string;
  events: CachedEvent[];
}

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));
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
  sessionId = newSessionId();
  ledger: Ledger;
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
  /** A response.create went out and its response.created has not arrived yet. */
  private responsePending = false;
  /** A refusal landed before that response existed: cancel it the moment it is created. */
  private cancelWhenCreated = false;
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
  /** Where the current request starts in shopperTexts; moves past each refusal and checkout. */
  private requestStart = 0;
  private lastLang: Lang | undefined;
  private cache = new ItemCache();
  private cart = new Cart();
  private gate = new ReadBackGate();
  /** Committed shopper turns (voice commits and typed messages); a turn after read_cart is the "yes". */
  private userTurns = 0;
  private waitingForCaregiver = false;
  private sessionLang: Lang | undefined;
  /** The order the station placed last, for a receipt when the merchant's is unavailable. */
  private lastOrder: PlacedOrder | null = null;
  private receiptsDone = new Set<string>();
  private lastReceipt: Receipt | null = null;
  private approvalWait: { id: string } | null = null;
  private stream: RelayStream | null = null;
  private lastLocalReset = -Infinity;
  /** Recording of this session (audio, transcripts, tool calls) that can be saved as a cached session. */
  private rec: { t0: number; events: CachedEvent[] } = { t0: performance.now(), events: [] };
  private replaying: { gen: number } | null = null;
  /** Set by the Host's "Arm replay" (relay event replay_armed); consumed by the next press. */
  private replayArmed = false;
  private replayGen = 0;
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
    this.stream = new RelayStream(["paid", "reset", "replay_armed"], (ev) => this.onStreamEvent(ev), (m) => this.warn("stream", m));
    void this.stream.open();
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
    this.stream?.close();
    this.stream = null;
    this.cancelApprovalWait("station stopped");
    this.stopReplay();
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
    if (msg.type === "response.create") this.responsePending = true;
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
    if (this.replaying) this.stopReplay();
    if (this.replayArmed) {
      this.replayArmed = false;
      void this.replay(this.lang);
      return;
    }
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
    // A new request cancels a caregiver wait; the gate needs a fresh read-back anyway.
    this.cancelApprovalWait("the shopper spoke again");
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
    this.cancelApprovalWait("the shopper typed again");
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
    // Only a turn with words counts as the read-back "yes"; an empty or noise press does not arm checkout.
    if (!turn.counted && text.trim()) {
      turn.counted = true;
      this.userTurns++;
    }
    this.record({ kind: "shopper", text, lang: turn.lang });
    if (turn.shopperIndex === undefined) {
      turn.shopperIndex = this.shopperTexts.push(text) - 1;
    } else if (turn.shopperIndex >= this.requestStart) {
      this.shopperTexts[turn.shopperIndex] = text;
    } // else the turn is already behind a refusal or checkout and stays out of the next request
    const langLabel = turn.lang ? `${turn.lang}${langSource === "guess" ? "?" : ""}` : "?";
    console.log(`%c[${ts()}] SHOPPER (${langLabel}): ${text}`, "color:#0a7;font-weight:bold");
    this.ui.transcript("shopper", key, text, true, langLabel);
    this.ledger.post("heard", payload.heard("shopper", text, turn.lang, turn.itemId ?? `${this.sessionId}-${key}`));
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
    if (turn.screenSettled) {
      // Only a refusal of a longer transcript may replace a settled result; anything else is stale.
      if (result?.action !== "refuse" || turn.screenResult?.action === "refuse") return;
      turn.screenResult = result;
    } else {
      turn.screenSettled = true;
      turn.screenResult = result;
      turn.screen.resolve(result);
    }
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
    // Policy screens the checkout transcript again; a refused request must not ride along into the next one.
    this.requestStart = this.shopperTexts.length;
    this.ledger.post("refusal", payload.refusal(ids, result.refusal?.spoken_key ?? "refusal", result.refusal?.lang ?? turn.lang, via));
    this.ui.note(`Refused (${ids.join(", ")}) via ${via}`, "rule");
  }

  /** Refusal without the model: cancel what it is saying, then play the clip or speak the text verbatim. */
  private async refuseOutOfBand(turn: Turn, result: ScreenResult): Promise<void> {
    if (turn.refusal !== "none") return;
    turn.refusal = "out_of_band";
    this.generation++;
    const waitingForAudio = this.awaitingFirstAudio;
    // The reply requested on release may not exist yet, so bargeIn has nothing to cancel: catch it on creation.
    if (this.responsePending) this.cancelWhenCreated = true;
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
        this.ledger.post("heard", payload.heard("agent", say, (result.refusal?.lang as Lang | undefined) ?? turn.lang, `${this.sessionId}-refusal-${turn.n}`));
        this.record({ kind: "clip", url, text: say });
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
        // Grok may complete a turn several times with a longer transcript each time ("मेरे..." then the whole
        // sentence), so screen every new version; a later refusal overrides an earlier proceed.
        if (turn.finalScreened !== text && turn.screenResult?.action !== "refuse") {
          turn.finalScreened = text;
          void screenText(this.sessionId, text, turn.lang, (m) => this.warn("screen", m)).then((r) => this.settleScreen(turn, r));
        } else if (turn.screenResult?.action === "refuse" && !turn.finalAfterRefusal) {
          // Refused on a partial: the policy's repeat-attempt memory counts only final (partial: false)
          // transcripts, so send this one once and ignore the answer.
          turn.finalAfterRefusal = true;
          void screenText(this.sessionId, text, turn.lang, (m) => this.warn("screen", m));
        }
        break;
      }

      case "response.created": {
        this.responseActive = true;
        this.currentResponseId = ev.response?.id ?? null;
        const refusedFirst = this.cancelWhenCreated && this.responsePending;
        this.responsePending = false;
        this.cancelWhenCreated = false;
        if ((this.pressed || refusedFirst) && this.currentResponseId) {
          // Requested before the button went down, or overtaken by a rule refusal; never speak over either.
          this.cancelled.add(this.currentResponseId);
          this.send({ type: "response.cancel" });
        }
        this.refreshState();
        break;
      }

      case "response.output_audio.delta": {
        if (this.cancelled.has(ev.response_id) || !this.player || typeof ev.delta !== "string") break;
        this.markFirstAudio(this.pendingForce ? "refusal speech" : "model");
        this.player.enqueue(base64ToPcm16(ev.delta), ev.item_id);
        this.record({ kind: "agent_audio", audio: ev.delta, item: ev.item_id });
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
        this.ledger.post("heard", payload.heard("agent", text, this.lastLang, key));
        this.record({ kind: "agent_text", text, item: key });
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
      this.record({ kind: "tool", name, args });
      const screen = await this.awaitScreen(turn);
      if (this.cancelled.has(responseId)) {
        // The shopper pressed during the screen wait (up to 1.5 s): nothing may run, least of all a checkout.
        output = { cancelled: true, note: "The shopper interrupted before this ran. Nothing was done." };
      } else if (screen?.action === "refuse") {
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
    this.ledger.post("items_found", payload.itemsFound(query, result.items, result.source));
    return { query, source: result.source, items: result.items.map(compactItem) };
  }

  /** Every cart change: the version bumps (voiding an earlier read-back), cart_updated is posted, the screen redraws. */
  private cartChanged(): { lines: CartLineView[]; total: number } {
    const summary = this.cart.summary();
    this.ledger.post("cart_updated", payload.cartUpdated(summary.lines, summary.total));
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
    // One read-back buys one checkout: consume it before the await so a parallel or repeated call is held.
    this.gate.reset();
    const cart = this.cart.priced(VOICE.merchant);
    const totalCents = this.cart.totalCents;
    const transcript = this.shopperTexts.slice(this.requestStart).join(" ");
    this.requestStart = this.shopperTexts.length;
    const body = buildCheckoutBody({
      sessionId: this.sessionId,
      mandateId: VOICE.mandate_id,
      cart,
      transcript,
      lang: this.lastLang,
      readBack: true,
    });
    this.ledger.post("checkout_requested", payload.checkoutRequested(cart.total));
    this.ui.note(`Checkout: ${cart.items.map((i) => `${i.qty} x ${i.name} $${i.price.toFixed(2)}`).join(", ")} = $${cart.total.toFixed(2)}`, "tool");
    const reply = await postCheckout(body, (m) => this.warn("policy", m));
    if (typeof reply.decision === "string") {
      // policy_decision is posted by the policy service itself.
      const failed = Array.isArray(reply.rules)
        ? (reply.rules as Array<{ id?: string; passed?: boolean }>).filter((r) => r && r.passed === false).map((r) => String(r.id))
        : [];
      if (failed.length) this.ui.rules(failed, String(reply.decision));
    }
    this.ui.decision(reply);
    const outcome = checkoutOutcome(reply, totalCents, this.lang);
    this.ui.outcome(outcome);
    const lines = this.cart.lines();
    if (outcome.status === "ordered" && outcome.order_id) {
      this.lastOrder = { order_id: outcome.order_id, decision_id: outcome.decision_id, lines, totalCents, lang: this.lang };
      this.cart.clear();
      this.cartChanged();
    } else if (outcome.status === "waiting_for_caregiver") {
      this.waitingForCaregiver = true;
      if (outcome.approval_id) this.waitForApproval(outcome.approval_id, totalCents, lines, outcome.decision_id);
    }
    return { ...outcome };
  }

  // ---------------------------------------------------------------- caregiver approval

  /** Polls the approval every second; the button stays live, and a new request cancels the wait. */
  private waitForApproval(approvalId: string, totalCents: number, lines: CartLineView[], decisionId?: string): void {
    const wait = { id: approvalId };
    this.approvalWait = wait;
    const deadline = performance.now() + APPROVAL_WAIT_MS;
    let approvedAt = 0;
    console.info(`[${ts()}] waiting for the caregiver on ${approvalId}`);
    const tick = async (): Promise<void> => {
      if (this.approvalWait !== wait || !this.started) return;
      this.ui.waiting(Math.max(0, Math.ceil((deadline - performance.now()) / 1000)));
      const status = await getApproval(approvalId);
      if (this.approvalWait !== wait) return;
      if (status?.state === "approved" && !status.order_id) {
        // Approved, and policy is still getting the order from the merchant: keep polling a little longer.
        approvedAt ||= performance.now();
        if (performance.now() - approvedAt < ORDER_AFTER_APPROVAL_MS) {
          setTimeout(() => void tick(), 1000);
          return;
        }
      }
      if (status && status.state !== "pending") return this.finishApproval(status, totalCents, lines, decisionId);
      if (performance.now() >= deadline) return this.finishApproval({ approval_id: approvalId, state: "expired" }, totalCents, lines, decisionId);
      setTimeout(() => void tick(), 1000);
    };
    void tick();
  }

  private cancelApprovalWait(reason: string): void {
    this.waitingForCaregiver = false;
    if (!this.approvalWait) return;
    console.info(`[${ts()}] caregiver wait cancelled: ${reason}`);
    this.approvalWait = null;
    this.ui.waiting(null);
  }

  private finishApproval(status: ApprovalStatus, totalCents: number, lines: CartLineView[], decisionId?: string): void {
    this.approvalWait = null;
    this.waitingForCaregiver = false;
    this.ui.waiting(null);
    const lang = this.lang;
    const total = money(totalCents, lang);
    console.info(`[${ts()}] caregiver answer: ${status.state}${status.order_id ? ` (order ${status.order_id})` : ""}`);
    if (status.state === "approved" && status.order_id) {
      this.lastOrder = { order_id: status.order_id, decision_id: decisionId, lines, totalCents, lang };
      this.cart.clear();
      this.cartChanged();
      const say = sayFor("caregiver_approved", lang, { total });
      this.ui.outcome({ status: "ordered", say_key: "caregiver_approved", say, total: fromCents(totalCents), decision_id: decisionId, order_id: status.order_id });
      void this.speakFixed(say);
    } else if (status.state === "approved") {
      const say = sayFor("checkout_unavailable", lang);
      this.ui.outcome({ status: "error", say_key: "checkout_unavailable", say, decision_id: decisionId, error: "approved but no order was placed" });
      void this.speakFixed(say);
    } else if (status.state === "rejected") {
      const say = status.message ?? sayFor("caregiver_declined", lang);
      this.ui.outcome({ status: "declined", say_key: "caregiver_declined", say, decision_id: decisionId });
      void this.speakFixed(say);
    } else {
      const say = sayFor("caregiver_timeout", lang);
      this.ui.outcome({ status: "declined", say_key: "caregiver_timeout", say, decision_id: decisionId });
      void this.speakFixed(say);
    }
    this.refreshState();
  }

  /** A fixed line, spoken verbatim (force_message) once the shopper and the model are quiet; never over them. */
  private async speakFixed(text: string): Promise<void> {
    if (this.replaying) return; // the recording carries its own audio
    const t0 = performance.now();
    while ((this.pressed || this.responseActive || this.player?.active) && performance.now() - t0 < 8000) await sleep(150);
    if (!this.started || this.pressed || !this.configured) return; // the screen still shows it
    this.speakVerbatim(text);
  }

  // ---------------------------------------------------------------- paid -> receipt, reset (relay stream)

  private onStreamEvent(ev: StreamEvent): void {
    if (ev.type === "paid" && ev.session_id === this.sessionId && typeof ev.order_id === "string") {
      void this.onPaid(ev.order_id, typeof ev.t === "number" ? ev.t : Date.now());
    } else if (ev.type === "reset") {
      if (performance.now() - this.lastLocalReset < RESET_ECHO_MS) return; // our own reset coming back
      void this.resetSession("reset from the relay", false);
    } else if (ev.type === "replay_armed") {
      // The Host's "Arm replay": the next button press plays the recorded session instead of talking live.
      this.replayArmed = true;
      this.ui.note("Replay armed: the next press plays the recorded session.", "warn");
    }
  }

  /** paid for this session: fetch the receipt (or build it), show it, print it, say so. */
  private async onPaid(orderId: string, paidAt: number): Promise<void> {
    if (this.receiptsDone.has(orderId)) return;
    this.receiptsDone.add(orderId);
    const order = this.lastOrder?.order_id === orderId ? this.lastOrder : null;
    const lang = order?.lang ?? this.lang;
    const url = sessionUrl(TUNNEL_HOST, this.sessionId);
    let receipt = await getReceipt(orderId, lang);
    if (!receipt && order) receipt = localReceipt(order, url, paidAt);
    if (!receipt) {
      this.warn("receipt", `paid ${orderId}, but no receipt is available (merchant receipt endpoint down and no local order record)`);
      return;
    }
    if (!receipt.session_url && url) receipt = { ...receipt, session_url: url };
    this.lastReceipt = receipt;
    console.info(`[${ts()}] paid ${orderId}: printing the receipt`);
    this.ui.receipt(receipt, "Preparing your receipt...");
    const printed = await printReceipt(receipt);
    const onPaper = printed.ok && printed.via === "printer";
    const files = { png: printed.pngUrl, pdf: printed.pdfUrl };
    this.ui.receipt(
      receipt,
      onPaper ? `Printed in ${(printed.ms / 1000).toFixed(1)} s` : printed.ok ? "" : `Receipt on screen (${printed.reason ?? "no print helper"})`,
      printed.ok ? files : undefined,
    );
    this.ledger.post("receipt_printed", payload.receiptPrinted(orderId, onPaper ? "printer" : "screen", printed.ok && printed.via === "pdf"));
    const totalText = money(toCents(receipt.total), receipt.lang);
    void this.speakFixed(sayFor(onPaper ? "receipt_done" : "receipt_on_screen", receipt.lang, { total: totalText }));
  }

  /** The on-screen Reprint button. */
  async reprint(): Promise<void> {
    if (!this.lastReceipt) return;
    this.ui.receipt(this.lastReceipt, "Preparing your receipt...");
    const printed = await printReceipt(this.lastReceipt);
    const onPaper = printed.ok && printed.via === "printer";
    this.ui.receipt(this.lastReceipt, onPaper ? "Printed again" : printed.ok ? "" : `Receipt on screen (${printed.reason ?? "no print helper"})`,
      printed.ok ? { png: printed.pngUrl, pdf: printed.pdfUrl } : undefined);
    if (onPaper) this.ledger.post("receipt_printed", payload.receiptPrinted(this.lastReceipt.order_id, "printer"));
  }

  /**
   * A fresh session for the next shopper: new session id, empty cart, gate, transcript and screen, and a new
   * voice conversation so the model does not carry the last shopper's history. `fanOut` also asks the relay
   * to reset policy, merchant and the live ledger (the relay then posts a reset event, ignored as our echo).
   */
  async resetSession(reason: string, fanOut: boolean): Promise<void> {
    const t0 = performance.now();
    this.lastLocalReset = t0;
    const relay = fanOut ? requestReset() : Promise.resolve(true);
    this.cancelApprovalWait("reset");
    this.stopReplay();
    this.bargeIn("reset");
    this.sessionId = newSessionId();
    this.ledger = new Ledger(this.sessionId, VOICE.mandate_id, (m) => this.warn("ledger", m));
    this.cart = new Cart();
    this.gate.reset();
    this.cache = new ItemCache();
    this.userTurns = 0;
    this.turns = [];
    this.shopperTexts = [];
    this.requestStart = 0;
    this.lastOrder = null;
    this.lastReceipt = null;
    this.deferredRefusal = null;
    this.pendingForce = null;
    this.waitingForCaregiver = false;
    this.rec = { t0: performance.now(), events: [] };
    this.ui.cleared(this.sessionId);
    console.info(`[${ts()}] RESET (${reason}): new session ${this.sessionId}`);
    if (this.started) {
      // A new conversation: close the old socket first (tier 0 allows 10 concurrent sessions).
      const ws = this.ws;
      this.ws = null;
      this.conversationId = null;
      this.configured = false;
      this.sessionUpdateSent = false;
      this.sessionLang = undefined;
      this.responseActive = false;
      this.currentResponseId = null;
      this.toolBatches.clear();
      this.outbox = [];
      if (ws && ws.readyState <= WebSocket.OPEN) ws.close(1000, "reset");
      try {
        this.openSocket((await fetchToken()).value);
      } catch (err) {
        this.ui.status(`Reset done, but the voice session could not restart: ${err instanceof Error ? err.message : err}`, "error");
      }
    }
    const relayOk = await relay;
    const ms = Math.round(performance.now() - t0);
    this.ui.note(`Reset in ${ms} ms${fanOut ? (relayOk ? " (relay reset policy, merchant and ledger)" : " (relay reset FAILED; spend and orders may be stale)") : ""}`, relayOk ? "info" : "error");
    this.refreshState();
  }

  // ---------------------------------------------------------------- cached sessions

  private record(ev: Omit<CachedEvent, "t">): void {
    if (this.replaying) return;
    this.rec.events.push({ t: Math.round(performance.now() - this.rec.t0), ...ev });
  }

  /** Saves this session's recording as the cached session for its language. */
  async saveRecording(): Promise<void> {
    const lang = this.lang;
    const events = this.rec.events;
    if (!events.some((e) => e.kind === "agent_audio")) {
      this.ui.note("Nothing to save yet: record a full session first.", "warn");
      return;
    }
    const start = events[0].t;
    const session: CachedSession = {
      version: 1,
      lang,
      rate: this.rate || 24000,
      recorded_at: new Date().toISOString(),
      events: events.map((e) => ({ ...e, t: e.t - start })),
    };
    const saved = await saveCachedSession(lang, session);
    this.ui.note(saved.ok ? `Saved the cached ${lang} session (${events.length} events) in ${saved.where}.` : `Could not save the cached session: ${saved.error}`, saved.ok ? "info" : "error");
  }

  /**
   * Replays a cached session through the same player and screen, labelled REPLAY. The voice is the recording,
   * but the actions are live: the rule screen, the cart tools and checkout go to the real services again.
   */
  async replay(lang: Lang): Promise<void> {
    const loaded = (await loadCachedSession(lang)) as CachedSession | null;
    if (!loaded || !Array.isArray(loaded.events)) {
      this.ui.note(`No cached ${lang} session. Record one and press Ctrl+Shift+S.`, "warn");
      return;
    }
    if (!this.ctx || !this.player) {
      const ctx = createAudioContext(VOICE.capture.preferred_sample_rate);
      void ctx.resume();
      this.ctx = ctx;
      this.player = new Player(ctx);
      this.player.onIdle = () => this.refreshState();
    }
    this.cancelApprovalWait("replay");
    this.bargeIn("replay");
    const run = { gen: ++this.replayGen };
    this.replaying = run;
    this.ledger.replay = true;
    this.ui.replay(true, lang);
    this.lastLang = lang as Lang;
    console.info(`[${ts()}] REPLAY ${lang}: ${loaded.events.length} events`);
    const t0 = performance.now();
    let tools: Promise<unknown> = Promise.resolve();
    let n = 0;
    for (const ev of loaded.events) {
      const wait = ev.t - (performance.now() - t0);
      if (wait > 0) await sleep(wait);
      if (this.replaying !== run) return;
      if (ev.kind === "agent_audio" && ev.audio && this.player) {
        this.player.enqueue(base64ToPcm16(ev.audio), ev.item, loaded.rate);
      } else if (ev.kind === "agent_text" && ev.text) {
        this.ui.transcript("agent", `replay-${ev.item ?? n}`, ev.text, true, lang);
        this.ledger.post("heard", payload.heard("agent", ev.text, lang as Lang, `${this.sessionId}-replay-${ev.item ?? n}`));
      } else if (ev.kind === "shopper" && ev.text) {
        const turn = this.newTurn("text");
        turn.text = ev.text;
        turn.lang = lang as Lang;
        this.recordShopper(turn, ev.text, `replay-shopper-${turn.n}`, "guess");
        const text = ev.text;
        tools = tools.then(async () => {
          const result = await screenText(this.sessionId, text, lang as Lang, (m) => this.warn("screen", m));
          if (result?.action === "refuse") {
            this.ui.rules(ruleIds(result), "refuse", result.refusal?.text);
            this.logRefusal(turn, result, "replay");
          }
        });
      } else if (ev.kind === "tool" && ev.name) {
        const name = ev.name;
        const args = ev.args ?? {};
        tools = tools.then(() => this.dispatchTool(name, args)).then((out) => console.info(`[${ts()}] REPLAY TOOL ${name}:`, out));
      } else if (ev.kind === "clip" && ev.url && this.ctx && this.player) {
        const clip = await loadClip(this.ctx, ev.url, 1500, (m) => this.warn(`clip:${ev.url}`, m));
        if (clip && this.replaying === run) void this.player.playClip(clip);
        if (ev.text) this.ui.transcript("agent", `replay-clip-${n}`, ev.text, true, lang);
      }
      n++;
    }
    await tools;
    await this.player?.drained();
    if (this.replaying === run) this.stopReplay();
  }

  stopReplay(): void {
    if (!this.replaying) return;
    this.replaying = null;
    this.ledger.replay = false;
    this.player?.stop();
    this.ui.replay(false);
  }

  get isReplaying(): boolean {
    return this.replaying !== null;
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
