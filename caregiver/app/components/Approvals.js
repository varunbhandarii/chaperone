import { useState } from "react";
import { money } from "@/lib/money";
import { payeeName, storeName } from "@/lib/stores";

const field = { fontSize: "1.15rem", width: "100%", boxSizing: "border-box" };
const quiet = { background: "none", border: 0, padding: "0.6rem 0", minHeight: "44px", color: "inherit", fontSize: "1rem", textDecoration: "underline" };

// A backup for when the passkey prompt fails on the phone: the code shown on the host screen.
function CodeFallback({ code, onCode, onSubmit }) {
  const [open, setOpen] = useState(false);
  if (!open) return <p><button type="button" style={quiet} onClick={() => setOpen(true)}>Use a code instead</button></p>;
  return (
    <div>
      <label style={{ display: "block", fontSize: "1rem" }}>Code from the host screen
        <input value={code} onChange={(event) => onCode(event.target.value)} inputMode="numeric" autoComplete="one-time-code" maxLength={6} placeholder="6 digits" style={field} />
      </label>
      <button className="ch-btn" onClick={onSubmit}>Submit code</button>
    </div>
  );
}

export default function Approvals({ approval, now, holds, declineNote, onDecline, onApprove, onReject, onWhy, onAllow, onKeep, code, onCode, onSubmitCode }) {
  const seconds = approval ? Math.max(0, Math.ceil((new Date(approval.expires_at).getTime() - now) / 1000)) : 0;
  const groups = {};
  for (const item of (approval && approval.items) || []) {
    const id = item.merchant || (approval.stores && approval.stores[0]) || approval.merchant;
    groups[id] = groups[id] || [];
    groups[id].push(item);
  }
  return (
    <section>
      <h1>Approvals</h1>
      {approval ? (
        <article className="ch-card" style={{ borderLeft: "6px solid var(--caution)" }}>
          <p style={{ fontSize: "2rem", margin: "0.2rem 0" }}>{money(approval.amount)}</p>
          <p>{payeeName(approval)}</p>
          {Object.entries(groups).map(([id, items]) => (
            <div key={id}>
              <p><strong>{storeName(id)}</strong></p>
              {items.map((item) => <p key={item.name}>{item.qty} × {item.name}</p>)}
            </div>
          ))}
          {approval.reason ? <p>{approval.reason}</p> : null}
          <p>{seconds}s left</p>
          <button className="ch-btn" onClick={onApprove}>Approve with passkey</button>
          <input value={declineNote} onChange={(event) => onDecline(event.target.value)} placeholder="note to Ruth" style={field} />
          <button className="ch-btn" onClick={onReject}>Reject</button>
          <button className="ch-btn" onClick={onWhy}>Why?</button>
          <CodeFallback key={approval.approval_id} code={code} onCode={onCode} onSubmit={onSubmitCode} />
        </article>
      ) : <p>Nothing waiting.</p>}
      <h2>Card holds</h2>
      {holds.length === 0 ? <p>No holds.</p> : holds.map((hold) => {
        const blocked = hold.reason_key === "card_blocked_category";
        return (
          <article key={hold.hold_id} className="ch-card">
            <p>{storeName(hold.store) || "A store"} · {money(hold.max_amount)}</p>
            <p>{blocked ? "This kind of store stays blocked on Ruth's card." : hold.reason || "Held by Ruth's card rules."}</p>
            {blocked ? null : <button className="ch-btn" onClick={() => onAllow(hold.hold_id)}>Allow once (10 min)</button>}
            <button className="ch-btn" onClick={() => onKeep(hold.hold_id)}>Keep blocked</button>
          </article>
        );
      })}
    </section>
  );
}
