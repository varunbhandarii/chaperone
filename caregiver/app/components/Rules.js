import { shortMoney } from "@/lib/money";
import { rulesSentence } from "@/lib/rulesSentence";
import { storeNames, storefronts } from "@/lib/stores";
import { capitalize, categoryName, clockTime } from "@/lib/words";
import Icon from "./Icon";

const BLOCKED = ["gift_card", "prepaid_card", "wire", "crypto", "lottery"];
export const LIMITS = [
  { key: "per_purchase_cap", label: "Per purchase", help: "The most Ruth can spend at one time." },
  { key: "monthly_cap", label: "Per month", help: "Everything Ruth spends in a month, bills included." },
  { key: "approval_threshold", label: "Ask me above", help: "Anything above this comes to your phone first." },
];

const blank = (value) => String(value ?? "").trim() === "" || !Number.isFinite(Number(value)) || Number(value) < 0;

// What differs from the rules Priyank last signed: each limit, category and store counts once.
export function ruleChanges(mandate, signed) {
  const changes = { limits: {}, blocked: new Set(), stores: new Set(), count: 0 };
  if (!signed) return changes;
  for (const { key } of LIMITS) {
    if (blank(mandate[key]) || Number(mandate[key]) !== Number(signed[key])) changes.limits[key] = signed[key];
  }
  const diff = (now, before, into) => {
    const a = new Set(now || []);
    const b = new Set(before || []);
    for (const id of new Set([...a, ...b])) if (a.has(id) !== b.has(id)) into.add(id);
  };
  diff(mandate.blocked_categories, signed.blocked_categories, changes.blocked);
  diff(mandate.allowed_merchants, signed.allowed_merchants, changes.stores);
  changes.count = Object.keys(changes.limits).length + changes.blocked.size + changes.stores.size;
  return changes;
}

function storeDetail(store, mandate) {
  if (store.kind === "biller") {
    const biller = (mandate.billers || []).find((entry) => entry.merchant_id === store.id);
    const kind = /power/i.test(store.name) ? "Power bill" : "Utility bill";
    return biller && Number(biller.monthly_cap) > 0 ? `${kind}, up to ${shortMoney(biller.monthly_cap)} a month` : kind;
  }
  return categoryName((store.categories || [])[0]);
}

function cardLines(mandate) {
  const card = mandate.card || {};
  const lines = [];
  if ((card.blocked_mccs || []).length) lines.push(["lock", "Gift-card shops, crypto and wires are always declined."]);
  const drugstore = (card.category_caps || {})["5912"] ?? card.default_cap;
  if (drugstore !== undefined && drugstore !== null) lines.push(["store", `A drugstore swipe can be up to ${shortMoney(drugstore)}.`]);
  if (Number(card.atm_daily_cap) > 0) lines.push(["card", `Cash from a machine is limited to ${shortMoney(card.atm_daily_cap)} a day.`]);
  const cooldown = card.cooldown || {};
  const hours = Number(cooldown.hours);
  if (hours > 0) {
    const over = (cooldown.caps || {}).default;
    const span = hours === 24 ? "a day" : `${hours} hours`;
    lines.push(["clock", over !== undefined ? `After a scam call, charges over ${shortMoney(over)} wait for you for ${span}.` : `After a scam call, the card takes extra care for ${span}.`]);
  }
  return lines;
}

function Status({ signed, cosign, signedAt }) {
  const badge = cosign ? ["ch-badge--ok", "In force"] : signed ? ["ch-badge--wait", "Waiting for Ruth"] : ["", "Not signed yet"];
  const agreedAt = cosign ? clockTime(cosign.at) : "";
  const said = cosign ? String(cosign.said || "").trim() : "";
  return (
    <section className="cg-card" aria-label="Signatures">
      <span className={`ch-badge ${badge[0]}`} style={{ alignSelf: "flex-start" }}>{badge[1]}</span>
      {signed ? (
        <p className="cg-seal"><Icon name="seal" size={24} /><span><b>You signed</b>{signedAt ? ` at ${clockTime(signedAt)}` : ""} with your passkey.</span></p>
      ) : (
        <p className="cg-seal cg-seal--wait"><Icon name="key" size={24} /><span><b>Sign the rules below with your passkey.</b> Then Ruth hears them and says yes.</span></p>
      )}
      {cosign ? (
        <p className="cg-seal"><Icon name="seal" size={24} /><span><b>Ruth agreed by voice</b>{agreedAt ? ` at ${agreedAt}` : ""}{said ? `: “${said}”` : "."}</span></p>
      ) : signed ? (
        <p className="cg-seal cg-seal--wait"><Icon name="clock" size={24} /><span><b>Ruth hasn&apos;t agreed yet.</b> She hears the rules in her language and says yes.</span></p>
      ) : null}
    </section>
  );
}

