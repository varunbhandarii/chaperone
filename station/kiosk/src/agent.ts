// Grok Voice session for the shopper station: push-to-talk capture, gapless playback, barge-in,
// rule screening in front of every tool, the search_catalog and checkout tools, refusals and the ledger.

import { Capture, MIC_CONSTRAINTS, Player, createAudioContext, type CaptureBlock } from "./audio.ts";
import { Earcon } from "./earcon.ts";
import { TUNNEL_HOST, VOICE, VOICE_NAME, buildSession, languageHint, realtimeUrl, storeName, voiceFor } from "./config.ts";
import * as payload from "./events.ts";
import { localReceipt, placedOrders, sessionUrl, storeOrders, type ApprovalStatus, type PlacedOrder, type Receipt, type ReceiptNote } from "./receipt.ts";
import {
  Cart,
  ItemCache,
  ReadBackGate,
  buildCheckoutBody,
  checkoutOutcome,
  compactItem,
  fromCents,
  hasSay,
  isBill,
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
import { collapseShopperTurns } from "./recording.ts";
import { RefundGate, historySummary, isRepeatRequest, spokenCode, statusWords, type RefundReply, type RefundTarget } from "./postpurchase.ts";
import { billItem, billSayKey, billerId, scamToolOutput, spokenDate, yesOrNo, type ScamVerdict } from "./guards.ts";
import { NOTICE_WORDS, THE_STORE } from "./words.ts";
import {
  Ledger,
  RelayStream,
  cancelApproval,
  cancelOrder,
  fetchToken,
  getApproval,
  getBill,
  getBudget,
  getGuardState,
  getMandate,
  getHistory,
  getMandateBillers,
  getOrder,
  getReceipt,
  health,
  loadCachedSession,
  loadClip,
  postCheckout,
  postCosign,
  postRefund,
  printReceipt,
  requestReset,
  scamCheck,
  saveCachedSession,
  screenText,
  searchCatalog,
  type PrintResult,
  type StreamEvent,
} from "./services.ts";

export type AgentState = "off" | "connecting" | "ready" | "listening" | "thinking" | "checking" | "speaking" | "waiting";
export type NoteKind = "info" | "tool" | "warn" | "error" | "rule";
/** a small word after a transcript line, in Ruth's language */
export type TranscriptMark = "interrupted" | "nothing_heard";

/** The full-screen Protected card: a stopped scam, a scam refusal or a declined swipe. */
export interface ProtectedView {
  tone: "protected" | "care";
  say: string;
  action?: string;
  title?: string;
  /** operator view only (reason keys, rule ids) */
  detail?: string;
  lang?: Lang;
}

export interface AgentUI {
  state(state: AgentState): void;
  /** Ruth's language: the state words and labels follow it */
  language(lang: Lang): void;
  /** the full-screen Protected card, null to dismiss it */
  protect(view: ProtectedView | null): void;
  /** a calm banner under the strip (the card's cool-down, a pause), null to remove it */
  banner(key: string, text: string | null): void;
  /** release -> first sound (the earcon or the first word) */
  firstSound(ms: number, via: string): void;
  status(text: string, kind?: NoteKind): void;
  transcript(role: "shopper" | "agent", key: string, text: string, final: boolean, lang?: string, mark?: TranscriptMark): void;
  note(text: string, kind?: NoteKind): void;
  /** stats covers voice turns answered by the model only; `label` is set for typed or refusal-clip timings. */
  latency(ms: number, stats: { min: number; median: number; count: number } | null, label: string): void;
  rules(ids: string[], action: string, say?: string): void;
  items(items: CatalogItem[], source: string): void;
  decision(result: Record<string, unknown>): void;
  cart(lines: CartLineView[], total: number): void;
  outcome(outcome: CheckoutOutcome): void;
  /** after payment: a cancel or refund result in the outcome banner */
  notice(title: string, detail: string, tone: "ok" | "warn" | "bad", devDetail?: string): void;
  /** seconds left while waiting for the caregiver, null when not waiting */
  waiting(secondsLeft: number | null): void;
  /** the receipt shown full-screen (always, even when it printed), null to close it */
  receipt(receipt: Receipt | null, note?: ReceiptNote, files?: { png?: string; pdf?: string }): void;
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
  /** /scam-check already ran for this turn (the station's own, or the model's scam_check tool): never twice. */
  scamChecked?: boolean;
  /** the final (partial: false) screen of this turn's transcript */
  finalScreen?: Deferred<ScreenResult | null>;
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
  repeated?: boolean;
}

/** When the rule screen's refusal comes without words of its own. */
const DEFAULT_SAY: Record<Lang, string> = {
  en: "I can't help with that purchase on this account.",
  es: "No puedo ayudarle con esa compra en esta cuenta.",
  hi: "इस खाते से मैं यह खरीदारी नहीं कर सकती।",
};
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
  turn?: number;
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

/** Every event the station follows on the relay's stream. */
const STREAM_TYPES = ["paid", "reset", "replay_armed", "card_decision", "card_hold_released", "risk_changed", "mandate_paused", "mandate_resumed", "mandate_signed"];
const GUARD_EVENTS = new Set(["card_decision", "card_hold_released", "risk_changed", "mandate_paused", "mandate_resumed", "mandate_signed"]);

/** A declined swipe's reason -> the line Ruth hears. */
const CARD_LINES: Record<string, string> = {
  card_blocked_category: "card_declined_blocked",
  card_cooldown: "card_declined_cooldown",
  card_over_cap: "card_declined_over_cap",
  card_unusual_amount: "card_declined_unusual",
  card_atm_cap: "card_declined_atm",
};

const PAUSED_BANNER: Record<Lang, string> = {
  en: "Priyank has paused shopping for now",
  es: "Priyank pausó las compras por ahora",
  hi: "प्रियंक ने अभी खरीदारी रोक रखी है",
};

/** "Extra care on your card until 9:05 PM tomorrow", in Ruth's language. */
function cooldownBanner(until: Date, lang: Lang): string {
  const locale = { en: "en-US", es: "es-MX", hi: "hi-IN" }[lang];
  const time = new Intl.DateTimeFormat(locale, { hour: "numeric", minute: "2-digit" }).format(until);
  const tomorrow = until.toDateString() !== new Date().toDateString();
  if (lang === "es") return `Cuidado extra con su tarjeta hasta las ${time}${tomorrow ? " de mañana" : ""}`;
  if (lang === "hi") return `${tomorrow ? "कल " : ""}${time} तक आपके कार्ड का ख़ास ध्यान`;
  return `Extra care on your card until ${time}${tomorrow ? " tomorrow" : ""}`;
}

/** A card terminal's descriptor ("FIVE POINTS DRUG") or a registry id, as Ruth would say the store's name. */
function displayStore(raw: string, lang: Lang): string {
  const named = storeName(raw);
  if (named) return named;
  if (raw && raw === raw.toUpperCase()) return raw.toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase());
  return raw || THE_STORE[lang];
}

/** The scam check's first useful action, as a short instruction. */
function actionWords(actions: string[], lang: Lang): string {
  for (const a of actions) {
    if (a === "hang_up") return { en: "Please hang up.", es: "Por favor cuelgue.", hi: "कृपया फ़ोन रख दीजिए।" }[lang];
    if (a === "do_not_pay") return { en: "Don't pay anyone.", es: "No le pague a nadie.", hi: "किसी को पैसे न दें।" }[lang];
    if (a.startsWith("call_trusted")) {
      const who = a.split(":")[1] || "Priyank";
      return { en: `Call ${who} on the number you know.`, es: `Llame a ${who} al número de siempre.`, hi: `${who} को उनके पुराने नंबर पर फ़ोन कीजिए।` }[lang];
    }
    if (a === "call_priya") return { en: "Call Priyank.", es: "Llame a Priyank.", hi: "प्रियंक को फ़ोन कीजिए।" }[lang];
  }
  return "";
}

