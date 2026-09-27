import { useState } from "react";
import { money } from "@/lib/money";
import { declineWords, refusalTitle } from "@/lib/status";
import { storeName } from "@/lib/stores";
import { channelWords, clockTime, dayLabel, patternWords } from "@/lib/words";
import Icon from "./Icon";

const FILTERS = [
  { id: "all", label: "All" },
  { id: "scam", label: "Scam calls" },
  { id: "refusal", label: "Refusals" },
  { id: "card", label: "Card" },
];

const VERDICTS = {
  scam: { label: "Scam stopped", badge: "ch-badge--protected", icon: "shield" },
  unsure: { label: "Looked risky", badge: "ch-badge--wait", icon: null },
  ok: { label: "Looked fine", badge: "ch-badge--info", icon: null },
};

const EMPTY = {
  all: "Nothing to show yet. Scam calls, refusals and card declines will appear here.",
  scam: "No scam calls checked yet.",
  refusal: "No refusals yet.",
  card: "No card declines yet.",
};

function safeUrl(url) {
  return /^https?:\/\//i.test(url || "") ? url : null;
}

function siteOf(url) {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return "";
  }
}

function headline(check) {
  const words = patternWords(check.pattern);
  if (words) return words;
  if (check.verdict === "scam") return "A scam call";
  if (check.verdict === "unsure") return "A call that looked risky";
  return "A call Chaperone checked";
}

function cooldownWords(mandate) {
  const hours = Number((((mandate || {}).card || {}).cooldown || {}).hours);
  if (!(hours > 0)) return "";
  return hours === 24 ? "a day" : `${hours} hours`;
}

// Everything Chaperone stopped or checked, newest first: scam checks, refusals (the stored ones plus any that
// arrived live and are not stored yet) and card declines. A scam check's own decision is not a second refusal.
export function safetyItems({ checks, refusals, alerts, declines }) {
  const checkIds = new Set(checks.map((check) => check.decision_id).filter(Boolean));
  const stored = (refusals || []).filter((row) => !checkIds.has(row.decision_id) && !String(row.say_key || "").startsWith("scam_check"));
  const storedIds = new Set(stored.map((row) => row.decision_id).filter(Boolean));
  const live = (alerts || []).filter((alert) => !alert.decision_id || (!storedIds.has(alert.decision_id) && !checkIds.has(alert.decision_id)));
  const items = [
    ...checks.map((check) => ({ kind: "scam", key: `s-${check.check_id || check.decision_id}`, at: check.at, check })),
    ...stored.map((row, index) => ({ kind: "refusal", key: `r-${row.decision_id || index}`, at: row.at, refusal: row })),
    ...live.map((alert) => ({ kind: "refusal", key: `a-${alert.id}`, at: alert.at, refusal: { decision_id: alert.decision_id, at: alert.at } })),
    ...declines.map((row, index) => ({ kind: "card", key: `c-${row.token || row.at || index}`, at: row.at, decline: row })),
  ];
  const time = (item) => Date.parse(item.at || "") || 0;
  return items.sort((a, b) => time(b) - time(a));
}

function ScamCard({ check, ruthPhone, mandate, onWhy }) {
  const verdict = VERDICTS[check.verdict] || VERDICTS.ok;
  const where = channelWords(check.channel);
  const time = clockTime(check.at);
  const sources = (check.sources || []).filter((source) => safeUrl(source.url) || source.title);
  const amount = Number(check.amount);
  const care = check.verdict === "scam" ? cooldownWords(mandate) : "";
  const story = String(check.story_excerpt || "").trim();
  return (
    <article className="cg-card">
      <div className="cg-card__head">
        <span className={`ch-badge ${verdict.badge}`}>
          {verdict.icon ? <Icon name={verdict.icon} size={13} stroke={2.8} /> : null}
          {verdict.label}
        </span>
        <span className="cg-card__time">{[time, where].filter(Boolean).join(" · ")}</span>
      </div>
      <h3 className="cg-card__title">{headline(check)}</h3>
      {story ? <blockquote className="cg-quote">“{story}”</blockquote> : null}
      {amount > 0 || care ? (
        <div className="cg-chips">
          {amount > 0 ? <span className="ch-badge ch-badge--money cg-num">{money(amount)} asked for</span> : null}
          {care ? <span className="cg-tag">Card on extra care for {care}</span> : null}
        </div>
      ) : null}
      {check.say ? (
        <div className="cg-sheet__section">
          <h4 className="cg-label-small">Chaperone told Ruth</h4>
          <p style={{ fontSize: 15, lineHeight: 1.5 }}>{check.say}</p>
        </div>
      ) : null}
      {sources.length ? (
        <div className="cg-sources">
          {sources.map((source, index) => {
            const url = safeUrl(source.url);
            const site = url ? siteOf(url) : "";
            const title = source.title || site || "Source";
            return url ? (
              <a key={url} className="cg-source" href={url} target="_blank" rel="noopener noreferrer">
                <Icon name="external" size={16} stroke={2.2} />
                <span className="cg-source__title">{title}</span>
                {site && site !== title ? <span className="cg-source__site">{site}</span> : null}
                <span className="cg-sr">(opens in a new tab)</span>
              </a>
            ) : (
              <p key={`${source.title}-${index}`} className="cg-source cg-source--plain"><span className="cg-source__title">{source.title}</span></p>
            );
          })}
        </div>
      ) : null}
      {ruthPhone || check.decision_id ? (
        <div className={ruthPhone && check.decision_id ? "cg-btn-row" : "cg-btn-col"}>
          {ruthPhone ? (
            <a className={`ch-btn cg-btn ${check.verdict === "scam" ? "ch-btn--primary" : ""}`} href={`tel:${ruthPhone}`}>
              <Icon name="phone" size={18} stroke={2.2} />Call Ruth
            </a>
          ) : null}
          {check.decision_id ? <button type="button" className="ch-btn cg-btn" onClick={() => onWhy(check.decision_id)}>Why?</button> : null}
        </div>
      ) : null}
    </article>
  );
}

