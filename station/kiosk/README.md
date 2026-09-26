# Station voice client

A one-page push-to-talk client for the shopper station. It opens a Grok Voice realtime session with an
ephemeral token from the relay, streams the microphone only while the button is held, plays the reply,
and runs the shopping tools for the model: `search_catalog`, `add_to_cart`, `remove_from_cart`,
`read_cart`, `budget_left` and `checkout`. The cart lives in the page, and `checkout` takes no arguments:
it buys the cart that was read back, and only after the shopper spoke again (the "yes"). Every tool call
waits for the policy service's rule screen first, and a refused request is answered with a fixed refusal
instead of the model. The page doubles as the large-type companion screen (**Shopper view**).

Session settings (model, voice, instructions, tools, audio format) live in
`station/config/voice.json`; the page and the CLI probe both read that file.

| File | What it does |
|---|---|
| `src/agent.ts` | realtime protocol, push-to-talk, barge-in, per-turn rule screen, tools, refusals, ledger events, latency |
| `src/audio.ts` | AudioWorklet capture (PCM16, 20 ms blocks) and gapless playback |
| `src/cart.ts` | the cart (integer cents, versioned), the read-back gate, read-back and outcome sentences in es/hi/en, checkout body |
| `src/pcm.ts`, `src/lang.ts`, `src/screen.ts` | pure helpers (PCM/base64, pre-roll, language guess, screen parsing) |
| `src/services.ts` | HTTP calls to relay, catalog (`/resolve`, `/search`), policy (`/screen`, `/checkout`, `/budget`); reachability probe; each degrades with a warning |
| `src/ui.ts`, `src/main.ts`, `index.html` | large-type page and companion screen (state strip, cart, outcome, refusal banner), key mapping, device pickers |
| `ws_probe.mjs` | CLI check of the realtime protocol and the tool round trip |
| `voice_samples.mjs` | renders one read-back line in several voices (xAI TTS) to choose the station voice by ear |
| `tests/` | relay tests, a local mock of the realtime API and services, probe integration test |
| `test/` | Node unit tests for the pure helpers |
| `../../relay/` | `POST /session/token`, `GET /health` (mints ephemeral secrets; the API key never reaches a browser); mounts `relay/ledger.py` when present. CORS allows only `STATION_ORIGINS` |

## Run

Needs `XAI_API_KEY` in the repo-root `.env`.

```bash
# 1. relay (repo root)
.venv/Scripts/python -m uvicorn relay.main:app --host 0.0.0.0 --port 8000
curl -X POST http://localhost:8000/session/token      # 200 {"value","expires_at"}; 503 means no key

# 2. page
cd station/kiosk
npm install
npm run dev                                           # http://localhost:5173
```

Open **http://localhost:5173** on the station laptop. Browsers only allow the microphone on `localhost`
or HTTPS, so do not open the page by LAN IP.

Service addresses: the page calls `/svc/relay`, `/svc/policy` and `/svc/catalog` on its own origin, and the
dev server proxies them to `SERVICES_HOST` from the root `.env` (relay :8000, policy :8001, catalog :8003;
restart `npm run dev` after changing it). The services therefore need no CORS headers, and a service that is
down answers the proxy's marked 502, which the page treats as "down" at once. `?host=192.168.8.10` or
`?relay=`, `?policy=`, `?catalog=` (full URLs) call services directly instead, which needs CORS on them. The voice follows the shopper's language (`voice_by_lang` in `voice.json`: Spanish
`carina`, English and Hindi `ara`); `?voice=<name>` forces one voice for the whole session. `?view=shopper` opens the
companion screen without the operator panels (also the **Shopper view** button).

For direct calls, the relay answers browsers only from `STATION_ORIGINS` (default
`http://localhost:5173,http://127.0.0.1:5173`), so another page on the LAN cannot mint voice tokens.

Press **Start** (token, microphone and socket start in parallel), then hold **Space** or the big button
while speaking. Pressing while the agent talks stops it immediately. A tap shorter than 250 ms sends nothing.
A typed box is there as a fallback and for debugging.

