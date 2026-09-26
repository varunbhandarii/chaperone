import { createHmac } from "crypto";

export function actionMarker(id, action) {
  const key = process.env.POLICY_CODE_KEY || "chaperone-dev-code-key";
  return createHmac("sha256", key).update(`${action}:${id}`).digest("hex");
}
