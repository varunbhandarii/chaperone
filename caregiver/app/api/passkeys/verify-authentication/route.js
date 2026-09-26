import { verifyAuthenticationResponse } from "@simplewebauthn/server";
import { isoBase64URL } from "@simplewebauthn/server/helpers";
import { cookies } from "next/headers";
import { markMandateReady, openSession, takeChallenge } from "@/lib/challenges";
import { loadCredentials, origin, requireUV, rpID, saveCredentials } from "@/lib/passkeys";
import { sessionCookie } from "@/lib/session";

export async function POST(request) {
  const body = await request.json();
  const response = body.response;
  const purpose = body.purpose === "session" ? "session" : "mandate";
  const credentials = loadCredentials();
  const stored = credentials.find((item) => item.id === response?.id) || credentials[0];
  if (!stored) return Response.json({ error: "register a passkey first" }, { status: 400 });
  const jar = await cookies();
  const sid = jar.get("sid")?.value;
  const expectedChallenge = takeChallenge(sid, purpose);
  if (!expectedChallenge) return Response.json({ error: "missing challenge" }, { status: 400 });
  try {
    const verified = await verifyAuthenticationResponse({
      response,
      expectedChallenge,
      expectedOrigin: origin(),
      expectedRPID: rpID(),
      requireUserVerification: requireUV(),
      credential: {
        id: stored.id,
        publicKey: isoBase64URL.toBuffer(stored.publicKey),
        counter: stored.counter,
        transports: stored.transports,
      },
    });
    if (!verified.verified) return Response.json({ verified: false }, { status: 400 });
    stored.counter = verified.authenticationInfo.newCounter;
    saveCredentials(credentials.map((item) => (item.id === stored.id ? stored : item)));
    if (purpose === "session") {
      jar.set("cg_session", openSession(), sessionCookie());
      return Response.json({ verified: true, counter: stored.counter, session: true });
    }
    markMandateReady(sid);
    return Response.json({ verified: true, counter: stored.counter, public_key: stored.publicKey, credential_id: stored.id });
  } catch (error) {
    return Response.json({ error: String(error) }, { status: 400 });
  }
}
