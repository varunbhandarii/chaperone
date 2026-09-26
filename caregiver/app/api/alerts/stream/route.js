export const dynamic = "force-dynamic";

export async function GET(request) {
  const relay = (process.env.RELAY_URL || "http://127.0.0.1:8000").replace(/\/$/, "");
  const upstream = await fetch(`${relay}/events/stream?types=refusal,approval_requested,caregiver_alerted`, {
    cache: "no-store",
    signal: request.signal,
  }).catch(() => null);
  const encoder = new TextEncoder();
  let reader = null;
  let timer = null;

  const stream = new ReadableStream({
    async start(controller) {
      const finish = () => {
        if (timer) clearInterval(timer);
        timer = null;
        try {
          controller.close();
        } catch {
          /* already closed */
        }
      };
      controller.enqueue(encoder.encode(`:${" ".repeat(2048)}\n\n`));
      if (!upstream || !upstream.body) {
        controller.enqueue(encoder.encode("event: error\ndata: relay unavailable\n\n"));
        finish();
        return;
      }
      reader = upstream.body.getReader();
      timer = setInterval(() => {
        try {
          controller.enqueue(encoder.encode(":\n\n"));
        } catch {
          if (timer) clearInterval(timer);
        }
      }, 15000);
      try {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          controller.enqueue(value);
        }
      } catch {
        /* the phone disconnected */
      } finally {
        if (timer) clearInterval(timer);
        timer = null;
        try {
          reader.releaseLock();
        } catch {
          /* already released */
        }
        finish();
      }
    },
    cancel() {
      if (timer) clearInterval(timer);
      timer = null;
      if (reader) reader.cancel().catch(() => {});
    },
  });
  return new Response(stream, {
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
    },
  });
}
