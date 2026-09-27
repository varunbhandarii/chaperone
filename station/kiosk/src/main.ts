// Station page wiring: Start/Stop, push-to-talk (key, USB HID button, touch), typed input, devices.

import { health } from "./services.ts";
import { StationAgent, type AgentState } from "./agent.ts";
import { canSelectOutput, setOutputDevice } from "./audio.ts";
import { SERVICES_HOST, URLS, VOICE, VOICE_NAME, WS_OVERRIDE } from "./config.ts";
import type { Lang } from "./lang.ts";
import { LINES_LOADED } from "./lines.ts";
import { requestReset } from "./services.ts";
import { createUI } from "./ui.ts";

const KEY_STORAGE = "chaperone.pttKey";
const SPEAKER_STORAGE = "chaperone.speaker";
const MIC_STORAGE = "chaperone.mic";
const DEFAULT_KEY = "Space";

function $<T extends HTMLElement = HTMLElement>(id: string): T {
  const el = document.getElementById(id);
  if (!el) throw new Error(`missing #${id}`);
  return el as T;
}

function load(key: string, fallback: string): string {
  try {
    return localStorage.getItem(key) || fallback;
  } catch {
    return fallback;
  }
}

function save(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* storage blocked: the setting lasts for this page only */
  }
}

const startBtn = $<HTMLButtonElement>("start");
const ptt = $<HTMLButtonElement>("ptt");
const pttHint = $("ptt-hint");
const pttKeyEl = $("ptt-key");
const lastKeyEl = $("last-key");
const mapBtn = $<HTMLButtonElement>("map-key");
const resetBtn = $<HTMLButtonElement>("reset-key");
const micSelect = $<HTMLSelectElement>("mic");
const speakerSelect = $<HTMLSelectElement>("speaker");
const speakerNote = $("speaker-note");
const typedForm = $<HTMLFormElement>("typed");
const typedInput = $<HTMLInputElement>("typed-input");

function renderServices(): void {
  const s = health.snapshot();
  const mark = (name: keyof typeof s) => (s[name] === "up" ? "up" : s[name] === "down" ? "DOWN" : "?");
  $("services").textContent =
    `relay ${URLS.relay} [${mark("relay")}] · policy ${URLS.policy} [${mark("policy")}] · catalog ${URLS.catalog} [${mark("catalog")}]` +
    ` (host ${SERVICES_HOST}) · voice ${VOICE_NAME} · model ${VOICE.model}${WS_OVERRIDE ? ` · realtime MOCK ${WS_OVERRIDE}` : ""}`;
}
health.onChange(renderServices);
renderServices();

let agent: StationAgent | null = null;
let pttKey = load(KEY_STORAGE, DEFAULT_KEY);
let mapping = false;

const ui = createUI((state: AgentState) => {
  startBtn.textContent = state === "off" ? "Start" : "Stop";
});

function showKey(): void {
  pttKeyEl.textContent = pttKey;
  pttHint.textContent = `hold to talk · key: ${pttKey}`;
}
showKey();

console.info(`[lines] ${LINES_LOADED} spoken lines loaded from ai/prompts`);

// ---------- Host shortcuts ----------
// Ctrl+Shift+R  reset: new session, cart and screen; the relay resets policy, merchant and the live ledger
// Ctrl+Shift+P  replay a cached session (chooser)
// Ctrl+Shift+S  save this session as the cached session for its language
// Escape        close the receipt or the chooser, or stop a replay

const chooser = $("replay-chooser");

async function hostReset(): Promise<void> {
  if (agent) await agent.resetSession("Host key", true);
  else ui.note((await requestReset()) ? "Relay reset done (station not started)." : "Relay reset failed.", "info");
}

chooser.addEventListener("click", (e) => {
  const lang = (e.target as HTMLElement).dataset?.replay as Lang | undefined;
  if (!lang) return;
  chooser.hidden = true;
  agent ??= new StationAgent(ui); // replay works without a voice session
  void agent.replay(lang);
});
$("replay-cancel").addEventListener("click", () => (chooser.hidden = true));
$("receipt-reprint").addEventListener("click", () => void agent?.reprint());
$("receipt-close").addEventListener("click", () => ui.receipt(null));

window.addEventListener(
  "keydown",
  (e) => {
    if (e.key === "Escape") {
      if (!chooser.hidden) chooser.hidden = true;
      else if (agent?.isReplaying) agent.stopReplay();
      else if (!$("protected").hidden) ui.protect(null);
      else ui.receipt(null);
      return;
    }
    if (!(e.ctrlKey && e.shiftKey) || e.repeat) return;
    const k = e.key.toLowerCase();
    if (k !== "r" && k !== "p" && k !== "s") return;
    e.preventDefault(); // Ctrl+Shift+R would otherwise reload the page
    e.stopImmediatePropagation();
    if (k === "r") void hostReset();
    else if (k === "p") chooser.hidden = false;
    else void agent?.saveRecording();
  },
  { capture: true },
);

// ---------- kiosk: Ruth's shopper view by default; ?operator (or Ctrl+Shift+O) shows the operator panels ----------

const viewBtn = $<HTMLButtonElement>("view-toggle");

function setShopperView(on: boolean): void {
  document.body.classList.toggle("shopper", on);
  viewBtn.textContent = on ? "Operator view" : "Shopper view";
  viewBtn.setAttribute("aria-pressed", String(on));
}

setShopperView(!new URLSearchParams(location.search).has("operator"));
viewBtn.addEventListener("click", () => {
  setShopperView(!document.body.classList.contains("shopper"));
  viewBtn.blur(); // keep Space for push-to-talk
});
window.addEventListener("keydown", (e) => {
  if (e.ctrlKey && e.shiftKey && e.key.toLowerCase() === "o") {
    e.preventDefault();
    setShopperView(!document.body.classList.contains("shopper"));
  }
});

