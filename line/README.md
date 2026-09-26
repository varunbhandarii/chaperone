# Chaperone Line

A phone number Ruth can call from any phone, answered by Grok in the Chaperone persona, using the station's tools.
The number and the voice agent live in xAI's **Voice Agent Builder**; this service gives that agent our tools as a
remote **MCP server** (Streamable HTTP).

```
caller -> xAI number -> Builder agent (Grok voice, persona) --MCP--> https://<TUNNEL_HOST>/line/mcp
       -> caregiver app rewrite /line/* -> line/server.py (127.0.0.1:8005) -> policy, catalog, merchant, relay
```

**Number:** the xAI-provisioned number on the Voice Agent Builder agent "Chaperone" (shown in the xAI console). xAI releases it after 30 days without calls.

## Tools

`scam_check`, `budget_left`, `search_catalog`, `add_to_cart`, `remove_from_cart`, `read_cart`, `checkout`, `bill_status`,
`order_status`, `cancel_order`, `request_refund` and `purchase_history`. They take the same arguments as the station's tools, plus an optional `ruth_said`
(Ruth's latest words).

A phone call has no station screen, so safety lives in the tools:
- `scam_check` sends her story to policy's `/scam-check` with `channel: "line"`. If that route isn't there yet, the
  line says a safe fallback: "don't pay anyone or share any codes until you talk to Priyank".
- `checkout` works only right after `read_cart`, for exactly that cart. Policy then runs the mandate, the rule screen
  and the judge on Ruth's words from the call.
- `request_refund` with `confirmed: true` works only right after its own `confirmed: false` preview.
- One cart per phone call. The Builder opens a new MCP session for every tool call, so every result carries a
  `call_id` that the agent passes back (the prompt tells it to). A new session within 5 minutes of the last tool
  call, with no `call_id`, continues that call. Idle calls are dropped after 2 hours.
- Every route except `GET /health` needs the token, either as `Authorization: Bearer $LINE_MCP_TOKEN` or inside the
  path (`/k/<token>/mcp`). Without the token set, the line serves nothing.

`POST /api/<tool>` answers the same tools as plain JSON, for the Builder's `api_request` tool if its MCP field doesn't
work. The call is keyed by the `X-Call-Id` header.

## Run

```bash
bash .claude/run.sh line
```

Or run it directly:

```bash
.venv/Scripts/python -m uvicorn line.server:app --host 127.0.0.1 --port 8005
```

Service URLs come from `POLICY_URL`, `CATALOG_URL`, `MERCHANT_URL` and `RELAY_URL` (default `127.0.0.1:8001–8003/8000`).
The token, `LINE_MCP_TOKEN`, is in the root `.env`.

Tests use a real MCP client against the station's mock services:

```bash
.venv/Scripts/python -m pytest line -q
```

## Connect the Builder (console.x.ai → Voice → Agents)

1. Create a **Custom** agent (**Skip** the setup assistant):
   - **Instructions:** the station persona from `station/config/voice.json` (`session.instructions`), with two changes.
     Replace the "station plays a soft sound" sentence with: say one short "Let me check that for you" before
     `scam_check` only. Then add an `## On the phone` section: pass Ruth's latest words as `ruth_said` on every tool
     call, and there is no screen.
   - **Welcome message:** "Hi, this is Chaperone. How can I help you today? Hola, soy Chaperone, ¿en qué le puedo
     ayudar?"
   - **Speech:** the voice Ara.
   - Leave the built-in web search and X search **off**. The scam check does its own search, with the rules and
     Ruth's real facts behind it.
   - **Set up** takes the free phone number.
2. **Connectors → Custom MCP server → Add.** The Builder's form takes only a URL. It treats a server that answers 401
   as OAuth-only and asks for an OAuth client, so the token goes in the path. Copy the URL to the clipboard without
   showing it (PowerShell, repo root):

   ```powershell
   "https://<TUNNEL_HOST>/line/k/" + (Select-String -Path .env -Pattern '^LINE_MCP_TOKEN=(.*)').Matches[0].Groups[1].Value.Trim() + "/mcp" | Set-Clipboard
   ```

   Paste it as the URL, leave the authorization fields empty, and use the name `chaperone-tools`. Add the header
   `ngrok-skip-browser-warning: 1` if the form has a field for it. It should list 12 tools. Treat this URL like a
   password.
3. Open the connector and make sure all 12 tools are enabled. The Builder turns off the ones that change things
   (`add_to_cart`, `remove_from_cart`, `request_refund`, `cancel_order`) by default, and the agent then can't add to
   the cart. The server's own gates (read-back, refund preview, policy) keep them safe.
4. Test in the browser preview first ("I just got a call from my grandson…", "how much can I still spend?"), then dial
   the number.

**Go** means `budget_left` or `scam_check` runs and its answer is spoken. The line's console and the relay's ledger
(source `line`) show each call.

If the MCP field is missing or rejected, add `api_request` tools that `POST https://<TUNNEL_HOST>/line/api/<tool>` with
the tool's arguments as JSON and the same Authorization header.
