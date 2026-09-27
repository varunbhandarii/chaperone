import { money } from "@/lib/money";
import { spendByStore, weekSummary } from "@/lib/spend";
import { storeName } from "@/lib/stores";
import { patternWords, plural, whenWords } from "@/lib/words";
import Icon from "./Icon";

function greeting(now) {
  const hour = new Date(now).getHours();
  if (hour >= 5 && hour < 12) return "Good morning, Priyank";
  if (hour >= 12 && hour < 17) return "Good afternoon, Priyank";
  return "Good evening, Priyank";
}

// "1 scam call · 1 refusal · 2 card declines": scams_stopped counts scam calls and refusals together, and its
// rows tell the scam calls ("ask") apart.
function keptCounts(totals) {
  const stopped = Number(totals.scams_stopped) || 0;
  const declines = Number(totals.card_declines) || 0;
  const rows = Array.isArray(totals.rows) ? totals.rows : null;
  const parts = [];
  if (rows) {
    const calls = Math.min(stopped, rows.filter((row) => row.guard === "ask").length);
    if (calls) parts.push(plural(calls, "scam call", "scam calls"));
    if (stopped - calls) parts.push(plural(stopped - calls, "refusal", "refusals"));
  } else if (stopped) {
    parts.push(plural(stopped, "scam stopped", "scams stopped"));
  }
  if (declines) parts.push(plural(declines, "card decline", "card declines"));
  return parts.length ? parts.join(" · ") : "Nothing has needed stopping yet.";
}

function thisMonth(history, now) {
  const today = new Date(now);
  const inMonth = (row) => {
    const at = new Date(row.at || "");
    return Number.isNaN(at.getTime()) || (at.getFullYear() === today.getFullYear() && at.getMonth() === today.getMonth());
  };
  const orders = ((history && history.orders) || []).filter(inMonth);
  const waiting = orders.filter((order) => order.status === "awaiting_payment").reduce((sum, order) => sum + (Number(order.total) || 0), 0);
  return { paid: spendByStore({ orders, refunds: (history && history.refunds) || [] }), waiting };
}

