import { actionMarker } from "@/lib/marker";
import { requireSession } from "@/lib/session";

export async function POST() {
  const denied = await requireSession();
  if (denied) return denied;
  const policy = (process.env.POLICY_URL || "http://127.0.0.1:8001").replace(/\/$/, "");
  const response = await fetch(`${policy}/mandate/pause`, {
    method: "POST",
    headers: { "X-Chaperone-Marker": actionMarker("mandate", "pause") },
  }).catch(() => null);
  if (!response) return Response.json({ error: "policy unavailable" }, { status: 502 });
  return new Response(await response.text(), { status: response.status, headers: { "Content-Type": "application/json" } });
}
