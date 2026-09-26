import { cookies } from "next/headers";
import { consumeMandateReady } from "@/lib/challenges";
import { requireSession } from "@/lib/session";

export async function GET() {
  const denied = await requireSession();
  if (denied) return denied;
  const policy = (process.env.POLICY_URL || "http://127.0.0.1:8001").replace(/\/$/, "");
  const response = await fetch(`${policy}/mandate`, { cache: "no-store" }).catch(() => null);
  if (!response) return Response.json({ signed: false }, { status: 502 });
  return new Response(await response.text(), { status: response.status, headers: { "Content-Type": "application/json" } });
}

export async function POST(request) {
  const sid = (await cookies()).get("sid")?.value;
  if (!consumeMandateReady(sid)) {
    return Response.json({ error: "sign the mandate in this session first" }, { status: 401 });
  }
  const policy = process.env.POLICY_URL || "http://127.0.0.1:8001";
  const body = await request.text();
  const response = await fetch(`${policy.replace(/\/$/, "")}/mandate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body,
  });
  return new Response(await response.text(), { status: response.status, headers: { "Content-Type": "application/json" } });
}
