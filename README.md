# Twilio Voice Assistant

Call a Twilio phone number and talk to your Home Assistant voice assistant from any allowed phone.

This Home Assistant add-on receives incoming Twilio calls, authenticates known callers through Caller Access, sends Conversation Relay transcript text to Home Assistant Assist / Conversation, and returns response text to Twilio for ElevenLabs speech playback.

The current product path is Conversation Relay only. The v2.0.0 target architecture is documented in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): external voice services handle STT/TTS and Home Assistant Conversation remains the assistant brain. Version `1.4.0` removes the deprecated Gather/audio-file rollback path, speech PIN mode, local Whisper, and local generated TTS audio files from the call path.

Version `1.4.2` makes secure Conversation Relay the only normal product path: Twilio HTTP webhook signature validation is always on unless the explicit development-only unsigned request bypass is enabled, `/start_session` requires a short-lived signed session token, and the Conversation Relay websocket setup validates the same session token before trusting user metadata.

Version `1.4.3` makes Caller Access the single source of truth for caller identity and fallback PINs. Legacy `allowed_callers`, separate PIN-map storage, the old PIN admin UI, and `/admin/api/pins` have been removed.

## Requirements

Before installing, make sure you have:

- Home Assistant OS or Home Assistant Supervised with add-ons available.
- A working Home Assistant voice assistant setup, including:
  - A configured conversation agent.
- A Twilio account with:
  - An active phone number.
  - Account SID.
  - Auth token.
- A secure public HTTPS URL that can reach the Twilio webhook and Conversation Relay websocket routes on this add-on.

Twilio must be able to reach the webhook and Conversation Relay websocket routes from the public internet. Common options include:

- Cloudflare Tunnel.
- NGINX Proxy Manager.
- A reverse proxy with a valid SSL certificate.
- Another HTTPS tunnel/proxy that forwards selected paths to Home Assistant add-on port `8000`.

Do not expose the add-on over plain HTTP. Twilio webhooks should use HTTPS.

Do not expose the admin page publicly. The admin page is intended to be opened through Home Assistant Ingress, where Home Assistant handles authentication.

## Install

### Add To My Home Assistant