function RefusalCard({ refusal, onWhy }) {
  const total = Number(refusal.total);
  return (
    <article className="cg-card">
      <div className="cg-card__head">
        <span className="ch-badge ch-badge--protected">Refused</span>
        <span className="cg-card__time">{clockTime(refusal.at)}</span>
      </div>
      <div className="cg-card__line">
        <h3 className="cg-card__store">{refusalTitle(refusal.say_key)}</h3>
        {total > 0 ? <span className="cg-card__amount">{money(total)}</span> : null}
      </div>
      <p className="cg-card__body">Nothing was bought. Chaperone told Ruth kindly why.</p>
      {refusal.decision_id ? <button type="button" className="cg-link-btn" onClick={() => onWhy(refusal.decision_id)}>Why?</button> : null}
    </article>
  );
}

function DeclineCard({ decline, allowedHolds }) {
  const allowed = decline.hold_id && allowedHolds[decline.hold_id];
  const until = allowed && allowed.allowed_until ? new Date(allowed.allowed_until).getTime() : NaN;
  const allowedAt = Number.isFinite(until) ? clockTime(new Date(until - 10 * 60 * 1000).toISOString()) : "";
  return (
    <article className="cg-card">
      <div className="cg-card__head">
        <span className="ch-badge ch-badge--protected">Card declined</span>
        <span className="cg-card__time">{clockTime(decline.at)}</span>
      </div>
      <div className="cg-card__line">
        <h3 className="cg-card__store">{storeName(decline.store) || decline.store || "A store"}</h3>
        <span className="cg-card__amount">{money(decline.amount)}</span>
      </div>
      <p className="cg-card__body">{declineWords(decline.reason_key, decline.reason)}</p>
      {allowedAt ? <p className="cg-ok-line"><Icon name="check" size={16} stroke={2.8} />You allowed it once at {allowedAt}</p> : null}
    </article>
  );
}

export default function Safety({ items, now, ruthPhone, mandate, allowedHolds, onWhy }) {
  const [filter, setFilter] = useState("all");
  const shown = items.filter((item) => filter === "all" || item.kind === filter);
  const groups = [];
  for (const item of shown) {
    const label = dayLabel(item.at, now);
    const last = groups[groups.length - 1];
    if (last && last.label === label) last.items.push(item);
    else groups.push({ label, items: [item] });
  }
  return (
    <>
      <h1 className="cg-title">Safety</h1>
      <div className="cg-segmented" role="group" aria-label="Show">
        {FILTERS.map(({ id, label }) => (
          <button key={id} type="button" aria-pressed={filter === id} onClick={() => setFilter(id)}>{label}</button>
        ))}
      </div>
      {groups.length === 0 ? (
        <p className="cg-empty"><Icon name="shieldCheck" size={22} />{EMPTY[filter]}</p>
      ) : groups.map((group) => (
        <section key={group.label} className="cg-btn-col" style={{ gap: 14 }} aria-label={group.label}>
          <h2 className="cg-overline">{group.label}</h2>
          {group.items.map((item) => {
            if (item.kind === "scam") return <ScamCard key={item.key} check={item.check} ruthPhone={ruthPhone} mandate={mandate} onWhy={onWhy} />;
            if (item.kind === "refusal") return <RefusalCard key={item.key} refusal={item.refusal} onWhy={onWhy} />;
            return <DeclineCard key={item.key} decline={item.decline} allowedHolds={allowedHolds} />;
          })}
        </section>
      ))}
    </>
  );
}
