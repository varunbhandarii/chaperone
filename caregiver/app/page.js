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

async function post(url, body) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", "ngrok-skip-browser-warning": "1" },
    body: body ? JSON.stringify(body) : "{}",
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || response.statusText);
  return payload;
}

export default function Page() {
  const [log, setLog] = useState("");
  const [config, setConfig] = useState(null);
  const [setupCode, setSetupCode] = useState("");
  const [approval, setApproval] = useState(null);

  useEffect(() => {
    fetch("/api/config", { headers: { "ngrok-skip-browser-warning": "1" } })
      .then((response) => response.json())
      .then(setConfig);
    const timer = setInterval(() => {
      fetch("/api/approvals", { headers: { "ngrok-skip-browser-warning": "1" } })
        .then((response) => response.json())
        .then((rows) => setApproval(Array.isArray(rows) && rows.length ? rows[0] : null))
        .catch(() => {});
    }, 10000);
    return () => clearInterval(timer);
  }, []);

  function note(line) {
    setLog((prev) => prev + line + "\n");
  }

  async function register() {
    const optionsJSON = await post("/api/passkeys/generate-registration-options", { setup_code: setupCode });
    const attestation = await startRegistration({ optionsJSON });
    const verified = await post("/api/passkeys/verify-registration", attestation);
    note("registration verified=" + verified.verified);
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

  async function approve() {
    const prepared = await post(`/api/approvals/${approval.approval_id}/decide`, { prepare: true });
    const assertion = await startAuthentication({ optionsJSON: prepared.optionsJSON });
    const result = await post(`/api/approvals/${approval.approval_id}/decide`, { approved: true, response: assertion });
    note("approval " + result.state);
    setApproval(null);
  }

  async function reject() {
    const result = await post(`/api/approvals/${approval.approval_id}/decide`, { approved: false });
    note("approval " + result.state);
    setApproval(null);
  }

  async function armAlerts() {
    const audio = new AudioContext();
    await audio.resume();
    if (navigator.vibrate) navigator.vibrate(50);
    if (navigator.wakeLock) await navigator.wakeLock.request("screen");
    const response = await fetch("/api/alerts/stream", { headers: { "ngrok-skip-browser-warning": "1" } });
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    note("alerts armed");
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      const text = decoder.decode(value);
      if (text.includes("refusal") || text.includes("approval_requested") || text.includes("caregiver_alerted")) {
        refreshApprovals().catch(() => {});
        if (navigator.vibrate) navigator.vibrate([200, 100, 200]);
        const beep = audio.createOscillator();
        beep.connect(audio.destination);
        beep.start();
        beep.stop(audio.currentTime + 0.2);
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
          <button style={btn} onClick={() => approve().catch((error) => note(String(error)))}>Approve with passkey</button>
          <button style={btn} onClick={() => reject().catch((error) => note(String(error)))}>Reject</button>
          <a href="tel:">Call Ruth</a>
        </section>
      ) : null}
      <p>
        <input value={setupCode} onChange={(event) => setSetupCode(event.target.value)} inputMode="numeric" placeholder="setup code" style={{ fontSize: "1.2rem", padding: "0.4rem" }} />
        <button style={btn} onClick={() => register().catch((error) => note(String(error)))}>Register passkey</button>
      </p>
      <p><button style={btn} onClick={() => armAlerts().catch((error) => note(String(error)))}>Arm alerts</button></p>
      <p>
        <button style={btn} onClick={() => assertMandate().catch((error) => note(String(error)))}>Sign mandate</button>
      </p>
      <p>Approval codes are printed on the host screen, not on this phone.</p>
      <pre style={{ whiteSpace: "pre-wrap" }}>{log}</pre>
    </main>
  );
}

const btn = { fontSize: "1.3rem", padding: "0.8rem 1.1rem", marginRight: "0.6rem" };
