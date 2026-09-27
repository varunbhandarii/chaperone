import { cardSentence, rulesSentence } from "@/lib/rulesSentence";
import { storeNames, storefronts } from "@/lib/stores";
import { categoryName } from "@/lib/words";

const BLOCKED = ["gift_card", "prepaid_card", "wire", "crypto", "lottery"];
const field = { fontSize: "1.15rem", padding: "0.55rem", width: "100%", boxSizing: "border-box" };
const row = { display: "block", fontSize: "1.15rem" };

export default function Rules({ mandate, cosign, onChange, onToggleBlocked, onToggleStore, onSign }) {
  const blocked = [...new Set([...BLOCKED, ...(mandate.blocked_categories || [])])];
  return (
    <section>
      <h1>Ruth&apos;s rules</h1>
      <label style={row}>Per purchase
        <input style={field} inputMode="decimal" value={mandate.per_purchase_cap} onChange={(event) => onChange("per_purchase_cap", event.target.value)} />
      </label>
      <label style={row}>Per month
        <input style={field} inputMode="decimal" value={mandate.monthly_cap} onChange={(event) => onChange("monthly_cap", event.target.value)} />
      </label>
      <label style={row}>Ask me above
        <input style={field} inputMode="decimal" value={mandate.approval_threshold} onChange={(event) => onChange("approval_threshold", event.target.value)} />
      </label>
      <p style={{ fontSize: "1.15rem" }}>Blocked</p>
      {blocked.map((id) => (
        <label key={id} style={{ ...row, margin: "0.3rem 0" }}>
          <input type="checkbox" checked={(mandate.blocked_categories || []).includes(id)} onChange={() => onToggleBlocked(id)} /> {categoryName(id)}
        </label>
      ))}
      <p className="ch-card">{rulesSentence(mandate, storeNames)}</p>
      <h2>Stores</h2>
      {storefronts().map((store) => (
        <label key={store.id} style={row}>
          <input type="checkbox" checked={(mandate.allowed_merchants || []).includes(store.id)} onChange={() => onToggleStore(store.id)} /> {store.name}
        </label>
      ))}
      <h2>Card</h2>
      <p>{cardSentence(mandate)}</p>
      <h2>People Ruth trusts</h2>
      {(mandate.trusted_contacts || []).map((person) => <p key={person.phone}>{person.name}, {person.relation}</p>)}
      <p>{cosign ? `Ruth agreed by voice at ${new Date(cosign.at).toLocaleString("en-US", { hour: "numeric", minute: "2-digit" })}.` : "Waiting for Ruth."}</p>
      <button className="ch-btn" onClick={onSign}>Sign with passkey</button>
    </section>
  );
}
