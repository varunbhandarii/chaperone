import { actionMarker } from "@/lib/marker";
import { requireSession } from "@/lib/session";

const policy = () => (process.env.POLICY_URL || "http://127.0.0.1:8001").replace(/\/$/, "");

export async function POST(_request, { params }) {
  const denied = await requireSession();
  if (denied) return denied;
  const { id } = await params;
  const response = await fetch(`${policy()}/card/holds/${encodeURIComponent(id)}/allow`, {
    method: "POST",
    headers: { "X-Chaperone-Marker": actionMarker(id, "card") },
  }).catch(() => null);
  if (!response) return Response.json({ error: "policy unavailable" }, { status: 502 });
  return new Response(await response.text(), { status: response.status, headers: { "Content-Type": "application/json" } });
}
