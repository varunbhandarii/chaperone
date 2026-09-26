import { escapeHtml, SESSION_ID } from "@/lib/ids";

export async function GET(_request, { params }) {
  const { id } = await params;
  if (!SESSION_ID.test(id)) {
    return new Response("Unknown session", { status: 404, headers: { "Content-Type": "text/plain; charset=utf-8" } });
  }
  const relay = (process.env.RELAY_URL || "http://127.0.0.1:8000").replace(/\/$/, "");
  const response = await fetch(`${relay}/sessions/${encodeURIComponent(id)}?format=html`, { cache: "no-store" }).catch(() => null);
  if (response && response.ok) {
    const type = response.headers.get("content-type") || "";
    if (type.includes("html")) return new Response(await response.text(), { headers: { "Content-Type": "text/html; charset=utf-8" } });
  }
  const json = await fetch(`${relay}/sessions/${encodeURIComponent(id)}`, { cache: "no-store" }).catch(() => null);
  const body = json && json.ok ? await json.text() : "[]";
  return new Response(
    `<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1"><title>Session</title></head><body style="font-family:Georgia,serif;background:#1c140c;color:#f6f1e7;padding:1.5rem"><h1>Session ${escapeHtml(id)}</h1><pre style="white-space:pre-wrap;font-size:1.1rem">${escapeHtml(body)}</pre></body></html>`,
    { headers: { "Content-Type": "text/html; charset=utf-8" } },
  );
}
