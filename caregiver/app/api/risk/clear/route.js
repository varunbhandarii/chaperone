import { actionMarker } from "@/lib/marker";
import { requireSession } from "@/lib/session";

const policy = () => (process.env.POLICY_URL || "http://127.0.0.1:8001").replace(/\/$/, "");

export async function POST(request) {
  const denied = await requireSession();
  if (denied) return denied;
  const body = await request.json().catch(() => ({}));
  const response = await fetch(`${policy()}/risk/clear`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Chaperone-Marker": actionMarker("mandate", "risk"),
    },
    body: JSON.stringify({ mandate_id: body.mandate_id || "m_ruth_2026_09" }),
  }).catch(() => null);
  if (!response) return Response.json({ error: "policy unavailable" }, { status: 502 });
  return new Response(await response.text(), { status: response.status, headers: { "Content-Type": "application/json" } });
}
