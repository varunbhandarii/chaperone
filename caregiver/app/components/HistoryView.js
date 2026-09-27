import { money } from "@/lib/money";
import { isBiller, storeName } from "@/lib/stores";
import { cardWords } from "@/lib/words";

const STATUS = {
  awaiting_payment: "waiting for payment",
  paid: "paid",
  preparing: "being prepared",
  ready_for_pickup: "ready for pickup",
  picked_up: "picked up",
  cancelled: "cancelled",
  partially_refunded: "partly refunded",
  refunded: "refunded",
};

export default function HistoryView({ history, onCancel }) {
  const orders = (history && history.orders) || [];
  const refunds = (history && history.refunds) || [];
  return (
    <section>
      <h1>Activity</h1>
      {orders.length === 0 ? <p>No orders yet.</p> : orders.map((order) => (
        <article key={order.order_id} className="ch-card">
          <p>{storeName(order.store || order.merchant) || "Order"} · {money(order.total)}</p>
          <p>{isBiller(order.store) ? `Bill, ${STATUS[order.status] || "sent"}` : STATUS[order.status] || "in progress"}</p>
          {order.status === "awaiting_payment" ? <button className="ch-btn" onClick={() => onCancel(order.order_id)}>Cancel</button> : null}
        </article>
      ))}
      <h2>Refunds</h2>
      {refunds.length === 0 ? <p>No refunds yet.</p> : refunds.map((refund, index) => (
        <p key={refund.order_id ? `${refund.order_id}-${index}` : index}>{money(refund.amount)} back to {cardWords(refund.card_last4)}</p>
      ))}
    </section>
  );
}
