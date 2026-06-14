# Twilio Voice Assistant

Call a Twilio phone number, enter a PIN, and talk to your Home Assistant voice assistant from any phone.

This Home Assistant add-on receives incoming Twilio calls, authenticates the caller with a PIN, transcribes spoken commands, sends them to Home Assistant Assist / Conversation, and plays the spoken response back over the phone.

The current default mode remains the v1-compatible Gather/audio-file flow. The v2.0.0 target architecture is documented in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): the preferred path is a text-only bridge where external voice services handle STT/TTS and Home Assistant Conversation remains the assistant brain.

## Requirements

Before installing, make sure you have:

- Home Assistant OS or Home Assistant Supervised with add-ons available.
- A working Home Assistant voice assistant setup, including:
  - Speech-to-text.
  - A configured conversation agent.
  - A configured text-to-speech engine.
- A Twilio account with:
  - An active phone number.
  - Account SID.
  - Auth token.
- A secure public HTTPS URL that can reach the Twilio webhook and generated audio routes on this add-on.

Twilio must be able to reach the webhook and generated audio routes from the public internet. Common options include:

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

Your public domain should forward only these paths to the add-on on port `8000`:

```text
/incoming_call
/check_pin
/start_session
/process_command
/conversation_relay
/conversation_relay/status
/audio/*
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
  - `pin`: Legacy PIN-first behavior.
  - `caller_whitelist_or_pin`: Known callers skip PIN; unknown callers can use PIN fallback.
- `unknown_caller_policy`: Defaults to `reject`. Use `pin_fallback` to allow unknown callers to try PIN auth.
- `allowed_callers`: Optional list of known callers with `name`, E.164 `phone_numbers`, and Home Assistant `ha_user_id`. Legacy single-value `phone_number` entries still work.
- `voice_bridge_mode`: Optional bridge mode. Defaults to `gather`.
  - `gather`: v1-compatible fallback mode using the existing local audio path.
  - `conversation_relay`: Experimental v2 prototype using Twilio Conversation Relay.
  - `elevenlabs_agent`: Reserved for a future ElevenLabs Agent experiment.
- `conversation_relay_tts_provider`: Defaults to `ElevenLabs`.
- `conversation_relay_voice`: Optional Conversation Relay voice identifier.
- `conversation_relay_transcription_provider`: Defaults to `Deepgram`.
- `conversation_relay_language`: Defaults to `en-US`.
- `pin_mode`: Defaults to `dtmf`. Set to `speech` to use the older spoken-PIN recording flow.
- `debug`: Optional extra logging.

Start the add-on after saving the configuration.

## Admin Page Setup

Open the add-on web UI from Home Assistant. The admin page uses Home Assistant Ingress, so it should be accessed from inside Home Assistant instead of through the public Twilio URL.

From the admin page:

1. Select the Home Assistant conversation agent to use.
2. Select the Home Assistant TTS engine.
3. Select the TTS language.
4. Select a voice if the TTS engine provides voices.
5. Click **Save Settings**.
6. Create one or more PIN mappings:
   - Enter a 4 digit PIN.
   - Select the Home Assistant user for that PIN.
   - Click **Add PIN**.

PINs and assistant settings are stored in `/share/twilio_voice_assistant` so they survive add-on rebuilds.

## Test

1. Call your Twilio phone number.
2. Say your 4 digit PIN after the prompt.
3. After authentication, ask Home Assistant a command.
4. Home Assistant should process the command through the selected conversation agent and reply using the selected TTS engine.
5. To end the call, say a phrase such as `goodbye`, `hang up`, `end call`, `that's all`, or `I'm done`.
6. The add-on should say goodbye and disconnect the call.

## Notes

- Rotate your Twilio auth token if it is ever pasted into logs, screenshots, chat, or documentation.
- The add-on currently uses local Whisper transcription for call audio before sending text to Home Assistant Conversation.
- Caller whitelist authentication is the preferred v2 path. PIN authentication remains the default fallback/legacy path for compatibility.
- Conversation Relay mode is experimental. After caller whitelist or PIN authentication, it returns `<Connect><ConversationRelay>` TwiML and expects Twilio to connect to the add-on websocket at `/conversation_relay`.
- ElevenLabs TTS is the target voice provider for the v2.0.0 path.
- The primary v2 path avoids local caller-audio and generated-TTS audio file handling; local audio files remain part of the `gather` fallback mode.
- The selected Home Assistant TTS engine must be able to generate audio that Twilio can play through the public URL configured in `public_base_url`.
- Generated audio is the only content served from `/audio`. Admin data and PIN settings are not served from the public audio route.
