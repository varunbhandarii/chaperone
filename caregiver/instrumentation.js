export async function register() {
  if (process.env.NEXT_RUNTIME !== "nodejs") return;
  const { ensureSetupCode } = await import("./lib/passkeys.js");
  ensureSetupCode();
}
