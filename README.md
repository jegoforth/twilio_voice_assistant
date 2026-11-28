# Twilio Voice Assistant

Release versions are mapped to immutable tags and build revisions using the [release procedure](RELEASING.md).

Call a Twilio phone number and talk to your Home Assistant voice assistant.

Twilio Voice Assistant is a Home Assistant App that receives Twilio Voice calls, resolves the caller's identity, sends transcript text to your Elspeth/Home Assistant conversation agent, and returns response text through Twilio Conversation Relay using a configured voice provider such as ElevenLabs.

The App is a text bridge. It does not run local STT/TTS or generated audio-file playback.

**Requires [HA Extended User Management](https://github.com/jegoforth/ha-extended-user-management).** Since the current release, caller phone numbers and PINs are no longer stored by this App at all -- they live as `phone_number`/PIN profile data on the household's own `person` entities, managed through that integration. Install and configure it first (see its README for the admin card that sets these). A known caller's number is matched via its `find_person_by_phone` service; an unrecognized caller is identified and PIN-verified through your conversation agent's own voice ladder (e.g. Elspeth Core's self-declaration + PIN elevation), not through this App's admin UI.

## Current Beta Shape

- Twilio Conversation Relay only.
- Caller identity resolved via HA Extended User Management (`find_person_by_phone`), not a local caller list.
- Unrecognized callers are identified and PIN-verified through the conversation agent's own spoken ladder -- no DTMF PIN prompt.
- Every caller turn is handed to the conversation agent with a real per-caller identity (via `elspeth_local.twilio_conversation` when paired with Elspeth Core), not an anonymous request.
- ElevenLabs voice through Twilio Conversation Relay.
- Twilio webhook signature validation enabled by default.
- Protected `/start_session` with short-lived signed session tokens.
- Protected `/conversation_relay` websocket session setup.
- No local audio files, no local Whisper, no local generated TTS files.

The legacy Caller Access admin UI, DTMF `/check_pin` fallback, and `auth_mode`/`unknown_caller_policy` options are no longer used by `/incoming_call` and are kept only as unreached legacy code pending removal -- see `CHANGELOG.md`.

## Supported

| Supported | Notes |
| --- | --- |
| Home Assistant OS / Supervised | Requires Supervisor Apps. |
| Twilio Voice webhook | Incoming call webhook points to `/incoming_call`. |
| Twilio Conversation Relay | Used for STT event delivery and TTS playback. |
| HA Extended User Management | Required. Caller identity (phone number + PIN) is managed there, not in this App. |
| Voice-based PIN for unrecognized callers | Handled by the conversation agent's own spoken identity ladder, not DTMF. |
| Home Assistant Conversation agents | The selected HA agent handles the request. |
| ElevenLabs through Conversation Relay | Configure provider and voice in App options. |

## Not Supported

| Not supported | Notes |
| --- | --- |
| Home Assistant Core-only installs | Supervisor Apps are required. |
| Local Whisper | No local speech-to-text runtime is included. |
| DTMF PIN entry | Unrecognized-caller identity is spoken PIN through the conversation agent, not DTMF. |
| PIN/phone storage in this App | Moved to HA Extended User Management -- see Requirements. |
| Local generated TTS audio files | Speech playback is handled by Conversation Relay. |
| Public admin access | `/admin` and `/admin/api/*` must stay private. |
| Non-Twilio webhook callers | Public call routes expect Twilio request signatures. |

## Requirements

- Home Assistant OS or Home Assistant Supervised.
- [HA Extended User Management](https://github.com/jegoforth/ha-extended-user-management) installed and configured, with `phone_number` and a PIN set for each household member who should be reachable by phone. This App has no caller-identity storage of its own anymore.
- A configured Home Assistant Conversation agent (e.g. Elspeth Core) that supports the `elspeth_local.twilio_conversation`-style per-caller handoff, or one reachable through the generic `conversation/process` API for a caller identity managed some other way.
- A Twilio account with:
  - An active phone number.
  - Account SID.
  - Auth token.
  - Conversation Relay available on the account.
- A public HTTPS URL that can forward the required Twilio routes to the App.
- A Twilio Conversation Relay TTS provider/voice. ElevenLabs is the intended voice provider.

## Install

### Install From Repository

1. In Home Assistant, go to **Settings** > **Apps** > **App Store**.
2. Open the menu in the top right and choose **Repositories**.
3. Add this repository URL:

   ```text
   https://github.com/jegoforth/twilio_voice_assistant
   ```

4. Close the repositories dialog.
5. Find **Twilio Voice Assistant** in the App Store.
6. Install the App.

The installable App lives in:

```text
twilio_voice_assistant/
```

## First-Time Setup

### 1. Configure App Options

Open the App configuration page in Home Assistant and set the required values.

Example using fake values:

```yaml
twilio_account_sid: ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
twilio_auth_token: YOUR_TWILIO_AUTH_TOKEN
public_base_url: https://assistant.example.com
auth_mode: caller_whitelist_or_pin
unknown_caller_policy: pin_fallback
conversation_relay_tts_provider: ElevenLabs
conversation_relay_voice: YOUR_TWILIO_CONVERSATION_RELAY_VOICE_ID
conversation_relay_transcription_provider: Deepgram
conversation_relay_language: en-US
allow_unsigned_twilio_requests_for_dev: false
debug: false
log_call_transcripts: false
outbound_caller_id: "+15555550123"
```

Configuration notes:

- `public_base_url` must be the public HTTPS base URL Twilio uses, without a trailing slash.
- `auth_mode` and `unknown_caller_policy` are legacy options: `/incoming_call` no longer reads them. Caller identity is now always resolved via HA Extended User Management's `find_person_by_phone`, and an unrecognized number is always routed into the conversation agent's own spoken identity ladder rather than a DTMF prompt. These options are kept only so existing configs don't fail validation, and are candidates for removal in a future release.
- `conversation_relay_tts_provider` should be a Twilio-supported provider such as `ElevenLabs`.
- `conversation_relay_voice` is provider/account specific. Do not assume a voice ID from another installation will work.
- `allow_unsigned_twilio_requests_for_dev` must remain `false` for public or exposed endpoints.
- `log_call_transcripts` records each turn of a call (what the caller said and what Elspeth replied) so a test call can be reviewed afterward. Turns go to the App log prefixed `TRANSCRIPT` and to `/data/call-transcripts.jsonl`, which survives App rebuilds and rotates at 10MB. It's `false` by default because it records real spoken content, not just metadata: turn it on while you're testing and back off once you're done.

- `outbound_caller_id` is the assistant's own Twilio number in E.164 form. It is only used for calls a household speaker places through your SIP domain (see "Calls from a household speaker" below); leave it empty and those calls are refused.

Start the App after saving the configuration.

### 2. Expose Only Twilio Routes

Your public HTTPS proxy or tunnel should forward only these paths to the App on port `8000`:

```text
/incoming_call
/check_pin
/start_session
/conversation_relay
/conversation_relay/status
/errand/answer
/errand/status
/errand_relay
/sip/outbound
```

The last four are only needed for calls the assistant places (below).

Do not expose these paths publicly:

```text
/admin
/admin/api/*
```

The admin UI is intended to be opened through Home Assistant Ingress.

### 3. Configure Twilio Phone Number

In the Twilio Console:

1. Open **Phone Numbers** > **Manage** > **Active numbers**.
2. Select the phone number to use.
3. Under **Voice Configuration**, set **A call comes in** to **Webhook**.
4. Enter:

   ```text
   https://YOUR_PUBLIC_DOMAIN/incoming_call
   ```

5. Set the method to `HTTP POST`.
6. Save the phone number configuration.

### 4. Register Callers In HA Extended User Management

Caller identity is no longer configured in this App's admin UI. Instead, in the HA Extended User Management admin card (Settings -> Dashboards, or wherever you've placed it):

1. For each household member who should be reachable by phone, enter their `phone_number` in E.164 format (e.g. `+15551234567`) in that person's row.
2. Make sure that person also has a PIN set -- it's the same PIN used for voice PIN step-up elsewhere, and is what an unrecognized-number caller will be asked to confirm.

There is no per-caller configuration left in this App's own web UI for this.

### 5. Make A Test Call

1. Call the Twilio phone number from a registered number.
2. The call should be recognized immediately and enter Conversation Relay with no PIN prompt.
3. Ask your assistant a command or question.
4. It should process through your configured conversation agent.
5. Twilio should speak the response through Conversation Relay using the configured voice.
6. To end the call, say `goodbye`, `hang up`, `end call`, `that's all`, or `I'm done`.
7. Optionally, call again from an unregistered number: you should be asked "To whom am I speaking?", then asked to confirm your PIN by voice -- no keypad. Nothing else is answered until the PIN verifies.

## Reverse Proxy Guidance

### Cloudflare Tunnel

Use Cloudflare Tunnel to expose your public hostname over HTTPS and route only the Twilio paths to the App service. Keep Home Assistant Ingress/admin paths private.

Suggested route shape:

```text
https://assistant.example.com/incoming_call              -> App port 8000
https://assistant.example.com/check_pin                  -> App port 8000
https://assistant.example.com/start_session              -> App port 8000
https://assistant.example.com/conversation_relay         -> App port 8000
https://assistant.example.com/conversation_relay/status  -> App port 8000
```

Confirm websocket upgrade support for `/conversation_relay`.

### NGINX Proxy Manager

Create a proxy host for your public domain and forward to the Home Assistant App service on port `8000`. Enable websocket support. Use access controls or custom locations so only the Twilio public paths are exposed.

Do not publish `/admin` or `/admin/api/*` through NGINX Proxy Manager.

### Generic Reverse Proxy

The proxy must:

- Terminate HTTPS with a trusted certificate.
- Preserve request path and query string.
- Forward `POST` requests to Twilio webhook paths.
- Support websocket upgrades for `/conversation_relay`.
- Avoid exposing `/admin` and `/admin/api/*`.

## Security

Treat this App as an internet-facing webhook service.

- Do not expose `/admin` or `/admin/api/*` publicly.
- Twilio signature validation is enabled by default for `/incoming_call`, `/check_pin`, and `/start_session`.
- `allow_unsigned_twilio_requests_for_dev` is only for controlled local testing and must remain `false` for exposed endpoints.
- `/start_session` requires a short-lived signed session token created only after caller authentication.
- `/conversation_relay` validates the same signed session token during websocket setup.
- Public endpoint exposure should be minimal.
- Logs must not contain Twilio auth tokens, request signatures, session tokens, PINs, full phone numbers, full transcripts, or full Home Assistant responses.
- Caller ID matching is useful for convenience, but it is not strong authentication by itself.
- Rotate your Twilio auth token if it is ever pasted into logs, screenshots, chat, or documentation.

## Troubleshooting

### Twilio Signature Validation Failed

- Confirm `public_base_url` exactly matches the public URL Twilio calls, including scheme and host.
- Confirm Twilio uses `HTTP POST`.
- Confirm your proxy preserves the path and query string.
- Confirm `twilio_auth_token` is the active Twilio auth token.
- Do not enable `allow_unsigned_twilio_requests_for_dev` on public endpoints.

### Conversation Relay Websocket Does Not Connect

- Confirm your public URL is HTTPS and the websocket URL resolves as `wss://`.
- Confirm the reverse proxy supports websocket upgrades.
- Confirm `/conversation_relay` is publicly reachable.
- Check Twilio call logs for Conversation Relay errors.

### `/start_session` Invalid Or Expired

- `/start_session` is intentionally protected.
- Start sessions only through `/incoming_call` or `/check_pin`.
- Confirm Twilio is following the generated redirect quickly enough.
- Confirm system time is reasonable on the host.

### Caller Access User Not Matched

- Confirm the caller phone number is stored in E.164 format, such as `+1XXXXXXXXXX`.
- Confirm Twilio sends a `From` number.
- Confirm the Caller Access record is saved.
- Check logs for masked caller match status.

### PIN Fallback Not Working

- Confirm `auth_mode` is `pin` or `caller_whitelist_or_pin`.
- Confirm `unknown_caller_policy` is `pin_fallback` for unknown caller tests.
- Confirm the fallback PIN is set on a Caller Access record.
- Confirm Twilio posts DTMF `Digits` to `/check_pin`.

### Home Assistant Conversation Agent Not Selected

- Open the App web UI through Home Assistant.
- Select the conversation agent.
- Click **Save Settings**.
- Retry the call.

### Voice Is Wrong Or Default

- Confirm `conversation_relay_tts_provider` is valid for Twilio Conversation Relay.
- Confirm `conversation_relay_voice` is valid for the selected provider and Twilio account.
- Temporarily leave `conversation_relay_voice` blank to test provider defaults.

### Public Base URL Or Proxy Problem

- Confirm Twilio webhook URL is `https://YOUR_PUBLIC_DOMAIN/incoming_call`.
- Confirm the same host is configured in `public_base_url`.
- Confirm the proxy forwards all five public routes listed above.
- Confirm `/admin` is not exposed publicly.

## Development Docs

- [Twilio development guide](docs/TWILIO_DEVELOPMENT.md)
- [Local testing checklist](docs/LOCAL_TESTING.md)
- [Decision log](docs/DECISIONS.md)
- [Architecture notes](docs/ARCHITECTURE.md)

## Project History

This repository is being prepared for first public beta use from the current Conversation Relay and Caller Access design. See [CHANGELOG.md](CHANGELOG.md) and [docs/DECISIONS.md](docs/DECISIONS.md) for release notes and implementation decisions.


## Calls the assistant places

Elspeth Core can phone a household member on someone's behalf ("call Shelley and ask her what she'd like for dinner"). Core starts the call through Twilio with this App's `/errand/answer` and `/errand/status` as its webhooks. This App serves the call with Conversation Relay over `/errand_relay`, and sends each step to Core through `elspeth_local.twilio_errand` (elspeth_local 0.6.0 or later). Core answers with the next line of a short fixed script: ask, take the answer, offer to carry anything back, and say goodbye. Whoever answers is never treated as a household member and can't ask for anything else. A voicemail gets the message read once with `<Say>`. Nothing needs configuring here beyond exposing the routes; Core holds the setting for this App's public address.

## Calls from a household speaker

A speaker with its own SIP phone (an Echo Dot running TECHO5 firmware) can place calls through a Twilio SIP domain on this account:

1. In Twilio, create a SIP domain with SIP registration on, a credential list holding one username and a long random password per speaker, and secure media (SRTP) on.
2. Set the domain's voice URL to `<public_base_url>/sip/outbound` (HTTP POST).
3. Set `outbound_caller_id` here to the assistant's number.
4. Sign the speaker in from Home Assistant with its `phone_account` action: the server is the SIP domain (`<name>.sip.twilio.com`), with that username and password.

`/sip/outbound` only connects calls to numbers registered to someone in Home Assistant Extended User Management, the same records caller ID uses, and shows the assistant's number as caller ID. Anything else is told it can only call the household.


## The Claude line (optional)

A second Twilio number can connect you straight to Claude instead of Elspeth, for hands-free conversations while driving. Twilio and ElevenLabs handle the voice exactly as they do for Elspeth; the App streams Claude's reply into Conversation Relay as it is written, so the first words play almost at once, and talking over Claude cuts it off. Calls start with a quick spoken PIN, then Claude picks up. If the call drops without a goodbye, calling back within 20 minutes resumes the conversation without asking for the PIN again.

On the call, Claude can search the web, hang up when you're done, and, if a Claude project is wired up, send a request to it (after reading it back and hearing a yes) and read back the project's latest status.

### Setup

1. Buy a second number in Twilio. Under **Voice Configuration**, set **A call comes in** to Webhook, `https://YOUR_PUBLIC_DOMAIN/claude/incoming_call`, HTTP POST.
2. Expose `/claude/incoming_call`, `/claude/check_pin`, `/claude/relay` (websocket) and, if you use status readback, `/claude/project_status` through your proxy, the same way as the Elspeth routes.
3. Set these App options:

| Option | What it does |
| --- | --- |
| `anthropic_api_key` | Turns the line on. A Claude API key from platform.claude.com. |
| `claude_model` | Default `claude-opus-5-5`. `claude-haiku-5-5` answers faster and costs far less, if you'd rather trade depth for speed. |
| `claude_effort` | `low` (default), `medium` or `high`. Low keeps replies quick. |
| `claude_fast_mode` | Opus 5.5 fast mode: faster output at twice the price. Off by default. |
| `claude_web_search` | On by default. |
| `claude_voice` | A Conversation Relay ElevenLabs voice for Claude. Blank uses `conversation_relay_voice`. |
| `claude_require_pin` | On by default. After caller ID matches, the caller says or keys their HA Extended User Management PIN before Claude picks up, since caller ID alone can be spoofed. Checked in Home Assistant with `verify_pin` (and its lockout); the PIN never reaches Claude. Redialing after a dropped call within 20 minutes skips it. |
| `claude_allowed_user_ids` | Comma-separated HA user ids allowed on this line. Blank lets any recognized household member through. Unrecognized numbers are always refused. |
| `claude_project_fire_url`, `claude_project_fire_token` | The Fire URL and token of a Claude Code scheduled task with an API trigger. Set both to let Claude send requests to your project. |
| `claude_project_status_secret` | A long random string. When set, `POST /claude/project_status` with `Authorization: Bearer <secret>` and `{"summary": "..."}` stores the status Claude reads back. |

### Connecting a Claude project

Create a Claude Code scheduled task with no schedule whose prompt tells it to treat the text in its `routine-fire-payload` block as a request spoken on this line, and point it at your project. On the web at claude.ai/code/routines, open the task, add an **API** trigger, copy the Fire URL, click **Generate token**, and paste both into the options above. The token is shown once. Each request Claude sends arrives as one run of that task.
