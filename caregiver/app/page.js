"use client";

import { startAuthentication, startRegistration } from "@simplewebauthn/browser";
import { useEffect, useRef, useState } from "react";
import AppBar from "./components/AppBar";
import Approvals from "./components/Approvals";
import Confirm from "./components/Confirm";
import HistoryView from "./components/HistoryView";
import Home from "./components/Home";
import Message from "./components/Message";
import Rules, { ruleChanges, SignFooter } from "./components/Rules";
import Safety, { safetyItems } from "./components/Safety";
import TabBar, { TABS } from "./components/TabBar";
import Welcome from "./components/Welcome";
import Why from "./components/Why";
import { money } from "@/lib/money";
import { approvalWords, plainError, RETRY } from "@/lib/status";
import { payeeName, storeName } from "@/lib/stores";
import { categoryName, clockTime } from "@/lib/words";


const MANDATE = {
  mandate_id: "m_ruth_2026_09",
  shopper: "ruth",
  caregiver: "priya",
  currency: "USD",
  per_purchase_cap: 60,
  monthly_cap: 300,
  approval_threshold: 40,
  allowed_merchants: ["corner_market", "parkside_pharmacy", "main_street_home", "peachtree_power"],
  allowed_categories: ["grocery", "pharmacy", "household", "utility_bill"],
  blocked_categories: ["gift_card", "prepaid_card", "wire", "crypto", "lottery"],
  languages: ["es", "en", "hi"],
  valid_from: "2026-09-01",
  valid_to: "2026-12-31",
  billers: [{ merchant_id: "peachtree_power", account_ref: "PP-2231-0098", monthly_cap: 200 }],
  card: {
    blocked_mccs: ["4829", "6051", "6540", "7995"],
    category_caps: { 5411: 150, 5912: 80, 5310: 100, 5311: 100, 5251: 100 },
    default_cap: 60,
    atm_daily_cap: 100,
    unusual_multiplier: 3,
    cooldown: { hours: 24, caps: { 5912: 25, 5310: 25, 5311: 25, 6011: 0, default: 25 } },
  },
  trusted_contacts: [
    { name: "Priyank", relation: "son", phone: "+1-404-555-0142" },
    { name: "Alex", relation: "grandson", phone: "+1-404-555-0187" },
  ],
};

function b64urlToBuffer(input) {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  let cleaned = String(input).replace(/-/g, "+").replace(/_/g, "/").replace(/=+$/, "");
  if (cleaned.length % 4 === 1) throw new Error("bad passkey length " + cleaned.length);
  cleaned += "=".repeat((4 - (cleaned.length % 4)) % 4);
  const bytes = [];
  for (let i = 0; i < cleaned.length; i += 4) {
    const values = [0, 1, 2, 3].map((offset) => (cleaned[i + offset] === "=" ? 0 : alphabet.indexOf(cleaned[i + offset])));
    if (values.some((value) => value < 0)) throw new Error("bad passkey char");
    const packed = (values[0] << 18) | (values[1] << 12) | (values[2] << 6) | values[3];
    bytes.push((packed >> 16) & 255);
    if (cleaned[i + 2] !== "=") bytes.push((packed >> 8) & 255);
    if (cleaned[i + 3] !== "=") bytes.push(packed & 255);
  }
  const buffer = new ArrayBuffer(bytes.length);
  new Uint8Array(buffer).set(bytes);
  return buffer;
}

function bytesToB64url(bytes) {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
  const data = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
  let out = "";
  for (let i = 0; i < data.length; i += 3) {
    const a = data[i];
    const b = i + 1 < data.length ? data[i + 1] : 0;
    const c = i + 2 < data.length ? data[i + 2] : 0;
    out += alphabet[a >> 2];
    out += alphabet[((a & 3) << 4) | (b >> 4)];
    if (i + 1 < data.length) out += alphabet[((b & 15) << 2) | (c >> 6)];
    if (i + 2 < data.length) out += alphabet[c & 63];
  }
  return out;
}

const HEADERS = { "ngrok-skip-browser-warning": "1" };
const TAB_IDS = TABS.map((item) => item.id);
const SEEN_KEY = "chaperone.safety.seen";

