import { requireSession } from "@/lib/session";

export async function GET() {
  const denied = await requireSession();
  if (denied) return denied;
  const policy = (process.env.POLICY_URL || "http://127.0.0.1:8001").replace(/\/$/, "");
  const response = await fetch(`${policy}/budget?mandate_id=m_ruth_2026_09`, { cache: "no-store" }).catch(() => null);
  if (!response) return Response.json({ error: "policy unavailable" }, { status: 502 });
  return new Response(await response.text(), { status: response.status, headers: { "Content-Type": "application/json" } });
}
