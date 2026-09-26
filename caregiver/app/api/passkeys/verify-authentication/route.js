import { verifyAuthenticationResponse } from "@simplewebauthn/server";
import { isoBase64URL } from "@simplewebauthn/server/helpers";
import { loadCredential, mandateHash, origin, requireUV, rpID, saveCredential } from "@/lib/passkeys";

export async function POST(request) {
  const { mandate, response } = await request.json();
  const stored = loadCredential();
  if (!stored) return Response.json({ error: "register a passkey first" }, { status: 400 });
  const hash = mandateHash(mandate);
  try {
    const verified = await verifyAuthenticationResponse({
      response,
      expectedChallenge: isoBase64URL.fromBuffer(hash),
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
    saveCredential(stored);
    return Response.json({ verified: true, counter: stored.counter });
  } catch (error) {
    return Response.json({ error: String(error) }, { status: 400 });
  }
}
