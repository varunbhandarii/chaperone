import { actionMarker } from "@/lib/marker";
import { requireSession } from "@/lib/session";

const policy = () => (process.env.POLICY_URL || "http://127.0.0.1:8001").replace(/\/$/, "");

export async function GET() {
  const denied = await requireSession();
  if (denied) return denied;
  const response = await fetch(`${policy()}/mandate/resume/challenge`, {
    method: "POST",
    headers: { "X-Chaperone-Marker": actionMarker("mandate", "pause") },
  }).catch(() => null);
  if (!response) return Response.json({ error: "policy unavailable" }, { status: 502 });
  return new Response(await response.text(), { status: response.status, headers: { "Content-Type": "application/json" } });
}

export async function POST(request) {
  const denied = await requireSession();
  if (denied) return denied;
  const body = await request.json().catch(() => ({}));
  const response = await fetch(`${policy()}/mandate/resume`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }).catch(() => null);
  if (!response) return Response.json({ error: "policy unavailable" }, { status: 502 });
  return new Response(await response.text(), { status: response.status, headers: { "Content-Type": "application/json" } });
}
