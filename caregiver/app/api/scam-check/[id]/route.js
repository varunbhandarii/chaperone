import { actionMarker } from "@/lib/marker";
import { requireSession } from "@/lib/session";

const policy = () => (process.env.POLICY_URL || "http://127.0.0.1:8001").replace(/\/$/, "");

export async function GET(_request, { params }) {
  const denied = await requireSession();
  if (denied) return denied;
  const { id } = await params;
  const response = await fetch(`${policy()}/scam-check/${encodeURIComponent(id)}`, {
    cache: "no-store",
    headers: { "X-Chaperone-Marker": actionMarker(id, "view") },
  }).catch(() => null);
  if (!response) return Response.json({ error: "policy unavailable" }, { status: 502 });
  if (!response.ok) return Response.json({ error: "unknown check" }, { status: response.status });
  return new Response(await response.text(), { status: 200, headers: { "Content-Type": "application/json" } });
}
