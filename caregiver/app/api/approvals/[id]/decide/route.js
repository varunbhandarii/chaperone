import { createHmac } from "crypto";
import { generateAuthenticationOptions } from "@simplewebauthn/server";
import { isoBase64URL } from "@simplewebauthn/server/helpers";
import { cookies } from "next/headers";
import { saveChallenge } from "@/lib/challenges";
import { loadCredentials, origin, rpID } from "@/lib/passkeys";
import { requireSession } from "@/lib/session";
import { APPROVAL_ID } from "@/lib/ids";

const policy = () => (process.env.POLICY_URL || "http://127.0.0.1:8001").replace(/\/$/, "");

function rejectMarker(id) {
  const key = process.env.POLICY_CODE_KEY || "chaperone-dev-code-key";
  return createHmac("sha256", key).update(`reject:${id}`).digest("hex");
}

export async function POST(request, { params }) {
  const denied = await requireSession();
  if (denied) return denied;
  const { id: rawId } = await params;
  if (!APPROVAL_ID.test(rawId)) return Response.json({ error: "unknown approval" }, { status: 404 });
  const id = encodeURIComponent(rawId);
  const body = await request.json();
  if (body.prepare) {
    const challenge = await fetch(`${policy()}/approvals/${id}/challenge`, { cache: "no-store" });
    if (!challenge.ok) return Response.json({ error: "unknown approval" }, { status: 404 });
    const { challenge: challengeB64 } = await challenge.json();
    const approval = await fetch(`${policy()}/approvals/${id}`, { cache: "no-store" });
    const view = approval.ok ? await approval.json() : null;
    const options = await generateAuthenticationOptions({
      rpID: rpID(),
      challenge: isoBase64URL.toBuffer(challengeB64),
      userVerification: "preferred",
      allowCredentials: loadCredentials().map((credential) => ({
        id: credential.id,
        transports: credential.transports,
      })),
    });
    const jar = await cookies();
    jar.set("sid", saveChallenge("approval", options.challenge), {
      httpOnly: true,
      sameSite: "lax",
      secure: origin().startsWith("https"),
      path: "/",
    });
    return Response.json({ optionsJSON: options, approval: view });
  }
  const headers = { "Content-Type": "application/json" };
  if (body.approved === false) headers["X-Chaperone-Marker"] = rejectMarker(rawId);
  const response = await fetch(`${policy()}/approvals/${id}/decide`, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });
  return new Response(await response.text(), { status: response.status, headers: { "Content-Type": "application/json" } });
}
