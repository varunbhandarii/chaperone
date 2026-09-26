export const dynamic = "force-dynamic";

export async function GET() {
  const relay = process.env.RELAY_URL || "http://127.0.0.1:8000";
  const upstream = await fetch(`${relay.replace(/\/$/, "")}/events/stream?types=refusal,approval_requested,caregiver_alerted`, {
    cache: "no-store",
  }).catch(() => null);
  const encoder = new TextEncoder();
  const stream = new ReadableStream({
    async start(controller) {
      controller.enqueue(encoder.encode(`:${" ".repeat(2048)}\n\n`));
      if (!upstream || !upstream.body) {
        controller.enqueue(encoder.encode("event: error\ndata: relay unavailable\n\n"));
        controller.close();
        return;
      }
      const reader = upstream.body.getReader();
      const timer = setInterval(() => controller.enqueue(encoder.encode(":\n\n")), 15000);
      try {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          controller.enqueue(value);
        }
      } finally {
        clearInterval(timer);
        controller.close();
      }
    },
  });
  return new Response(stream, {
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache, no-transform",
    },
  });
}
