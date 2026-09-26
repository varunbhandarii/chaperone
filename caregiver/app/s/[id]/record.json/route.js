import { SESSION_ID } from "@/lib/ids";

// The session page's "Download the record" link: the relay's dispute record, passed through as a download.
export async function GET(_request, { params }) {
  const { id } = await params;
  if (!SESSION_ID.test(id)) return Response.json({ error: "unknown session" }, { status: 404 });
  const relay = (process.env.RELAY_URL || "http://127.0.0.1:8000").replace(/\/$/, "");
  const response = await fetch(`${relay}/sessions/${encodeURIComponent(id)}/record.json`, { cache: "no-store" }).catch(() => null);
  if (!response) return Response.json({ error: "relay unavailable" }, { status: 502 });
  if (!response.ok) return Response.json({ error: "unknown session" }, { status: response.status === 404 ? 404 : 502 });
  return new Response(await response.text(), {
    headers: {
      "Content-Type": "application/json",
      "Cache-Control": "no-store",
      "X-Robots-Tag": "noindex",
      "Content-Disposition": `attachment; filename="chaperone-${id}.json"`,
    },
  });
}
