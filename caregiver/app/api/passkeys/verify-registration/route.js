import { verifyRegistrationResponse } from "@simplewebauthn/server";
import { isoBase64URL } from "@simplewebauthn/server/helpers";
import { cookies } from "next/headers";
import { takeChallenge } from "@/lib/challenges";
import { loadCredentials, origin, requireUV, rpID, saveCredentials } from "@/lib/passkeys";

export async function POST(request) {
  const response = await request.json();
  const expectedChallenge = takeChallenge((await cookies()).get("sid")?.value, "register");
  if (!expectedChallenge) return Response.json({ error: "missing challenge cookie" }, { status: 400 });
  try {
    const verified = await verifyRegistrationResponse({
      response,
      expectedChallenge,
      expectedOrigin: origin(),
      expectedRPID: rpID(),
      requireUserVerification: requireUV(),
    });
    if (!verified.verified) return Response.json({ verified: false }, { status: 400 });
    const credential = verified.registrationInfo.credential;
    const stored = {
      id: credential.id,
      publicKey: isoBase64URL.fromBuffer(credential.publicKey),
      counter: credential.counter,
      transports: credential.transports || [],
    };
    saveCredentials([...loadCredentials().filter((item) => item.id !== stored.id), stored]);
    return Response.json({ verified: true, id: credential.id });
  } catch (error) {
    return Response.json({ error: String(error) }, { status: 400 });
  }
}
