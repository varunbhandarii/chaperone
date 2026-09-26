"use client";

import { startAuthentication, startRegistration } from "@simplewebauthn/browser";
import { useEffect, useState } from "react";
import HistoryView from "./components/HistoryView";
import Home from "./components/Home";
import Rules from "./components/Rules";
import Welcome from "./components/Welcome";
import Why from "./components/Why";

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
  if (!response.ok) throw new Error(payload.error || payload.detail || response.statusText);
  return payload;
}

export default function Page() {
  const [log, setLog] = useState("");
  const [config, setConfig] = useState(null);
  const [setupCode, setSetupCode] = useState("");
  const [approval, setApproval] = useState(null);
  const [now, setNow] = useState(Date.now());
  const [fallbackCode, setFallbackCode] = useState("");
  const [declineNote, setDeclineNote] = useState("");
  const [history, setHistory] = useState(null);
  const [prepared, setPrepared] = useState(null);
  const [screen, setScreen] = useState("welcome");
  const [mandate, setMandate] = useState(MANDATE);
  const [budget, setBudget] = useState(null);
  const [paused, setPaused] = useState(false);
  const [alerts, setAlerts] = useState([]);
  const [explanation, setExplanation] = useState(null);

  useEffect(() => {
    fetch("/api/config", { headers: { "ngrok-skip-browser-warning": "1" } })
      .then((response) => response.json())
      .then(setConfig);
    const pull = () => {
      fetch("/api/approvals", { headers: { "ngrok-skip-browser-warning": "1" } })
        .then((response) => response.json())
        .then((rows) => setApproval(Array.isArray(rows) && rows.length ? rows[0] : null))
        .catch(() => {});
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

  const approvalId = approval && approval.approval_id;
  useEffect(() => {
    if (!approvalId) {
      setPrepared(null);
      return undefined;
    }
    let cancel = false;
    post(`/api/approvals/${approvalId}/decide`, { prepare: true })
      .then((next) => {
        if (!cancel) setPrepared(next);
      })
      .catch(() => {});
    return () => {
      cancel = true;
    };
  }, [approvalId]);

  function note(line) {
    setLog((prev) => prev + line + "\n");
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
    note("registration verified=" + verified.verified);
  }

  async function signIn() {
    const optionsJSON = await post("/api/passkeys/generate-authentication-options", { session: true });
    const assertion = await startAuthentication({ optionsJSON });
    await post("/api/passkeys/verify-authentication", { response: assertion, purpose: "session" });
    note("signed in");
    setScreen("home");
    refreshHome().catch(() => {});
    armAlerts().catch(() => {});
  }

  async function refreshHome() {
    const headers = { "ngrok-skip-browser-warning": "1" };
    const budgetResponse = await fetch("/api/budget", { headers });
    if (budgetResponse.ok) setBudget(await budgetResponse.json());
    const mandateResponse = await fetch("/api/mandate", { headers });
    if (mandateResponse.ok) {
      const body = await mandateResponse.json();
      setPaused(Boolean(body.paused));
      // Start the rules form from what Priyank last signed, so signing again never resets a limit.
      if (body.signed && body.mandate) setMandate((prev) => ({ ...prev, ...body.mandate }));
    }
  }

  function changeRule(key, value) {
    // Kept as typed ("", "40.") until signing, so an emptied field is not signed as 0.
    const number = Number(value);
    const typing = value.trim() === "" || value.endsWith(".");
    setMandate((prev) => ({ ...prev, [key]: !typing && Number.isFinite(number) ? number : value }));
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
    note("rules signed");
    setScreen("home");
  }

  async function refreshApprovals() {
    const response = await fetch("/api/approvals", { headers: { "ngrok-skip-browser-warning": "1" } });
    const rows = await response.json();
    setApproval(Array.isArray(rows) && rows.length ? rows[0] : null);
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

  async function secureConfirmation(options) {
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
          payeeName: "Corner Market",
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

  async function approve() {
    const ready = prepared || (await post(`/api/approvals/${approval.approval_id}/decide`, { prepare: true }));
    const options = ready.optionsJSON;
    try {
      const assertion = await secureConfirmation(options);
      if (assertion) {
        const result = await post(`/api/approvals/${approval.approval_id}/decide`, { approved: true, spc: true, response: assertion });
        note("approval " + result.state);
        setApproval(null);
        return;
      }
    } catch (error) {
      if (error && error.name !== "NotAllowedError") note("payment dialog " + (error.message || error.name));
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
    note("approval " + result.state);
    setApproval(null);
  }

  async function reject() {
    const result = await post(`/api/approvals/${approval.approval_id}/decide`, { approved: false, message: declineNote });
    note("approval " + result.state);
    setApproval(null);
  }

  async function pauseAgent() {
    const result = await post("/api/pause");
    setPaused(Boolean(result.paused));
    note(result.paused ? "agent paused" : "pause failed");
  }

  async function resumeAgent() {
    const challenge = await fetch("/api/resume", { headers: { "ngrok-skip-browser-warning": "1" } }).then((response) => response.json());
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
    note(result.paused === false ? "agent resumed" : "resume failed");
  }

  async function loadHistory() {
    const response = await fetch("/api/history", { headers: { "ngrok-skip-browser-warning": "1" } });
    setHistory(await response.json());
  }

  async function explainDecision(decisionId) {
    const id = decisionId || (approval && approval.decision_id);
    if (!id) return;
    const response = await fetch(`/api/decisions/${id}/explain`, { headers: { "ngrok-skip-browser-warning": "1" } });
    const body = await response.json();
    if (!response.ok) throw new Error(body.error || "no explanation");
    setExplanation(body);
  }

  async function cancelOrder(orderId) {
    const result = await post(`/api/orders/${orderId}/cancel`);
    note(result.status === "cancelled" ? "order cancelled" : "cancel failed");
    loadHistory().catch(() => {});
  }

  async function submitCode() {
    const result = await post("/api/code/verify", { approval_id: approval.approval_id, code: fallbackCode });
    note(result.verified ? "code accepted" : "code rejected");
    if (result.verified) setApproval(null);
  }

  async function armAlerts() {
    if (armAlerts.started) return;
    armAlerts.started = true;
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
    note("alerts armed");
    let delay = 1000;
    const decoder = new TextDecoder();
    while (true) {
      try {
        const headers = { "ngrok-skip-browser-warning": "1" };
        if (armAlerts.lastEventId) headers["Last-Event-ID"] = armAlerts.lastEventId;
        const response = await fetch("/api/alerts/stream", { headers });
        if (response.status === 401) {
          armAlerts.started = false;
          throw new Error("sign in required");
        }
        if (!response.ok || !response.body) throw new Error("stream down");
        delay = 1000;
        const reader = response.body.getReader();
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          const text = decoder.decode(value, { stream: true });
          const eventId = text.match(/^id: (\d+)/m);
          if (eventId) armAlerts.lastEventId = eventId[1];
          if (text.includes("refusal") || text.includes("approval_requested") || text.includes("caregiver_alerted")) {
            const dataLine = text.split("\n").find((line) => line.startsWith("data:"));
            if (dataLine) {
              try {
                const event = JSON.parse(dataLine.slice(5).trim());
                if (event.type === "refusal" || event.type === "caregiver_alerted") {
                  setAlerts((prev) => [{ id: event.seq || Date.now(), text: event.type === "refusal" ? "Ruth was refused." : "Something was stopped. Open Why? for the reason.", decision_id: event.decision_id }, ...prev].slice(0, 12));
                }
              } catch {
                /* a heartbeat is not an alert */
              }
            }
            refreshApprovals().catch(() => {});
            if (navigator.vibrate) navigator.vibrate([200, 100, 200]);
            const beep = audio.createOscillator();
            beep.connect(audio.destination);
            beep.start();
            beep.stop(audio.currentTime + 0.2);
          }
        }
      } catch {
        await new Promise((resolve) => setTimeout(resolve, delay));
        delay = Math.min(delay * 2, 10000);
      }
    }
  }

  return (
    <main style={{ maxWidth: "36rem", fontSize: "18px" }}>
      {screen === "welcome" ? (
        <Welcome setupCode={setupCode} onSetupCode={setSetupCode} onRegister={() => register().catch((error) => note(String(error)))} onSignIn={() => signIn().catch((error) => note(String(error)))} />
      ) : null}
      {screen === "rules" ? (
        <Rules mandate={mandate} onChange={changeRule} onToggleBlocked={toggleBlocked} onSign={() => assertMandate().catch((error) => note(String(error)))} onHome={() => setScreen("home")} />
      ) : null}
      {screen === "home" ? (
        <Home
          budget={budget}
          paused={paused}
          approval={approval}
          now={now}
          alerts={alerts}
          declineNote={declineNote}
          fallbackCode={fallbackCode}
          onDecline={setDeclineNote}
          onCode={setFallbackCode}
          onApprove={() => approve().catch((error) => note(String(error)))}
          onReject={() => reject().catch((error) => note(String(error)))}
          onWhy={() => explainDecision().catch((error) => note(String(error)))}
          onCodeSubmit={() => submitCode().catch((error) => note(String(error)))}
          onPause={() => pauseAgent().catch((error) => note(String(error)))}
          onResume={() => resumeAgent().catch((error) => note(String(error)))}
          onRules={() => setScreen("rules")}
          onHistory={() => { setScreen("history"); loadHistory().catch((error) => note(String(error))); }}
          onAlertWhy={(id) => explainDecision(id).catch((error) => note(String(error)))}
        />
      ) : null}
      {screen === "history" ? <HistoryView history={history} onHome={() => setScreen("home")} onCancel={(id) => cancelOrder(id).catch((error) => note(String(error)))} /> : null}
      <Why explanation={explanation} onClose={() => setExplanation(null)} />
      <p style={{ fontSize: "1rem" }}>Approval codes are printed on the host screen, not on this phone.</p>
      {log ? <p role="status" style={{ fontSize: "1rem" }}>{log.trim().split("\n").pop()}</p> : null}
    </main>
  );
}
