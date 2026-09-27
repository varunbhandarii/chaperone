import { actionMarker } from "@/lib/marker";
import { requireSession } from "@/lib/session";

const policy = () => (process.env.POLICY_URL || "http://127.0.0.1:8001").replace(/\/$/, "");

export async function GET() {
  const denied = await requireSession();
  if (denied) return denied;
  const response = await fetch(`${policy()}/scam-checks?mandate_id=m_ruth_2026_09&limit=20`, {
    cache: "no-store",
    headers: { "X-Chaperone-Marker": actionMarker("scam_checks", "list") },
  }).catch(() => null);
  if (!response || !response.ok) return Response.json([]);
  return new Response(await response.text(), { status: 200, headers: { "Content-Type": "application/json" } });
}
