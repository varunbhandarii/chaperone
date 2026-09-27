import { useState } from "react";
import { money } from "@/lib/money";
import { approvalReason, declineWords } from "@/lib/status";
import { payeeName, storeName } from "@/lib/stores";
import { clockTime, countdown } from "@/lib/words";
import Icon from "./Icon";

// An approval is open for 90 seconds (policy/checkout.py); the ring empties over that time.
const WINDOW_SECONDS = 90;
const RING = 2 * Math.PI * 31;

function Ring({ seconds }) {
  const share = Math.min(1, seconds / WINDOW_SECONDS);
  return (
    <div className="cg-ring" role="timer" aria-label={seconds > 0 ? `${countdown(seconds)} left to decide` : "Ran out of time"}>
      <svg width="72" height="72" viewBox="0 0 72 72" aria-hidden="true">
        <circle cx="36" cy="36" r="31" fill="none" stroke="var(--ch-raised)" strokeWidth="7" />
        {seconds > 0 ? (
          <circle cx="36" cy="36" r="31" fill="none" stroke="var(--ch-wait)" strokeWidth="7" strokeLinecap="round"
            strokeDasharray={RING} strokeDashoffset={RING * (1 - share)} transform="rotate(-90 36 36)" />
        ) : null}
      </svg>
      <div className="cg-ring__text" aria-hidden="true">
        <span className="cg-ring__time">{countdown(seconds)}</span>
        <span className="cg-ring__left">left</span>
      </div>
    </div>
  );
}

// A backup for when the passkey prompt fails on the phone: the code shown on the host screen.
function CodeFallback({ id, closed, onSubmit }) {
  const [open, setOpen] = useState(false);
  const [code, setCode] = useState("");
  if (!open) {
    return (
      <button type="button" className="cg-link-btn cg-link-btn--center" disabled={closed} onClick={() => setOpen(true)} aria-expanded="false">
        Passkey not working? Use a code
      </button>
    );
  }
  return (
    <form className="cg-code" onSubmit={(event) => { event.preventDefault(); onSubmit(code, () => setCode("")); }}>
      <div className="cg-field">
        <label className="cg-field__label" htmlFor={`code-${id}`}>Code from the host screen</label>
        <input id={`code-${id}`} className="cg-input cg-input--code" value={code} onChange={(event) => setCode(event.target.value.replace(/\D/g, "").slice(0, 6))}
          inputMode="numeric" autoComplete="one-time-code" maxLength={6} disabled={closed} aria-describedby={`code-help-${id}`} />
        <p id={`code-help-${id}`} className="cg-field__help">Six digits, shown on the screen next to Ruth.</p>
      </div>
      <button type="submit" className="ch-btn cg-btn" disabled={closed}>Approve with code</button>
    </form>
  );
}

function ApprovalCard({ approval, now, threshold, onApprove, onDecline, onWhy, onSubmitCode }) {
  const seconds = Math.max(0, Math.ceil((new Date(approval.expires_at).getTime() - now) / 1000)) || 0;
  const closed = seconds <= 0;
  const groups = new Map();
  for (const item of approval.items || []) {
    const id = item.merchant || (approval.stores && approval.stores[0]) || approval.merchant || "";
    if (!groups.has(id)) groups.set(id, []);
    groups.get(id).push(item);
  }
  const several = groups.size > 1;
  const reason = approvalReason(approval, threshold);
  const said = String(approval.excerpt || "").trim();
  return (
    <article className="cg-card cg-card--raised" aria-label={`${money(approval.amount)} at ${payeeName(approval)}`}>
      <div className="cg-approval__top">
        <div className="cg-approval__main">
          <span className="ch-badge ch-badge--wait">Needs your okay</span>
          <p className="cg-approval__amount">{money(approval.amount)}</p>
          <p className="cg-approval__store">{payeeName(approval)}</p>
        </div>
        <Ring seconds={seconds} />
      </div>
      {groups.size ? (
        <div className="cg-items">
          {[...groups].map(([id, items]) => (
            <div key={id || "store"}>
              {several ? <p className="cg-items__store">{storeName(id)}</p> : null}
              {items.map((item, index) => (
                <p key={`${item.name}-${index}`} className="cg-items__row">
                  <span className="cg-items__qty">{item.qty}&nbsp;×</span>
                  <span>{item.name}</span>
                </p>
              ))}
            </div>
          ))}
        </div>
      ) : null}
      {reason || said ? (
        <div className="cg-btn-col" style={{ gap: 8 }}>
          {reason ? <p className="cg-icon-line"><Icon name="info" size={18} />{reason}</p> : null}
          {said ? <p className="cg-icon-line"><Icon name="chat" size={18} /><span>Ruth said: “{said}”</span></p> : null}
        </div>
      ) : null}
      {closed ? (
        <p className="cg-expired" role="status"><Icon name="clock" size={20} />Ran out of time. Nothing was bought.</p>
      ) : null}
      <button type="button" className="ch-btn ch-btn--primary cg-btn cg-btn--tall" disabled={closed} onClick={() => onApprove(approval)}>
        <Icon name="key" size={20} />Approve with passkey
      </button>
      <div className={approval.decision_id ? "cg-btn-row" : "cg-btn-col"}>
        <button type="button" className="ch-btn ch-btn--danger cg-btn" disabled={closed} onClick={() => onDecline(approval)}>Decline</button>
        {approval.decision_id ? <button type="button" className="ch-btn cg-btn" onClick={() => onWhy(approval.decision_id)}>Why?</button> : null}
      </div>
      <CodeFallback id={approval.approval_id} closed={closed} onSubmit={(code, clear) => onSubmitCode(approval, code, clear)} />
    </article>
  );
}