// Set by the page: a 401 "sign in required" from any call sends Priyank back to sign in.
let sessionEnded = () => {};

async function post(url, body) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", "ngrok-skip-browser-warning": "1" },
    body: body ? JSON.stringify(body) : "{}",
  });
  const text = await response.text();
  let payload;
  try {
    payload = text ? JSON.parse(text) : {};
  } catch {
    throw new Error("bad response " + response.status + " " + text.slice(0, 60));
  }
  if (!response.ok) {
    const error = new Error(payload.error || payload.detail || response.statusText);
    // Only a lapsed session: a wrong setup code is also a 401, and so is a rules signature from another session.
    if (response.status === 401 && /sign in required/i.test(String(payload.error || payload.detail || ""))) {
      error.expired = true;
      sessionEnded();
    }
    throw error;
  }
  return payload;
}

// Every GET behind the session. A 401 means the session lapsed.
async function get(url, init) {
  const response = await fetch(url, { ...init, headers: { ...HEADERS, ...((init && init.headers) || {}) } });
  if (response.status === 401) sessionEnded();
  return response;
}

function readSeen() {
  try {
    const value = Number(window.localStorage.getItem(SEEN_KEY));
    return Number.isFinite(value) && value > 0 ? value : null;
  } catch {
    return null;
  }
}

function writeSeen(value) {
  try {
    window.localStorage.setItem(SEEN_KEY, String(value));
  } catch {
    /* private mode: the badge just resets on reload */
  }
}

