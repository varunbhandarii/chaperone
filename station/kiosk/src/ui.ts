// DOM rendering for the station page: state strip, transcripts, cart, outcome, latency, rule banner, items, meter.

import type { AgentState, AgentUI, NoteKind } from "./agent.ts";
import { renderSVG } from "uqr";
import type { CartLineView, CatalogItem, CheckoutOutcome } from "./cart.ts";
import { RECEIPT_LABELS, formatPaidAt, type Receipt } from "./receipt.ts";
import { levelFromRms } from "./pcm.ts";

function $(id: string): HTMLElement {
  const el = document.getElementById(id);
  if (!el) throw new Error(`missing #${id}`);
  return el;
}

const STATE_LABEL: Record<AgentState, string> = {
  off: "Press Start first",
  connecting: "Connecting...",
  ready: "Hold to talk",
  listening: "Listening... release when done",
  thinking: "Thinking...",
  checking: "Checking...",
  speaking: "Speaking... press to interrupt",
  waiting: "Waiting for Priyank... hold to talk",
};

/** The companion screen's state strip: what the shopper needs to know at a glance. */
const STRIP_LABEL: Record<AgentState, string> = {
  off: "Press Start",
  connecting: "Connecting",
  ready: "Ready: hold the button and talk",
  listening: "Listening",
  thinking: "Thinking",
  checking: "Checking…",
  speaking: "Speaking",
  waiting: "Waiting for Priyank",
};

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
  const cartEl = $("cart");
  const cartTotal = $("cart-total");
  const outcomeEl = $("outcome");
  const receiptOverlay = $("receipt-overlay");
  const replayBanner = $("replay-banner");
  const sessionEl = $("session");
  let currentState: AgentState = "off";
  let waitSecs: number | null = null;

  function stripLabel(state: AgentState): string {
    return state === "waiting" && waitSecs !== null ? `${STRIP_LABEL.waiting} · ${waitSecs} s` : STRIP_LABEL[state];
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

  return {
    state(state) {
      stateEl.textContent = state;
      stateEl.className = `pill state-${state}`;
      ptt.className = `ptt state-${state}`;
      ptt.disabled = state === "off";
      pttLabel.textContent = STATE_LABEL[state];
      currentState = state;
      strip.textContent = stripLabel(state);
      strip.className = `strip state-${state}`;
      onStateChange(state);
    },

    status(text, kind = "info") {
      statusEl.textContent = text;
      statusEl.className = `status ${kind}`;
    },

    transcript(role, key, text, final, lang) {
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
      who.textContent = role === "shopper" ? `You${lang ? ` (${lang})` : ""}:` : "Chaperone:";
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
      rulesEl.className = action === "refuse" || action === "deny" ? "rules" : "rules soft";
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
          if (item.usual) {
            const u = document.createElement("span");
            u.className = "usual";
            u.textContent = "usual";
            name.append(u);
          }
          const price = document.createElement("span");
          price.className = "price";
          price.textContent = `$${item.price.toFixed(2)}`;
          li.append(name, price);
          return li;
        }),
      );
      if (!items.length) {
        const li = document.createElement("li");
        li.textContent = "No matches";
        itemsEl.append(li);
      }
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

    cart(lines: CartLineView[], total: number) {
      cartTotal.textContent = usd(total);
      if (!lines.length) {
        const li = document.createElement("li");
        li.className = "empty";
        li.textContent = "Nothing yet";
        cartEl.replaceChildren(li);
        return;
      }
      cartEl.replaceChildren(
        ...lines.map((line) => {
          const li = document.createElement("li");
          const name = document.createElement("span");
          name.textContent = `${line.qty > 1 ? `${line.qty} × ` : ""}${line.name}`;
          const price = document.createElement("span");
          price.className = "price";
          price.textContent = usd(line.line_total);
          li.append(name, price);
          return li;
        }),
      );
    },

    outcome(outcome: CheckoutOutcome) {
      outcomeEl.hidden = false;
      outcomeEl.className = `outcome ${outcome.status}`;
      const title = {
        ordered: "Ordered",
        waiting_for_caregiver: "Waiting for Priyank to approve",
        declined: "Not ordered",
        error: "Order not placed",
      }[outcome.status];
      outcomeEl.textContent = `${title}${outcome.total !== undefined ? ` · ${usd(outcome.total)}` : ""}`;
      const small = document.createElement("small");
      small.textContent = [outcome.say, outcome.order_id && `order ${outcome.order_id}`, outcome.decision_id && `decision ${outcome.decision_id}`]
        .filter(Boolean)
        .join(" · ");
      outcomeEl.append(small);
    },

    notice(title: string, detail: string, tone: "ok" | "warn" | "bad") {
      outcomeEl.hidden = false;
      outcomeEl.className = `outcome ${tone === "ok" ? "ordered" : tone === "warn" ? "waiting_for_caregiver" : "declined"}`;
      outcomeEl.textContent = title;
      const small = document.createElement("small");
      small.textContent = detail;
      outcomeEl.append(small);
    },

    waiting(secondsLeft: number | null) {
      waitSecs = secondsLeft;
      if (currentState === "waiting") strip.textContent = stripLabel(currentState);
    },

    receipt(receipt: Receipt | null, note?: string, files?: { png?: string; pdf?: string }) {
      if (!receipt) {
        receiptOverlay.hidden = true;
        return;
      }
      // The rendered paper slip (what a thermal printer would print) beside the large-type receipt.
      const slip = $("receipt-slip");
      const slipImg = $("receipt-slip-img") as HTMLImageElement;
      const pdfLink = $("receipt-pdf") as HTMLAnchorElement;
      if (files?.png) {
        slipImg.src = `${files.png}?v=${Date.now()}`;
        slip.hidden = false;
      } else if (files !== undefined || !note?.startsWith("Preparing")) {
        slip.hidden = true;
      }
      pdfLink.hidden = !files?.pdf;
      if (files?.pdf) pdfLink.href = files.pdf;
      const L = RECEIPT_LABELS[receipt.lang] ?? RECEIPT_LABELS.en;
      $("receipt-store").textContent = receipt.merchant;
      $("receipt-title").textContent = L.title;
      $("receipt-items").replaceChildren(
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
      $("receipt-total-label").textContent = L.total;
      $("receipt-total").textContent = usd(receipt.total);
      $("receipt-pickup").textContent = L.pickup(receipt.pickup);
      const code = $("receipt-code");
      code.hidden = !receipt.pickup_code;
      code.textContent = receipt.pickup_code ? `${L.code}: ${receipt.pickup_code.split("").join(" ")}` : "";
      const extras = [receipt.savings ? L.saved(usd(receipt.savings)) : "", receipt.loyalty_points ? L.points(receipt.loyalty_points) : ""].filter(Boolean);
      const rewards = $("receipt-rewards");
      rewards.hidden = !extras.length;
      rewards.textContent = extras.join(" · ");
      const qr = $("receipt-qr");
      qr.innerHTML = receipt.session_url ? renderSVG(receipt.session_url, { border: 2 }) : "";
      $("receipt-scan").textContent = receipt.session_url ? L.scan : "";
      const paid = formatPaidAt(receipt.paid_at);
      $("receipt-ids").textContent = [`${L.order} ${receipt.order_id}`, receipt.decision_id && `${L.decision} ${receipt.decision_id}`, paid && `${L.paid} ${paid}`]
        .filter(Boolean)
        .join(" · ");
      $("receipt-sandbox").textContent = L.sandbox;
      $("receipt-note").textContent = note ?? "";
      receiptOverlay.hidden = false;
    },

    replay(on: boolean, lang?: string) {
      replayBanner.hidden = !on;
      replayBanner.textContent = on ? `REPLAY${lang ? ` · ${lang}` : ""}` : "";
      document.body.classList.toggle("replaying", on);
    },

    cleared(sessionId: string) {
      log.replaceChildren();
      lines.clear();
      cartEl.replaceChildren(Object.assign(document.createElement("li"), { className: "empty", textContent: "Nothing yet" }));
      cartTotal.textContent = usd(0);
      outcomeEl.hidden = true;
      rulesEl.hidden = true;
      rulesEl.textContent = "";
      itemsPanel.hidden = true;
      receiptOverlay.hidden = true;
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
