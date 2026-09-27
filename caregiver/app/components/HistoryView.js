import { money } from "@/lib/money";
import { orderStatus, refundStatus } from "@/lib/status";
import { isBiller, storeName } from "@/lib/stores";
import { clockTime, dayLabel, joinWords } from "@/lib/words";
import Icon from "./Icon";

// Items arrive as "2 x Rice"; a single one reads as just its name.
function itemWords(items) {
  const names = (items || []).map((line) => {
    const match = String(line).match(/^(\d+)\s*x\s+(.+)$/i);
    if (!match) return String(line);
    return match[1] === "1" ? match[2] : `${match[2]} (${match[1]})`;
  }).filter(Boolean);
  if (names.length <= 3) return joinWords(names);
  return `${names.slice(0, 3).join(", ")} and ${names.length - 3} more`;
}

function cardName(last4) {
  const digits = String(last4 || "");
  return /^\d{4}$/.test(digits) ? `Visa ending ${digits}` : "the card that paid";
}

function Order({ order, now, onCancel }) {
  const store = order.store || order.merchant;
  const bill = isBiller(store);
  const status = orderStatus(order.status, bill);
  const what = bill ? "Bill" : itemWords(order.items);
  const when = [dayLabel(order.at, now) === "Today" ? "" : dayLabel(order.at, now), clockTime(order.at)].filter(Boolean).join(", ");
  return (
    <div className="cg-order">
      <span className="cg-order__tile" aria-hidden="true"><Icon name={bill ? "bolt" : "store"} size={22} /></span>
      <div className="cg-order__body">
        <div className="cg-order__line">
          <h3 className="cg-order__store">{storeName(store) || "Order"}</h3>
          <span className="cg-order__total">{money(order.total)}</span>
        </div>
        <p className="cg-order__meta">{[what, when].filter(Boolean).join(" · ")}</p>
        <div className="cg-order__status">
          <span className={`ch-badge${status.tone === "ok" ? " ch-badge--ok" : ""}`}>{status.label}</span>
          {order.pickup_code ? <span className="cg-order__code">code {order.pickup_code}</span> : null}
          {order.status === "awaiting_payment" ? (
            <button type="button" className="cg-link-btn cg-link-btn--danger" onClick={() => onCancel(order)}>Cancel order</button>
          ) : null}
        </div>
      </div>
    </div>
  );
}

export default function HistoryView({ history, now, onCancel }) {
  const orders = (history && history.orders) || [];
  const refunds = (history && history.refunds) || [];
  const byId = new Map(orders.map((order) => [order.order_id, order]));
  const groups = [];
  for (const order of orders) {
    const label = dayLabel(order.at, now);
    const last = groups[groups.length - 1];
    if (last && last.label === label) last.orders.push(order);
    else groups.push({ label, orders: [order] });
  }
  return (
    <>
      <h1 className="cg-title">Activity</h1>
      {groups.length === 0 ? (
        <>
          <h2 className="cg-overline" style={{ paddingTop: 0 }}>Orders</h2>
          <p className="cg-empty"><Icon name="receipt" size={22} />No orders yet.</p>
        </>
      ) : groups.map((group, index) => (
        <section key={group.label} className="cg-btn-col" style={{ gap: 14 }} aria-label={`Orders, ${group.label}`}>
          <h2 className="cg-overline" style={index ? undefined : { paddingTop: 0 }}>Orders · {group.label}</h2>
          <div className="cg-card cg-card--flush">
            {group.orders.map((order, row) => <Order key={order.order_id || row} order={order} now={now} onCancel={onCancel} />)}
          </div>
        </section>
      ))}

      <h2 className="cg-overline cg-overline--gap">Refunds</h2>
      {refunds.length === 0 ? <p className="cg-empty"><Icon name="refund" size={22} />No refunds yet.</p> : (
        <>
          <div className="cg-card cg-card--flush">
            {refunds.map((refund, index) => {
              const order = byId.get(refund.order_id);
              const status = refundStatus(refund.status);
              const what = order ? `From ${storeName(order.store || order.merchant)}` : "";
              const when = refund.at ? `${dayLabel(refund.at, now)}, ${clockTime(refund.at)}` : "";
              return (
                <div key={refund.order_id ? `${refund.order_id}-${index}` : index} className="cg-order">
                  <span className={`cg-order__tile${status.tone === "err" ? "" : " cg-order__tile--ok"}`} aria-hidden="true"><Icon name="refund" size={22} /></span>
                  <div className="cg-order__body">
                    <div className="cg-order__line">
                      <h3 className="cg-order__store">Back to {cardName(refund.card_last4)}</h3>
                      <span className="cg-order__total">{money(refund.amount)}</span>
                    </div>
                    {what || when ? <p className="cg-order__meta">{[what, when].filter(Boolean).join(" · ")}</p> : null}
                    <div className="cg-order__status">
                      <span className={`ch-badge ch-badge--${status.tone}`}>{status.label}</span>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
          <p className="cg-note">Refunds only go back to the card that paid.</p>
        </>
      )}
    </>
  );
}