[![Add repository to Home Assistant](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fjegoforth%2Ftwilio_voice_assistant)

If this repository is private, your Home Assistant Supervisor must be able to access it. Public repositories are the simplest option for normal add-on store installation.

This repository is structured as a Home Assistant add-on repository. The installable add-on lives in:

```text
twilio_voice_assistant/
```

For local add-on development, copy or mount that folder under Home Assistant's `/addons` directory.

### Manual Install

1. In Home Assistant, go to **Settings** > **Add-ons** > **Add-on Store**.
2. Open the menu in the top right and choose **Repositories**.
3. Add this repository URL:

   ```text
   https://github.com/jegoforth/twilio_voice_assistant
   ```

4. Close the repositories dialog.
5. Find **Twilio Voice Assistant** in the add-on store.
6. Install the add-on.

## Twilio Setup

In Twilio, configure your phone number so incoming voice calls are sent to this add-on.

1. Open the Twilio Console.
2. Go to **Phone Numbers** > **Manage** > **Active numbers**.
3. Select the phone number you want to use.
4. Under **Voice Configuration**, set **A call comes in** to **Webhook**.
5. Enter your public HTTPS webhook URL:

   ```text
   https://YOUR_PUBLIC_DOMAIN/incoming_call
   ```

6. Set the method to `HTTP POST`.
7. Save the phone number configuration.

For the normal Conversation Relay path, your public domain should forward only these paths to the add-on on port `8000`:

```text
/incoming_call
/check_pin
/start_session
/conversation_relay
/conversation_relay/status
```

Do not forward `/admin` or `/admin/api/*` to the public internet.

## Add-on Configuration

Open the add-on configuration page in Home Assistant and set:

- `twilio_account_sid`: Your Twilio Account SID.
- `twilio_auth_token`: Your Twilio Auth Token.
- `public_base_url`: The public HTTPS base URL Twilio can reach, without a trailing slash.
  - Example: `https://assistant.example.com`
  - `assistant.example.com` is also accepted and will be treated as HTTPS.
- `auth_mode`: Optional authentication mode. Defaults to `pin` for compatibility.
  - `caller_whitelist`: Match Twilio `From` against configured callers.
  - `caller_whitelist_or_pin`: Known callers skip PIN; unknown callers can use PIN fallback.
- `unknown_caller_policy`: Defaults to `reject`. Use `pin_fallback` to allow unknown callers to try PIN auth.
- `callers`: Advanced/import-only YAML form of the Caller Access identity model. Normal setup should leave this empty and use Caller Access in the admin page, which stores records in `/share/twilio_voice_assistant/callers.json`.
- `conversation_relay_tts_provider`: Defaults to `ElevenLabs`.
- `conversation_relay_voice`: Optional Conversation Relay voice identifier.
- `conversation_relay_transcription_provider`: Defaults to `Deepgram`.
- `conversation_relay_language`: Defaults to `en-US`.
- `allow_unsigned_twilio_requests_for_dev`: Defaults to `false`. Enable only for controlled local testing where requests are not signed by Twilio. Never enable this on exposed/public endpoints.
- `debug`: Optional extra logging.

Start the add-on after saving the configuration.

## Admin Page Setup

Open the add-on web UI from Home Assistant. The admin page uses Home Assistant Ingress, so it should be accessed from inside Home Assistant instead of through the public Twilio URL.

From the admin page:

1. Select the Home Assistant conversation agent to use.
2. Click **Save Settings**.
3. Use **Caller Access** for normal access management:
   - Select the Home Assistant user.
   - Enter one or more caller phone numbers.
   - Optionally enter a 4 digit fallback PIN.
   - Click **Add Caller Access**.

Caller Access stores `ha_user_id` as the stable key and resolves the display name from Home Assistant. Existing records show masked phone numbers and only `PIN set` or `No PIN`; saved PIN values are not displayed. Add-on config `callers` records remain supported as advanced/import-only records and appear as read-only config records in the UI. Caller Access is the normal editable source. Assistant settings and admin-managed caller access are stored in `/share/twilio_voice_assistant` so they survive add-on rebuilds.

Validated path: leave add-on config `callers` empty for normal use, add users and optional fallback PINs through Caller Access, and use Conversation Relay as the only voice bridge. Add-on YAML `callers` remains supported for advanced/import-only configuration.

## Removed Legacy Paths

Version `1.4.0` removes the old local audio pipeline:

- Gather TwiML command loop.
- Speech PIN mode.
- Local Whisper transcription.
- Home Assistant TTS-to-file response generation.
- Public `/process_command` and `/audio/*` routes.

Version `1.4.3` also removes the legacy caller identity and PIN management paths:

- `allowed_callers` add-on options and runtime parsing.
- Separate legacy PIN-map storage.
- Legacy PIN Management UI.
- `/admin/api/pins` routes.

## Test

1. Call your Twilio phone number from a Caller Access allowed number.
2. The call should skip PIN and enter Conversation Relay.
3. Ask Home Assistant a command.
4. Home Assistant should process the command through the selected conversation agent and Twilio should speak the response with ElevenLabs.
5. To end the call, say a phrase such as `goodbye`, `hang up`, `end call`, `that's all`, or `I'm done`.
6. The add-on should say goodbye and disconnect the call.

## Notes

- Rotate your Twilio auth token if it is ever pasted into logs, screenshots, chat, or documentation.
- Twilio signature validation is enabled by default for `/incoming_call`, `/check_pin`, and `/start_session`.
- Unsigned Twilio request bypass is development-only and disabled by default.
- `/start_session` is protected by a short-lived signed session token generated only after caller whitelist or PIN authentication.
- Conversation Relay websocket setup validates the same session token before trusting `user_id` or `user_name` custom parameters.
- Version `1.4.0` is the Conversation Relay-only baseline: Caller Access authentication, DTMF PIN fallback, Home Assistant Conversation text bridge, and ElevenLabs through Twilio Conversation Relay.
- Caller whitelist authentication is the preferred v2 path. DTMF PIN authentication remains fallback behavior.
- Conversation Relay is the only supported voice bridge. After caller whitelist or PIN authentication, it returns `<Connect><ConversationRelay>` TwiML and expects Twilio to connect to the add-on websocket at `/conversation_relay`.
- ElevenLabs TTS is the target voice provider for the v2.0.0 path.
- The call path avoids local caller-audio and generated-TTS audio file handling.
- Whisper is no longer a dependency and is not loaded at startup.