// ---------- start / stop ----------

startBtn.addEventListener("click", async () => {
  if (agent?.isStarted) {
    await agent.stop();
    agent = null;
    return;
  }
  agent = new StationAgent(ui);
  $("session").textContent = agent.sessionId;
  ui.status("Starting: token, microphone and voice connection in parallel...");
  const starting = agent.start(micSelect.value || undefined); // creates the AudioContext synchronously (user gesture)
  const current = agent;
  void applySpeaker(current);
  await starting;
  await refreshDevices();
  void applySpeaker(current);
});

// ---------- push-to-talk ----------

function press(): void {
  if (!agent?.isStarted) return;
  ui.clearRules();
  agent.press();
}

function release(): void {
  void agent?.release();
}

function isTypingTarget(target: EventTarget | null, e: KeyboardEvent): boolean {
  const el = target as HTMLElement | null;
  if (!el) return false;
  const typing = el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT" || el.isContentEditable;
  // Printable keys and Space belong to the text box; dedicated HID keys (F13, media keys...) still work.
  return typing && (e.key.length === 1 || e.code === "Space");
}

window.addEventListener("keydown", (e) => {
  const code = e.code || e.key;
  lastKeyEl.textContent = `${code}${e.key && e.key !== code ? ` (key "${e.key}")` : ""}`;
  if (mapping) {
    e.preventDefault();
    mapping = false;
    pttKey = code;
    save(KEY_STORAGE, pttKey);
    mapBtn.textContent = "Map a new key";
    showKey();
    ui.note(`Push-to-talk key set to ${pttKey}`);
    return;
  }
  if (code !== pttKey || isTypingTarget(e.target, e)) return;
  e.preventDefault();
  if (!e.repeat) press();
});

window.addEventListener("keyup", (e) => {
  const code = e.code || e.key;
  if (code !== pttKey || isTypingTarget(e.target, e)) return;
  e.preventDefault();
  release();
});

// A lost focus must never leave the microphone streaming.
window.addEventListener("blur", release);
document.addEventListener("visibilitychange", () => {
  if (document.hidden) release();
});

ptt.addEventListener("pointerdown", (e) => {
  if (e.button !== 0) return;
  e.preventDefault();
  ptt.setPointerCapture(e.pointerId);
  press();
});
ptt.addEventListener("pointerup", release);
ptt.addEventListener("pointercancel", release);
ptt.addEventListener("lostpointercapture", release);
ptt.addEventListener("contextmenu", (e) => e.preventDefault());
// Keyboard activation of the focused button is handled by the key listener, not by click.
ptt.addEventListener("click", (e) => e.preventDefault());

mapBtn.addEventListener("click", () => {
  mapping = !mapping;
  mapBtn.textContent = mapping ? "Press the button or key now..." : "Map a new key";
  mapBtn.blur();
});
resetBtn.addEventListener("click", () => {
  pttKey = DEFAULT_KEY;
  save(KEY_STORAGE, pttKey);
  showKey();
  resetBtn.blur();
});

// ---------- typed input ----------

typedForm.addEventListener("submit", (e) => {
  e.preventDefault();
  const text = typedInput.value.trim();
  if (!text) return;
  if (!agent?.isStarted) {
    ui.status("Press Start first.", "warn");
    return;
  }
  typedInput.value = "";
  ui.clearRules();
  void agent.sendText(text);
});

// ---------- devices ----------

async function refreshDevices(): Promise<void> {
  if (!navigator.mediaDevices?.enumerateDevices) return;
  const devices = await navigator.mediaDevices.enumerateDevices();
  fill(micSelect, devices.filter((d) => d.kind === "audioinput"), "Default microphone", load(MIC_STORAGE, ""));
  fill(speakerSelect, devices.filter((d) => d.kind === "audiooutput"), "Default speaker", load(SPEAKER_STORAGE, ""));
}

function fill(select: HTMLSelectElement, devices: MediaDeviceInfo[], defaultLabel: string, wanted: string): void {
  const current = select.value || wanted;
  const options = [new Option(defaultLabel, "")];
  devices
    .filter((d) => d.deviceId && d.deviceId !== "default")
    .forEach((d, i) => options.push(new Option(d.label || `${defaultLabel} ${i + 1}`, d.deviceId)));
  select.replaceChildren(...options);
  if ([...select.options].some((o) => o.value === current)) select.value = current;
}

async function applySpeaker(target: StationAgent | null): Promise<void> {
  const ctx = target?.ctx;
  if (!ctx) return;
  if (!canSelectOutput(ctx)) {
    speakerNote.textContent = "this browser cannot pick an output; use the system default";
    return;
  }
  try {
    await setOutputDevice(ctx, speakerSelect.value);
    speakerNote.textContent = "";
  } catch (err) {
    speakerNote.textContent = `could not switch: ${err instanceof Error ? err.message : err}`;
  }
}

micSelect.addEventListener("change", () => {
  save(MIC_STORAGE, micSelect.value);
  void agent?.switchMic(micSelect.value);
});
speakerSelect.addEventListener("change", () => {
  save(SPEAKER_STORAGE, speakerSelect.value);
  void applySpeaker(agent);
});
navigator.mediaDevices?.addEventListener?.("devicechange", () => void refreshDevices());
void refreshDevices();

if (!window.isSecureContext) {
  ui.status("This page is not a secure context, so the browser blocks the microphone. Open it on http://localhost:5173 (or via HTTPS).", "error");
}
