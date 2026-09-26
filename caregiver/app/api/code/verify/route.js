import { verifyCode } from "@/lib/passkeys";

export async function POST(request) {
  const { code } = await request.json();
  const verified = verifyCode(code);
  return Response.json({ verified }, { status: verified ? 200 : 400 });
}
