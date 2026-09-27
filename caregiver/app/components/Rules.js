import { rulesSentence } from "@/lib/rulesSentence";
import { storeNames, storefronts } from "@/lib/stores";
import { btn, field } from "./styles";

const BLOCKED = [
  ["gift_card", "Gift cards"],
  ["prepaid_card", "Prepaid cards"],
  ["wire", "Wire transfers"],
  ["crypto", "Crypto"],
  ["lottery", "Lottery"],
];

export default function Rules({ mandate, cosign, onChange, onToggleBlocked, onToggleStore, onSign }) {
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
      <p className="ch-card">{rulesSentence(mandate, storeNames)}</p>
      <h2>Stores</h2>
      {storefronts().map((store) => (
        <label key={store.id} style={{ display: "block", fontSize: "1.15rem" }}>
          <input type="checkbox" checked={(mandate.allowed_merchants || []).includes(store.id)} onChange={() => onToggleStore(store.id)} /> {store.name}
        </label>
      ))}
      <h2>Card</h2>
      <p>Gift-card shops, crypto and wires stay blocked. A drugstore swipe can be up to $80. After a scam check, risky spending is tighter for a day.</p>
      <h2>People Ruth trusts</h2>
      {(mandate.trusted_contacts || []).map((person) => <p key={person.phone}>{person.name}, {person.relation}</p>)}
      <p>{cosign ? `Ruth agreed by voice at ${new Date(cosign.at).toLocaleString("en-US", { hour: "numeric", minute: "2-digit" })}.` : "Waiting for Ruth."}</p>
      <button style={btn} onClick={onSign}>Sign with passkey</button>
    </section>
  );
}
