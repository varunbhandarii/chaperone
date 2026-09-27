import { origin, rpID } from "@/lib/passkeys";
import { requireSession } from "@/lib/session";

export async function GET() {
  // Ruth's number only for a signed-in Priyank: this page is reachable over the tunnel.
  const signedIn = !(await requireSession());
  return Response.json({ rpID: rpID(), origin: origin(), ruthPhone: signedIn ? process.env.RUTH_PHONE || "" : "" });
}
