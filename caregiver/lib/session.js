import { cookies } from "next/headers";
import { sessionOpen } from "./challenges";
import { origin } from "./passkeys";

export async function requireSession() {
  const id = (await cookies()).get("cg_session")?.value;
  if (!sessionOpen(id)) return Response.json({ error: "sign in required" }, { status: 401 });
  return null;
}

export function sessionCookie() {
  return {
    httpOnly: true,
    sameSite: "strict",
    secure: origin().startsWith("https"),
    path: "/",
    maxAge: 30 * 60,
  };
}
