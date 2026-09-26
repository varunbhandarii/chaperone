import { btn, card, field } from "./styles";

export default function Home({
  budget, paused, approval, now, alerts, declineNote, fallbackCode,
  onDecline, onCode, onApprove, onReject, onWhy, onCodeSubmit,
  onPause, onResume, onRules, onHistory, onAlertWhy,
}) {
  const spent = budget ? budget.spent : "…";
  const cap = budget ? budget.monthly_cap : 300;
  const seconds = approval ? Math.max(0, Math.ceil((new Date(approval.expires_at).getTime() - now) / 1000)) : 0;
  return (
    <section>
      <h1>Chaperone</h1>
      <p style={{ fontSize: "1.4rem" }}>This month: ${spent} of ${cap}</p>
      <p style={{ fontSize: "1.15rem" }}>{paused ? "The agent is paused." : "The agent is running."}</p>
      <p>
        {paused ? <button style={btn} onClick={onResume}>Resume</button> : <button style={btn} onClick={onPause}>Pause</button>}
        <a href="tel:" style={{ fontSize: "1.15rem", marginLeft: "0.5rem" }}>Call Ruth</a>
      </p>
      <h2>Needs your approval</h2>
      {approval ? (
        <article style={{ ...card, background: "#8c2f2f", color: "white" }}>
          <p style={{ fontSize: "2.4rem", margin: "0.2rem 0" }}>${approval.amount}</p>
          <p>{approval.merchant}</p>
          <p>{approval.excerpt}</p>
          <p>{seconds}s left</p>
          <button style={btn} onClick={onApprove}>Approve with passkey</button>
          <input value={declineNote} onChange={(event) => onDecline(event.target.value)} placeholder="optional note to Ruth" style={field} />
          <button style={btn} onClick={onReject}>Reject</button>
          <button style={btn} onClick={onWhy}>Why?</button>
          <p>
            <input value={fallbackCode} onChange={(event) => onCode(event.target.value)} inputMode="numeric" maxLength={6} placeholder="approval code" style={field} />
            <button style={btn} onClick={onCodeSubmit}>Submit code</button>
          </p>
        </article>
      ) : <p>Nothing waiting.</p>}
      <h2>Alerts</h2>
      {alerts.length === 0 ? <p>No alerts yet.</p> : alerts.map((alert) => (
        <article key={alert.id} style={card}>
          <p style={{ fontSize: "1.15rem" }}>{alert.text}</p>
          {alert.decision_id ? <button style={btn} onClick={() => onAlertWhy(alert.decision_id)}>Why?</button> : null}
        </article>
      ))}
      <p>
        <button style={btn} onClick={onHistory}>Orders</button>
        <button style={btn} onClick={onRules}>Rules</button>
      </p>
    </section>
  );
}
