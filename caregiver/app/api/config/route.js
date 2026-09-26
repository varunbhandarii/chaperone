import { origin, rpID } from "@/lib/passkeys";

export async function GET() {
  return Response.json({ rpID: rpID(), origin: origin() });
}
