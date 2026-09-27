import { money } from "@/lib/money";
import { storeName } from "@/lib/stores";

const PATTERNS = {
  grandparent_emergency: "a family emergency",
  refund_scam: "a refund scam",
  tech_support: "a tech-support scam",
  utility_impersonation: "someone pretending to be the power company",
};

export default function Safety({ checks, alerts, declines, onWhy }) {
  return (
    <section>
      <h1>Safety</h1>
      <h2>Scam checks</h2>
      {checks.length === 0 ? <p>No scam checks yet.</p> : checks.map((check) => (
        <article key={check.check_id || check.decision_id} className="ch-card">
          <p>{check.story_excerpt}</p>
          <p>{check.verdict === "scam" ? "This was a scam." : check.verdict} {PATTERNS[check.pattern] || check.pattern}</p>
          {(check.sources || []).map((source) => <p key={source.url}><a href={source.url}>{source.title}</a></p>)}
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
