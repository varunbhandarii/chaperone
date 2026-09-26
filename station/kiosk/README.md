# Station voice client

A one-page push-to-talk client for the shopper station. It opens a Grok Voice realtime session with an
ephemeral token from the relay, streams the microphone only while the button is held, plays the reply,
and runs two tools for the model: `search_catalog` (catalog service) and `checkout` (policy service).
Every tool call waits for the policy service's rule screen first, and a refused request is answered with
a fixed refusal instead of the model.

Session settings (model, voice, instructions, tools, audio format) live in
`station/config/voice.json`; the page and the CLI probe both read that file.

| File | What it does |
|---|---|
| `src/agent.ts` | realtime protocol, push-to-talk, barge-in, per-turn rule screen, tools, refusals, ledger events, latency |
| `src/audio.ts` | AudioWorklet capture (PCM16, 20 ms blocks) and gapless playback |
| `src/pcm.ts`, `src/cart.ts`, `src/lang.ts`, `src/screen.ts` | pure helpers (PCM/base64, pre-roll, cart and checkout body, language guess, screen parsing) |
| `src/services.ts` | HTTP calls to relay, catalog, policy; each degrades with a warning |
| `src/ui.ts`, `src/main.ts`, `index.html` | large-type page, key mapping, device pickers |
| `ws_probe.mjs` | CLI check of the realtime protocol and the tool round trip |
| `tests/` | relay tests, a local mock of the realtime API and services, probe integration test |
| `test/` | Node unit tests for the pure helpers |
| `../../relay/` | `POST /session/token`, `GET /health` (mints ephemeral secrets; the API key never reaches a browser) |

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

Service addresses: `SERVICES_HOST` in the root `.env` (or `VITE_SERVICES_HOST`), or `?host=192.168.8.10`
in the URL; relay :8000, policy :8001, catalog :8003. Individual overrides: `?relay=`, `?policy=`,
`?catalog=` (full URLs). `?voice=naksh` picks the Indian-accented voice for Hindi sessions.

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
- **Tools.** `search_catalog` calls `GET {catalog}/search?q=&limit=3`, falling back to three bread items
  (marked `source: "fallback"`). `checkout` prices the cart from cached catalog items (integer cents) and
  posts `{session_id, mandate_id, cart{merchant, items[{sku,name,category,qty,price}], total}, transcript,
  lang, read_back}` to `{policy}/checkout`; the JSON answer goes back to the model. Tool outputs are sent
  at once; the next `response.create` waits until the current response is done and playback has drained.
- **Ledger.** `session_started`, `heard`, `items_found`, `checkout_requested`, `policy_decision` and
  `refusal` are posted fire-and-forget to `{relay}/events` with `session_id`, `mandate_id`, `t` and
  `source: "station"`. If the relay has no ledger yet the page warns once and carries on.

Details checked against the xAI docs (Sept 2026): the `.updated` live user transcript is only sent when
`audio.input.transcription.model` is `grok-transcribe` (the page retries without it if the server rejects
the field); the `.completed` event schema has no language field, so the page logs one if present and
otherwise guesses es/hi/en from the text; user text items use `input_text`; the client-secret request
takes only `expires_after`.

## Settings in `station/config/voice.json`

`capture.preferred_sample_rate` (null = device rate), `capture.preroll_ms`, `capture.chunk_ms`,
`capture.min_press_ms`, `screen.timeout_ms`, `screen.screen_partials`, `refusal.fallback`
(`force_message` or `instruct`), `refusal.clip_timeout_ms`, `barge_in.truncate`, the `session` object,
and `measured.release_to_first_audio_ms`.

## Tests (no credentials needed)

```bash
cd station/kiosk && npm run build && npm test          # typecheck + bundle; unit tests
.venv/Scripts/python -m pytest station/kiosk/tests -q  # relay + probe against the local mock (repo root)
```

The mock in `tests/mock_realtime.py` follows the documented event flow (validated `session.update`,
commit, transcription events, a preamble plus `search_catalog` call, `checkout` on "sí", cancel,
truncate, `force_message`) and stands in for the relay token, catalog, policy and ledger endpoints. To
drive the page against it:

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
