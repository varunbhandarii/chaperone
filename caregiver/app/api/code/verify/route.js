// Approval codes live in policy, bound to one approval; this route only forwards the caregiver's entry.
export async function POST(request) {
  const { approval_id: approvalId, code } = await request.json().catch(() => ({}));
  if (!approvalId || !code) {
    return Response.json({ verified: false, error: "approval_id and code are required" }, { status: 400 });
  }
  const policy = process.env.POLICY_URL || "http://127.0.0.1:8001";
  const response = await fetch(`${policy.replace(/\/$/, "")}/approvals/${encodeURIComponent(approvalId)}/code`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ code: String(code) }),
  }).catch(() => null);
  if (!response) return Response.json({ verified: false, error: "policy unavailable" }, { status: 502 });
  const body = await response.json().catch(() => ({}));
  return Response.json({ ...body, verified: response.ok }, { status: response.ok ? 200 : 400 });
}
