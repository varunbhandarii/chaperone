import { requireSession } from "@/lib/session";

const policy = () => (process.env.POLICY_URL || "http://127.0.0.1:8001").replace(/\/$/, "");

export async function GET() {
  const denied = await requireSession();
  if (denied) return denied;
  const response = await fetch(`${policy()}/card/state`, { cache: "no-store" }).catch(() => null);
  if (!response || !response.ok) return Response.json({ decisions: [], holds: [] });
  return new Response(await response.text(), { status: 200, headers: { "Content-Type": "application/json" } });
}
