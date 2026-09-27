import { money } from "@/lib/money";
import { payeeName, storeName } from "@/lib/stores";

export default function Approvals({ approval, now, holds, declineNote, onDecline, onApprove, onReject, onWhy, onAllow }) {
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
          <input value={declineNote} onChange={(event) => onDecline(event.target.value)} placeholder="note to Ruth" style={{ fontSize: "1.15rem", width: "100%" }} />
          <button className="ch-btn" onClick={onReject}>Reject</button>
          <button className="ch-btn" onClick={onWhy}>Why?</button>
        </article>
      ) : <p>Nothing waiting.</p>}
      <h2>Card holds</h2>
      {holds.length === 0 ? <p>No holds.</p> : holds.map((hold) => (
        <article key={hold.hold_id} className="ch-card">
          <p>{hold.store} · {money(hold.max_amount)}</p>
          <p>{hold.reason_key === "card_blocked_category" ? "This kind of store stays blocked on Ruth's card." : hold.reason || "Held by Ruth's card rules."}</p>
          {hold.reason_key === "card_blocked_category"
            ? null
            : <button className="ch-btn" onClick={() => onAllow(hold.hold_id)}>Allow once (10 min)</button>}
        </article>
      ))}
    </section>
  );
}
