// DOM rendering for the station page: state strip, transcripts, cart by store, outcome, the Protected card,
// banners, receipts (one per store), latency, rule banner, items, meter.

import type { AgentState, AgentUI, NoteKind, ProtectedView } from "./agent.ts";
import { renderSVG } from "uqr";
import type { CartLineView, CatalogItem, CheckoutOutcome } from "./cart.ts";
import type { Lang } from "./lang.ts";
import { RECEIPT_LABELS, formatPaidAt, type Receipt } from "./receipt.ts";
import { levelFromRms } from "./pcm.ts";

function $(id: string): HTMLElement {
  const el = document.getElementById(id);
  if (!el) throw new Error(`missing #${id}`);
  return el;
}

/** One state at a time, in words, in Ruth's language: the strip and the big button say the same thing. */
const STATE_WORDS: Record<Lang, Record<AgentState, string>> = {
  en: {
    off: "Press Start",
    connecting: "Getting ready…",
    ready: "Press and hold to talk",
    listening: "Listening…",
    thinking: "Checking…",
    checking: "Checking…",
    speaking: "Speaking…",
    waiting: "Asking Priyank…",
  },
  es: {
    off: "Pulse Start",
    connecting: "Preparando…",
    ready: "Mantenga presionado para hablar",
    listening: "Escuchando…",
    thinking: "Revisando…",
    checking: "Revisando…",
    speaking: "Hablando…",
    waiting: "Preguntando a Priyank…",
  },
  hi: {
    off: "Start दबाएँ",
    connecting: "तैयार हो रही हूँ…",
    ready: "बोलने के लिए दबाकर रखें",
    listening: "सुन रही हूँ…",
    thinking: "जाँच रही हूँ…",
    checking: "जाँच रही हूँ…",
    speaking: "बोल रही हूँ…",
    waiting: "प्रियंक से पूछ रही हूँ…",
  },
};

const TOLD: Record<Lang, string> = { en: "Priyank has been told", es: "Priyank ya lo sabe", hi: "प्रियंक को बता दिया गया है" };
const TITLES: Record<Lang, { protected: string; care: string }> = {
  en: { protected: "Protected", care: "Be careful" },
  es: { protected: "Protegida", care: "Tenga cuidado" },
  hi: { protected: "सुरक्षित", care: "सावधान रहें" },
};
const NOTHING_YET: Record<Lang, string> = { en: "Nothing yet", es: "Nada todavía", hi: "अभी कुछ नहीं" };

function usd(n: number): string {
  return `$${n.toFixed(2)}`;
}

function clock(): string {
  return new Date().toLocaleTimeString([], { hour12: false });
}

