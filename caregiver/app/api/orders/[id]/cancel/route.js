import { requireSession } from "@/lib/session";

const ORDER_ID = /^ord_[A-Za-z0-9_-]{1,64}$/;

export async function POST(_request, { params }) {
  const denied = await requireSession();
  if (denied) return denied;
  const { id } = await params;
  if (!ORDER_ID.test(id)) return Response.json({ error: "unknown order" }, { status: 404 });
  const policy = (process.env.POLICY_URL || "http://127.0.0.1:8001").replace(/\/$/, "");
  const response = await fetch(`${policy}/orders/${id}/cancel`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mandate_id: "m_ruth_2026_09" }),
  }).catch(() => null);
  if (!response) return Response.json({ error: "policy unavailable" }, { status: 502 });
  return new Response(await response.text(), { status: response.status, headers: { "Content-Type": "application/json" } });
}
