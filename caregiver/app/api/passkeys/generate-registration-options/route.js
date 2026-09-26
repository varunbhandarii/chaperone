import { generateRegistrationOptions } from "@simplewebauthn/server";
import { cookies } from "next/headers";
import { origin, rpID } from "@/lib/passkeys";

export async function POST() {
  const options = await generateRegistrationOptions({
    rpName: "Chaperone",
    rpID: rpID(),
    userName: "priyank",
    attestationType: "none",
    authenticatorSelection: { residentKey: "preferred", userVerification: "preferred" },
  });
  const jar = await cookies();
  jar.set("wa_challenge", options.challenge, {
    httpOnly: true,
    sameSite: "lax",
    secure: origin().startsWith("https"),
    path: "/",
  });
  return Response.json(options);
}
