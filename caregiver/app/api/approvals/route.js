export async function GET() {
  const policy = process.env.POLICY_URL || "http://127.0.0.1:8001";
  const response = await fetch(`${policy.replace(/\/$/, "")}/approvals`, { cache: "no-store" }).catch(() => null);
  if (!response) return Response.json([]);
  return new Response(await response.text(), { status: response.status, headers: { "Content-Type": "application/json" } });
}
