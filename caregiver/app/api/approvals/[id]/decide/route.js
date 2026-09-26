import { generateAuthenticationOptions, verifyAuthenticationResponse } from "@simplewebauthn/server";
import { isoBase64URL } from "@simplewebauthn/server/helpers";
import { cookies } from "next/headers";
import { saveChallenge } from "@/lib/challenges";
import { loadCredentials, origin, rpID, saveCredentials } from "@/lib/passkeys";
import { actionMarker } from "@/lib/marker";
import { requireSession } from "@/lib/session";
import { APPROVAL_ID } from "@/lib/ids";

const policy = () => (process.env.POLICY_URL || "http://127.0.0.1:8001").replace(/\/$/, "");

async function verifyPayment(response, approval) {
  const credentials = loadCredentials();
  const match = credentials.find((item) => item.id === response?.id);
  if (!match) return { error: "unknown credential" };
  const challenge = await fetch(`${policy()}/approvals/${approval.approval_id}/challenge`, { cache: "no-store" });
  if (!challenge.ok) return { error: "unknown approval" };
  const { challenge: expectedChallenge } = await challenge.json();
  const verified = await verifyAuthenticationResponse({
    response,
    expectedChallenge,
    expectedOrigin: origin(),
    expectedRPID: rpID(),
    expectedType: "payment.get",
    requireUserVerification: true,
    credential: {
      id: match.id,
      publicKey: isoBase64URL.toBuffer(match.publicKey),
      counter: match.counter,
      transports: match.transports,
    },
  });
  if (!verified.verified) return { error: "payment not verified" };
  const client = JSON.parse(Buffer.from(response.response.clientDataJSON, "base64url").toString("utf8"));
  const payment = client.payment || {};
  const amount = Number(approval.amount).toFixed(2);
  if (payment.payeeName !== "Corner Market" || payment.payeeOrigin !== origin()) {
    return { error: "payment details do not match" };
  }
  if (!payment.total || payment.total.value !== amount || payment.total.currency !== "USD") {
    return { error: "payment total does not match" };
  }
  match.counter = verified.authenticationInfo.newCounter;
  saveCredentials(credentials.map((item) => (item.id === match.id ? match : item)));
  return { signCount: match.counter };
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
  if (body.spc) {
    const approval = await fetch(`${policy()}/approvals/${id}`, { cache: "no-store" });
    if (!approval.ok) return Response.json({ error: "unknown approval" }, { status: 404 });
    const checked = await verifyPayment(body.response, await approval.json()).catch((error) => ({ error: String(error) }));
    if (checked.error) return Response.json({ error: checked.error }, { status: 400 });
    body.sign_count = checked.signCount;
    headers["X-Chaperone-Marker"] = actionMarker(rawId, "approve");
  } else if (body.approved === false) {
    headers["X-Chaperone-Marker"] = actionMarker(rawId, "reject");
  }
  const response = await fetch(`${policy()}/approvals/${id}/decide`, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });
  return new Response(await response.text(), { status: response.status, headers: { "Content-Type": "application/json" } });
}
