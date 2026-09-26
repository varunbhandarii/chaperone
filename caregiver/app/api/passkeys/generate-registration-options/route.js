import { generateRegistrationOptions, verifyAuthenticationResponse } from "@simplewebauthn/server";
import { isoBase64URL, isoUint8Array } from "@simplewebauthn/server/helpers";
import { cookies } from "next/headers";
import { saveChallenge, takeChallenge } from "@/lib/challenges";
import { consumeSetupCode, ensureSetupCode, loadCredentials, origin, requireUV, rpID } from "@/lib/passkeys";

export async function POST(request) {
  ensureSetupCode();
  const body = await request.json().catch(() => ({}));
  const existing = loadCredentials();
  if (existing.length === 0 && !consumeSetupCode(body.setup_code)) {
    return Response.json({ error: "setup code required" }, { status: 401 });
  }
  if (existing.length > 0) {
    const expectedChallenge = takeChallenge((await cookies()).get("sid")?.value, "register");
    const match = existing.find((item) => item.id === body.assertion?.id);
    if (!expectedChallenge || !match) {
      return Response.json({ error: "registration is closed" }, { status: 403 });
    }
    try {
      const verified = await verifyAuthenticationResponse({
        response: body.assertion,
        expectedChallenge,
        expectedOrigin: origin(),
        expectedRPID: rpID(),
        requireUserVerification: requireUV(),
        credential: {
          id: match.id,
          publicKey: isoBase64URL.toBuffer(match.publicKey),
          counter: match.counter,
          transports: match.transports,
        },
      });
      if (!verified.verified) return Response.json({ error: "registration is closed" }, { status: 403 });
    } catch {
      return Response.json({ error: "registration is closed" }, { status: 403 });
    }
  }
  const options = await generateRegistrationOptions({
    rpName: "Chaperone",
    rpID: rpID(),
    userName: "priyank",
    userID: isoUint8Array.fromUTF8String("priya"),
    attestationType: "none",
    excludeCredentials: existing.map((credential) => ({ id: credential.id, transports: credential.transports })),
    authenticatorSelection: { residentKey: "preferred", userVerification: "preferred" },
  });
  const jar = await cookies();
  jar.set("sid", saveChallenge("register", options.challenge), {
    httpOnly: true,
    sameSite: "lax",
    secure: origin().startsWith("https"),
    path: "/",
  });
  return Response.json(options);
}
