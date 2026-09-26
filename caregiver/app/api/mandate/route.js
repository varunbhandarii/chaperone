export async function POST(request) {
  const policy = process.env.POLICY_URL || "http://127.0.0.1:8001";
  const body = await request.text();
  const response = await fetch(`${policy.replace(/\/$/, "")}/mandate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body,
  });
  return new Response(await response.text(), { status: response.status, headers: { "Content-Type": "application/json" } });
}
