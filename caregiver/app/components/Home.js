import { money } from "@/lib/money";
import { storeName } from "@/lib/stores";

function thisWeek(history, declines) {
  const start = Date.now() - 7 * 24 * 60 * 60 * 1000;
  const recent = (list) => (list || []).filter((row) => !row.at || new Date(row.at).getTime() >= start);
  const orders = recent(history && history.orders).filter((order) => order.status && order.status !== "cancelled" && order.status !== "awaiting_payment");
  const bills = orders.filter((order) => order.store === "peachtree_power" || (order.items || []).some((line) => String(line).toLowerCase().includes("bill")));
  const errands = orders.filter((order) => !bills.includes(order));
  const stopped = recent(history && history.refusals).length + recent(declines).length;
  return { errands: errands.length, bills: bills.length, stopped };
}

export default function Home({ budget, paused, risk, protectedTotals, history, declines, ruthPhone, onPause, onResume, onClear }) {
  const spent = budget ? Number(budget.spent) : 0;
  const cap = budget ? Number(budget.monthly_cap) : 300;
  const left = Math.max(0, cap - (Number.isFinite(spent) ? spent : 0));
  const byStore = {};
  for (const order of (history && history.orders) || []) {
    const id = order.store || order.merchant || "corner_market";
    byStore[id] = (byStore[id] || 0) + Number(order.total || 0);
  }
  const week = thisWeek(history, declines);
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
      {Object.entries(byStore).map(([id, amount]) => (
        <p key={id}>{storeName(id)} <span className="ch-pill">{money(amount)}</span></p>
      ))}
      <h2>Protected</h2>
      <p>{money(protectedTotals.dollars)} stopped. {protectedTotals.scams_stopped} scams stopped. {protectedTotals.card_declines} card declines.</p>
      <h2>This week</h2>
      <p>{week.errands} errands done. {week.bills} bills paid. {week.stopped} attempts stopped.</p>
    </section>
  );
}