/** The signed rules in plain words, for Ruth: "up to $60 a trip at your 4 stores, $300 a month, …". */
function rulesInWords(m: Record<string, unknown>, lang: Lang): string {
  const n = (v: unknown) => (typeof v === "number" ? v : Number(v));
  const cap = money(toCents(n(m.per_purchase_cap)), lang);
  const month = money(toCents(n(m.monthly_cap)), lang);
  const ask = money(toCents(n(m.approval_threshold)), lang);
  const stores = Array.isArray(m.allowed_merchants) ? m.allowed_merchants.length : 1;
  if (lang === "es") return `hasta ${cap} por compra en sus ${stores} tiendas, ${month} al mes, y le pregunto a Priyank arriba de ${ask}; nunca tarjetas de regalo, giros ni cripto`;
  if (lang === "hi") return `आपकी ${stores} दुकानों पर एक बार में ${cap} तक, महीने में ${month}, ${ask} से ऊपर प्रियंक से पूछूँगी; गिफ्ट कार्ड, वायर या क्रिप्टो कभी नहीं`;
  return `up to ${cap} a trip at your ${stores} stores, ${month} a month, and I ask Priyank above ${ask}; never gift cards, wires or crypto`;
}
const SHORT_AUDIO_MS = 100;
const MAX_OUTBOX = 1200;

function ts(): string {
  return new Date().toISOString().slice(11, 23);
}

/** "ready for pickup" -> "Ready for pickup", for a title. */
function capitalized(text: string): string {
  return text.charAt(0).toLocaleUpperCase() + text.slice(1);
}