function HoldCard({ hold, at, onKeep, onAllow }) {
  const blocked = hold.reason_key === "card_blocked_category";
  const store = storeName(hold.store) || "A store";
  return (
    <article className="cg-card" aria-label={`${store}, ${money(hold.max_amount)}`}>
      <div className="cg-card__head">
        <span className="ch-badge ch-badge--protected">Declined</span>
        {at ? <span className="cg-card__time">{clockTime(at)}</span> : null}
      </div>
      <div className="cg-card__line">
        <h3 className="cg-card__store" style={{ fontSize: 18 }}>{store}</h3>
        <span className="cg-card__amount" style={{ fontSize: 20 }}>{money(hold.max_amount)}</span>
      </div>
      {blocked ? (
        <>
          <p className="cg-icon-line cg-muted"><Icon name="lock" size={18} />This kind of store stays blocked on Ruth&apos;s card.</p>
          <button type="button" className="ch-btn cg-btn" onClick={() => onKeep(hold.hold_id)}>Got it</button>
        </>
      ) : (
        <>
          <p className="cg-card__body">{declineWords(hold.reason_key, hold.reason)} If Ruth really needs this, let it through once.</p>
          <div className="cg-btn-row">
            <button type="button" className="ch-btn ch-btn--primary cg-btn" onClick={() => onKeep(hold.hold_id)}>Keep blocked</button>
            <button type="button" className="ch-btn cg-btn" onClick={() => onAllow(hold)}>Allow once</button>
          </div>
          <p className="cg-muted" style={{ fontSize: 13, lineHeight: 1.5 }}>Allow once asks you to confirm. Ruth then has 10 minutes to tap her card again.</p>
        </>
      )}
    </article>
  );
}

export default function Approvals({ approvals, now, holds, declines, allowedHolds, threshold, onApprove, onDecline, onWhy, onKeep, onAllow, onSubmitCode }) {
  const timeOf = (hold) => (declines.find((row) => row.token && row.token === hold.token) || {}).at;
  const allowed = Object.values(allowedHolds).filter((entry) => new Date(entry.allowed_until).getTime() > now);
  return (
    <>
      <h1 className="cg-title">Approvals</h1>
      <h2 className="cg-overline" style={{ paddingTop: 0 }}>Waiting for you · {approvals.length}</h2>
      {approvals.length ? approvals.map((approval) => (
        <ApprovalCard key={approval.approval_id} approval={approval} now={now} threshold={threshold}
          onApprove={onApprove} onDecline={onDecline} onWhy={onWhy} onSubmitCode={onSubmitCode} />
      )) : <p className="cg-empty"><Icon name="check" size={22} />Nothing is waiting for you.</p>}

      <h2 className="cg-overline cg-overline--gap">Card holds · {holds.length}</h2>
      {allowed.map((entry) => (
        <p key={entry.hold_id} className="cg-allowed" role="status">
          <Icon name="check" size={20} stroke={2.6} />
          <span>Allowed once: {money(entry.max_amount)} at {storeName(entry.store) || "the store"}. Ruth can tap her card again until {clockTime(entry.allowed_until)}.</span>
        </p>
      ))}
      {holds.length ? holds.map((hold) => (
        <HoldCard key={hold.hold_id} hold={hold} at={timeOf(hold)} onKeep={onKeep} onAllow={onAllow} />
      )) : <p className="cg-empty"><Icon name="card" size={22} />No card holds.</p>}
    </>
  );
}
