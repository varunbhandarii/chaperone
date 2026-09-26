import { actionMarker } from "@/lib/marker";
import { requireSession } from "@/lib/session";

const DECISION_ID = /^d_[0-9a-f]{12}$/;

export async function GET(_request, { params }) {
  const denied = await requireSession();
  if (denied) return denied;
  const { id } = await params;
  if (!DECISION_ID.test(id)) return Response.json({ error: "unknown decision" }, { status: 404 });
  const policy = (process.env.POLICY_URL || "http://127.0.0.1:8001").replace(/\/$/, "");
  const response = await fetch(`${policy}/decisions/${id}/explain`, {
    cache: "no-store",
    headers: { "X-Chaperone-Marker": actionMarker(id, "explain") },
  }).catch(() => null);
  if (!response) return Response.json({ error: "policy unavailable" }, { status: 502 });
  return new Response(await response.text(), { status: response.status, headers: { "Content-Type": "application/json" } });
}
