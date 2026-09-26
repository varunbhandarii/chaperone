"use client";

import { startAuthentication, startRegistration } from "@simplewebauthn/browser";
import { useEffect, useState } from "react";

const MANDATE = {
  mandate_id: "m_ruth_2026_09",
  shopper: "ruth",
  caregiver: "priya",
  currency: "USD",
  per_purchase_cap: 60,
  monthly_cap: 300,
  approval_threshold: 40,
  allowed_merchants: ["corner_market"],
  allowed_categories: ["grocery", "pharmacy"],
  blocked_categories: ["gift_card", "prepaid_card", "wire", "crypto", "lottery"],
  languages: ["es", "en", "hi"],
  valid_from: "2026-09-01",
  valid_to: "2026-12-31",
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
  const [prepared, setPrepared] = useState(null);

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
  }

  async function assertMandate() {
    const optionsJSON = await post("/api/passkeys/generate-authentication-options", { mandate: MANDATE });
    const assertion = await startAuthentication({ optionsJSON });
    const verified = await post("/api/passkeys/verify-authentication", { mandate: MANDATE, response: assertion });
    note("assertion verified=" + verified.verified + " counter=" + verified.counter);
    const stored = await post("/api/mandate", {
      ...MANDATE,
      passkey: { credential_id: verified.credential_id, public_key: verified.public_key, response: assertion },
    });
    note("mandate stored " + stored.mandate_id);
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
    const result = await post(`/api/approvals/${approval.approval_id}/decide`, { approved: false });
    note("approval " + result.state);
    setApproval(null);
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
    <main style={{ maxWidth: "36rem" }}>
      <h1>Caregiver</h1>
      <p>Priyank signs Ruth&apos;s mandate. Relying party: {config ? config.rpID : "..."}</p>
      <pre style={{ whiteSpace: "pre-wrap", background: "white", padding: "1rem" }}>{JSON.stringify(MANDATE, null, 2)}</pre>
      {approval ? (
        <section style={{ background: "#8c2f2f", color: "white", padding: "1rem" }}>
          <p style={{ fontSize: "2.4rem", margin: "0.2rem 0" }}>${approval.amount}</p>
          <p>{approval.merchant}</p>
          <p>{approval.excerpt}</p>
          <p>{approval.rule}</p>
          <p>{Math.max(0, Math.ceil((new Date(approval.expires_at).getTime() - now) / 1000))}s left</p>
          <button style={btn} onClick={() => approve().catch((error) => note(String(error)))}>Approve with passkey</button>
          <button style={btn} onClick={() => reject().catch((error) => note(String(error)))}>Reject</button>
          <a href="tel:">Call Ruth</a>
          <p>
            <input value={fallbackCode} onChange={(event) => setFallbackCode(event.target.value)} inputMode="numeric" maxLength={6} placeholder="approval code" style={{ fontSize: "1.2rem", padding: "0.4rem" }} />
            <button style={btn} onClick={() => submitCode().catch((error) => note(String(error)))}>Submit code</button>
          </p>
        </section>
      ) : null}
      <p>
        <input value={setupCode} onChange={(event) => setSetupCode(event.target.value)} inputMode="numeric" placeholder="setup code" style={{ fontSize: "1.2rem", padding: "0.4rem" }} />
        <button style={btn} onClick={() => register().catch((error) => note(String(error)))}>Register passkey</button>
      </p>
      <p>
        <button style={btn} onClick={() => signIn().catch((error) => note(String(error)))}>Sign in</button>
        <button style={btn} onClick={() => armAlerts().catch((error) => note(String(error)))}>Arm alerts</button>
      </p>
      <p>
        <button style={btn} onClick={() => assertMandate().catch((error) => note(String(error)))}>Sign mandate</button>
      </p>
      <p>Approval codes are printed on the host screen, not on this phone.</p>
      <pre style={{ whiteSpace: "pre-wrap" }}>{log}</pre>
    </main>
  );
}

const btn = { fontSize: "1.3rem", padding: "0.8rem 1.1rem", marginRight: "0.6rem" };