export default function Page() {
  const [message, setMessage] = useState(null);
  const [config, setConfig] = useState(null);
  const [setupCode, setSetupCode] = useState("");
  const [approvals, setApprovals] = useState([]);
  const [now, setNow] = useState(Date.now());
  const [history, setHistory] = useState(null);
  const [prepared, setPrepared] = useState(null);
  const [screen, setScreen] = useState("loading");
  const [mandate, setMandate] = useState(MANDATE);
  const [signedMandate, setSignedMandate] = useState(null);
  const [rulesSigned, setRulesSigned] = useState(false);
  const [signedAt, setSignedAt] = useState(null);
  const [budget, setBudget] = useState(null);
  const [paused, setPaused] = useState(false);
  const [alerts, setAlerts] = useState([]);
  const [alertsOn, setAlertsOn] = useState(false);
  const [explanation, setExplanation] = useState(null);
  const [ask, setAsk] = useState(null);
  const [tab, setTab] = useState("home");
  const [cosign, setCosign] = useState(null);
  const [risk, setRisk] = useState(null);
  const [holds, setHolds] = useState([]);
  const [allowedHolds, setAllowedHolds] = useState({});
  const [declines, setDeclines] = useState([]);
  const [checks, setChecks] = useState([]);
  const [seenAt, setSeenAt] = useState(null);
  const [protectedTotals, setProtectedTotals] = useState({ dollars: 0, scams_stopped: 0, card_declines: 0 });
  const screenRef = useRef(screen);
  // One alert stream per page, whichever render armed it.
  const stream = useRef({ started: false, lastEventId: null });

  useEffect(() => {
    screenRef.current = screen;
  }, [screen]);

  // Back to sign in, once, when the session lapses while Priyank is signed in.
  useEffect(() => {
    sessionEnded = () => {
      if (screenRef.current !== "app") return;
      screenRef.current = "welcome";
      setScreen("welcome");
      setExplanation(null);
      setAsk(null);
      setMessage({ text: "Please sign in again.", tone: "info", at: Date.now() });
    };
    return () => {
      sessionEnded = () => {};
    };
  }, []);

  useEffect(() => {
    fetch("/api/config", { headers: { "ngrok-skip-browser-warning": "1" } })
      .then((response) => response.json())
      .then(setConfig)
      .catch(() => {});
    const pull = () => {
      get("/api/approvals")
        .then((response) => {
          if (response.ok) setScreen((current) => (current === "welcome" || current === "loading" ? "app" : current));
          else setScreen((current) => (current === "loading" ? "welcome" : current));
          return response.ok ? response.json() : [];
        })
        .then((rows) => setApprovals(Array.isArray(rows) ? rows : []))
        .catch(() => setScreen((current) => (current === "loading" ? "welcome" : current)));
    };
    pull();
    // A safety net only: the alert stream refreshes approvals at once. ngrok's free tier is 20k requests a month.
    const timer = setInterval(pull, 10000);
    const clock = setInterval(() => setNow(Date.now()), 1000);
    return () => {
      clearInterval(timer);
      clearInterval(clock);
    };
  }, []);

  // The tab lives in the address (#approvals), so a reload keeps it.
  useEffect(() => {
    const fromHash = () => {
      const id = window.location.hash.replace("#", "");
      if (TAB_IDS.includes(id)) setTab(id);
    };
    fromHash();
    window.addEventListener("hashchange", fromHash);
    return () => window.removeEventListener("hashchange", fromHash);
  }, []);

  const signedIn = screen === "app";
  useEffect(() => {
    if (!signedIn) return undefined;
    // Reopening the page with a live session skips Welcome: load what Priyank last signed and the family data
    // here too, so a later Sign never starts from the defaults. Sound and the wake lock need a tap first.
    refreshHome().catch(() => {});
    loadFamily().catch(() => {});
    fetch("/api/config", { headers: { "ngrok-skip-browser-warning": "1" } }).then((response) => response.json()).then(setConfig).catch(() => {});
    const arm = () => armAlerts().catch(() => {});
    window.addEventListener("pointerdown", arm, { once: true });
    return () => window.removeEventListener("pointerdown", arm);
  }, [signedIn]);

  const approvalId = approvals[0] && approvals[0].approval_id;
  useEffect(() => {
    if (!approvalId) {
      setPrepared(null);
      return undefined;
    }
    let cancel = false;
    post(`/api/approvals/${approvalId}/decide`, { prepare: true })
      .then((next) => {
        if (!cancel) setPrepared({ id: approvalId, next });
      })
      .catch(() => {});
    return () => {
      cancel = true;
    };
  }, [approvalId]);

  // A success fades after a while; an error stays until Priyank closes it or something new replaces it.
  useEffect(() => {
    if (!message || message.tone === "err") return undefined;
    const timer = setTimeout(() => setMessage((current) => (current === message ? null : current)), 8000);
    return () => clearTimeout(timer);
  }, [message]);

  function note(text, tone = "ok") {
    setMessage({ text, tone, at: Date.now() });
  }

  // Priyank sees plain words; the detail goes to the console.
  function fail(error) {
    console.error(error);
    if (error && error.expired) return;
    const words = plainError(error);
    note(words, words === "Cancelled" ? "info" : "err");
  }

  function approvalNote(state) {
    const words = approvalWords(state);
    note(words, state === "approved" ? "ok" : words === RETRY ? "err" : "info");
  }

  async function register() {
    let optionsJSON;
    try {
      optionsJSON = await post("/api/passkeys/generate-registration-options", { setup_code: setupCode });
    } catch (error) {
      if (!String(error.message).includes("registration is closed")) throw error;
      const auth = await post("/api/passkeys/generate-authentication-options", { register: true });
      const assertion = await startAuthentication({ optionsJSON: auth });
      optionsJSON = await post("/api/passkeys/generate-registration-options", { setup_code: setupCode, assertion });
    }
    const attestation = await startRegistration({ optionsJSON });
    const verified = await post("/api/passkeys/verify-registration", attestation);
    if (verified.verified) note("Passkey saved. Now sign in with it.");
    else note("Your passkey wasn't saved. Please try again.", "err");
  }

  async function signIn() {
    const optionsJSON = await post("/api/passkeys/generate-authentication-options", { session: true });
    const assertion = await startAuthentication({ optionsJSON });
    await post("/api/passkeys/verify-authentication", { response: assertion, purpose: "session" });
    note("Signed in");
    screenRef.current = "app";
    setScreen("app");
    refreshHome().catch(() => {});
    loadFamily().catch(() => {});
    armAlerts().catch(() => {});
  }

  async function refreshHome() {
    const budgetResponse = await get("/api/budget");
    if (budgetResponse.ok) setBudget(await budgetResponse.json());
    const mandateResponse = await get("/api/mandate");
    if (mandateResponse.ok) {
      const body = await mandateResponse.json();
      setPaused(Boolean(body.paused));
      setCosign(body.cosign || null);
      setRulesSigned(Boolean(body.signed));
      // Start the rules form from what Priyank last signed, so signing again never resets a limit.
      if (body.signed && body.mandate) {
        setMandate((prev) => ({ ...prev, ...body.mandate }));
        setSignedMandate({ ...MANDATE, ...body.mandate });
      }
    }
  }

  function changeRule(key, value) {
    // Kept as typed ("", "40.") until signing, so an emptied field is not signed as 0.
    const number = Number(value);
    const typing = value.trim() === "" || value.endsWith(".");
    setMandate((prev) => ({ ...prev, [key]: !typing && Number.isFinite(number) ? number : value }));
  }

  function toggleStore(id) {
    setMandate((prev) => {
      const has = (prev.allowed_merchants || []).includes(id);
      const allowed_merchants = has ? prev.allowed_merchants.filter((item) => item !== id) : [...(prev.allowed_merchants || []), id];
      return { ...prev, allowed_merchants };
    });
  }

  function toggleBlocked(id) {
    setMandate((prev) => {
      const has = prev.blocked_categories.includes(id);
      const blocked_categories = has ? prev.blocked_categories.filter((item) => item !== id) : [...prev.blocked_categories, id];
      return { ...prev, blocked_categories };
    });
  }

  async function assertMandate() {
    const limits = ["per_purchase_cap", "monthly_cap", "approval_threshold"];
    for (const key of limits) {
      const value = Number(mandate[key]);
      if (String(mandate[key]).trim() === "" || !Number.isFinite(value) || value < 0) throw new Error("Every limit needs an amount");
    }
    const signed = { ...mandate, ...Object.fromEntries(limits.map((key) => [key, Number(mandate[key])])) };
    const optionsJSON = await post("/api/passkeys/generate-authentication-options", { mandate: signed });
    const assertion = await startAuthentication({ optionsJSON });
    const verified = await post("/api/passkeys/verify-authentication", { mandate: signed, response: assertion });
    setMandate(signed);
    await post("/api/mandate", {
      ...signed,
      passkey: { credential_id: verified.credential_id, public_key: verified.public_key, response: assertion },
    });
    setSignedMandate(signed);
    setRulesSigned(true);
    setSignedAt(new Date().toISOString());
    note("Rules signed. Ruth will hear them next.");
    refreshHome().catch(() => {});
  }

  async function refreshApprovals() {
    const response = await get("/api/approvals");
    const rows = await response.json();
    setApprovals(Array.isArray(rows) ? rows : []);
  }

  function asBytes(value) {
    if (typeof value === "string") return null;
    if (value instanceof ArrayBuffer) return new Uint8Array(value);
    return new Uint8Array(value.buffer, value.byteOffset, value.byteLength);
  }

  function packField(value) {
    return typeof value === "string" ? value : bytesToB64url(asBytes(value));
  }

  function packAssertion(credential) {
    return {
      id: credential.id,
      rawId: packField(credential.rawId),
      type: credential.type,
      response: {
        authenticatorData: packField(credential.response.authenticatorData),
        clientDataJSON: packField(credential.response.clientDataJSON),
        signature: packField(credential.response.signature),
        userHandle: credential.response.userHandle ? packField(credential.response.userHandle) : undefined,
      },
      clientExtensionResults: credential.getClientExtensionResults ? credential.getClientExtensionResults() : {},
      authenticatorAttachment: credential.authenticatorAttachment,
    };
  }

  async function secureConfirmation(options, approval) {
    if (!window.PaymentRequest || !PaymentRequest.securePaymentConfirmationAvailability) return null;
    if ((await PaymentRequest.securePaymentConfirmationAvailability()) !== "available") return null;
    const request = new PaymentRequest(
      [{
        supportedMethods: "secure-payment-confirmation",
        data: {
          credentialIds: (options.allowCredentials || []).map((item) => b64urlToBuffer(item.id)),
          challenge: b64urlToBuffer(options.challenge),
          rpId: options.rpId,
          instrument: {
            displayName: "Ruth's Visa (sandbox)",
            icon: "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='40' height='40'%3E%3Crect width='40' height='40' fill='%238c2f2f'/%3E%3C/svg%3E",
            iconMustBeShown: false,
          },
          payeeName: payeeName(approval),
          payeeOrigin: window.location.origin,
          timeout: 90000,
        },
      }],
      { total: { label: "Total", amount: { currency: "USD", value: Number(approval.amount).toFixed(2) } } },
    );
    const payment = await request.show();
    const assertion = packAssertion(payment.details);
    await payment.complete("success");
    return assertion;
  }

  function settled(approval) {
    setApprovals((prev) => prev.filter((row) => row.approval_id !== approval.approval_id));
  }

  async function approve(approval) {
    const ready = (prepared && prepared.id === approval.approval_id && prepared.next)
      || (await post(`/api/approvals/${approval.approval_id}/decide`, { prepare: true }));
    const options = ready.optionsJSON;
    try {
      const assertion = await secureConfirmation(options, approval);
      if (assertion) {
        const result = await post(`/api/approvals/${approval.approval_id}/decide`, { approved: true, spc: true, response: assertion });
        approvalNote(result.state);
        settled(approval);
        return;
      }
    } catch (error) {
      // The passkey prompt below is the fallback, so a failed payment dialog is only logged.
      if (error && error.name !== "NotAllowedError") console.error("payment dialog", error);
    }
    const challenge = b64urlToBuffer(options.challenge);
    const request = {
      challenge,
      rpId: options.rpId,
      timeout: options.timeout || 60000,
      userVerification: "required",
    };
    let credential;
    try {
      credential = await navigator.credentials.get({
        publicKey: {
          ...request,
          allowCredentials: (options.allowCredentials || []).map((item) => ({
            type: "public-key",
            id: b64urlToBuffer(item.id),
          })),
        },
      });
    } catch (error) {
      if (!String(error.message || error).includes("expected pattern")) throw error;
      credential = await navigator.credentials.get({ publicKey: request });
    }
    const assertion = packAssertion(credential);
    const result = await post(`/api/approvals/${approval.approval_id}/decide`, { approved: true, response: assertion });
    approvalNote(result.state);
    settled(approval);
  }

  async function reject(approval, declineNote) {
    const result = await post(`/api/approvals/${approval.approval_id}/decide`, { approved: false, message: declineNote });
    approvalNote(result.state);
    settled(approval);
  }

  async function pauseAgent() {
    const result = await post("/api/pause");
    setPaused(Boolean(result.paused));
    if (result.paused) note("Shopping is paused. Chaperone won't buy anything until you resume.", "info");
    else note(RETRY, "err");
  }

  async function resumeAgent() {
    const challenge = await get("/api/resume").then((response) => response.json());
    const credential = await navigator.credentials.get({
      publicKey: {
        challenge: b64urlToBuffer(challenge.challenge),
        rpId: config ? config.rpID : undefined,
        userVerification: "required",
        timeout: 60000,
      },
    });
    const result = await post("/api/resume", { response: packAssertion(credential), nonce: challenge.nonce });
    setPaused(result.paused !== false);
    if (result.paused === false) note("Shopping is back on.");
    else note(RETRY, "err");
  }

  async function loadHistory() {
    const response = await get("/api/history");
    setHistory(await response.json());
  }

  async function explainDecision(decisionId) {
    if (!decisionId) return;
    const response = await get(`/api/decisions/${decisionId}/explain`);
    const body = await response.json();
    if (!response.ok) throw new Error(body.error || "no explanation");
    setExplanation(body);
  }

  async function cancelOrder(orderId) {
    const result = await post(`/api/orders/${orderId}/cancel`);
    if (result.status === "cancelled") note("Order cancelled. Nothing was charged.");
    else note(RETRY, "err");
    loadHistory().catch(() => {});
  }

  async function submitCode(approval, typed, clear) {
    const code = String(typed || "").trim();
    if (!code) {
      note("Type the code from the host screen first.", "info");
      return;
    }
    const result = await post("/api/code/verify", { approval_id: approval.approval_id, code });
    if (result.verified) note("Approved");
    else note("That code didn't work.", "err");
    if (result.verified) {
      clear();
      settled(approval);
    }
  }

  // Allow once or keep blocked. The family data reloads either way, so a hold already handled drops off too.
  function decideHold(id, action) {
    return post(`/api/card/holds/${encodeURIComponent(id)}/${action}`)
      .then((result) => {
        if (action === "allow") {
          if (result && result.allowed_until) setAllowedHolds((prev) => ({ ...prev, [id]: { ...result, hold_id: id } }));
          const until = result && result.allowed_until ? clockTime(result.allowed_until) : "";
          note(until ? `Allowed once. Ruth can tap her card again until ${until}.` : "Allowed once for 10 minutes");
        } else {
          note("Kept blocked");
        }
      })
      .catch(fail)
      .finally(() => loadFamily().catch(() => {}));
  }

  async function armAlerts() {
    if (stream.current.started) return;
    stream.current.started = true;
    const audio = new AudioContext();
    await audio.resume();
    const holdWake = async () => {
      if (!navigator.wakeLock || document.visibilityState !== "visible") return;
      try {
        await navigator.wakeLock.request("screen");
      } catch {
        /* the phone can refuse a wake lock */
      }
    };
    await holdWake();
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") holdWake();
    });
    if (navigator.vibrate) navigator.vibrate(50);
    setAlertsOn(true);
    let delay = 1000;
    const decoder = new TextDecoder();
    while (true) {
      try {
        const headers = { "ngrok-skip-browser-warning": "1" };
        if (stream.current.lastEventId) headers["Last-Event-ID"] = stream.current.lastEventId;
        const response = await fetch("/api/alerts/stream", { headers });
        if (response.status === 401) {
          // The session lapsed: stop listening until Priyank signs in again (signIn arms the alerts anew).
          stream.current.started = false;
          setAlertsOn(false);
          sessionEnded();
          return;
        }
        if (!response.ok || !response.body) throw new Error("stream down");
        delay = 1000;
        const reader = response.body.getReader();
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          const text = decoder.decode(value, { stream: true });
          const eventId = text.match(/^id: (\d+)/m);
          if (eventId) stream.current.lastEventId = eventId[1];
          const events = [];
          for (const line of text.split("\n")) {
            if (!line.startsWith("data:")) continue;
            try {
              events.push(JSON.parse(line.slice(5).trim()));
            } catch {
              /* a heartbeat is not an alert */
            }
          }
          if (events.length) {
            // Beep only for what needs Priyank: an approval, a scam, a declined swipe.
            const loud = events.some((event) => event.type === "approval_requested"
              || (event.type === "scam_checked" && event.verdict === "scam")
              || (event.type === "card_decision" && event.result === "declined"));
            const stops = events.filter((event) => event.type === "caregiver_alerted" && event.kind === "screen_refusal");
            if (stops.length) {
              const at = new Date().toISOString();
              setAlerts((prev) => {
                const fresh = stops
                  .map((event, index) => ({ id: event.seq ? String(event.seq) : `${Date.now()}-${index}`, text: "Chaperone stopped a request.", decision_id: event.decision_id, at }))
                  .filter((item) => !prev.some((old) => old.id === item.id));
                return [...fresh, ...prev].slice(0, 12);
              });
            }
            const released = events.filter((event) => event.type === "card_hold_released" && event.hold_id && event.allowed_until);
            if (released.length) {
              setAllowedHolds((prev) => {
                const next = { ...prev };
                for (const event of released) {
                  next[event.hold_id] = { hold_id: event.hold_id, store: event.store, max_amount: event.max_amount, allowed_until: event.allowed_until };
                }
                return next;
              });
            }
            refreshApprovals().catch(() => {});
            if (events.some((event) => ["scam_checked", "card_decision", "card_hold_released", "risk_changed"].includes(event.type))) loadFamily().catch(() => {});
            if (events.some((event) => ["cosigned", "mandate_paused"].includes(event.type))) refreshHome().catch(() => {});
            if (loud) {
              if (navigator.vibrate) navigator.vibrate([200, 100, 200]);
              const beep = audio.createOscillator();
              beep.connect(audio.destination);
              beep.start();
              beep.stop(audio.currentTime + 0.2);
            }
          }
        }
      } catch {
        await new Promise((resolve) => setTimeout(resolve, delay));
        delay = Math.min(delay * 2, 10000);
      }
    }
  }

  async function loadFamily() {
    const [riskBody, cardBody, checksBody, protectedBody] = await Promise.all([
      get("/api/risk").then((response) => (response.ok ? response.json() : null)).catch(() => null),
      get("/api/card").then((response) => (response.ok ? response.json() : null)).catch(() => null),
      get("/api/scam-checks").then((response) => (response.ok ? response.json() : [])).catch(() => []),
      get("/api/protected").then((response) => (response.ok ? response.json() : null)).catch(() => null),
    ]);
    if (riskBody) setRisk(riskBody);
    if (cardBody) {
      setHolds(cardBody.holds || []);
      setDeclines((cardBody.decisions || []).filter((row) => row.result === "declined"));
    }
    if (Array.isArray(checksBody)) setChecks(checksBody);
    if (protectedBody) setProtectedTotals(protectedBody);
    loadHistory().catch(() => {});
  }

  function openTab(name) {
    setTab(name);
    try {
      window.history.replaceState(null, "", `#${name}`);
    } catch {
      /* the address just keeps the old tab */
    }
    window.scrollTo(0, 0);
    loadFamily().catch(() => {});
  }

  // Safety's badge counts only what arrived since Priyank last looked (kept on this phone).
  const items = safetyItems({ checks, refusals: history && history.refusals, alerts, declines });
  const newest = items.reduce((latest, item) => Math.max(latest, Date.parse(item.at || "") || 0), 0);
  useEffect(() => {
    const stored = readSeen();
    if (stored) setSeenAt(stored);
    else {
      const start = Date.now();
      writeSeen(start);
      setSeenAt(start);
    }
  }, []);
  useEffect(() => {
    if (tab !== "safety" || !signedIn) return;
    const mark = Math.max(Date.now(), newest);
    writeSeen(mark);
    setSeenAt(mark);
  }, [tab, signedIn, newest]);
  const unseen = seenAt === null ? 0 : items.filter((item) => {
    if (item.kind === "scam" && item.check.verdict === "ok") return false;
    return (Date.parse(item.at || "") || 0) > seenAt;
  }).length;

  const rules = signedMandate || mandate;
  const changes = ruleChanges(mandate, rulesSigned ? signedMandate : null);
  const showFooter = tab === "rules" && (!rulesSigned || changes.count > 0);

  function askDecline(approval) {
    setAsk({
      kind: "destroy",
      title: `Decline ${money(approval.amount)} at ${payeeName(approval)}?`,
      body: "Nothing is bought. Ruth hears that you said no.",
      note: { label: "Note for Ruth (optional)", help: "Chaperone passes it on with your answer." },
      confirmLabel: "Decline",
      safeLabel: "Back",
      run: (text) => reject(approval, text).catch(fail),
    });
  }

  function askAllow(hold) {
    setAsk({
      kind: "loosen",
      title: `Let ${money(hold.max_amount)} at ${storeName(hold.store) || "this store"} through once?`,
      body: "Ruth has 10 minutes to tap her card again.",
      confirmLabel: "Allow once",
      safeLabel: "Keep blocked",
      run: () => decideHold(hold.hold_id, "allow"),
    });
  }

  function askEndCare() {
    const over = (((rules.card || {}).cooldown || {}).caps || {}).default;
    setAsk({
      kind: "loosen",
      title: "End extra care now?",
      body: `${over !== undefined ? `Card charges over ${money(over)} will go through without asking you again.` : "Ruth's card goes back to its usual limits."} Only end it early if you're sure the call was not a scam.`,
      confirmLabel: "End early",
      safeLabel: "Keep extra care",
      run: () => post("/api/risk/clear", {}).then(() => { note("Extra care ended."); return loadFamily(); }).catch(fail),
    });
  }

  function askUnblock(id) {
    const name = categoryName(id).toLowerCase();
    setAsk({
      kind: "loosen",
      title: `Unblock ${name}?`,
      body: `Chaperone could then buy ${name} for Ruth. Scammers often ask for these. Nothing changes until you sign and Ruth agrees by voice.`,
      confirmLabel: "Unblock",
      safeLabel: "Keep blocked",
      run: () => toggleBlocked(id),
    });
  }

  function askCancel(order) {
    const store = storeName(order.store || order.merchant) || "this store";
    setAsk({
      kind: "destroy",
      title: `Cancel the ${store} order?`,
      body: `${money(order.total)} has not been paid yet. Cancelling puts Ruth's budget back where it was.`,
      confirmLabel: "Cancel order",
      safeLabel: "Keep order",
      run: () => cancelOrder(order.order_id).catch(fail),
    });
  }

  if (screen === "loading") {
    return (
      <div className="cg-loading" aria-busy="true">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src="/design/logo.svg" alt="Chaperone" height={34} />
        <p role="status">Opening…</p>
      </div>
    );
  }

  if (screen === "welcome") {
    return (
      <Welcome
        setupCode={setupCode}
        onSetupCode={setSetupCode}
        onRegister={() => register().catch(fail)}
        onSignIn={() => signIn().catch(fail)}
        message={message}
        onDismiss={() => setMessage(null)}
      />
    );
  }

  const ruthPhone = config && config.ruthPhone;
  return (
    <div className="cg-shell">
      <AppBar alertsOn={alertsOn} />
      <main className={`cg-main${showFooter ? " cg-main--footer" : ""}`}>
        <Message message={message} onDismiss={() => setMessage(null)} />
        {tab === "home" ? (
          <Home
            budget={budget}
            paused={paused}
            risk={risk}
            protectedTotals={protectedTotals}
            history={history}
            declines={declines}
            mandate={rules}
            ruthPhone={ruthPhone}
            now={now}
            onPause={() => pauseAgent().catch(fail)}
            onResume={() => resumeAgent().catch(fail)}
            onEndCare={askEndCare}
            onOpenSafety={() => openTab("safety")}
          />
        ) : null}
        {tab === "safety" ? (
          <Safety items={items} now={now} ruthPhone={ruthPhone} mandate={rules} allowedHolds={allowedHolds} onWhy={(id) => explainDecision(id).catch(fail)} />
        ) : null}
        {tab === "approvals" ? (
          <Approvals
            approvals={approvals}
            now={now}
            holds={holds}
            declines={declines}
            allowedHolds={allowedHolds}
            threshold={rules.approval_threshold}
            onApprove={(approval) => approve(approval).catch(fail)}
            onDecline={askDecline}
            onWhy={(id) => explainDecision(id).catch(fail)}
            onAllow={askAllow}
            onKeep={(id) => decideHold(id, "keep")}
            onSubmitCode={(approval, code, clear) => submitCode(approval, code, clear).catch(fail)}
          />
        ) : null}
        {tab === "activity" ? <HistoryView history={history} now={now} onCancel={askCancel} /> : null}
        {tab === "rules" ? (
          <Rules
            mandate={mandate}
            signedMandate={signedMandate}
            signed={rulesSigned}
            cosign={cosign}
            signedAt={signedAt}
            changes={changes}
            onChange={changeRule}
            onBlock={toggleBlocked}
            onUnblock={askUnblock}
            onToggleStore={toggleStore}
          />
        ) : null}
      </main>
      {showFooter ? <SignFooter signed={rulesSigned} count={changes.count} onSign={() => assertMandate().catch(fail)} /> : null}
      <TabBar tab={tab} counts={{ approvals: approvals.length + holds.length, safety: unseen }} onTab={openTab} />
      <Why explanation={explanation} ruthPhone={ruthPhone} onClose={() => setExplanation(null)} />
      <Confirm key={ask ? ask.title : "none"} ask={ask} onClose={() => setAsk(null)} />
    </div>
  );
}