/** Whether the receipt printed, for the line under it; the time it took, or why not, is for the operator view. */
function printNote(printed: PrintResult, onPaper: "printed" | "printed_again"): ReceiptNote {
  if (printed.ok && printed.via === "printer") return { key: onPaper, detail: `printed in ${(printed.ms / 1000).toFixed(1)} s` };
  if (printed.ok) return { detail: `saved as PDF${printed.reason ? ` (${printed.reason})` : ""}` };
  return { key: "on_screen", detail: `not printed: ${printed.reason ?? "no print helper"}` };
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
  /** Each store's order placed in this session, by order id, for a receipt when the merchant's is unavailable. */
  private placed = new Map<string, PlacedOrder>();
  private receiptsDone = new Set<string>();
  private lastReceipt: Receipt | null = null;
  private approvalWait: { id: string; totalCents: number; lines: CartLineView[]; decisionId?: string } | null = null;
  private stream: RelayStream | null = null;
  private lastLocalReset = -Infinity;
  /** Recording of this session (audio, transcripts, tool calls) that can be saved as a cached session. */
  private rec: { t0: number; events: CachedEvent[] } = { t0: performance.now(), events: [] };
  private replaying: { gen: number } | null = null;
  /** Set by the Host's "Arm replay" (relay event replay_armed); consumed by the next press. */
  private replayArmed = false;
  /** Responses whose line the station already played as a clip, with that line: added to the history after the
   *  tool outputs, and no response.create follows. */
  private silentResponses = new Map<string, string>();
  /** Orders placed in this session, oldest first: order_status, cancel_order and request_refund default to the last. */
  private sessionOrders: string[] = [];
  private refundGate = new RefundGate();
  private refundPreview: { amount: number; last4?: string } | null = null;
  /** The last line the shopper heard (model, clip or fixed line), for "repeat that". */
  private lastSpoken: string | null = null;
  /** The soft tick while tools, the rule screen or a slow reply run; stops the moment any audio plays. */
  private earcon: Earcon | null = null;
  private checking = false;
  private toolsRunning = 0;
  /** station work with no model reply running (the scam check it asks for itself) keeps the tick going */
  private stationBusy = 0;
  /** Priyank paused shopping (policy declines every checkout with agent_paused) */
  private paused = false;
  /** the cool-down already announced, so cooldown_on is said once per cool-down */
  private cooldownSaid: string | null = null;
  /** after the station read Ruth her new rules: her next turn answers "Do you agree?"; `hash` names the rules she heard */
  private cosignPending: { until: number; hash?: string } | null = null;
  /** bumps on every reading of new rules: only the latest reading waits for her answer */
  private cosignAsk = 0;
  private slowWaitTimer: ReturnType<typeof setTimeout> | null = null;
  /** release (or send) -> first sound, the earcon included; the meter's figure is release -> first word */
  private firstSoundPending = false;
  private soundLatencies: number[] = [];
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
    this.earcon = this.makeEarcon(ctx);

    this.rateReady = this.setupCapture(ctx, micDeviceId);
    void health.start();
    if (!this.stream) {
      this.stream = new RelayStream(STREAM_TYPES, (ev) => this.onStreamEvent(ev), (m) => this.warn("stream", m));
      void this.stream.open();
    }
    void this.loadGuardState();
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
    this.endChecking();
    this.capture?.close();
    const ctx = this.ctx;
    this.ctx = null;
    this.capture = null;
    this.player = null;
    this.earcon = null;
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
    this.ui.protect(null); // the next press dismisses the Protected card
    this.pressed = true;
    this.pressStart = performance.now();
    this.appendedSamples = 0;
    this.generation++;
    this.bargeIn("button pressed");
    this.endChecking();

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
    this.armSlowWait();
    if (turn) {
      turn.pressed = false;
      // Placeholder keeps the shopper's line above the agent's reply; transcription events fill it in.
      if (!turn.text) this.ui.transcript("shopper", `voice-${turn.n}`, "...", false);
    }
    console.info(`[${ts()}] released after ${Math.round(heldMs)} ms; committed ${Math.round((this.appendedSamples / this.rate) * 1000)} ms of audio`);

    if (turn && turn.screenResult?.action === "scam_check") {
      this.deferredRefusal = null;
      void this.scamCheckOutOfBand(turn, turn.screenResult);
    } else if (turn && turn.screenResult?.action === "refuse") {
      this.deferredRefusal = null;
      void this.refuseAfterFinal(turn, turn.screenResult);
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
    if (deferred.result.action === "scam_check") void this.scamCheckOutOfBand(deferred.turn, deferred.result);
    else void this.refuseOutOfBand(deferred.turn, deferred.result);
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
    this.endChecking();
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
    // "repeat that" replays the last line in its own language; it does not switch the session's language
    const repeat = isRepeatRequest(text);
    if (turn.lang && !repeat) this.lastLang = turn.lang;
    if (this.lastLang) this.ui.language(this.lastLang);
    this.recordShopper(turn, text, `text-${turn.n}`, "guess");
    if (repeat) {
      this.repeatLast(turn);
      return;
    }
    if (this.answerCosign(turn, text)) return;
    this.send({ type: "conversation.item.create", item: { type: "message", role: "user", content: [{ type: "input_text", text }] } });
    if (turn.lang) this.applyLanguage(turn.lang);
    this.tRelease = performance.now();
    this.latencyKind = "typed";
    this.awaitingFirstAudio = true;
    this.armSlowWait();

    this.stationBusy++; // the tick plays while the rule screen reads a typed line too
    const result = await screenText(this.sessionId, text, turn.lang, (m) => this.warn("screen", m)).finally(() => this.stationBusy--);
    this.settleScreen(turn, result);
    if (result?.action === "refuse" || result?.action === "scam_check") return; // settleScreen answered it
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
      finalScreen: deferred<ScreenResult | null>(),
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
    if (!turn.counted && text.trim() && !isRepeatRequest(text)) {
      turn.counted = true;
      this.userTurns++;
      // Words alone ("okay", "thank you") keep the caregiver wait: only a cart change closes the approval.
    }
    this.record({ kind: "shopper", text, lang: turn.lang, turn: turn.n });
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
    const stops = (a?: string) => a === "refuse" || a === "scam_check";
    if (turn.screenSettled) {
      // Only a refusal or a scam story in a longer transcript may replace a settled result; anything else is stale.
      if (!stops(result?.action) || stops(turn.screenResult?.action)) return;
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
    } else if (result.action === "scam_check") {
      // Ruth is telling a story about a call, a text or a pop-up: answer with the scam check, not a refusal.
      console.warn(`%c[${ts()}] SCAM STORY ${ids.join(", ")}`, "color:#111;background:#fc3;font-size:16px;padding:2px 6px");
      this.ui.rules(ids, "scam_check");
      if (turn.pendingToolCalls > 0) return; // the waiting tool call hands it over (runTool)
      if (turn.pressed) return; // still talking: the final transcript decides
      if (this.pressed) {
        this.deferredRefusal = { turn, result }; // never talk over the shopper; delivered on release
        return;
      }
      void this.scamCheckOutOfBand(turn, result);
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
    this.takeTurn(turn, "rule refusal");
    this.logRefusal(turn, result, "out_of_band");
    this.protectRefusal(turn, result);
    await this.deliverRefusal(turn, result);
  }

  /** The full-screen Protected card for a refusal; the rule ids are for the operator view. */
  private protectRefusal(turn: Turn, result: ScreenResult): void {
    const lang = turn.lang ?? this.lang;
    this.ui.protect({ tone: "protected", say: result.refusal?.text || DEFAULT_SAY[lang], detail: ruleIds(result).join(", "), lang });
  }

  /** A partial transcript was refused: the whole sentence may be a scam told as a story. Wait briefly for its screen. */
  private async refuseAfterFinal(turn: Turn, partial: ScreenResult): Promise<void> {
    this.stationBusy++;
    this.beginChecking();
    const final = await Promise.race([turn.finalScreen?.promise ?? Promise.resolve(null), sleep(1500).then(() => null)]).finally(() => this.stationBusy--);
    if (final?.action === "scam_check") await this.scamCheckOutOfBand(turn, final);
    else await this.refuseOutOfBand(turn, partial);
  }

  /** The station answers this turn itself: cancel what the model is saying or about to say. */
  private takeTurn(turn: Turn, reason: string): void {
    turn.refusal = "out_of_band";
    this.generation++;
    const waitingForAudio = this.awaitingFirstAudio;
    // The reply requested on release may not exist yet, so bargeIn has nothing to cancel: catch it on creation.
    if (this.responsePending) this.cancelWhenCreated = true;
    this.bargeIn(reason);
    this.awaitingFirstAudio = waitingForAudio;
  }

  /**
   * A scam told as a story ("Peachtree Power called, pay in gift cards"): POST /scam-check with Ruth's exact words
   * (policy checks her real bill, her trusted contacts and this week's reports, sets the card cool-down and tells
   * Priyank), then say its answer verbatim. If the check fails, the screen's refusal is the fallback.
   */
  private async scamCheckOutOfBand(turn: Turn, result: ScreenResult): Promise<void> {
    if (turn.scamChecked || (turn.refusal !== "none" && turn.refusal !== "out_of_band")) return;
    turn.scamChecked = true;
    this.takeTurn(turn, "scam story");
    this.requestStart = this.shopperTexts.length; // the story never rides along into a later checkout
    const gen = this.generation;
    const words = (turn.text ?? "").trim() || this.shopperTexts.slice(-1).join(" ");
    this.awaitingFirstAudio = true;
    this.stationBusy++;
    this.beginChecking(); // the tick plays while policy checks (rules first; Grok's search adds sources later)
    const reply = await scamCheck({
      session_id: this.sessionId,
      mandate_id: VOICE.mandate_id,
      lang: turn.lang ?? this.lang,
      channel: "station",
      story: words,
      transcript: words,
      caller: { org: null, name: null, phone: null },
    }).finally(() => this.stationBusy--);
    if (gen !== this.generation) return; // the shopper pressed again: they are talking now
    if (reply.kind === "ok") {
      const v = reply.verdict;
      if (v.verdict !== "ok") this.protectFromScam(v, turn.lang ?? this.lang);
      console.info(`[${ts()}] scam check (screen): ${v.verdict} ${v.pattern ?? ""} in ${v.ms ?? "?"} ms${v.from_cache ? " (cache)" : ""}`);
      // policy's fixed scam line has a recorded clip; any other answer is spoken verbatim (the tick stops on audio)
      if (v.say === sayFor("scam_check_scam", turn.lang ?? this.lang) && (await this.playLineClip("scam_check_scam", v.say))) {
        this.send({ type: "conversation.item.create", item: { type: "message", role: "assistant", content: [{ type: "text", text: v.say }] } });
      } else {
        this.speakVerbatim(v.say);
      }
      return;
    }
    this.warn("scam-check", `scam check failed (${reply.kind === "not_ready" ? `HTTP ${reply.status}` : reply.error}); the refusal is spoken instead`);
    this.endChecking();
    this.logRefusal(turn, result, "scam_check_fallback");
    this.protectRefusal(turn, result);
    await this.deliverRefusal(turn, result);
  }

  /** The refusal clip in the session voice if it loads in time, else the refusal text verbatim. */
  private async deliverRefusal(turn: Turn, result: ScreenResult): Promise<void> {
    const say = result.refusal?.text || DEFAULT_SAY[turn.lang ?? this.lang];

    let played = false;
    const url = result.refusal?.audio_url;
    if (url && this.ctx && this.player) {
      const gen = this.generation;
      const clip = await loadClip(this.ctx, url, VOICE.refusal.clip_timeout_ms, (m) => this.warn(`clip:${url}`, m));
      if (clip && gen === this.generation && this.player) {
        played = true;
        this.markFirstAudio("refusal clip");
        this.endChecking();
        this.refreshState("speaking");
        this.ui.transcript("agent", `refusal-${turn.n}`, say, true);
        console.log(`%c[${ts()}] AGENT (clip): ${say}`, "color:#36c;font-weight:bold");
        // Keep the model's history consistent with what the shopper heard.
        this.send({ type: "conversation.item.create", item: { type: "message", role: "assistant", content: [{ type: "text", text: say }] } });
        this.ledger.post("heard", payload.heard("agent", say, (result.refusal?.lang as Lang | undefined) ?? turn.lang, `${this.sessionId}-refusal-${turn.n}`));
        this.record({ kind: "clip", url, text: say });
        this.lastSpoken = say;
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
          this.ui.transcript("shopper", `voice-${turn.n}`, "", true, undefined, "nothing_heard");
          break;
        }
        turn.text = text;
        const detected = detectLang(ev.language, text);
        turn.lang = detected.lang ?? this.lastLang;
        const repeat = isRepeatRequest(text); // replays the last line; the session's language stays as it was
        if (turn.lang && !repeat) this.lastLang = turn.lang;
        if (this.lastLang) this.ui.language(this.lastLang);
        if (turn.lang && detected.source !== "none" && !repeat) this.applyLanguage(turn.lang);
        if (ev.language) console.info(`[${ts()}] detected language (api): ${ev.language}`);
        this.recordShopper(turn, text, `voice-${turn.n}`, detected.source);
        if (repeat) {
          // Grok can complete the same turn more than once; the last line is spoken again only once.
          if (!turn.repeated) {
            turn.repeated = true;
            this.repeatLast(turn);
          }
          break;
        }
        if (this.answerCosign(turn, text)) break;
        // Grok may complete a turn several times with a longer transcript each time ("मेरे..." then the whole
        // sentence), so screen every new version; a later refusal overrides an earlier proceed.
        if (turn.finalScreened !== text && turn.screenResult?.action !== "refuse") {
          turn.finalScreened = text;
          void screenText(this.sessionId, text, turn.lang, (m) => this.warn("screen", m)).then((r) => {
            this.settleScreen(turn, r);
            turn.finalScreen?.resolve(r);
          });
        } else if (turn.screenResult?.action === "refuse" && !turn.finalAfterRefusal) {
          // Refused on a partial: the final screen counts once in policy's repeat-attempt memory, and it may show
          // the whole sentence was a scam told as a story, which gets the scam check instead (refuseAfterFinal).
          turn.finalAfterRefusal = true;
          void screenText(this.sessionId, text, turn.lang, (m) => this.warn("screen", m)).then((r) => turn.finalScreen?.resolve(r));
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
        this.endChecking();
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
          this.ui.transcript("agent", key, text, true, undefined, "interrupted");
          break;
        }
        if (!text) break;
        console.log(`%c[${ts()}] AGENT: ${text}`, "color:#36c;font-weight:bold");
        this.ui.transcript("agent", key, text, true, this.lastLang);
        this.ledger.post("heard", payload.heard("agent", text, this.lastLang, key));
        this.record({ kind: "agent_text", text, item: key });
        this.lastSpoken = text;
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
          this.ui.transcript("agent", liveKey, partial.trim(), true, undefined, "interrupted");
        }
        const batch = this.toolBatches.get(id);
        if (batch) {
          this.toolBatches.delete(id);
          void this.continueAfterTools(batch, id);
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
    this.markFirstSound(via);
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
    this.toolsRunning++;
    this.beginChecking();
    try {
      await this.runToolInner(name, callId, args, turn, gen, responseId);
    } finally {
      this.toolsRunning--;
      this.refreshState();
    }
  }

  private async runToolInner(name: string, callId: string, args: Record<string, unknown>, turn: Turn | undefined, gen: number, responseId: string): Promise<void> {
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
      } else if (screen?.action === "scam_check" && turn && !(name === "scam_check" && !turn.scamChecked)) {
        // The station answers the story itself (scam check, spoken verbatim); the model stays quiet this turn.
        void this.scamCheckOutOfBand(turn, screen);
        output = { handled_by_station: true, note: "The station is answering Ruth about this call itself. Say nothing." };
      } else if (screen?.action === "refuse") {
        const say = screen.refusal?.text || DEFAULT_SAY[turn?.lang ?? this.lang];
        output = { refused: true, rule_id: screen.refusal?.rule_id ?? ruleIds(screen)[0] ?? "unknown", say };
        if (turn && turn.refusal === "none") {
          turn.refusal = "tool";
          this.logRefusal(turn, screen, `tool ${name}`);
          // the model says the refusal, and Ruth gets the same full-screen card as when the station says it
          this.protectRefusal(turn, screen);
        }
      } else {
        output = await this.dispatchTool(name, args);
      }
    }
    console.info(`[${ts()}] TOOL RESULT ${name}:`, output);
    if (output.already_said === true) this.silentResponses.set(responseId, String(output.say ?? ""));
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
      case "scam_check":
        return this.toolScamCheck(args);
      case "bill_status":
        return this.toolBillStatus(args);
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
      case "order_status":
        return this.toolOrderStatus(args);
      case "cancel_order":
        return this.toolCancelOrder(args);
      case "request_refund":
        return this.toolRequestRefund(args);
      case "purchase_history":
        return this.toolPurchaseHistory(args);
      default:
        return { error: `unknown tool ${name}` };
    }
  }

  /** After a response with tool calls: outputs are sent; ask for the next response once playback has drained. */
  private async continueAfterTools(batch: Promise<void>[], responseId: string): Promise<void> {
    const gen = this.generation;
    const sessionId = this.sessionId;
    this.continuing = true;
    this.refreshState();
    try {
      await Promise.all(batch);
      await this.player?.drained();
    } finally {
      this.continuing = false;
    }
    const turn = this.currentTurn();
    const said = this.silentResponses.get(responseId);
    this.silentResponses.delete(responseId);
    if (said !== undefined) {
      // The station already played this moment's line (the asking-Priyank clip): record it in the history after the
      // tool outputs, so the model sees call, result, then its own words; the model then waits for the shopper.
      // Also when the shopper pressed during the clip: they heard it, so the model must know it was said.
      if (said && sessionId === this.sessionId) {
        this.send({ type: "conversation.item.create", item: { type: "message", role: "assistant", content: [{ type: "text", text: said }] } });
      }
      this.refreshState();
      return;
    }
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
    const store = typeof args.store === "string" && args.store.trim() ? args.store.trim() : undefined;
    const result = await searchCatalog(query, (m) => this.warn("catalog", m), store);
    this.cache.add(result.items);
    this.ui.items(result.items, result.source);
    this.ledger.post("items_found", payload.itemsFound(query, result.items, result.source));
    return { query, source: result.source, items: result.items.map(compactItem) };
  }

  /** Every cart change: the version bumps (voiding an earlier read-back), cart_updated is posted, the screen redraws. */
  private cartChanged(): { lines: CartLineView[]; total: number } {
    this.cancelApprovalWait("the cart changed", true);
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
    return { ok: true, added: { sku, qty: isBill(item) ? 1 : qty, name: compactItem(item).name }, cart: this.cartChanged() };
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
    if (this.paused) return { status: "declined", say_key: "agent_paused", say: sayFor("agent_paused", this.lang) };
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
      const orders = storeOrders(reply);
      this.remember(orders.length ? orders : [{ order_id: outcome.order_id }], lines, totalCents, this.lang, outcome.decision_id);
      this.sessionOrders.push(...(outcome.order_ids ?? [outcome.order_id]));
      this.cart.clear();
      this.cartChanged();
    } else if (outcome.status === "waiting_for_caregiver") {
      this.waitingForCaregiver = !this.replaying;
      if (this.replaying) {
        // A replay has no voice session to poll with: the Host sees the approval on the wall and the phone.
        console.info(`[${ts()}] replay: approval ${outcome.approval_id} left to the caregiver's phone`);
      } else if (outcome.approval_id && this.approvalWait?.id === outcome.approval_id) {
        // An unchanged cart checked out again: policy returned the approval that is already pending.
        console.info(`[${ts()}] still waiting on ${outcome.approval_id}`);
      } else if (outcome.approval_id) {
        this.waitForApproval(outcome.approval_id, totalCents, lines, outcome.decision_id);
      }
      if (await this.playLineClip(outcome.say_key, outcome.say)) {
        return { ...outcome, already_said: true, instruction: "The shopper has heard this. Say nothing until they speak again." };
      }
    }
    return { ...outcome };
  }

  /** One record per store's order (its own lines, store and subtotal), for a receipt when the merchant's is unavailable. */
  private remember(orders: Array<{ order_id: string; merchant?: string }>, lines: CartLineView[], totalCents: number, lang: Lang, decisionId?: string): void {
    const placed = placedOrders(orders, lines, { decision_id: decisionId, totalCents, lang, fallbackMerchant: VOICE.merchant, storeName });
    for (const order of placed) this.placed.set(order.order_id, order);
  }

  /**
   * Plays a line rendered as a clip in the session voice (ai/warnings/line.<key>.<lang>.mp3 through the relay),
   * in place of a model turn. False when the clip cannot load in time; the model then speaks the line.
   */
  private async playLineClip(key: string, text: string): Promise<boolean> {
    if (this.replaying || !this.ctx || !this.player) return false;
    const url = `/audio/line.${key}.${this.lang}.mp3`;
    const clip = await loadClip(this.ctx, url, VOICE.refusal.clip_timeout_ms, (m) => this.warn(`clip:${url}`, m));
    if (!clip || !this.player || this.pressed) return false;
    this.ui.transcript("agent", `line-${key}-${this.turns.length}`, text, true, this.lastLang);
    // The assistant history item follows the tool output (continueAfterTools), not before it.
    this.ledger.post("heard", payload.heard("agent", text, this.lastLang, `${this.sessionId}-line-${key}-${this.turns.length}`));
    this.record({ kind: "clip", url, text });
    this.lastSpoken = text;
    this.endChecking();
    void this.player.playClip(clip).then(() => this.refreshState());
    return true;
  }

  // ---------------------------------------------------------------- the Ask guard

  /**
   * "Someone called and asked for money": policy checks the story against the rules, Ruth's real facts (her bill,
   * her trusted contacts, her orders) and this week's reports, and answers with one clear action to say.
   */
  private async toolScamCheck(args: Record<string, unknown>): Promise<Record<string, unknown>> {
    const turn = this.currentTurn();
    if (turn?.scamChecked) return { handled_by_station: true, note: "This story was already checked and answered. Say nothing." };
    if (turn) turn.scamChecked = true;
    const heard = this.shopperTexts.slice(this.requestStart).join(" ").trim();
    const story = String(args.story ?? "").trim() || heard;
    if (!story) return { error: "story is required: pass Ruth's own words" };
    const body = {
      session_id: this.sessionId,
      mandate_id: VOICE.mandate_id,
      lang: this.lang,
      channel: "station",
      story,
      // her exact words as transcribed, beside the model's retelling: the rules read what she actually said
      ...(heard && heard !== story ? { transcript: heard } : {}),
      caller: {
        org: typeof args.caller_org === "string" && args.caller_org ? args.caller_org : null,
        name: null,
        phone: typeof args.caller_phone === "string" && args.caller_phone ? args.caller_phone : null,
      },
    };
    const reply = await scamCheck(body);
    if (reply.kind !== "ok") {
      const why = reply.kind === "not_ready" ? `scam check not ready (HTTP ${reply.status})` : reply.error;
      this.warn("scam-check", `${why}; the safe fallback line is spoken`);
      return { error: "not ready", say: sayFor("scam_check_unavailable", this.lang) };
    }
    const v = reply.verdict;
    this.requestStart = this.shopperTexts.length; // the story never rides along into a later checkout
    if (v.verdict !== "ok") this.protectFromScam(v, this.lang);
    console.info(`[${ts()}] scam check: ${v.verdict} ${v.pattern ?? ""} in ${v.ms ?? "?"} ms${v.from_cache ? " (cache)" : ""}`);
    return scamToolOutput(v);
  }

  /** What Ruth really owes a biller; the bill becomes a cart item, so paying it is read back like any order. */
  private async toolBillStatus(args: Record<string, unknown>): Promise<Record<string, unknown>> {
    const known = Object.keys(VOICE.billers);
    const id = billerId(args.biller, known);
    if (!id) return { error: "unknown biller", billers: known.map((k) => VOICE.billers[k].name) };
    const fromMandate = (await getMandateBillers())?.find((b) => b.merchant_id === id)?.account_ref;
    const ref = fromMandate ?? VOICE.billers[id].account_ref;
    const bill = await getBill(id, ref, this.sessionId);
    if (!bill) return { error: "not ready", say: sayFor("store_unavailable", this.lang) };
    const name = VOICE.billers[id].name;
    const item = billItem(id, name, bill);
    if (bill.balance_due > 0) this.cache.add([item]);
    const say = sayFor(billSayKey(bill), this.lang, {
      biller: name,
      amount: money(toCents(bill.balance_due), this.lang),
      due: spokenDate(bill.due_date, this.lang),
    });
    return {
      biller: name,
      balance_due: bill.balance_due,
      ...(bill.due_date ? { due_date: bill.due_date } : {}),
      past_due: bill.past_due,
      disconnect_notice: bill.disconnect_notice,
      ...(bill.last_payment ? { last_payment: bill.last_payment } : {}),
      ...(bill.balance_due > 0 ? { sku: item.sku } : {}),
      say,
    };
  }

  // ---------------------------------------------------------------- after payment

  /** The order a tool means: the one named, else this session's latest. */
  private orderFor(args: Record<string, unknown>): string | null {
    const named = typeof args.order_id === "string" && args.order_id.trim() ? args.order_id.trim() : null;
    return named ?? this.sessionOrders[this.sessionOrders.length - 1] ?? null;
  }

  private async toolOrderStatus(args: Record<string, unknown>): Promise<Record<string, unknown>> {
    const orderId = this.orderFor(args);
    if (!orderId) return { error: "no_orders", say: sayFor("no_orders", this.lang) };
    const order = await getOrder(orderId);
    if (!order) return { error: "order status unavailable", say: sayFor("store_unavailable", this.lang) };
    const code = spokenCode(order.pickup_code);
    // After a partial refund the rest of the order is still picked up: speak its pickup progress.
    const ready = order.status === "ready_for_pickup" || (order.status === "partially_refunded" && order.fulfilment === "ready_for_pickup");
    const progress = order.status === "partially_refunded" ? order.fulfilment : order.status;
    const say =
      ready && code
        ? sayFor("order_ready", this.lang, { code, store: order.store ?? THE_STORE[this.lang] })
        : (progress === "paid" || progress === "preparing") && code
          ? sayFor("order_status_pickup", this.lang, { status: statusWords(order.status, this.lang), code })
          : sayFor("order_status", this.lang, { status: statusWords(order.status, this.lang), code });
    const words = NOTICE_WORDS[this.lang];
    this.ui.notice(capitalized(statusWords(order.status, this.lang)), order.pickup_code ? words.pickup_code(order.pickup_code) : "", "ok", order.order_id);
    return {
      order_id: order.order_id,
      status: order.status,
      ...(order.pickup_code ? { pickup_code: order.pickup_code } : {}),
      timeline: order.timeline.map((e) => e.status),
      say,
    };
  }

  private async toolCancelOrder(args: Record<string, unknown>): Promise<Record<string, unknown>> {
    const orderId = this.orderFor(args);
    if (!orderId) return { error: "no_orders", say: sayFor("no_orders", this.lang) };
    const reply = await cancelOrder(orderId, { session_id: this.sessionId, mandate_id: VOICE.mandate_id, lang: this.lastLang });
    const words = NOTICE_WORDS[this.lang];
    if (reply.kind === "cancelled") {
      this.ui.notice(words.order_cancelled, words.nothing_charged, "ok", `${orderId}${reply.link_status ? ` · payment link ${reply.link_status}` : ""}`);
      return { status: "cancelled", order_id: orderId, ...(reply.link_status ? { link_status: reply.link_status } : {}), say: sayFor("order_cancelled", this.lang) };
    }
    if (reply.kind === "too_late") {
      this.ui.notice(words.not_cancelled, words.already_paid, "warn", orderId);
      return { status: "not_cancelled", reason: "already paid", order_id: orderId, say: sayFor("cancel_too_late", this.lang) };
    }
    const say = sayFor("store_unavailable", this.lang);
    this.ui.notice(words.cancel_failed, say, "bad", `${orderId} · ${reply.error}`);
    return { status: "error", error: reply.error, say };
  }

  /**
   * Returns: a preview (confirmed false) that policy prices from the order's own lines, then, after the shopper's
   * yes, the refund to the original card. The model never passes an amount or a destination.
   */
  private async toolRequestRefund(args: Record<string, unknown>): Promise<Record<string, unknown>> {
    const orderId = this.orderFor(args);
    if (!orderId) return { error: "no_orders", say: sayFor("no_orders", this.lang) };
    const target: RefundTarget = {
      order_id: orderId,
      ...(typeof args.sku === "string" && args.sku ? { sku: args.sku } : {}),
      ...(Number.isInteger(args.qty) && Number(args.qty) > 0 ? { qty: Number(args.qty) } : {}),
    };
    const reason = String(args.reason ?? "").slice(0, 200);
    const confirmed = args.confirmed === true;
    const base = { session_id: this.sessionId, mandate_id: VOICE.mandate_id, ...target, reason, lang: this.lastLang };
    const words = NOTICE_WORDS[this.lang];

    if (!confirmed) {
      const reply = await postRefund({ ...base, confirmed: false, transcript: this.shopperTexts.slice(this.requestStart).join(" ") });
      if (reply.kind === "preview") {
        this.refundGate.markPreview(target, this.userTurns, this.shopperTexts.length);
        this.refundPreview = { amount: reply.amount, ...(reply.card_last4 ? { last4: reply.card_last4 } : {}) };
        this.ui.notice(words.refund_ask(money(toCents(reply.amount), "en")), `${words.back_to_card(reply.card_last4)} · ${words.say_yes}`, "warn", orderId);
        const say = sayFor("refund_preview", this.lang, { amount: money(toCents(reply.amount), this.lang), last4: spokenCode(reply.card_last4) });
        return {
          status: "preview",
          amount: reply.amount,
          ...(reply.card_last4 ? { card_last4: reply.card_last4 } : {}),
          items: reply.items,
          say,
          instruction: "Say this to the shopper and wait for their yes; then call request_refund again with confirmed true and the same order_id, sku and qty.",
        };
      }
      return this.refundNotDone(reply);
    }

    const gate = this.refundGate.check(target, this.userTurns);
    if (!gate.ok) {
      console.warn(`[${ts()}] refund held by the confirm gate: ${gate.reason}`);
      this.ui.note(`Refund held: confirm required (${gate.reason})`, "warn");
      return {
        error: "refund_confirm_required",
        reason: gate.reason,
        instruction: "Call request_refund with confirmed false first, say its say text, and wait for the shopper's yes.",
      };
    }
    // Only the words since the preview go to policy: a refund-scam line said earlier does not block a real return.
    const transcript = this.shopperTexts.slice(this.refundGate.sinceShopperIndex).join(" ");
    const preview = this.refundPreview;
    this.refundGate.reset(); // one preview, one refund
    this.refundPreview = null;
    const reply = await postRefund({ ...base, confirmed: true, transcript });
    if (reply.kind === "done") {
      const amount = reply.amount ?? preview?.amount ?? 0;
      const last4 = spokenCode(preview?.last4);
      this.ui.notice(
        words.refund_done(money(toCents(amount), "en")),
        words.back_to_card(preview?.last4),
        "ok",
        `${reply.status} · sandbox processor stub${reply.reconciliation_id ? ` · ${reply.reconciliation_id}` : ""}`,
      );
      return {
        status: reply.status,
        amount,
        ...(reply.refund_id ? { refund_id: reply.refund_id } : {}),
        say: sayFor("refund_done", this.lang, { amount: money(toCents(amount), this.lang), last4 }),
      };
    }
    return this.refundNotDone(reply);
  }

  /** A refusal (refund_not_allowed_rx, refund_scam, ...), an error, or a reply out of order (treated as an error). */
  private refundNotDone(r: RefundReply): Record<string, unknown> {
    if (r.kind === "preview" || r.kind === "done") r = { kind: "error", error: `unexpected ${r.kind} reply from the refund service` };
    const words = NOTICE_WORDS[this.lang];
    if (r.kind === "declined") {
      // a purchase line ("I can't buy that") would be wrong here: unknown or generic keys get the refund line
      const key = hasSay(r.say_key) && r.say_key !== "declined" ? r.say_key : "refund_not_possible";
      if (r.rules_failed.length) this.ui.rules(r.rules_failed, "deny");
      const biller = Object.values(VOICE.billers ?? {})[0]?.name ?? "the biller";
      const say = sayFor(key, this.lang, { biller });
      this.ui.notice(words.refund_not_made, say, "warn", `${r.say_key}${r.rules_failed.length ? ` · ${r.rules_failed.join(", ")}` : ""}`);
      return { status: "declined", say_key: r.say_key, say };
    }
    const say = sayFor("store_unavailable", this.lang);
    this.ui.notice(words.refund_failed, say, "bad", r.error);
    return { status: "error", error: r.error, say };
  }

  private async toolPurchaseHistory(args: Record<string, unknown>): Promise<Record<string, unknown>> {
    const days = Math.min(60, Math.max(1, Number.isInteger(args.days) ? Number(args.days) : 30));
    const history = await getHistory(VOICE.mandate_id, days);
    if (!history) return { error: "history unavailable", say: sayFor("store_unavailable", this.lang) };
    const { count, spentCents, lastItems } = historySummary(history);
    const vars = { days: String(days), count: String(count), spent: money(spentCents, this.lang), items: lastItems.join(", ") };
    const say = !count
      ? sayFor("history_none", this.lang, vars)
      : `${sayFor(count === 1 ? "history_summary_one" : "history_summary", this.lang, vars)}${lastItems.length ? ` ${sayFor("history_last", this.lang, vars)}` : ""}`;
    return { days, ...history, say };
  }

  /** "Repeat that": the last line again, verbatim, with no model turn (and it is not the read-back "yes"). */
  private repeatLast(turn: Turn): void {
    turn.refusal = "out_of_band"; // nothing else answers this turn: no tool runs, no continuation
    turn.screen.resolve(null);
    turn.screenSettled = true;
    if (this.responsePending) this.cancelWhenCreated = true;
    this.bargeIn("repeat request");
    const text = this.lastSpoken ?? sayFor("repeat_nothing", this.lang);
    console.info(`[${ts()}] repeat: ${text}`);
    this.speakVerbatim(text);
  }

  // ---------------------------------------------------------------- caregiver approval

  /** Polls the approval every second; the button stays live, and a new request cancels the wait. */
  private waitForApproval(approvalId: string, totalCents: number, lines: CartLineView[], decisionId?: string): void {
    const wait = { id: approvalId, totalCents, lines, decisionId };
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

  /** Ends the caregiver wait; `closeAtPolicy` also asks policy to close the approval (so a late tap cannot order). */
  private cancelApprovalWait(reason: string, closeAtPolicy = false): void {
    this.waitingForCaregiver = false;
    const wait = this.approvalWait;
    if (!wait) return;
    console.info(`[${ts()}] caregiver wait cancelled: ${reason}`);
    this.approvalWait = null;
    this.ui.waiting(null);
    if (closeAtPolicy) void this.closeApproval(wait);
  }

  /** Asks policy to close the approval; if it already closed (Priyank tapped in the same second), finish it instead. */
  private async closeApproval(wait: NonNullable<StationAgent["approvalWait"]>): Promise<void> {
    if (await cancelApproval(wait.id)) return;
    const status = await getApproval(wait.id);
    if (status && status.state !== "pending" && status.state !== "cancelled") {
      this.finishApproval(status, wait.totalCents, wait.lines, wait.decisionId);
    }
  }

  private finishApproval(status: ApprovalStatus, totalCents: number, lines: CartLineView[], decisionId?: string): void {
    this.approvalWait = null;
    this.waitingForCaregiver = false;
    this.ui.waiting(null);
    const lang = this.lang;
    const total = money(totalCents, lang);
    console.info(`[${ts()}] caregiver answer: ${status.state}${status.order_id ? ` (order ${status.order_id})` : ""}`);
    if (status.state === "approved" && status.order_id) {
      const orders = status.orders?.length ? status.orders : [{ order_id: status.order_id }];
      this.remember(orders, lines, totalCents, lang, decisionId);
      this.sessionOrders.push(...orders.map((o) => o.order_id));
      this.cart.clear();
      this.cartChanged();
      const say = sayFor("caregiver_approved", lang, { total });
      this.ui.outcome({
        status: "ordered",
        say_key: "caregiver_approved",
        say,
        total: fromCents(totalCents),
        decision_id: decisionId,
        order_id: status.order_id,
        order_ids: orders.map((o) => o.order_id),
      });
      void this.speakFixed(say);
    } else if (status.state === "approved") {
      const say = sayFor("checkout_unavailable", lang);
      this.ui.outcome({ status: "error", say_key: "checkout_unavailable", say, decision_id: decisionId, error: "approved but no order was placed" });
      void this.speakFixed(say);
    } else if (status.state === "cancelled") {
      this.ui.outcome({ status: "declined", say_key: "cancelled", say: "", decision_id: decisionId });
    } else if (status.state === "rejected") {
      // Priyank's own words when he typed a message on the phone, else the standard line.
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

  /**
   * A line with a recorded clip (in the session voice): once the shopper and the model are quiet, play the
   * clip if it loads in time, else speak `full` verbatim. The model's history gets what Ruth actually heard.
   */
  private async speakClipOr(key: string | null, clipText: string, full: string): Promise<void> {
    if (this.replaying) return;
    const t0 = performance.now();
    while ((this.pressed || this.responseActive || this.player?.active) && performance.now() - t0 < 8000) await sleep(150);
    if (!this.started || this.pressed || !this.configured) return; // the screen still shows it
    if (key && (await this.playLineClip(key, clipText))) {
      this.send({ type: "conversation.item.create", item: { type: "message", role: "assistant", content: [{ type: "text", text: clipText }] } });
      return;
    }
    this.lastSpoken = full;
    this.speakVerbatim(full);
  }

  /** A fixed line, spoken verbatim (force_message) once the shopper and the model are quiet; never over them. */
  /** Says a fixed line once the station is quiet. True when it was spoken (the screen shows it either way). */
  private async speakFixed(text: string): Promise<boolean> {
    if (this.replaying) return false; // the recording carries its own audio
    const t0 = performance.now();
    while ((this.pressed || this.responseActive || this.player?.active) && performance.now() - t0 < 8000) await sleep(150);
    if (!this.started || this.pressed || !this.configured) return false; // the screen still shows it
    this.lastSpoken = text; // only what was actually spoken can be repeated
    this.speakVerbatim(text);
    return true;
  }

  // ---------------------------------------------------------------- the guards at the station

  /** A scam verdict (unsure or scam): the full-screen card with the words she heard and one action. */
  private protectFromScam(v: ScamVerdict, lang: Lang): void {
    const words = actionWords(v.actions, lang);
    // the answer usually already says it ("…Por favor cuelgue."): then the card does not repeat it
    const action = words && !v.say.toLowerCase().includes(words.toLowerCase().replace(/[.।]$/, "")) ? words : "";
    this.ui.protect({
      tone: v.verdict === "scam" ? "protected" : "care",
      say: v.say,
      ...(action ? { action } : {}),
      detail: `${v.pattern ?? ""}${v.sources.length ? ` · ${v.sources.map((s) => s.title).join(" · ")}` : ""}`,
      lang,
    });
  }

  /** The cool-down and pause as they stand now, for a station that starts after they began. */
  private async loadGuardState(): Promise<void> {
    const state = await getGuardState();
    if (!state) return;
    this.showCooldown(state.cooldown_until ?? null, false);
    this.showPaused(state.paused === true);
  }

  private onGuardEvent(ev: StreamEvent): void {
    switch (ev.type) {
      case "card_decision":
        if (ev.result === "declined") this.onCardDeclined(ev);
        break;
      case "card_hold_released": {
        const say = sayFor("card_allowed_once", this.lang);
        this.ui.notice(NOTICE_WORDS[this.lang].allowed_once, say, "ok", String(ev.hold_id ?? ""));
        void this.speakClipOr("card_allowed_once", say, say);
        break;
      }
      case "risk_changed":
        this.showCooldown(typeof ev.cooldown_until === "string" || typeof ev.cooldown_until === "number" ? ev.cooldown_until : null, true);
        break;
      case "mandate_paused":
        this.showPaused(true);
        break;
      case "mandate_resumed":
        this.showPaused(false);
        break;
      case "mandate_signed":
        void this.askCosign();
        break;
    }
  }

  /** A declined swipe at a real store: say why in Ruth's language, never through the model, and show the card. */
  private onCardDeclined(ev: StreamEvent): void {
    const key = CARD_LINES[String(ev.reason_key ?? "")] ?? "card_declined_blocked";
    const amount = Number(ev.amount);
    const store = displayStore(String(ev.store ?? ""), this.lang);
    const say = sayFor(key, this.lang, { amount: Number.isFinite(amount) ? money(toCents(amount), this.lang) : "", store });
    console.info(`[${ts()}] card declined at ${store}: ${ev.reason_key}`);
    this.ui.protect({ tone: "protected", say, detail: `${ev.reason_key ?? ""} · ${ev.reason ?? ""} · card …${ev.card_last4 ?? ""}`, lang: this.lang });
    // The recorded clip leaves out the amount and store (the card shows them); without the clip, the whole line is spoken
    const clipKey = key === "card_declined_blocked" || key === "card_declined_cooldown" ? key : null;
    void this.speakClipOr(clipKey, clipKey ? sayFor(`${clipKey}_clip`, this.lang) : say, say);
  }

  private showCooldown(until: string | number | null, announce: boolean): void {
    const when = until === null ? null : new Date(typeof until === "number" ? (until < 1e12 ? until * 1000 : until) : until);
    if (!when || Number.isNaN(when.getTime()) || when.getTime() <= Date.now()) {
      this.ui.banner("cooldown", null);
      this.cooldownSaid = null;
      return;
    }
    this.ui.banner("cooldown", cooldownBanner(when, this.lang));
    const key = when.toISOString();
    if (announce && this.cooldownSaid !== key) {
      this.cooldownSaid = key;
      void this.speakFixed(sayFor("cooldown_on", this.lang));
    }
  }

  private showPaused(on: boolean): void {
    this.paused = on;
    this.ui.banner("paused", on ? PAUSED_BANNER[this.lang] : null);
  }

  /** Priyank signed new rules: read them to Ruth in plain words and ask if she agrees (her next turn answers). */
  private async askCosign(): Promise<void> {
    const ask = ++this.cosignAsk;
    this.cosignPending = null; // an answer to an earlier reading is not a yes to these rules
    const read = await getMandate();
    if (!read || ask !== this.cosignAsk) return;
    const say = sayFor("cosign_ask", this.lang, { rules: rulesInWords(read.mandate, this.lang) });
    this.ui.notice(NOTICE_WORDS[this.lang].new_rules, say, "warn");
    // her next turn answers the question only once she has heard it, never a read-back or an earlier question;
    // her yes goes with the hash from the same reply as the words she heard (an older policy sends none)
    if ((await this.speakFixed(say)) && ask === this.cosignAsk) {
      this.cosignPending = { until: Date.now() + 5 * 60_000, ...(read.mandate_hash ? { hash: read.mandate_hash } : {}) };
    }
  }

  /** Ruth's answer to "Do you agree?": a yes is recorded with her own words; anything else carries on as usual. */
  private answerCosign(turn: Turn, text: string): boolean {
    const pending = this.cosignPending;
    if (!pending) return false;
    this.cosignPending = null;
    if (Date.now() > pending.until) return false;
    const answer = yesOrNo(text);
    if (!answer) return false;
    turn.refusal = "out_of_band"; // the station answers this turn; the model does not
    turn.screen.resolve(null);
    turn.screenSettled = true;
    if (this.responsePending) this.cancelWhenCreated = true;
    this.bargeIn("co-sign answer");
    if (answer === "no") {
      this.speakVerbatim(sayFor("cosign_not_yet", this.lang));
      return true;
    }
    void this.recordCosign(turn, text, pending.hash);
    return true;
  }

  /** Her yes goes to policy for the rules she heard; the thanks follows policy's answer. */
  private async recordCosign(turn: Turn, text: string, hash: string | undefined): Promise<void> {
    const gen = this.generation;
    this.stationBusy++;
    this.beginChecking(); // the tick plays while policy records it
    const result = await postCosign({ session_id: this.sessionId, said: text, lang: turn.lang ?? this.lang, ...(hash ? { mandate_hash: hash } : {}) })
      .finally(() => this.stationBusy--);
    if (result === "stale") {
      // Priyank changed the rules after they were read to Ruth: this yes is not for them, so she hears the new ones
      const msg = "Co-sign not recorded: the rules changed after they were read to Ruth (policy answered 409); reading her the new rules.";
      console.warn(`[${ts()}] ${msg}`);
      this.ui.note(msg, "warn");
      this.refreshState();
      void this.askCosign();
      return;
    }
    if (result === "failed") this.warn("cosign", "policy did not record the co-sign (POST /mandate/cosign)");
    this.ui.notice(NOTICE_WORDS[this.lang].you_agreed, `“${text}”`, result === "ok" ? "ok" : "warn");
    if (gen !== this.generation) {
      this.refreshState(); // she pressed again: never talk over her
      return;
    }
    this.speakVerbatim(sayFor("cosign_thanks", this.lang));
  }

  // ---------------------------------------------------------------- paid -> receipt, reset (relay stream)

  private onStreamEvent(ev: StreamEvent): void {
    if (ev.type === "paid" && ev.session_id === this.sessionId && typeof ev.order_id === "string") {
      void this.onPaid(ev.order_id, typeof ev.t === "number" ? ev.t : Date.now());
    } else if (ev.type === "reset") {
      if (performance.now() - this.lastLocalReset < RESET_ECHO_MS) return; // our own reset coming back
      void this.resetSession("reset from the relay", false);
    } else if (GUARD_EVENTS.has(ev.type)) {
      this.onGuardEvent(ev);
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
    const session = this.sessionId; // a reset during the awaits below starts a new session: drop this receipt
    const stale = () => this.sessionId !== session;
    const order = this.placed.get(orderId) ?? null;
    const lang = order?.lang ?? this.lang;
    const url = sessionUrl(TUNNEL_HOST, this.sessionId);
    let receipt = await getReceipt(orderId, lang);
    if (stale()) return;
    if (!receipt && order) receipt = localReceipt(order, url, paidAt);
    if (!receipt) {
      this.warn("receipt", `paid ${orderId}, but no receipt is available (merchant receipt endpoint down and no local order record)`);
      return;
    }
    if (!receipt.session_url && url) receipt = { ...receipt, session_url: url };
    this.lastReceipt = receipt;
    console.info(`[${ts()}] paid ${orderId}: printing the receipt`);
    this.ui.receipt(receipt, { key: "preparing" });
    const printed = await printReceipt(receipt);
    if (stale()) {
      this.ui.receipt(null);
      return;
    }
    const onPaper = printed.ok && printed.via === "printer";
    const files = { png: printed.pngUrl, pdf: printed.pdfUrl };
    this.ui.receipt(receipt, printNote(printed, "printed"), printed.ok ? files : undefined);
    this.ledger.post("receipt_printed", payload.receiptPrinted(orderId, onPaper ? "printer" : "screen", printed.ok && printed.via === "pdf"));
    const totalText = money(toCents(receipt.total), receipt.lang);
    // {pickup} is the pickup line with its code, empty for a bill (nothing to pick up)
    const pickup = !receipt.bill && receipt.pickup_code ? sayFor("pickup_line", receipt.lang, { code: spokenCode(receipt.pickup_code) }) : "";
    const store = receipt.merchant || THE_STORE[receipt.lang];
    const parts = [sayFor(onPaper ? "receipt_done" : "receipt_on_screen", receipt.lang, { total: totalText, store, pickup }).replace(/\s{2,}/g, " ")];
    // Only real savings are mentioned; when nothing was saved the line is left out.
    if (receipt.savings) parts.push(sayFor("you_saved", receipt.lang, { saved: money(toCents(receipt.savings), receipt.lang) }));
    if (receipt.loyalty_points) parts.push(sayFor("loyalty_points", receipt.lang, { points: String(receipt.loyalty_points) }));
    void this.speakFixed(parts.join(" "));
  }

  /** The on-screen Reprint button. */
  async reprint(): Promise<void> {
    if (!this.lastReceipt) return;
    this.ui.receipt(this.lastReceipt, { key: "preparing" });
    const printed = await printReceipt(this.lastReceipt);
    const onPaper = printed.ok && printed.via === "printer";
    this.ui.receipt(this.lastReceipt, printNote(printed, "printed_again"), printed.ok ? { png: printed.pngUrl, pdf: printed.pdfUrl } : undefined);
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
    this.placed.clear();
    this.lastReceipt = null;
    this.sessionOrders = [];
    this.refundGate.reset();
    this.refundPreview = null;
    this.lastSpoken = null;
    this.deferredRefusal = null;
    this.pendingForce = null;
    this.waitingForCaregiver = false;
    this.rec = { t0: performance.now(), events: [] };
    this.cosignPending = null;
    this.cosignAsk++; // a reading still on its way does not wait for an answer in the new session
    this.ui.cleared(this.sessionId);
    // the reset cleared the pause and the cool-down on policy too: drop them here and read them back once it is done
    this.showPaused(false);
    void relay.then(() => this.loadGuardState());
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
      events: collapseShopperTurns(events).map((e) => ({ ...e, t: e.t - start })),
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
    this.cancelApprovalWait("replay", true);
    this.bargeIn("replay");
    if (!this.stream) {
      // Started without Start (no voice session): still follow paid and reset for this session.
      this.stream = new RelayStream(STREAM_TYPES, (ev) => this.onStreamEvent(ev), (m) => this.warn("stream", m));
      void this.stream.open();
    }
    const run = { gen: ++this.replayGen };
    this.replaying = run;
    this.ledger.replay = true;
    this.ui.replay(true, lang);
    this.lastLang = lang as Lang;
    this.ui.language(lang);
    console.info(`[${ts()}] REPLAY ${lang}: ${loaded.events.length} events`);
    const t0 = performance.now();
    let tools: Promise<unknown> = Promise.resolve();
    let n = 0;
    const replayTurns = new Map<number, Turn>();
    const live = () => this.replaying === run; // Stop (or a press) ends the replay: queued actions must not run
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
        // Later versions of the same recorded turn update that turn (counted once toward the read-back "yes").
        const key = typeof ev.turn === "number" ? ev.turn : -1 - n;
        let turn = replayTurns.get(key);
        if (!turn) {
          turn = this.newTurn("text");
          replayTurns.set(key, turn);
        }
        turn.text = ev.text;
        turn.lang = lang as Lang;
        this.recordShopper(turn, ev.text, `replay-shopper-${turn.n}`, "guess");
        const text = ev.text;
        const replayTurn = turn;
        tools = tools.then(async () => {
          if (!live() || replayTurn.refusal !== "none") return;
          const result = await screenText(this.sessionId, text, lang as Lang, (m) => this.warn("screen", m));
          if (live() && result?.action === "refuse") {
            replayTurn.refusal = "tool";
            this.ui.rules(ruleIds(result), "refuse", result.refusal?.text);
            this.logRefusal(replayTurn, result, "replay");
            this.protectRefusal(replayTurn, result);
          }
        });
      } else if (ev.kind === "tool" && ev.name) {
        const name = ev.name;
        const args = ev.args ?? {};
        tools = tools.then(async () => {
          if (!live()) return;
          const out = await this.dispatchTool(name, args);
          console.info(`[${ts()}] REPLAY TOOL ${name}:`, out);
        });
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
    // The stream stays open: the Host's Confirm payment comes after the replay ends, and Start reuses it.
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
    // A turn that ended with no audio (a silent tool result, a cancelled reply) also ends the tick.
    if (this.checking && !this.responseActive && !this.responsePending && !this.continuing && this.toolsRunning === 0 && this.stationBusy === 0) {
      this.endChecking();
    }
    const state = this.stateNow(force);
    // Tick only while checking and nothing else is audible (never over the shopper or the agent).
    if (state === "checking" && !this.replaying) this.earcon?.start();
    else this.earcon?.stop();
    this.ui.state(state);
  }

  private stateNow(force?: AgentState): AgentState {
    if (!this.started) return "off";
    if (force) return force;
    if (!this.configured) return this.pressed ? "listening" : "connecting";
    if (this.pressed) return "listening";
    if (this.player?.active) return "speaking";
    if (this.checking) return "checking";
    if (this.responseActive || this.continuing || this.awaitingFirstAudio) return "thinking";
    return this.waitingForCaregiver ? "waiting" : "ready";
  }

  // ---------------------------------------------------------------- the checking tick

  private makeEarcon(ctx: AudioContext): Earcon {
    const earcon = new Earcon(ctx, { intervalMs: VOICE.earcon.interval_ms, gainDb: VOICE.earcon.gain_db });
    earcon.onTick = () => this.markFirstSound("earcon");
    return earcon;
  }

  /** No word yet a moment after the shopper finished: tick, so the silence never reads as "it didn't hear me". */
  private armSlowWait(): void {
    if (this.slowWaitTimer) clearTimeout(this.slowWaitTimer);
    this.firstSoundPending = true;
    const gen = this.generation;
    this.slowWaitTimer = setTimeout(() => {
      this.slowWaitTimer = null;
      if (gen === this.generation && this.awaitingFirstAudio && !this.pressed) this.beginChecking();
    }, VOICE.earcon.after_ms);
  }

  private beginChecking(): void {
    if (this.pressed || this.replaying) return;
    this.checking = true;
    this.refreshState();
  }

  private endChecking(): void {
    if (this.slowWaitTimer) {
      clearTimeout(this.slowWaitTimer);
      this.slowWaitTimer = null;
    }
    this.checking = false;
    this.earcon?.stop();
  }

  private markFirstSound(via: string): void {
    if (!this.firstSoundPending) return;
    this.firstSoundPending = false;
    const ms = Math.round(performance.now() - this.tRelease);
    if (this.latencyKind === "voice") this.soundLatencies.push(ms);
    const n = this.soundLatencies.length;
    const med = n ? Math.round(median(this.soundLatencies) ?? ms) : null;
    console.log(`%c[${ts()}] ${this.latencyKind === "voice" ? "release" : "send"} -> first sound: ${ms} ms (${via})${med !== null ? `; voice median ${med}, n=${n}` : ""}`, "color:#b60");
    this.ui.firstSound(ms, via);
  }

  private warn(key: string, msg: string): void {
    console.warn(`[${ts()}] ${msg}`);
    if (this.warned.has(key)) return;
    this.warned.add(key);
    this.ui.note(msg, "warn");
  }
}
