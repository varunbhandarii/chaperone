import { generateAuthenticationOptions } from "@simplewebauthn/server";
import { cookies } from "next/headers";
import { saveChallenge } from "@/lib/challenges";
import { loadCredentials, mandateHash, origin, rpID } from "@/lib/passkeys";

export async function POST(request) {
  const { mandate } = await request.json();
  const credentials = loadCredentials();
  if (!credentials.length) return Response.json({ error: "register a passkey first" }, { status: 400 });
  const hash = mandateHash(mandate);
  const options = await generateAuthenticationOptions({
    rpID: rpID(),
    challenge: hash,
    allowCredentials: credentials.map((credential) => ({ id: credential.id, transports: credential.transports })),
  });
  const jar = await cookies();
  jar.set("sid", saveChallenge("mandate", options.challenge), {
    httpOnly: true,
    sameSite: "lax",
    secure: origin().startsWith("https"),
    path: "/",
  });
  return Response.json(options);
}
