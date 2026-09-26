import { issueCode } from "@/lib/passkeys";

export async function POST() {
  return Response.json({ code: issueCode() });
}
