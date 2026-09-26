import { generateAuthenticationOptions } from "@simplewebauthn/server";
import { isoBase64URL } from "@simplewebauthn/server/helpers";
import { cookies } from "next/headers";
import { saveChallenge } from "@/lib/challenges";
import { loadCredentials, origin, rpID } from "@/lib/passkeys";

const policy = () => (process.env.POLICY_URL || "http://127.0.0.1:8001").replace(/\/$/, "");

export async function POST(request, { params }) {
  const { id } = await params;
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
      allowCredentials: loadCredentials().map((credential) => ({ id: credential.id, transports: credential.transports })),
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
  const response = await fetch(`${policy()}/approvals/${id}/decide`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return new Response(await response.text(), { status: response.status, headers: { "Content-Type": "application/json" } });
}
