import { rulesSentence } from "@/lib/rulesSentence";
import { btn, field } from "./styles";

const BLOCKED = [
  ["gift_card", "Gift cards"],
  ["prepaid_card", "Prepaid cards"],
  ["wire", "Wire transfers"],
  ["crypto", "Crypto"],
  ["lottery", "Lottery"],
];

export default function Rules({ mandate, onChange, onToggleBlocked, onSign, onHome }) {
  return (
    <section>
      <h1>Ruth&apos;s rules</h1>
      <label style={{ display: "block", fontSize: "1.15rem" }}>Per purchase
        <input style={field} inputMode="decimal" value={mandate.per_purchase_cap} onChange={(event) => onChange("per_purchase_cap", event.target.value)} />
      </label>
      <label style={{ display: "block", fontSize: "1.15rem" }}>Per month
        <input style={field} inputMode="decimal" value={mandate.monthly_cap} onChange={(event) => onChange("monthly_cap", event.target.value)} />
      </label>
      <label style={{ display: "block", fontSize: "1.15rem" }}>Ask me above
        <input style={field} inputMode="decimal" value={mandate.approval_threshold} onChange={(event) => onChange("approval_threshold", event.target.value)} />
      </label>
      <p style={{ fontSize: "1.15rem" }}>Blocked</p>
      {BLOCKED.map(([id, label]) => (
        <label key={id} style={{ display: "block", fontSize: "1.15rem", margin: "0.3rem 0" }}>
          <input type="checkbox" checked={mandate.blocked_categories.includes(id)} onChange={() => onToggleBlocked(id)} /> {label}
        </label>
      ))}
      <p style={{ fontSize: "1.2rem", background: "white", padding: "1rem" }}>{rulesSentence(mandate)}</p>
      <button style={btn} onClick={onSign}>Sign with passkey</button>
      <button style={btn} onClick={onHome}>Home</button>
    </section>
  );
}
