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
  const [code, setCode] = useState("");
  const [entered, setEntered] = useState("");

  useEffect(() => {
    fetch("/api/config", { headers: { "ngrok-skip-browser-warning": "1" } })
      .then((response) => response.json())
      .then(setConfig);
  }, []);

  function note(line) {
    setLog((prev) => prev + line + "\n");
  }

  async function register() {
    const optionsJSON = await post("/api/passkeys/generate-registration-options");
    const attestation = await startRegistration({ optionsJSON });
    const verified = await post("/api/passkeys/verify-registration", attestation);
    note("registration verified=" + verified.verified);
  }

  async function assertMandate() {
    const optionsJSON = await post("/api/passkeys/generate-authentication-options", { mandate: MANDATE });
    const assertion = await startAuthentication({ optionsJSON });
    const verified = await post("/api/passkeys/verify-authentication", { mandate: MANDATE, response: assertion });
    note("assertion verified=" + verified.verified + " counter=" + verified.counter);
  }

  async function issue() {
    const issued = await post("/api/code/issue");
    setCode(issued.code);
    note("code issued, expires in 10 minutes");
  }

  async function checkCode() {
    const verified = await post("/api/code/verify", { code: entered });
    note("code verified=" + verified.verified);
  }

  return (
    <main style={{ maxWidth: "36rem" }}>
      <h1>Caregiver</h1>
      <p>Priyank signs Ruth&apos;s mandate. Relying party: {config ? config.rpID : "..."}</p>
      <pre style={{ whiteSpace: "pre-wrap", background: "white", padding: "1rem" }}>{JSON.stringify(MANDATE, null, 2)}</pre>
      <p>
        <button style={btn} onClick={() => register().catch((error) => note(String(error)))}>Register passkey</button>
      </p>
      <p>
        <button style={btn} onClick={() => assertMandate().catch((error) => note(String(error)))}>Sign mandate</button>
      </p>
      <h2>Six-digit fallback</h2>
      <p><button style={btn} onClick={() => issue().catch((error) => note(String(error)))}>Issue code</button> {code}</p>
      <p>
        <input value={entered} onChange={(event) => setEntered(event.target.value)} inputMode="numeric" style={{ fontSize: "1.4rem", padding: "0.4rem" }} />
        <button style={btn} onClick={() => checkCode().catch((error) => note(String(error)))}>Verify code</button>
      </p>
      <pre style={{ whiteSpace: "pre-wrap" }}>{log}</pre>
    </main>
  );
}

const btn = { fontSize: "1.3rem", padding: "0.8rem 1.1rem", marginRight: "0.6rem" };
