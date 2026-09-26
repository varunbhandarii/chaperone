import { btn, card } from "./styles";

export default function HistoryView({ history, onHome, onCancel }) {
  const orders = (history && history.orders) || [];
  const refunds = (history && history.refunds) || [];
  return (
    <section>
      <h1>Last 30 days</h1>
      <h2>Orders</h2>
      {orders.length === 0 ? <p>No orders yet.</p> : orders.map((order) => (
        <article key={order.order_id} style={card}>
          <p style={{ fontSize: "1.15rem" }}>{order.order_id} · ${order.total} · {order.status}</p>
          {order.status === "awaiting_payment" ? <button style={btn} onClick={() => onCancel(order.order_id)}>Cancel</button> : null}
        </article>
      ))}
      <h2>Refunds</h2>
      {refunds.length === 0 ? <p>No refunds yet.</p> : refunds.map((refund, index) => (
        <p key={index} style={{ fontSize: "1.15rem" }}>{refund.order_id} · ${refund.amount} back to the card</p>
      ))}
      <button style={btn} onClick={onHome}>Home</button>
    </section>
  );
}
