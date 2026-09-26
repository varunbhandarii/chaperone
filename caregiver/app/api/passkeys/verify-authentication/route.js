import { verifyAuthenticationResponse } from "@simplewebauthn/server";
import { isoBase64URL } from "@simplewebauthn/server/helpers";
import { loadCredentials, mandateHash, origin, requireUV, rpID, saveCredentials } from "@/lib/passkeys";

export async function POST(request) {
  const { mandate, response } = await request.json();
  const credentials = loadCredentials();
  const stored = credentials.find((item) => item.id === response?.id) || credentials[0];
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
    saveCredentials(credentials.map((item) => (item.id === stored.id ? stored : item)));
    return Response.json({ verified: true, counter: stored.counter, public_key: stored.publicKey, credential_id: stored.id });
  } catch (error) {
    return Response.json({ error: String(error) }, { status: 400 });
  }
}