## USB push-to-talk button

Open **Hardware**, click **Map a new key**, press the USB button. The page stores the key code
(`localStorage`, per browser) and shows every key code it sees under **Last key pressed**, so you can
tell what the button sends. Programmable buttons are best set to `F13`-`F24`; media keys (play/pause)
are often taken by the operating system. The page window must have focus. Releasing the key, the pointer
leaving the window, or the tab losing focus all end the turn, so the microphone never stays open.

Microphone and speaker pickers are in the same panel (the speaker picker needs a browser with
`AudioContext.setSinkId`, e.g. Chrome 110+). The level meter shows the live input.

## The shopping flow

1. The shopper names a product. The model calls `search_catalog`; the page asks `GET {catalog}/resolve`
   first (profile phrases such as "mi medicina de la presión" or "bp ki dawai" -> the saved pickup, shown as
   option one with `usual: true`), then `GET {catalog}/search?q=&limit=3`.
2. The model calls `add_to_cart {sku, qty}`. Only skus from this session's search results are accepted.
   Every change bumps the cart version, redraws the cart and posts `cart_updated`.
3. The model calls `read_cart`, which returns `{lines, total, say}`: `say` is the exact read-back sentence
   in the shopper's language. That arms the gate for this cart version.
4. The shopper says yes (a new voice or typed turn). `checkout {}` sends `read_back: true` to
   `POST {policy}/checkout` only if the cart has not changed since `read_cart` and a turn came after it;
   otherwise the model gets `{"error": "read_back_required", "say": ...}` and nothing leaves the page.
5. The policy reply becomes `{status, say_key, say}`: `ordered` ("ordering_now"), `waiting_for_caregiver`
   ("asking_priya"; the strip shows *Waiting for Priyank*) or `declined`. The model speaks `say`; only
   refusals are spoken verbatim without the model.
6. `budget_left` calls `GET {policy}/budget?mandate_id=` and returns `{monthly_cap, spent, left, say}`.

## After checkout: caregiver, payment, receipt

- **Waiting for the caregiver.** On `approve` the page plays the `line.asking_priya.<lang>.mp3` clip (no
  model turn follows; if the clip cannot load in 800 ms the model speaks the line instead) and polls
  `GET {policy}/approvals/{id}` every second for up to 95 s; the state strip counts down (*Waiting for Priyank
  · 85 s*). `approved` with an order: the page says `caregiver_approved` and the order goes on to payment.
  `rejected`: the caregiver's message, or `caregiver_declined`. `expired`: `caregiver_timeout`, and the cart
  stays; Priyank's own message is spoken when he typed one. These fixed lines are spoken verbatim with
  `force_message`, only once the shopper and the model are quiet. The button stays live: a new request (a turn
  with words, not a noise press) or a cart change ends the wait and asks policy to close the approval
  (`POST {policy}/approvals/{id}/cancel`), so a late tap cannot order. A second checkout of an unchanged cart
  that gets the same pending approval back keeps the existing wait. (`line.receipt_done` is not used: it says
  the receipt was printed, and without a printer the station says `receipt_on_screen`.)
- **Paid.** The page follows the relay's event stream (`/events/stream?types=paid,reset`, same-origin through
  the proxy; the backlog replayed on connect is skipped). On `paid` for its session it fetches
  `GET {merchant}/orders/{id}/receipt` (or builds the receipt from its own order record), shows it full-screen,
  posts it to the receipt helper (`POST /svc/printer/print`, `station/printer.py` on 127.0.0.1:8004), posts
  `receipt_printed {order_id, via: "printer" | "screen"}` and says `receipt_done` or `receipt_on_screen`.
  Without a printer the helper saves the receipt as a PDF: the page shows the rendered 58 mm slip beside the
  large-type receipt with an **Open PDF** button, and posts `via: "screen", pdf: true`. If the helper is not
  running, the large-type receipt alone is the copy. **Reprint** renders or prints it again.
