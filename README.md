# Twilio Voice Assistant

Call a Twilio phone number and talk to your Home Assistant voice assistant.

Twilio Voice Assistant is a Home Assistant App that receives Twilio Voice calls, authenticates callers through the Caller Access UI or optional DTMF PIN fallback, sends transcript text to Home Assistant Conversation, and returns response text through Twilio Conversation Relay using a configured voice provider such as ElevenLabs.

The App is a text bridge. It does not run local STT/TTS or generated audio-file playback.

## Current Beta Shape

- Twilio Conversation Relay only.
- Caller Access UI only for caller identity.
- Optional DTMF PIN fallback.
- Home Assistant Conversation as the assistant brain.
- ElevenLabs voice through Twilio Conversation Relay.
- Twilio webhook signature validation enabled by default.
- Protected `/start_session` with short-lived signed session tokens.
- Protected `/conversation_relay` websocket session setup.
- No local audio files, no local Whisper, no local generated TTS files.

## Supported

| Supported | Notes |
| --- | --- |
| Home Assistant OS / Supervised | Requires Supervisor Apps. |
| Twilio Voice webhook | Incoming call webhook points to `/incoming_call`. |
| Twilio Conversation Relay | Used for STT event delivery and TTS playback. |
| Caller Access UI | The only caller identity management surface. |
| DTMF PIN fallback | Optional fallback for unknown callers. |
| Home Assistant Conversation agents | The selected HA agent handles the request. |
| ElevenLabs through Conversation Relay | Configure provider and voice in App options. |

## Not Supported

| Not supported | Notes |
| --- | --- |
| Home Assistant Core-only installs | Supervisor Apps are required. |
| Local Whisper | No local speech-to-text runtime is included. |
| Speech PIN | PIN fallback is DTMF only. |
| Local generated TTS audio files | Speech playback is handled by Conversation Relay. |
| Public admin access | `/admin` and `/admin/api/*` must stay private. |
| Non-Twilio webhook callers | Public call routes expect Twilio request signatures. |

## Requirements

- Home Assistant OS or Home Assistant Supervised.
- A configured Home Assistant Conversation agent.
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
```

Configuration notes:

- `public_base_url` must be the public HTTPS base URL Twilio uses, without a trailing slash.
- `auth_mode` can be:
  - `caller_whitelist`: known Caller Access numbers only.
  - `caller_whitelist_or_pin`: known callers skip PIN; unknown callers can use PIN fallback.
  - `pin`: every caller must use DTMF PIN fallback.
- `unknown_caller_policy` can be:
  - `reject`: unknown callers are rejected.
  - `pin_fallback`: unknown callers can enter a Caller Access PIN.
- `conversation_relay_tts_provider` should be a Twilio-supported provider such as `ElevenLabs`.
- `conversation_relay_voice` is provider/account specific. Do not assume a voice ID from another installation will work.
- `allow_unsigned_twilio_requests_for_dev` must remain `false` for public or exposed endpoints.

Start the App after saving the configuration.

### 2. Expose Only Twilio Routes

Your public HTTPS proxy or tunnel should forward only these paths to the App on port `8000`:

```text
/incoming_call
/check_pin
/start_session
/conversation_relay
/conversation_relay/status
```

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

### 4. Configure Admin UI

Open the App web UI from Home Assistant.

1. Select the Home Assistant conversation agent.
2. Click **Save Settings**.
3. In **Caller Access**, select a Home Assistant user.
4. Enter one or more caller phone numbers in E.164 format, one per line:

   ```text
   +1XXXXXXXXXX
   +1YYYYYYYYYY
   ```

5. Optionally enter a 4-digit fallback PIN.
6. Click **Add Caller Access**.

Caller Access stores `ha_user_id` as the stable key and resolves the display name from Home Assistant. Existing records show masked phone numbers and only `PIN set` or `No PIN`; saved PIN values are not displayed.

### 5. Make A Test Call

1. Call the Twilio phone number from an allowed Caller Access number.
2. The call should skip PIN and enter Conversation Relay.
3. Ask Home Assistant a command or question.
4. Home Assistant should process it through the selected conversation agent.
5. Twilio should speak the response through Conversation Relay using the configured voice.
6. To end the call, say `goodbye`, `hang up`, `end call`, `that's all`, or `I'm done`.

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
