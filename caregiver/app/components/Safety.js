import { money } from "@/lib/money";
import { storeName } from "@/lib/stores";
import { humanize } from "@/lib/words";

const PATTERNS = {
  grandparent_emergency: "Someone claiming a family emergency.",
  refund_scam: "A refund scam.",
  refund_overpayment: "A refund scam.",
  refund_fee: "A refund scam.",
  recovery_scam: "Someone offering to get lost money back for a fee.",
  tech_support: "A fake tech-support call.",
  utility_impersonation: "Someone pretending to be the power company.",
  utility_shutoff: "Someone pretending to be the power company.",
  bank_impersonation: "Someone pretending to be the bank.",
  safe_account: "Someone asking to move money to a \"safe\" account.",
  government_impersonation: "Someone pretending to be the government.",
  digital_arrest: "Someone pretending to be the police.",
  gift_card_codes: "Someone asking for gift card numbers.",
  gift_card_demand: "Someone asking to be paid in gift cards.",
  crypto_atm: "Someone asking for cash at a crypto machine.",
  courier_pickup: "Someone sending a courier to collect money.",
  parcel_customs: "A fake parcel or customs fee.",
  fake_delivery: "A fake delivery fee.",
  fake_renewal: "A fake subscription renewal.",
};

const VERDICTS = { scam: "This was a scam.", unsure: "This looked risky.", ok: "This looked fine." };

function safeUrl(url) {
  return /^https?:\/\//i.test(url || "") ? url : null;
}

// A source with no title is named by its site, never by a long raw link.
function sourceLabel(source) {
  if (source.title) return source.title;
  try {
    return new URL(source.url).hostname.replace(/^www\./, "");
  } catch {
    return "Source";
  }
}

function patternWords(pattern) {
  if (PATTERNS[pattern]) return PATTERNS[pattern];
  return pattern && pattern !== "unknown" ? `${humanize(pattern)}.` : "";
}

export default function Safety({ checks, alerts, declines, onWhy }) {
  return (
    <section>
      <h1>Safety</h1>
      <h2>Scam checks</h2>
      {checks.length === 0 ? <p>No scam checks yet.</p> : checks.map((check) => (
        <article key={check.check_id || check.decision_id} className="ch-card">
          {check.story_excerpt ? <p>{check.story_excerpt}</p> : null}
          <p>{VERDICTS[check.verdict] || ""} {patternWords(check.pattern)}</p>
          {(check.sources || []).filter((source) => safeUrl(source.url) || source.title).map((source) => (
            <p key={source.url} style={{ overflowWrap: "anywhere" }}>{safeUrl(source.url) ? <a href={source.url} target="_blank" rel="noopener noreferrer">{sourceLabel(source)}</a> : source.title}</p>
          ))}
          {check.decision_id ? <button className="ch-btn" onClick={() => onWhy(check.decision_id)}>Why?</button> : null}
        </article>
      ))}
      <h2>Refusals</h2>
      {alerts.length === 0 ? <p>No refusals yet.</p> : alerts.map((alert) => (
        <article key={alert.id} className="ch-card">
          <p>{alert.text}</p>
          {alert.decision_id ? <button className="ch-btn" onClick={() => onWhy(alert.decision_id)}>Why?</button> : null}
        </article>
      ))}
      <h2>Card declines</h2>
      {declines.length === 0 ? <p>No card declines yet.</p> : declines.map((row) => (
        <article key={row.token || row.at} className="ch-card">
          <p>{storeName(row.store) || row.store} · {money(row.amount)}</p>
          <p>{row.reason}</p>
        </article>
      ))}
    </section>
  );
}
