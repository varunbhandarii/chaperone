// DOM rendering for Ruth's station: the big talk button, the stage (the latest exchange, large, with older lines
// scrolling above it), the order card (or what was just found), the read-back and co-sign choices, the wait for
// Priyank, the receipts, the Protected card and the calm banners; and, in the operator view, the state strip, status,
// latency, rule banner, notes and ids.

import type { AgentState, AgentUI, NoteKind, ProtectedView, TranscriptMark } from "./agent.ts";
import { renderSVG } from "uqr";
import type { CartLineView, CatalogItem, CheckoutOutcome } from "./cart.ts";
import type { Lang } from "./lang.ts";
import { RECEIPT_LABELS, formatPaidAt, type Receipt, type ReceiptNote } from "./receipt.ts";
import { levelFromRms } from "./pcm.ts";
import {
  BUTTON_HINTS,
  NOTHING_YET,
  NOTICE_WORDS,
  OUTCOME_TITLES,
  PAGE_WORDS,
  PROTECT_WORDS,
  STAGE_WORDS,
  STATE_WORDS,
  TITLES,
  TOLD,
  cosignLines,
  moneyAsDigits,
  withoutToldTail,
  type ButtonHint,
  type PageWord,
  type RuleLine,
} from "./words.ts";

function $<T extends HTMLElement = HTMLElement>(id: string): T {
  const el = document.getElementById(id);
  if (!el) throw new Error(`missing #${id}`);
  return el as T;
}

function usd(n: number): string {
  return `$${n.toFixed(2)}`;
}

function clock(): string {
  return new Date().toLocaleTimeString([], { hour12: false });
}

const LOCALE: Record<Lang, string> = { en: "en-US", es: "es-MX", hi: "hi-IN" };

/** "9:05 PM" in Ruth's language, with digits in every language. */
function timeOfDay(d: Date, lang: Lang): string {
  return new Intl.DateTimeFormat(LOCALE[lang], { hour: "numeric", minute: "2-digit", numberingSystem: "latn" }).format(d);
}