export default function Rules({ mandate, signedMandate, signed, cosign, signedAt, changes, onChange, onBlock, onUnblock, onToggleStore }) {
  const blocked = [...new Set([...BLOCKED, ...(mandate.blocked_categories || [])])];
  const allowed = new Set(mandate.allowed_merchants || []);
  const refused = new Set(mandate.blocked_categories || []);
  return (
    <>
      <h1 className="cg-title">Ruth&apos;s rules</h1>
      <Status signed={signed} cosign={cosign} signedAt={signedAt} />

      <section className="cg-plain" aria-labelledby="plain-title">
        <h2 id="plain-title" className="cg-plain__label">IN PLAIN WORDS</h2>
        <p className="cg-plain__text">{rulesSentence(mandate, storeNames)}</p>
      </section>

      <section className="cg-card" style={{ gap: 16 }} aria-labelledby="limits-title">
        <h2 id="limits-title" className="cg-card__title">Limits</h2>
        {LIMITS.map(({ key, label, help }) => {
          const empty = blank(mandate[key]);
          const changed = key in changes.limits && !empty;
          return (
            <div key={key} className="cg-field">
              <label className="cg-field__label" htmlFor={`limit-${key}`}>{label}</label>
              <div className={`cg-money${changed ? " cg-money--changed" : ""}${empty ? " cg-money--error" : ""}`}>
                <span className="cg-money__sign" aria-hidden="true">$</span>
                <input id={`limit-${key}`} inputMode="decimal" value={mandate[key]} aria-describedby={`limit-${key}-help`}
                  aria-invalid={empty || undefined} onChange={(event) => onChange(key, event.target.value)} />
              </div>
              <p id={`limit-${key}-help`} className={empty ? "cg-field__error" : changed ? "cg-field__changed" : "cg-field__help"}>
                {empty ? "Enter an amount in dollars." : changed ? `Changed from ${shortMoney(signedMandate[key])} · not signed yet` : help}
              </p>
            </div>
          );
        })}
      </section>

      <section className="cg-card cg-card--list" aria-labelledby="refused-title">
        <h2 id="refused-title" className="cg-card__title">Always refused</h2>
        {blocked.map((id) => {
          const on = refused.has(id);
          const changed = changes.blocked.has(id);
          return (
            <label key={id} className="cg-switch-row">
              <span className="cg-switch-row__text">
                <span className="cg-switch-row__name">{categoryName(id)}</span>
                {changed ? (
                  <span className="cg-switch-row__sub cg-switch-row__sub--changed">{on ? "Blocked again · not signed yet" : "Unblocked · not signed yet. Ruth has to agree."}</span>
                ) : on ? null : <span className="cg-switch-row__sub">Not blocked</span>}
              </span>
              <span className="cg-switch">
                <input type="checkbox" role="switch" checked={on} onChange={() => (on ? onUnblock(id) : onBlock(id))} />
                <span className="cg-switch__track" aria-hidden="true" />
              </span>
            </label>
          );
        })}
      </section>

      <section className="cg-card cg-card--list" aria-labelledby="stores-title">
        <h2 id="stores-title" className="cg-card__title">Where Ruth can shop</h2>
        {storefronts().map((store) => (
          <label key={store.id} className="cg-check-row">
            <span className="cg-check">
              <input type="checkbox" checked={allowed.has(store.id)} onChange={() => onToggleStore(store.id)} />
              <span className="cg-check__box" aria-hidden="true"><Icon name="check" size={16} stroke={3.2} /></span>
            </span>
            <span className="cg-switch-row__text">
              <span className="cg-switch-row__name">{store.name}</span>
              <span className={`cg-switch-row__sub${changes.stores.has(store.id) ? " cg-switch-row__sub--changed" : ""}`}>
                {storeDetail(store, mandate)}{changes.stores.has(store.id) ? " · not signed yet" : ""}
              </span>
            </span>
          </label>
        ))}
      </section>

      <section className="cg-card" aria-labelledby="card-title">
        <h2 id="card-title" className="cg-card__title">Ruth&apos;s card</h2>
        {cardLines(mandate).map(([icon, text]) => (
          <p key={text} className="cg-icon-line"><Icon name={icon} size={20} style={{ color: "var(--ch-blue)" }} />{text}</p>
        ))}
      </section>

      <section className="cg-card cg-card--list" aria-labelledby="people-title">
        <h2 id="people-title" className="cg-card__title">People Ruth trusts</h2>
        {(mandate.trusted_contacts || []).map((person) => {
          const you = String(person.name || "").toLowerCase() === String(mandate.caregiver || "").toLowerCase();
          return (
            <div key={person.phone || person.name} className="cg-person">
              <span className="cg-person__avatar" aria-hidden="true">{String(person.name || "?")[0].toUpperCase()}</span>
              <span className="cg-person__text">
                <span className="cg-switch-row__name">{person.name}</span>
                <span className="cg-switch-row__sub">{[capitalize(person.relation), you ? "you" : ""].filter(Boolean).join(" · ")}</span>
              </span>
              {person.phone && !you ? (
                <a className="cg-person__call" href={`tel:${person.phone}`} aria-label={`Call ${person.name}`}><Icon name="phone" size={18} stroke={2.2} /></a>
              ) : null}
            </div>
          );
        })}
      </section>
    </>
  );
}

// Shown above the tab bar only while something is not signed yet.
export function SignFooter({ signed, count, onSign }) {
  return (
    <div className="cg-footer">
      <div className="cg-footer__inner">
        <p className="cg-footer__text" role="status">
          <b>{signed ? `${count} ${count === 1 ? "change" : "changes"} not signed.` : "These rules are not signed yet."}</b>{" "}
          {signed ? "Ruth will hear the new rules and has to agree." : "Ruth will hear them and has to agree."}
        </p>
        <button type="button" className="ch-btn ch-btn--primary cg-btn cg-btn--tall" onClick={onSign}>
          <Icon name="key" size={20} />Sign with passkey
        </button>
      </div>
    </div>
  );
}
