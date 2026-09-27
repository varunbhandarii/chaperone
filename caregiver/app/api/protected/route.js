import { requireSession } from "@/lib/session";

const relay = () => (process.env.RELAY_URL || "http://127.0.0.1:8000").replace(/\/$/, "");

export async function GET() {
  const denied = await requireSession();
  if (denied) return denied;
  const response = await fetch(`${relay()}/wall/data/protected`, { cache: "no-store" }).catch(() => null);
  if (!response || !response.ok) return Response.json({ dollars: 0, scams_stopped: 0, card_declines: 0 });
  return new Response(await response.text(), { status: 200, headers: { "Content-Type": "application/json" } });
}