- **Receipt.** Store, items and prices, the total, pickup after 3 pm, order and decision ids, the paid time,
  "Paid in the Visa sandbox. No real money.", and a QR code to `https://<TUNNEL_HOST>/s/<session_id>`
  (`TUNNEL_HOST` from the root `.env`, or `?tunnel=`), in the session's language.

## Host shortcuts

| Keys | What |
|---|---|
| Ctrl+Shift+R | Reset: new session id, empty cart, gate, transcript and screen, a new voice conversation; the relay resets policy, merchant and the live ledger. A `reset` event from the relay (the Host page) does the same on the station |
| Ctrl+Shift+S | Save this session (agent audio, transcripts, tool calls, refusal clip) as the cached session for its language: `sessions/cached/<lang>.json` through the receipt helper, and a copy in this browser's IndexedDB (used when the helper is not running) |
| Ctrl+Shift+P | Replay a cached session (the Host page's **Arm replay** makes the next button press do the same): pick the language; the recorded voice plays and **REPLAY** shows in large type, while the rule screen, the cart tools and checkout run live against the services (events carry `replay: true`) |
| Escape | Close the receipt or the chooser, or stop a replay (pressing the talk button also stops it) |

Spoken lines: the built-in texts in `src/cart.ts` are replaced at build time by `ai/prompts/refusal.<key>.<lang>.txt`
and, when present, `ai/prompts/lines.<lang>.json` (`{"key": "text"}`; `{total}` or `$X` stands for the total).

## Companion screen

**Shopper view** hides the latency meter, hardware panel and tool notes, leaving the state strip
(*Listening*, *Thinking*, *Speaking*, *Waiting for Priyank*), the red refusal banner with the rule id, the
order outcome, the cart with its total and both sides of the transcript. All shopper-facing text is at
least 24 px on a dark background with at least 4.5:1 contrast; buttons are at least 48 px tall.

## Session resumption

`voice.json` enables `resumption`. The page keeps the conversation id from `conversation.created`; if the
socket closes while the station is running, it mints a new token and reopens the socket with
`&conversation_id=<id>` (up to 3 tries, 0.5/1/2 s apart), then adds a system message with the cart and
read-back state. The cart and the gate live in the page, so nothing is lost. The server keeps the history
for 30 minutes of inactivity.

## Choosing the voice

```bash
node station/kiosk/voice_samples.mjs            # es-MX and en in ara, luna, carina; hi in naksh and ara; speed 0.9
node station/kiosk/voice_samples.mjs --list     # voices this account can use
```

Clips go to `station/kiosk/voice-samples/` (gitignored). Put the choice in `session.voice` and
`voice_by_lang` in `voice.json`; the refusal clips should be rendered in the same voice.

## The pass test

1. Relay running with a key; page open on localhost; click Start; status says *Session ready*.
2. Hold the button, say **"necesito pan"**, release.
3. Pass when: the reply is spoken in Spanish, the big number (release to first audio) is at most
   1500 ms, the log shows `Tool call: search_catalog(...)`, and both transcripts appear on the page and in
   the browser console (with timestamps).

Write down:
- release-to-first-audio for 5 to 10 voice turns: the page shows min and median over voice turns only
  (typed turns and refusal clips are shown but not counted); put the median in
  `station/config/voice.json` at `measured.release_to_first_audio_ms`;
- the audio context rate from the console (`audio context 24000 Hz`);
- whether the model spoke a short preamble before calling `search_catalog`, and whether it answered in
  the language spoken;
- any `Server error` lines.

## CLI probe

Proves the protocol and the tool round trip without a browser or microphone:

```bash
node station/kiosk/ws_probe.mjs                  # token via relay, else XAI_API_KEY from .env
node station/kiosk/ws_probe.mjs --text "मुझे दूध चाहिए" --voice naksh --wav reply.wav
```

It sends `session.update` from `voice.json` at 24000 Hz, a text turn ("Necesito pan" by default),
answers `search_catalog` with three fallback bread items (`--catalog` asks the catalog service first),
prints every event type, both transcripts, the tool call and the time from `response.create` to the first
audio delta, and exits after the second `response.done` or 20 s. Exit code 0 means session configured,
tool called and audio received. `--wav` saves the spoken reply. The token and key are never printed.

## How a turn works

- **Session.** `session.update` from `voice.json` with both PCM rates set to the AudioContext's real
  rate. The page asks for a 24 kHz context (the API default; half the bandwidth of 48 kHz) and falls
  back to the device rate if the browser refuses. Turn detection is manual (`{"type": null}`), so nothing
  happens until the button is released. Reasoning is off for latency.
- **Capture.** An AudioWorklet converts to PCM16 in 20 ms blocks; while the button is up the last 300 ms
  are kept, and on press that pre-roll is sent first so the first syllable is not clipped. While held,
  audio goes out as `input_audio_buffer.append` every 100 ms. On release the worklet's partial block is
  flushed, then `input_audio_buffer.commit` and `response.create`.
- **Playback.** Each `response.output_audio.delta` becomes an AudioBuffer scheduled right after the
  previous one. Barge-in stops every scheduled buffer, sends `response.cancel` if a response is in
  flight and `conversation.item.truncate` with the milliseconds actually heard. A response that starts
  while the button is held is cancelled.
- **Rule screen.** Transcripts are sent to `POST {policy}/screen`: partial ones while talking (only a
  refusal counts early) and the final one. Tool calls wait up to 1.5 s for the turn's screen result.
  If the result is `refuse`, the tool is not run and the model receives
  `{"refused": true, "rule_id", "say"}`. If the model is talking without a tool call, it is cancelled and
  the page plays `refusal.audio_url` (resolved against the relay); if the clip cannot load in 800 ms it
  sends a `force_message` item so the text is spoken verbatim without the model (falling back to a
  one-response instruction if the server rejects that). Rule ids are printed large on the page and in
  red in the console. If `/screen` is unreachable the page proceeds and warns once.
- **Tools.** See *The shopping flow*. Search falls back to three real catalog breads (`BAK-001` usual,
  `BAK-003`, `BAK-002`; marked `source: "fallback"`) when the catalog is down. The priced cart's
  `category` carries the catalog's `mandate_category` (`grocery`, `pharmacy`, ...). Tool outputs are sent
  at once; the next `response.create` waits until the current response is done and playback has drained.
- **Language.** After the first detected turn (and on a change) the page re-sends the full session with
  `audio.input.transcription.language_hint` (`es-MX`, `hi`, `en`) and the voice for that language. It
  always sends the full session because the echo of a partial update did not show the tools.
- **Reachability.** On Start the page probes the relay, policy and catalog once; a service that is down is
  skipped instantly (fallback items, no rule screen) and re-probed every 30 s, so a stopped service does
  not cost a timeout on every turn. Checkout re-probes a down policy service before giving up.
- **Ledger.** `session_started`, `heard {role, text, lang, item_id}` (one line per turn on the wall),
  `items_found {query, items}`, `cart_updated {lines, total}`, `checkout_requested {total}`,
  `refusal {rule_id, rule_ids, spoken_key, lang}` and `receipt_printed {order_id, via}` are posted
  fire-and-forget to `{relay}/events` with `type`, `session_id`, `mandate_id`, `t` (integer ms) and
  `source: "station"`. `policy_decision` comes from the policy service. If the relay has no ledger the page
  warns once and carries on.

Details checked against the xAI docs (Sept 2026): the `.updated` live user transcript is only sent when
`audio.input.transcription.model` is `grok-transcribe` (the page retries without it if the server rejects
the field); the `.completed` event schema has no language field, so the page logs one if present and
otherwise guesses es/hi/en from the text; user text items use `input_text`; the client-secret request
takes only `expires_after`. Checked against the live API: assistant history items accept both `text` and
`output_text`; `resumption.enabled` and reconnecting with `conversation_id` resume the same conversation;
a system message with a `text` part is accepted; the server does not echo the voice in `session.updated`.

## Settings in `station/config/voice.json`

`capture.preferred_sample_rate` (null = device rate), `capture.preroll_ms`, `capture.chunk_ms`,
`capture.min_press_ms`, `screen.timeout_ms`, `screen.screen_partials`, `refusal.fallback`
(`force_message` or `instruct`), `refusal.clip_timeout_ms`, `barge_in.truncate`, `voice_by_lang`, the
`session` object (instructions, voice, speed 0.9, keyterms, the six tools, resumption), and
`measured.release_to_first_audio_ms`.

## Tests (no credentials needed)

```bash
cd station/kiosk && npm run build && npm test          # typecheck + bundle; unit tests
.venv/Scripts/python -m pytest station/kiosk/tests -q  # relay + probe against the local mock (repo root)
```

The mock in `tests/mock_realtime.py` follows the documented event flow (validated `session.update`,
commit, transcription events, then a scripted model: search -> `add_to_cart` -> `read_cart` -> read-back;
"sí" -> `checkout {}` -> the outcome; cancel, truncate, `force_message`). It stands in for the relay token,
catalog (`/search` `{q, items}`, `/resolve` `{q, matches}`), policy (`/screen`, `/checkout` in the agreed
reply shape: allow under $40, approve above, deny for gift cards; `/budget`) and ledger endpoints.
`POST /mock/reset {"eager_checkout": true}` makes the scripted model call checkout before the read-back
(the gate must hold it); `POST /mock/drop` closes the socket to exercise resumption. It also stands in for the
caregiver and payment side: `GET /approvals/{id}` (expiry on read; `{"approval_ttl": 4}` in `/mock/reset`
shortens it), `POST /mock/approvals/{id}/approve|reject`, `GET /orders/{id}/receipt`, `POST /mock/pay/{order_id}`
(emits `paid`), the relay's `/events/stream` and `POST /reset`, and the print helper's `POST /print`
(`{"print_ok": true}` makes it succeed) and `/cached/{lang}`. Point every service at it with
`?relay=...&policy=...&catalog=...&merchant=...&printer=...` (all `http://127.0.0.1:8010`). To drive the page:

```bash
.venv/Scripts/python -m uvicorn mock_realtime:app --app-dir station/kiosk/tests --port 8010
# http://localhost:5173/?relay=http://127.0.0.1:8010&catalog=http://127.0.0.1:8010&policy=http://127.0.0.1:8010&ws=ws://127.0.0.1:8010/v1/realtime
```

`?ws=` only accepts loopback addresses.

## If Grok Voice does not work on the day

Fallback pipeline, same page and tools, three pieces:
1. **Speech in:** Chrome's `webkitSpeechRecognition` started on press and stopped on release
   (`lang` es-MX, hi-IN or en-US, `interimResults` on for the live line). It needs internet (Chrome sends
   audio to Google) and cannot auto-detect language, so pick the language with a button or `?lang=`.
2. **Thinking:** a relay endpoint that forwards the transcript to a Grok text model with the same
   `instructions` and the same two tool schemas (the key stays on the relay); the page runs the tools
   exactly as now and returns results until the model answers in text.
3. **Speech out:** `speechSynthesis` with a voice matching the language (list them with
   `speechSynthesis.getVoices()` once on the station laptop; install Spanish and Hindi voices in
   Windows settings beforehand). Rate 0.9 for older listeners.

Expect roughly 1.5 to 3 s from release to first audio. The rule screen, refusal clips and ledger work
unchanged.

## Troubleshooting

- *Not a secure context*: open `http://localhost:5173`, not the LAN IP.
- *token request failed (503)*: `XAI_API_KEY` missing in `.env`; the relay reads it on the next request.
- *Voice session closed* right after Start: expired or rejected token, or the network blocks
  `wss://api.x.ai`; run `node station/kiosk/ws_probe.mjs` to see the raw events.
- Reply sounds too fast, slow or high-pitched: rate mismatch; the console prints the rate sent.
- The agent answers without the button: check `session.updated` in the console shows
  `turn_detection: {type: null}`.
