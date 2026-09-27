import { money } from "@/lib/money";
import { storeName } from "@/lib/stores";

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
          <p>{storeName(order.merchant) || "Order"} · {money(order.total)}</p>
          <p>{order.sku && String(order.sku).startsWith("BILL-") ? "Bill" : STATUS[order.status] || order.status}</p>
          {order.status === "awaiting_payment" ? <button className="ch-btn" onClick={() => onCancel(order.order_id)}>Cancel</button> : null}
        </article>
      ))}
      <h2>Refunds</h2>
      {refunds.length === 0 ? <p>No refunds yet.</p> : refunds.map((refund, index) => (
        <p key={index}>{money(refund.amount)} back to the card ending {refund.card_last4 || "the card that paid"}</p>
      ))}
    </section>
  );
}
