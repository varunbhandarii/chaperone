import { requireSession } from "@/lib/session";

export async function GET(request) {
  const denied = await requireSession();
  if (denied) return denied;
  const days = new URL(request.url).searchParams.get("days") || "30";
  const policy = (process.env.POLICY_URL || "http://127.0.0.1:8001").replace(/\/$/, "");
  const response = await fetch(`${policy}/history?mandate_id=m_ruth_2026_09&days=${encodeURIComponent(days)}`, { cache: "no-store" }).catch(() => null);
  if (!response) return Response.json({ orders: [], refunds: [], refusals: [] }, { status: 502 });
  return new Response(await response.text(), { status: response.status, headers: { "Content-Type": "application/json" } });
}