export default function Home({ budget, paused, risk, protectedTotals, history, declines, mandate, ruthPhone, now, onPause, onResume, onEndCare, onOpenSafety }) {
  const spent = budget && Number.isFinite(Number(budget.spent)) ? Number(budget.spent) : 0;
  const cap = budget && Number(budget.monthly_cap) > 0 ? Number(budget.monthly_cap) : Number(mandate.monthly_cap) || 300;
  const left = Math.max(0, cap - spent);
  const share = Math.min(100, Math.max(0, (spent / cap) * 100));
  const month = thisMonth(history, now);
  const week = weekSummary(history, declines, now);
  const care = Boolean(risk && risk.active);
  const why = care ? patternWords(risk.reason) : "";
  const careCap = ((mandate.card || {}).cooldown || {}).caps;
  const holdOver = careCap && careCap.default !== undefined ? Number(careCap.default) : null;
  const careText = [
    `Until ${whenWords(risk && risk.cooldown_until, now) || "later today"}, after a scam check${why ? `: ${why[0].toLowerCase()}${why.slice(1)}` : ""}.`,
    Number.isFinite(holdOver) && holdOver !== null ? `Card charges over ${money(holdOver)} wait for you.` : "",
  ].filter(Boolean).join(" ");

  return (
    <>
      <p className="cg-greeting">{greeting(now)}</p>

      <section className={`cg-hero${paused ? " cg-hero--paused" : ""}`} aria-labelledby="hero-title">
        <div className="cg-hero__top">
          <span className="cg-hero__badge" aria-hidden="true">
            <Icon name={paused ? "pause" : "shieldCheck"} size={26} stroke={2.4} />
          </span>
          <div>
            <h1 id="hero-title" className="cg-hero__title">{paused ? "Shopping is paused" : "Ruth is protected"}</h1>
            <p className="cg-hero__sub">
              {paused ? "Chaperone won't place orders or pay bills until you turn shopping back on." : "Shopping and bills are on, inside your rules."}
            </p>
          </div>
        </div>
        <div className={`cg-hero__row${ruthPhone && !paused ? "" : " cg-hero__row--one"}`}>
          {ruthPhone ? (
            <a className="cg-hero__btn cg-hero__btn--solid" href={`tel:${ruthPhone}`}>
              <Icon name="phone" size={18} stroke={2.2} />Call Ruth
            </a>
          ) : null}
          {paused ? (
            <button type="button" className="cg-hero__btn cg-hero__btn--ghost" onClick={onResume}>
              <Icon name="key" size={18} />Resume shopping
            </button>
          ) : (
            <button type="button" className="cg-hero__btn cg-hero__btn--ghost" onClick={onPause}>
              <Icon name="pause" size={18} stroke={2.4} />Pause shopping
            </button>
          )}
        </div>
        {paused ? <p className="cg-hero__sub">Resuming asks for your passkey.</p> : null}
      </section>

      {care ? (
        <section className="cg-care" aria-labelledby="care-title">
          <Icon name="clock" size={22} stroke={2.2} />
          <div className="cg-care__body">
            <h2 id="care-title" className="cg-care__title">Extra care on Ruth&apos;s card</h2>
            <p className="cg-care__text">{careText}</p>
          </div>
          <button type="button" className="cg-care__btn" onClick={onEndCare}>End early</button>
        </section>
      ) : null}

      <button type="button" className="cg-kept" onClick={onOpenSafety}>
        <span className="cg-kept__icon" aria-hidden="true"><Icon name="shieldCheck" size={26} stroke={2.2} /></span>
        <span className="cg-kept__body">
          <span className="cg-overline" style={{ padding: 0 }}>Kept safe</span>
          <span className="cg-kept__amount">{money(protectedTotals.dollars) || money(0)}</span>
          <span className="cg-kept__counts">{keptCounts(protectedTotals)}</span>
        </span>
        <Icon name="chevron" size={20} />
        <span className="cg-sr">Open Safety</span>
      </button>

      <section className="cg-card" aria-labelledby="month-title">
        <div className="cg-month__head">
          <h2 id="month-title" className="cg-card__title">This month</h2>
          <span className="cg-muted cg-num" style={{ fontSize: 14 }}>{money(left)} left</span>
        </div>
        <p className="cg-month__spent">{money(spent)} <span>of {money(cap)}</span></p>
        <div className="cg-bar" role="progressbar" aria-label="Spent this month" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(share)} aria-valuetext={`${money(spent)} of ${money(cap)}`}>
          <div className="cg-bar__fill" style={{ width: `${share}%` }} />
        </div>
        <p className="cg-muted" style={{ fontSize: 13 }}>Counts every order placed, paid or not.</p>
        <div>
          <h3 className="cg-label-small">Paid so far</h3>
          <div className="cg-rows">
            {month.paid.map(({ store, amount }) => (
              <div key={store || "other"} className="cg-row">
                <span>{store ? storeName(store) : "Other"}</span>
                <span className="cg-row__amount">{money(amount)}</span>
              </div>
            ))}
            {month.waiting > 0 ? (
              <div className="cg-row cg-row--muted">
                <span>Waiting for payment</span>
                <span className="cg-row__amount">{money(month.waiting)}</span>
              </div>
            ) : null}
            {!month.paid.length && !(month.waiting > 0) ? <p className="cg-row cg-row--muted">Nothing paid yet this month.</p> : null}
          </div>
        </div>
      </section>

      <section className="cg-card" aria-labelledby="week-title">
        <h2 id="week-title" className="cg-card__title">This week</h2>
        <div className="cg-week">
          {[
            [week.errands, "errand done", "errands done"],
            [week.bills, "bill paid", "bills paid"],
            [week.stopped, "attempt stopped", "attempts stopped"],
          ].map(([count, one, many]) => (
            <div key={one} className="cg-week__tile">
              <div className="cg-week__num">{count}</div>
              <div className="cg-week__label">{plural(count, one, many).replace(/^\S+ /, "")}</div>
            </div>
          ))}
        </div>
      </section>
    </>
  );
}
