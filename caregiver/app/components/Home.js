import { money } from "@/lib/money";
import { spendByStore, weekSummary } from "@/lib/spend";
import { storeName } from "@/lib/stores";

export default function Home({ budget, paused, risk, protectedTotals, history, declines, ruthPhone, onPause, onResume, onClear }) {
  const spent = budget ? Number(budget.spent) : 0;
  const cap = budget ? Number(budget.monthly_cap) : 300;
  const left = Math.max(0, cap - (Number.isFinite(spent) ? spent : 0));
  const byStore = spendByStore(history);
  const week = weekSummary(history, declines);
  const until = risk && risk.active && risk.cooldown_until
    ? new Date(risk.cooldown_until).toLocaleString("en-US", { hour: "numeric", minute: "2-digit", month: "short", day: "numeric" })
    : "";
  return (
    <section>
      <div className="ch-banner">
        <h1 style={{ margin: "0 0 0.3rem" }}>{risk && risk.active ? "Extra care on her card" : "Mom is protected"}</h1>
        <p>{risk && risk.active ? `Until ${until}.` : paused ? "The agent is paused." : "The agent is running."}</p>
        {risk && risk.active ? <button className="ch-btn" onClick={onClear}>Clear</button> : null}
        {paused ? <button className="ch-btn" onClick={onResume}>Resume</button> : <button className="ch-btn" onClick={onPause}>Pause</button>}
        {ruthPhone ? <a href={`tel:${ruthPhone}`}>Call Ruth</a> : null}
      </div>
      <h2>This month</h2>
      <p>{money(spent)} of {money(cap)}. {money(left)} left.</p>
      {byStore.map(({ store, amount }) => (
        <p key={store}>{store ? storeName(store) : "Other"} <span className="ch-pill">{money(amount)}</span></p>
      ))}
      <h2>Protected</h2>
      <p>{money(protectedTotals.dollars)} stopped. {protectedTotals.scams_stopped} scams stopped. {protectedTotals.card_declines} card declines.</p>
      <h2>This week</h2>
      <p>{week.errands} errands done. {week.bills} bills paid. {week.stopped} attempts stopped.</p>
    </section>
  );
}