function el<K extends keyof HTMLElementTagNameMap>(tag: K, cls?: string, text?: string): HTMLElementTagNameMap[K] {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

// Open 24-grid icons, 2px stroke (design: Components, Icons).
const ICONS = {
  store: ["M4 9l1.5-5h13L20 9", "M4 9h16v2a2.7 2.7 0 0 1-5.3 0 2.7 2.7 0 0 1-5.4 0A2.7 2.7 0 0 1 4 11V9z", "M5 12v8h14v-8"],
  bolt: ["M13 2L4 14h7l-1 8 9-12h-7l1-8z"],
  bag: ["M5 8h14l-1 12H6L5 8z", "M9 8V6a3 3 0 0 1 6 0v2"],
  people: ["M12.5 8a3.5 3.5 0 1 1-7 0 3.5 3.5 0 1 1 7 0", "M2.5 20c.5-3.5 3-5.5 6.5-5.5s6 2 6.5 5.5", "M19.5 9a2.5 2.5 0 1 1-5 0 2.5 2.5 0 1 1 5 0", "M16.5 14.5c2.6.2 4.4 1.9 5 4.5"],
  shield: ["M12 3l7 3v5c0 4.5-3 8-7 10-4-2-7-5.5-7-10V6l7-3z"],
  clock: ["M21 12a9 9 0 1 1-18 0 9 9 0 1 1 18 0", "M12 7v5l3 2"],
  pause: ["M9 5v14", "M15 5v14"],
} as const;
type IconName = keyof typeof ICONS;
const SVG_NS = "http://www.w3.org/2000/svg";

function icon(name: IconName, size = 24): SVGSVGElement {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("width", String(size));
  svg.setAttribute("height", String(size));
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("class", "icon");
  for (const d of ICONS[name]) {
    const path = document.createElementNS(SVG_NS, "path");
    path.setAttribute("d", d);
    svg.append(path);
  }
  return svg;
}

/** "Up to **$60** each time": the amounts in bold, as text nodes (never HTML). */
function boldAmounts(text: string): Node[] {
  return text.split("**").map((part, i) => (i % 2 ? el("b", "", part) : document.createTextNode(part)));
}

/** The circumference of the countdown ring (r = 41). */
const RING = 2 * Math.PI * 41;

type Calm = "no_mic" | "trouble" | "start_first";

/**
 * `onSay` sends a tapped choice ("yes", "not now") through the typed-input path, so every guard applies; it
 * answers false when nothing was sent (the station is not started).
 */
export function createUI(
  onStateChange: (state: AgentState) => void,
  onSay: (text: string) => boolean = () => false,
): AgentUI & { clearRules(): void } {
  const stateEl = $("state");
  const statusEl = $("status");
  const ptt = $<HTMLButtonElement>("ptt");
  const pttLabel = $("ptt-label");
  const pttHint = $("ptt-hint-text");
  const latencyEl = $("latency");
  const statsEl = $("latency-stats");
  const soundEl = $("first-sound");
  const rulesEl = $("rules");
  const stage = $("stage");
  const log = $("log");
  const greeting = $("greeting");
  const calmEl = $("calm");
  const askingEl = $("asking");
  const askingRing = document.getElementById("asking-ring") as unknown as SVGCircleElement;
  const askingSecs = $("asking-secs");
  const askingSent = $("asking-sent");
  const outcomeEl = $("outcome");
  const readbackEl = $("readback");
  const readbackYes = $<HTMLButtonElement>("readback-yes");
  const readbackNo = $<HTMLButtonElement>("readback-no");
  const itemsPanel = $("items-panel");
  const itemsEl = $("items");
  const itemsStore = $("items-store");
  const itemsSource = $("items-source");
  const foundFoot = $("found-foot");
  const orderView = $("order-view");
  const cartEl = $("cart");
  const orderEmpty = $("order-empty");
  const orderBadge = $("order-badge");
  const cartTotalRow = $("cart-total-row");
  const cartTotal = $("cart-total");
  const budgetEl = $("budget");
  const cosignEl = $("cosign");
  const cosignYes = $<HTMLButtonElement>("cosign-yes");
  const cosignNo = $<HTMLButtonElement>("cosign-no");
  const levelEl = $("level");
  const micLabel = $("mic-label");
  const strip = $("strip");
  const bannerEl = $("banner");
  const receiptOverlay = $("receipt-overlay");
  const receiptsEl = $("receipts");
  const receiptTemplate = $<HTMLTemplateElement>("receipt-template");
  const placedQr = $("placed-qr");
  const placedScan = $("placed-scan");
  const protectedEl = $("protected");
  const protectedOk = $<HTMLButtonElement>("protected-ok");
  const replayBanner = $("replay-banner");
  const sessionEl = $("session");

  let currentState: AgentState = "off";
  let lang: Lang = "en";
  const banners = new Map<string, string>();
  const lines = new Map<string, HTMLLIElement>();
  // what the stage and the order card are showing
  let waitSecs: number | null = null;
  let waitTotal = 0;
  let waitSince: Date | null = null;
  let readBackOn = false;
  let cosignOn: { mandate: Record<string, unknown>; at: Date } | null = null;
  let cosignTimer: ReturnType<typeof setTimeout> | null = null;
  let cartView: { lines: CartLineView[]; total: number } = { lines: [], total: 0 };
  let foundView: { items: CatalogItem[]; source: string } | null = null;
  let budgetLeft: number | null = null;
  let redrawOutcome: (() => void) | null = null;
  let placedLang: Lang = "en";
  let placedNote: ReceiptNote | undefined;
  const calm = new Set<Calm>();
  let focusBeforeProtect: HTMLElement | null = null;

  // ---------------------------------------------------------------- language and fixed words

  /** The page's fixed labels (data-words="<key>") in Ruth's language; a text box gets its placeholder. */
  function pageWords(): void {
    for (const node of document.querySelectorAll<HTMLElement>("[data-words]")) {
      const word = PAGE_WORDS[lang][node.dataset.words as PageWord];
      if (!word) continue;
      if (node instanceof HTMLInputElement) node.placeholder = word;
      else node.textContent = word;
    }
  }

  function renderLangs(): void {
    for (const span of document.querySelectorAll<HTMLElement>("#langs [data-lang]")) {
      if (span.dataset.lang === lang) span.setAttribute("aria-current", "true");
      else span.removeAttribute("aria-current");
    }
  }

  function renderGreeting(): void {
    const words = STAGE_WORDS[lang];
    $("greeting-hello").textContent = words.greeting(new Date().getHours());
    $("greeting-ask").textContent = words.ask;
    $("examples").replaceChildren(...words.examples.map((text) => el("li", "", text)));
  }

  function setLang(next: Lang): void {
    lang = next;
    document.documentElement.lang = next;
    renderLangs();
    pageWords();
    renderGreeting();
    renderButton();
    renderCalm();
    renderChoices();
    renderAsking();
    renderOrder();
    if (foundView) renderFound();
    if (cosignOn) renderCosign();
    redrawOutcome?.();
  }

  // ---------------------------------------------------------------- the big button, the strip, calm lines

  function hintFor(state: AgentState): ButtonHint | null {
    switch (state) {
      case "off":
        return "off";
      case "ready":
        return readBackOn ? "read_back" : cosignOn ? "cosign" : waitSecs !== null ? "waiting" : "ready";
      case "waiting":
        return "waiting";
      case "listening":
        return "listening";
      case "speaking":
        return "speaking";
      default:
        return null; // getting ready, one moment, checking: nothing for her to do
    }
  }

  function renderButton(): void {
    const state = currentState;
    ptt.className = `ptt state-${state}`;
    // waiting for Priyank: the button stays live and says what it does; the wait itself is shown on the stage
    pttLabel.textContent = STATE_WORDS[lang][state === "waiting" ? "ready" : state];
    const hint = hintFor(state);
    pttHint.textContent = hint ? BUTTON_HINTS[lang][hint] : "";
    ptt.classList.toggle("no-hint", !hint);
    strip.textContent = STATE_WORDS[lang][state];
    strip.className = `strip state-${state}`;
  }

  function renderCalm(): void {
    const which: Calm | null = calm.has("no_mic")
      ? "no_mic"
      : calm.has("trouble") && currentState === "off"
        ? "trouble"
        : calm.has("start_first") && currentState === "off"
          ? "start_first"
          : null;
    calmEl.hidden = !which;
    calmEl.textContent = which ? STAGE_WORDS[lang].calm[which] : "";
    // she is told she can type below: the typed row opens by itself
    document.body.classList.toggle("typing-auto", calm.has("no_mic"));
  }

  // ---------------------------------------------------------------- the stage

  function scroll(): void {
    log.scrollTop = log.scrollHeight;
  }

  function addLine(cls: string, fill: (li: HTMLLIElement) => void): HTMLLIElement {
    const li = document.createElement("li");
    li.className = cls;
    li.append(el("span", "time", clock()));
    fill(li);
    log.append(li);
    scroll();
    return li;
  }

  function devSpan(text: string): HTMLSpanElement {
    return el("span", "dev", text);
  }

  /** The latest exchange is large: her last line, and Chaperone's lines after it (at most two). */
  function markLatest(): void {
    const talk = [...log.querySelectorAll<HTMLLIElement>("li.shopper, li.agent")];
    let lastShopper = -1;
    talk.forEach((li, i) => {
      if (li.classList.contains("shopper")) lastShopper = i;
    });
    const replies = talk
      .map((li, i) => (i > lastShopper && li.classList.contains("agent") ? i : -1))
      .filter((i) => i >= 0)
      .slice(-2);
    talk.forEach((li, i) => li.classList.toggle("latest", i === lastShopper || replies.includes(i)));
    const quiet = talk.length === 0;
    greeting.hidden = !quiet;
    stage.classList.toggle("greeting-on", quiet);
  }

  /** Her new turn: the read-back and co-sign choices, what was found and the last outcome have been answered. */
  function newTurn(): void {
    showReadBack(false);
    hideCosign();
    hideFound();
    hideOutcome();
    calm.delete("start_first");
    renderCalm();
  }

  function renderChoices(): void {
    const words = STAGE_WORDS[lang];
    readbackYes.textContent = words.yes_place.label;
    readbackNo.textContent = words.no_place.label;
    cosignYes.textContent = words.cosign_yes.label;
    cosignNo.textContent = words.cosign_no.label;
  }

  function showReadBack(on: boolean): void {
    readBackOn = on;
    readbackEl.hidden = !on;
    renderButton();
  }

  function choose(send: string, after: () => void): void {
    if (onSay(send)) after();
  }
  readbackYes.addEventListener("click", () => choose(STAGE_WORDS[lang].yes_place.send, () => showReadBack(false)));
  readbackNo.addEventListener("click", () => choose(STAGE_WORDS[lang].no_place.send, () => showReadBack(false)));
  cosignYes.addEventListener("click", () => choose(STAGE_WORDS[lang].cosign_yes.send, hideCosign));
  cosignNo.addEventListener("click", () => choose(STAGE_WORDS[lang].cosign_no.send, hideCosign));

  function renderAsking(): void {
    askingEl.hidden = waitSecs === null;
    stage.classList.toggle("asking-on", waitSecs !== null);
    orderBadge.hidden = waitSecs === null || !cartView.lines.length;
    if (waitSecs === null) return;
    const left = Math.max(0, waitSecs);
    askingSecs.textContent = `${left}s`;
    askingRing.style.strokeDasharray = `${RING}`;
    askingRing.style.strokeDashoffset = `${RING * (1 - Math.min(1, left / Math.max(1, waitTotal)))}`;
    askingSent.textContent = waitSince ? STAGE_WORDS[lang].sent_at(timeOfDay(waitSince, lang)) : "";
  }

  function hideOutcome(): void {
    outcomeEl.hidden = true;
    outcomeEl.replaceChildren();
    redrawOutcome = null;
  }

  /** The outcome panel on the stage: green done, yellow a person decides, plain not done, red only when broken. */
  function showOutcome(tone: "ok" | "wait" | "none" | "err", title: string, detail: string, dev?: string, code?: string): void {
    outcomeEl.hidden = false;
    outcomeEl.className = `outcome tone-${tone}`;
    const body = el("div", "outcome-detail");
    detail = moneyAsDigits(detail);
    if (code && detail.endsWith(code)) body.append(document.createTextNode(detail.slice(0, -code.length)), el("span", "code", code));
    else body.textContent = detail;
    if (dev) body.append(devSpan(` · ${dev}`));
    outcomeEl.replaceChildren(el("div", "outcome-title", title), ...(detail || dev ? [body] : []));
  }

  // ---------------------------------------------------------------- the order card

  function hideFound(): void {
    foundView = null;
    itemsPanel.hidden = true;
    orderView.hidden = false;
  }

  function renderFound(): void {
    if (!foundView) return;
    const { items, source } = foundView;
    itemsSource.textContent = source === "fallback" ? "(fallback items: catalog unreachable)" : "";
    const stores = [...new Set(items.map((i) => (typeof i.store === "string" ? i.store : "")).filter(Boolean))];
    itemsStore.textContent = stores.length === 1 ? stores[0] : "";
    itemsEl.replaceChildren(
      ...items.map((item) => {
        const li = el("li", "found-item");
        const row = el("div", "found-row");
        row.append(el("span", "found-name", item.name), el("span", "found-price num", usd(item.price)));
        const sub = el("div", "found-sub");
        const size = [item.size, item.brand].filter((v) => typeof v === "string" && v).join(" · ");
        if (size) sub.append(el("span", "", size));
        if (item.usual) sub.append(el("span", "badge badge-ok", PAGE_WORDS[lang].usual));
        if (stores.length > 1 && typeof item.store === "string" && item.store) sub.append(el("span", "", item.store));
        li.append(row);
        if (sub.childNodes.length) li.append(sub);
        return li;
      }),
    );
    if (!items.length) itemsEl.append(el("li", "found-none", PAGE_WORDS[lang].no_matches));
    const n = cartView.lines.reduce((t, l) => t + l.qty, 0);
    foundFoot.hidden = n === 0;
    $("found-summary").textContent = `${PAGE_WORDS[lang].your_order} · ${STAGE_WORDS[lang].items(n)}`;
    $("found-total").textContent = usd(cartView.total);
    itemsPanel.hidden = false;
    orderView.hidden = true;
  }

  function renderOrder(): void {
    const { lines: cartLines, total } = cartView;
    cartTotal.textContent = usd(total);
    $("order-empty-text").textContent = NOTHING_YET[lang];
    const empty = cartLines.length === 0;
    cartEl.hidden = empty;
    orderEmpty.hidden = !empty;
    cartTotalRow.hidden = empty;
    budgetEl.hidden = !empty || budgetLeft === null;
    if (budgetLeft !== null) $("budget-left").textContent = usd(budgetLeft);
    orderBadge.hidden = waitSecs === null || empty;
    if (empty) {
      cartEl.replaceChildren();
      return;
    }
    // Grouped under the store's name, in the order the stores first appear; a bill has a bolt, a store a shopfront.
    const stores = [...new Set(cartLines.map((l) => l.store ?? ""))];
    const rows: HTMLLIElement[] = [];
    for (const store of stores) {
      const mine = cartLines.filter((l) => (l.store ?? "") === store);
      if (store) {
        const head = el("li", "store");
        head.append(icon(mine.every((l) => l.bill) ? "bolt" : "store"), document.createTextNode(store));
        rows.push(head);
      }
      for (const line of mine) {
        const li = el("li", "line");
        li.append(el("span", "line-name", `${line.qty > 1 ? `${line.qty} × ` : ""}${line.name}`), el("span", "price num", usd(line.line_total)));
        rows.push(li);
      }
    }
    cartEl.replaceChildren(...rows);
  }

  // ---------------------------------------------------------------- co-sign

  function renderCosign(): void {
    if (!cosignOn) return;
    const words = STAGE_WORDS[lang];
    $("cosign-title").textContent = words.cosign_title;
    $("cosign-lines").replaceChildren(
      ...cosignLines(cosignOn.mandate, lang).map((line: RuleLine) => {
        const li = el("li");
        const text = el("span");
        text.append(...boldAmounts(line.text));
        li.append(icon(line.icon, 34), text);
        return li;
      }),
    );
    $("cosign-when").textContent = words.signed_when(timeOfDay(cosignOn.at, lang));
  }

  function hideCosign(): void {
    if (cosignTimer) clearTimeout(cosignTimer);
    cosignTimer = null;
    cosignOn = null;
    cosignEl.hidden = true;
    document.body.classList.remove("cosign-on");
    renderButton();
  }

  // ---------------------------------------------------------------- receipts

  /** The card for this order in the receipt row, created from the template the first time. */
  function receiptCard(orderId: string): HTMLElement {
    const existing = receiptsEl.querySelector<HTMLElement>(`[data-order="${CSS.escape(orderId)}"]`);
    if (existing) return existing;
    const card = (receiptTemplate.content.firstElementChild as HTMLElement).cloneNode(true) as HTMLElement;
    card.dataset.order = orderId;
    receiptsEl.append(card);
    return card;
  }

  function renderPlacedHead(): void {
    const cards = [...receiptsEl.querySelectorAll<HTMLElement>("[data-order]")];
    const allBills = cards.length > 0 && cards.every((c) => c.dataset.bill === "1");
    $("placed-title").textContent = STAGE_WORDS[placedLang].placed(cards.length, allBills);
    const L = RECEIPT_LABELS[placedLang];
    $("placed-note").textContent = placedNote?.key ? L.note[placedNote.key] : L.note.on_screen;
    receiptsEl.dataset.count = String(cards.length);
  }

  function closeReceipts(): void {
    receiptOverlay.hidden = true;
    receiptsEl.replaceChildren();
    placedQr.replaceChildren();
    placedQr.hidden = true;
    placedScan.hidden = true;
    document.body.classList.remove("placed-on");
  }

  // ---------------------------------------------------------------- the Protected card

  function closeProtected(): void {
    if (protectedEl.hidden) return;
    protectedEl.hidden = true;
    const back = focusBeforeProtect;
    focusBeforeProtect = null;
    if (back && back.isConnected && back !== document.body) back.focus({ preventScroll: true });
    else if (document.activeElement instanceof HTMLElement) document.activeElement.blur();
  }

  function renderBanners(): void {
    bannerEl.replaceChildren(
      ...[...banners].map(([key, text]) => {
        const pill = el("div", "banner-pill");
        pill.append(icon(key === "paused" ? "pause" : "clock", 26), el("span", "", text));
        return pill;
      }),
    );
    bannerEl.hidden = banners.size === 0;
  }

  // ---------------------------------------------------------------- start

  setLang("en");
  markLatest();
  // the stage's footer grows and shrinks (choices, the wait, an outcome): the latest line stays in view
  if (typeof ResizeObserver === "function") new ResizeObserver(scroll).observe(log);

  return {
    state(state) {
      stateEl.textContent = state;
      stateEl.className = `pill state-${state} dev`;
      currentState = state;
      if (state !== "off" && state !== "connecting") calm.delete("trouble");
      if (state !== "off") calm.delete("start_first");
      if (state === "off") {
        showReadBack(false);
        hideCosign();
      }
      renderButton();
      renderCalm();
      onStateChange(state);
    },

    language(next) {
      if (next === lang) return;
      setLang(next);
    },

    status(text, kind = "info") {
      statusEl.textContent = text;
      statusEl.className = `status ${kind} dev`;
      // Ruth reads a calm line in her language, never the technical reason (that stays in the operator view)
      if (kind === "error") calm.add(/microphone|secure context/i.test(text) ? "no_mic" : "trouble");
      else if (/^press start first/i.test(text)) calm.add("start_first");
      else if (/^reconnected/i.test(text)) calm.delete("trouble");
      renderCalm();
    },

    transcript(role, key, text, final, spokenLang, mark?: TranscriptMark) {
      let li = lines.get(`${role}:${key}`);
      if (!li) {
        if (role === "shopper") newTurn();
        li = addLine(role, (row) => row.append(el("span", "who"), el("span", "text")));
        lines.set(`${role}:${key}`, li);
      }
      const who = li.querySelector(".who")!;
      who.replaceChildren(document.createTextNode(role === "shopper" ? PAGE_WORDS[lang].you : "Chaperone"));
      if (role === "shopper" && spokenLang) who.append(devSpan(` (${spokenLang})`));
      // Chaperone's own lines say money in words in Spanish and Hindi; the screen shows digits
      const shown = role === "agent" ? moneyAsDigits(text) : text;
      li.querySelector(".text")!.textContent = mark ? `${shown} ${PAGE_WORDS[lang][mark]}`.trim() : shown;
      li.classList.toggle("live", !final);
      li.classList.toggle("marked", !!mark || !text || text === "...");
      markLatest();
      scroll();
    },

    note(text, kind: NoteKind = "info") {
      addLine(`note ${kind}`, (row) => row.append(document.createTextNode(text)));
    },

    firstSound(ms, via) {
      soundEl.textContent = `first sound ${ms} ms (${via === "earcon" ? "tick" : via})`;
    },

    latency(ms, stats, label) {
      latencyEl.textContent = `${ms} ms`;
      latencyEl.className = `metric-value ${ms <= 1500 ? "good" : "slow"}`;
      const base = stats ? `voice turns: min ${stats.min} · median ${stats.median} · n ${stats.count}` : "voice turns: none yet";
      statsEl.textContent = label ? `${base} · last: ${label} (not counted)` : base;
    },

    rules(ids, action, say) {
      if (!ids.length && action === "proceed") return;
      rulesEl.hidden = false;
      rulesEl.className = `${action === "refuse" || action === "deny" ? "rules" : "rules soft"} dev`;
      const title = action === "refuse" ? "REFUSED" : action.toUpperCase();
      rulesEl.textContent = `${title}: ${ids.join(", ") || "(no rule id)"}`;
      if (say) rulesEl.append(el("small", "", say));
    },

    clearRules() {
      rulesEl.hidden = true;
      rulesEl.textContent = "";
    },

    items(items: CatalogItem[], source: string) {
      foundView = { items, source };
      renderFound();
    },

    decision(result) {
      if (typeof result.decision === "string") {
        const order = result.order as Record<string, unknown> | undefined;
        const link = order && typeof order === "object" ? String(order.payment_url ?? order.url ?? order.link ?? "") : "";
        this.note(`Policy decision: ${result.decision}${result.decision_id ? ` (${result.decision_id})` : ""}${link ? ` · payment link ${link}` : ""}`, result.decision === "deny" ? "rule" : "tool");
      } else if (result.error) {
        this.note(`Checkout: ${String(result.error)}`, "error");
      } else {
        this.note(`Checkout response: ${JSON.stringify(result).slice(0, 300)}`, "tool");
      }
    },

    cart(cartLines: CartLineView[], total: number) {
      cartView = { lines: cartLines, total };
      // a cart change voids the read-back, and what was found has been chosen from
      showReadBack(false);
      hideFound();
      renderOrder();
    },

    outcome(outcome: CheckoutOutcome) {
      showReadBack(false);
      const draw = () => {
        const title = OUTCOME_TITLES[lang][outcome.status];
        const tone = outcome.status === "ordered" ? "ok" : outcome.status === "waiting_for_caregiver" ? "wait" : outcome.status === "error" ? "err" : "none";
        const ids = [
          (outcome.order_ids ?? (outcome.order_id ? [outcome.order_id] : [])).map((id) => `order ${id}`).join(", "),
          outcome.decision_id && `decision ${outcome.decision_id}`,
        ]
          .filter(Boolean)
          .join(" · ");
        showOutcome(tone, `${title}${outcome.total !== undefined ? ` · ${usd(outcome.total)}` : ""}`, outcome.say ?? "", ids || undefined);
      };
      draw();
      redrawOutcome = draw;
    },

    notice(title: string, detail: string, tone: "ok" | "warn" | "bad", devDetail?: string) {
      // yellow is kept for "a person decides": a notice is done (green), for her to note (plain) or broken (red)
      const codePrefix = NOTICE_WORDS[lang].pickup_code("").trim();
      const code = codePrefix && detail.startsWith(codePrefix) ? detail.slice(codePrefix.length).trim() : "";
      showOutcome(tone === "ok" ? "ok" : tone === "bad" ? "err" : "none", title, detail, devDetail, code || undefined);
      redrawOutcome = null; // its words arrive already in her language
    },

    protect(view: ProtectedView | null) {
      if (!view) {
        closeProtected();
        return;
      }
      const L = view.lang ?? lang;
      const P = PROTECT_WORDS[L];
      const care = view.tone === "care";
      protectedEl.lang = L;
      protectedEl.classList.toggle("care", care);
      $("protected-title").textContent = view.title ?? TITLES[L][care ? "care" : "protected"];
      const say = moneyAsDigits(withoutToldTail(view.say, L));
      const sayEl = $("protected-say");
      sayEl.textContent = say;
      sayEl.classList.toggle("long", say.length > (L === "hi" ? 150 : 180));
      const chips = [
        view.amount !== undefined && Number.isFinite(view.amount) ? usd(view.amount) : "",
        view.store ?? "",
        view.card_last4 ? P.visa_ending(view.card_last4) : "",
      ].filter(Boolean);
      const chipsEl = $("protected-chips");
      chipsEl.replaceChildren(...chips.map((c) => el("span", "chip", c)));
      chipsEl.hidden = chips.length === 0;
      $("protected-action").textContent = moneyAsDigits(view.action ?? "");
      $("protected-action-card").hidden = !view.action;
      $("protected-told").textContent = TOLD[L];
      const nothingWrong = $("protected-nothing-wrong");
      nothingWrong.textContent = P.nothing_wrong;
      nothingWrong.hidden = say.toLowerCase().includes(P.nothing_wrong.toLowerCase().replace(/[.।]$/, ""));
      protectedOk.textContent = P.ok;
      $("protected-anywhere").textContent = P.tap_anywhere;
      $("protected-detail").textContent = view.detail ?? "";
      if (protectedEl.hidden) {
        const active = document.activeElement;
        focusBeforeProtect = active instanceof HTMLElement && active !== protectedOk ? active : null;
      }
      protectedEl.hidden = false;
      protectedEl.querySelector<HTMLElement>(".protected-body")!.scrollTop = 0;
      protectedOk.focus({ preventScroll: true });
    },

    banner(key: string, text: string | null) {
      if (text) banners.set(key, text);
      else banners.delete(key);
      renderBanners();
    },

    waiting(secondsLeft: number | null) {
      if (secondsLeft === null) {
        waitSecs = null;
        waitTotal = 0;
        waitSince = null;
      } else {
        waitSince ??= new Date();
        waitSecs = secondsLeft;
        waitTotal = Math.max(waitTotal, secondsLeft, 1);
      }
      renderAsking();
      renderButton();
    },

    receipt(receipt: Receipt | null, note?: ReceiptNote, files?: { png?: string; pdf?: string }) {
      if (!receipt) {
        closeReceipts();
        return;
      }
      const card = receiptCard(receipt.order_id);
      const q = <T extends HTMLElement = HTMLElement>(sel: string) => card.querySelector<T>(sel)!;
      // The rendered paper slip (what a thermal printer would print), in the operator view.
      const slip = q(".slip");
      const pdfLink = q<HTMLAnchorElement>(".receipt-pdf");
      if (files?.png) {
        q<HTMLImageElement>(".slip-img").src = `${files.png}?v=${Date.now()}`;
        slip.hidden = false;
      } else if (files !== undefined || note?.key !== "preparing") {
        slip.hidden = true;
      }
      const L = RECEIPT_LABELS[receipt.lang] ?? RECEIPT_LABELS.en;
      pdfLink.hidden = !files?.pdf;
      pdfLink.textContent = L.pdf;
      if (files?.pdf) pdfLink.href = files.pdf;
      card.dataset.bill = receipt.bill ? "1" : "0";
      card.lang = receipt.lang;
      q(".receipt-store").textContent = receipt.merchant;
      q(".receipt-title").textContent = L.title;
      q(".receipt-items").replaceChildren(
        ...receipt.items.map((item) => {
          const li = el("li");
          li.append(el("span", "", `${item.qty > 1 ? `${item.qty} × ` : ""}${item.name}`), el("span", "price num", usd(item.price * item.qty)));
          return li;
        }),
      );
      q(".receipt-total-label").textContent = L.total;
      q(".receipt-total-value").textContent = usd(receipt.total);
      // A bill has nothing to pick up: it shows that it is paid, and the account.
      q(".receipt-pickup").textContent = !receipt.bill && receipt.pickup ? L.pickup(receipt.pickup) : "";
      q(".receipt-code").hidden = !receipt.pickup_code || !!receipt.bill;
      q(".receipt-code-label").textContent = L.code;
      q(".receipt-code-value").textContent = receipt.pickup_code ?? "";
      q(".receipt-bill").hidden = !receipt.bill;
      // "Bill paid", with the account on a line of its own ("account …0098" is never split)
      const paidWords = L.billPaid("");
      q(".receipt-bill-text").textContent = paidWords;
      q(".receipt-bill-account").textContent = receipt.bill?.account_ref
        ? L.billPaid(receipt.bill.account_ref).slice(paidWords.length).replace(/^\s*·\s*/, "")
        : "";
      const extras = [receipt.savings ? L.saved(usd(receipt.savings)) : "", receipt.loyalty_points ? L.points(receipt.loyalty_points) : ""].filter(Boolean);
      const rewards = q(".receipt-rewards");
      rewards.hidden = !extras.length;
      rewards.textContent = extras.join(" · ");
      // One QR code for the session, beside the receipts, with the words under it.
      if (receipt.session_url) {
        placedQr.innerHTML = renderSVG(receipt.session_url, { border: 2 });
        placedQr.querySelector("svg")?.setAttribute("aria-hidden", "true");
        placedScan.textContent = L.scan;
      }
      placedQr.hidden = !placedQr.childElementCount;
      placedScan.hidden = placedQr.hidden;
      const paid = formatPaidAt(receipt.paid_at);
      q(".receipt-ids").textContent = [`${L.order} ${receipt.order_id}`, receipt.decision_id && `${L.decision} ${receipt.decision_id}`, paid && `${L.paid} ${paid}`]
        .filter(Boolean)
        .join(" · ");
      q(".receipt-sandbox").textContent = L.sandbox;
      // Ruth reads whether it printed (at the top); the time it took, or why it did not, is for the operator
      q(".receipt-note").textContent = [note?.key ? L.note[note.key] : "", note?.detail].filter(Boolean).join(" · ");
      placedLang = receipt.lang;
      placedNote = note;
      renderPlacedHead();
      receiptOverlay.lang = receipt.lang;
      receiptOverlay.hidden = false;
      document.body.classList.add("placed-on");
    },

    replay(on: boolean, replayLang?: string) {
      replayBanner.hidden = !on;
      replayBanner.textContent = on ? `REPLAY${replayLang ? ` · ${replayLang}` : ""}` : "";
      document.body.classList.toggle("replaying", on);
    },

    cleared(sessionId: string) {
      log.replaceChildren();
      lines.clear();
      cartView = { lines: [], total: 0 };
      budgetLeft = null;
      hideOutcome();
      rulesEl.hidden = true;
      rulesEl.textContent = "";
      hideFound();
      showReadBack(false);
      hideCosign();
      closeReceipts();
      closeProtected();
      banners.clear();
      renderBanners();
      waitSecs = null;
      waitTotal = 0;
      waitSince = null;
      calm.delete("start_first");
      calm.delete("trouble");
      sessionEl.textContent = sessionId;
      // the next shopper starts in English on the screen; her first words set the language again
      setLang("en");
      markLatest();
    },

    level(rms) {
      const level = levelFromRms(rms);
      levelEl.style.width = `${Math.round(level * 100)}%`;
      ptt.style.setProperty("--level", level.toFixed(2));
    },

    micDevice(label) {
      micLabel.textContent = label;
      calm.delete("no_mic"); // a microphone is open again
      renderCalm();
    },

    readBack(waiting: boolean) {
      showReadBack(waiting);
    },

    budget(left: number) {
      budgetLeft = Number.isFinite(left) ? left : null;
      renderOrder();
    },

    cosign(mandate: Record<string, unknown>) {
      cosignOn = { mandate, at: new Date() };
      renderCosign();
      cosignEl.hidden = false;
      document.body.classList.add("cosign-on");
      if (cosignTimer) clearTimeout(cosignTimer);
      cosignTimer = setTimeout(hideCosign, 5 * 60_000); // the station waits 5 minutes for her answer
      renderButton();
    },
  };
}