export function createUI(onStateChange: (state: AgentState) => void): AgentUI & { clearRules(): void } {
  const stateEl = $("state");
  const statusEl = $("status");
  const ptt = $("ptt") as HTMLButtonElement;
  const pttLabel = $("ptt-label");
  const latencyEl = $("latency");
  const statsEl = $("latency-stats");
  const soundEl = $("first-sound");
  const rulesEl = $("rules");
  const log = $("log");
  const itemsPanel = $("items-panel");
  const itemsEl = $("items");
  const itemsSource = $("items-source");
  const levelEl = $("level");
  const micLabel = $("mic-label");
  const strip = $("strip");
  const bannerEl = $("banner");
  const cartEl = $("cart");
  const cartTotal = $("cart-total");
  const outcomeEl = $("outcome");
  const receiptOverlay = $("receipt-overlay");
  const receiptsEl = $("receipts");
  const receiptTemplate = $("receipt-template") as HTMLTemplateElement;
  const protectedEl = $("protected");
  const replayBanner = $("replay-banner");
  const sessionEl = $("session");
  let currentState: AgentState = "off";
  let waitSecs: number | null = null;
  let lang: Lang = "en";
  const banners = new Map<string, string>();

  function stripLabel(state: AgentState): string {
    const words = STATE_WORDS[lang][state];
    return state === "waiting" && waitSecs !== null ? `${words} · ${waitSecs} s` : words;
  }

  const lines = new Map<string, HTMLLIElement>();

  function scroll(): void {
    log.scrollTop = log.scrollHeight;
  }

  function addLine(cls: string, html: (li: HTMLLIElement) => void): HTMLLIElement {
    const li = document.createElement("li");
    li.className = cls;
    const time = document.createElement("span");
    time.className = "time";
    time.textContent = clock();
    li.append(time);
    html(li);
    log.append(li);
    scroll();
    return li;
  }

  function devSpan(text: string): HTMLSpanElement {
    const s = document.createElement("span");
    s.className = "dev";
    s.textContent = text;
    return s;
  }

  function renderBanners(): void {
    bannerEl.replaceChildren(
      ...[...banners.values()].map((text) => Object.assign(document.createElement("div"), { className: "banner-row", textContent: text })),
    );
    bannerEl.hidden = banners.size === 0;
  }

  function emptyCart(): void {
    cartEl.replaceChildren(Object.assign(document.createElement("li"), { className: "empty", textContent: NOTHING_YET[lang] }));
  }

  /** The card for this order in the receipt stack, created from the template the first time. */
  function receiptCard(orderId: string): HTMLElement {
    const existing = receiptsEl.querySelector<HTMLElement>(`[data-order="${CSS.escape(orderId)}"]`);
    if (existing) return existing;
    const card = (receiptTemplate.content.firstElementChild as HTMLElement).cloneNode(true) as HTMLElement;
    card.dataset.order = orderId;
    receiptsEl.append(card);
    return card;
  }

  return {
    state(state) {
      stateEl.textContent = state;
      stateEl.className = `pill state-${state} dev`;
      ptt.className = `ptt state-${state}`;
      ptt.disabled = state === "off";
      pttLabel.textContent = STATE_WORDS[lang][state];
      currentState = state;
      strip.textContent = stripLabel(state);
      strip.className = `strip state-${state}`;
      onStateChange(state);
    },

    language(next) {
      if (next === lang) return;
      lang = next;
      pttLabel.textContent = STATE_WORDS[lang][currentState];
      strip.textContent = stripLabel(currentState);
      if (cartEl.querySelector("li.empty")) emptyCart();
    },

    status(text, kind = "info") {
      statusEl.textContent = text;
      statusEl.className = `status ${kind} dev`;
    },

    transcript(role, key, text, final, spokenLang) {
      let li = lines.get(`${role}:${key}`);
      if (!li) {
        li = addLine(role, (el) => {
          const who = document.createElement("span");
          who.className = "who";
          const body = document.createElement("span");
          body.className = "text";
          el.append(who, body);
        });
        lines.set(`${role}:${key}`, li);
      }
      const who = li.querySelector(".who")!;
      who.replaceChildren(document.createTextNode(role === "shopper" ? "You" : "Chaperone"));
      if (role === "shopper" && spokenLang) who.append(devSpan(` (${spokenLang})`));
      who.append(document.createTextNode(":"));
      li.querySelector(".text")!.textContent = text;
      li.classList.toggle("live", !final);
      scroll();
    },

    note(text, kind: NoteKind = "info") {
      addLine(`note ${kind}`, (el) => el.append(document.createTextNode(text)));
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
      if (say) {
        const small = document.createElement("small");
        small.textContent = say;
        rulesEl.append(small);
      }
    },

    clearRules() {
      rulesEl.hidden = true;
      rulesEl.textContent = "";
    },

    items(items: CatalogItem[], source: string) {
      itemsPanel.hidden = false;
      itemsSource.textContent = source === "fallback" ? "(fallback items: catalog unreachable)" : "";
      itemsEl.replaceChildren(
        ...items.map((item) => {
          const li = document.createElement("li");
          const name = document.createElement("span");
          name.textContent = `${item.name}${item.brand ? ` · ${item.brand}` : ""}${item.size ? ` · ${item.size}` : ""}`;
          if (item.usual) name.append(Object.assign(document.createElement("span"), { className: "usual", textContent: "usual" }));
          if (typeof item.store === "string" && item.store) {
            name.append(Object.assign(document.createElement("span"), { className: "store-name", textContent: item.store }));
          }
          const price = document.createElement("span");
          price.className = "price";
          price.textContent = usd(item.price);
          li.append(name, price);
          return li;
        }),
      );
      if (!items.length) itemsEl.append(Object.assign(document.createElement("li"), { textContent: "No matches" }));
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
      cartTotal.textContent = usd(total);
      if (!cartLines.length) {
        emptyCart();
        return;
      }
      // Grouped under the store's name, in the order the stores first appear.
      const stores = [...new Set(cartLines.map((l) => l.store ?? ""))];
      const rows: HTMLLIElement[] = [];
      for (const store of stores) {
        if (store) rows.push(Object.assign(document.createElement("li"), { className: "store", textContent: store }));
        for (const line of cartLines.filter((l) => (l.store ?? "") === store)) {
          const li = document.createElement("li");
          const name = document.createElement("span");
          name.textContent = `${line.qty > 1 ? `${line.qty} × ` : ""}${line.name}`;
          const price = document.createElement("span");
          price.className = "price";
          price.textContent = usd(line.line_total);
          li.append(name, price);
          rows.push(li);
        }
      }
      cartEl.replaceChildren(...rows);
    },

    outcome(outcome: CheckoutOutcome) {
      outcomeEl.hidden = false;
      outcomeEl.className = `outcome ${outcome.status}`;
      const title = {
        ordered: "Ordered",
        waiting_for_caregiver: "Asking Priyank",
        declined: "Not ordered",
        error: "Order not placed",
      }[outcome.status];
      outcomeEl.textContent = `${title}${outcome.total !== undefined ? ` · ${usd(outcome.total)}` : ""}`;
      const small = document.createElement("small");
      small.append(document.createTextNode(outcome.say ?? ""));
      const ids = [(outcome.order_ids ?? (outcome.order_id ? [outcome.order_id] : [])).map((id) => `order ${id}`).join(", "), outcome.decision_id && `decision ${outcome.decision_id}`]
        .filter(Boolean)
        .join(" · ");
      if (ids) small.append(devSpan(` · ${ids}`));
      outcomeEl.append(small);
    },

    notice(title: string, detail: string, tone: "ok" | "warn" | "bad", devDetail?: string) {
      outcomeEl.hidden = false;
      outcomeEl.className = `outcome ${tone === "ok" ? "ordered" : tone === "warn" ? "waiting_for_caregiver" : "declined"}`;
      outcomeEl.textContent = title;
      const small = document.createElement("small");
      small.append(document.createTextNode(detail));
      if (devDetail) small.append(devSpan(` · ${devDetail}`));
      outcomeEl.append(small);
    },

    protect(view: ProtectedView | null) {
      if (!view) {
        protectedEl.hidden = true;
        return;
      }
      const L = view.lang ?? lang;
      $("protected-card").className = `protected-card${view.tone === "care" ? " care" : ""}`;
      $("protected-title").textContent = view.title ?? TITLES[L][view.tone === "care" ? "care" : "protected"];
      $("protected-say").textContent = view.say;
      $("protected-action").textContent = view.action ?? "";
      $("protected-told").textContent = TOLD[L];
      $("protected-detail").textContent = view.detail ?? "";
      protectedEl.hidden = false;
    },

    banner(key: string, text: string | null) {
      if (text) banners.set(key, text);
      else banners.delete(key);
      renderBanners();
    },

    waiting(secondsLeft: number | null) {
      waitSecs = secondsLeft;
      if (currentState === "waiting") strip.textContent = stripLabel(currentState);
    },

    receipt(receipt: Receipt | null, note?: string, files?: { png?: string; pdf?: string }) {
      if (!receipt) {
        receiptOverlay.hidden = true;
        receiptsEl.replaceChildren();
        return;
      }
      const card = receiptCard(receipt.order_id);
      const q = <T extends HTMLElement = HTMLElement>(sel: string) => card.querySelector<T>(sel)!;
      // The rendered paper slip (what a thermal printer would print) beside the large-type receipt.
      const slip = q(".slip");
      const pdfLink = q<HTMLAnchorElement>(".receipt-pdf");
      if (files?.png) {
        q<HTMLImageElement>(".slip-img").src = `${files.png}?v=${Date.now()}`;
        slip.hidden = false;
      } else if (files !== undefined || !note?.startsWith("Preparing")) {
        slip.hidden = true;
      }
      pdfLink.hidden = !files?.pdf;
      if (files?.pdf) pdfLink.href = files.pdf;
      const L = RECEIPT_LABELS[receipt.lang] ?? RECEIPT_LABELS.en;
      q(".receipt-store").textContent = receipt.merchant;
      q(".receipt-title").textContent = L.title;
      q(".receipt-items").replaceChildren(
        ...receipt.items.map((item) => {
          const li = document.createElement("li");
          const name = document.createElement("span");
          name.textContent = `${item.qty > 1 ? `${item.qty} × ` : ""}${item.name}`;
          const price = document.createElement("span");
          price.className = "price";
          price.textContent = usd(item.price * item.qty);
          li.append(name, price);
          return li;
        }),
      );
      q(".receipt-total-label").textContent = L.total;
      q(".receipt-total-value").textContent = usd(receipt.total);
      // A bill says who was paid and the account; it has no pickup.
      q(".receipt-pickup").textContent = receipt.bill
        ? L.paidTo(receipt.merchant, receipt.bill.account_ref)
        : receipt.pickup
          ? L.pickup(receipt.pickup)
          : "";
      const code = q(".receipt-code");
      code.hidden = !receipt.pickup_code || !!receipt.bill;
      code.textContent = receipt.pickup_code ? `${L.code}: ${receipt.pickup_code.split("").join(" ")}` : "";
      const extras = [receipt.savings ? L.saved(usd(receipt.savings)) : "", receipt.loyalty_points ? L.points(receipt.loyalty_points) : ""].filter(Boolean);
      const rewards = q(".receipt-rewards");
      rewards.hidden = !extras.length;
      rewards.textContent = extras.join(" · ");
      q(".receipt-qr").innerHTML = receipt.session_url ? renderSVG(receipt.session_url, { border: 2 }) : "";
      q(".receipt-scan").textContent = receipt.session_url ? L.scan : "";
      const paid = formatPaidAt(receipt.paid_at);
      q(".receipt-ids").textContent = [`${L.order} ${receipt.order_id}`, receipt.decision_id && `${L.decision} ${receipt.decision_id}`, paid && `${L.paid} ${paid}`]
        .filter(Boolean)
        .join(" · ");
      q(".receipt-sandbox").textContent = L.sandbox;
      q(".receipt-note").textContent = note ?? "";
      receiptOverlay.hidden = false;
    },

    replay(on: boolean, replayLang?: string) {
      replayBanner.hidden = !on;
      replayBanner.textContent = on ? `REPLAY${replayLang ? ` · ${replayLang}` : ""}` : "";
      document.body.classList.toggle("replaying", on);
    },

    cleared(sessionId: string) {
      log.replaceChildren();
      lines.clear();
      emptyCart();
      cartTotal.textContent = usd(0);
      outcomeEl.hidden = true;
      rulesEl.hidden = true;
      rulesEl.textContent = "";
      itemsPanel.hidden = true;
      receiptOverlay.hidden = true;
      receiptsEl.replaceChildren();
      protectedEl.hidden = true;
      banners.clear();
      renderBanners();
      waitSecs = null;
      sessionEl.textContent = sessionId;
    },

    level(rms) {
      levelEl.style.width = `${Math.round(levelFromRms(rms) * 100)}%`;
    },

    micDevice(label) {
      micLabel.textContent = label;
    },
  };
}
