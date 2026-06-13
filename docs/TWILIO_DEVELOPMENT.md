# Twilio Development Guide

This guide covers the Twilio side of development for Twilio Voice Assistant. It is focused on building and testing the public webhook, PIN flow, Gather fallback, and experimental Conversation Relay bridge.

For the target v2.0.0 architecture, read `docs/ARCHITECTURE.md` first. The important direction is that `gather` remains the default compatibility mode, while `conversation_relay` is the first text-only prototype.

## Twilio Responsibilities

Twilio is responsible for:

- Receiving the phone call on a Twilio phone number.
- Calling the add-on webhook at `/incoming_call`.
- Collecting the caller PIN through DTMF or the legacy spoken-PIN flow.
- Running the selected voice bridge mode after successful PIN validation.
- In `gather` mode, recording caller audio and fetching generated response audio.
- In `conversation_relay` mode, connecting to the add-on websocket, sending transcript events, and speaking text responses.

Home Assistant remains responsible for:

- PIN-to-user mappings.
- Conversation agent selection.
- Processing transcript text through Home Assistant Conversation.
- Returning response text to the bridge.

## Public Endpoints

For local or remote Twilio development, expose only the routes Twilio needs:

```text
/incoming_call
/check_pin
/start_session
/process_command
/conversation_relay
/conversation_relay/status
/audio/*
```

Do not expose these routes publicly:

```text
/admin
/admin/api/*
```

The admin UI is intended to run behind Home Assistant Ingress.

## Twilio Phone Number Setup

In the Twilio Console:

1. Open **Phone Numbers** > **Manage** > **Active numbers**.
2. Select the development phone number.
3. Under **Voice Configuration**, set **A call comes in** to **Webhook**.
4. Set the URL to:

   ```text
   https://YOUR_PUBLIC_DOMAIN/incoming_call
   ```

5. Set the method to `HTTP POST`.
6. Save the phone number.

Use HTTPS. Twilio should not be pointed at plain HTTP for development unless you are using a short-lived local tunnel that terminates HTTPS externally.

## Add-on Configuration For Twilio Testing

Required:

```yaml
twilio_account_sid: ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
twilio_auth_token: your_twilio_auth_token
public_base_url: https://YOUR_PUBLIC_DOMAIN
```

Compatibility mode:

```yaml
voice_bridge_mode: gather
pin_mode: dtmf
```

Conversation Relay prototype:

```yaml
voice_bridge_mode: conversation_relay
pin_mode: dtmf
conversation_relay_tts_provider: ElevenLabs
conversation_relay_voice: ""
conversation_relay_transcription_provider: Deepgram
conversation_relay_language: en-US
```

Leave `conversation_relay_voice` empty until the Twilio account has a confirmed Conversation Relay voice ID. Twilio will use its default voice for the selected provider when no voice is supplied.

## Gather Mode Build Path

`gather` is the default and should remain the regression baseline.

Flow:

```text
Twilio call
  -> /incoming_call
  -> DTMF PIN Gather
  -> /check_pin
  -> /start_session
  -> Home Assistant TTS audio generated locally
  -> /audio/*
  -> /process_command
  -> Twilio recording downloaded
  -> local Whisper transcription
  -> Home Assistant Conversation
  -> Home Assistant TTS audio generated locally
  -> /audio/*
```

Use this mode to verify the existing v1 behavior after any Twilio-side change.

Expected TwiML after `/incoming_call` with `pin_mode: dtmf`:

```xml
<Response>
  <Gather input="dtmf" numDigits="4" timeout="10" action="/check_pin" method="POST">
    <Say>Please enter your four digit PIN.</Say>
  </Gather>
  <Say>I did not receive a PIN. Goodbye.</Say>
  <Hangup/>
</Response>
```

## Conversation Relay Build Path

`conversation_relay` is experimental and depends on the active Twilio account having Conversation Relay enabled.

Flow:

```text
Twilio call
  -> /incoming_call
  -> DTMF PIN Gather
  -> /check_pin
  -> /start_session
  -> <Connect><ConversationRelay>
  -> wss://YOUR_PUBLIC_DOMAIN/conversation_relay
  -> final transcript text
  -> Home Assistant Conversation
  -> response text
  -> Conversation Relay text message
  -> Twilio / ElevenLabs TTS
```

The add-on returns Conversation Relay TwiML only after PIN validation. The websocket URL is derived from `public_base_url` by converting `https://` to `wss://`.

Current generated TwiML shape:

```xml
<Response>
  <Connect action="/conversation_relay/status">
    <ConversationRelay
      url="wss://YOUR_PUBLIC_DOMAIN/conversation_relay"
      welcomeGreeting="Hello USER. What would you like to do?"
      language="en-US"
      ttsProvider="ElevenLabs"
      transcriptionProvider="Deepgram">
      <Parameter name="user_id" value="..."/>
      <Parameter name="user_name" value="..."/>
      <Parameter name="conversation_id" value="twilio_..."/>
    </ConversationRelay>
  </Connect>
</Response>
```

The app includes `voice="..."` only when `conversation_relay_voice` is configured.

## Conversation Relay Websocket Contract

Twilio connects to:

```text
wss://YOUR_PUBLIC_DOMAIN/conversation_relay
```

The prototype expects these incoming message types:

- `setup`: used to read custom parameters from the TwiML.
- `prompt`: used for caller transcripts.
- `dtmf`: logged/ignored for now.
- `interrupt`: logged/ignored for now.
- `error`: logged without exposing secrets.

For `prompt` messages, the app only sends final transcripts to Home Assistant Conversation:

```json
{
  "type": "prompt",
  "voicePrompt": "turn on the kitchen lights",
  "lang": "en-US",
  "last": true
}
```

Partial transcripts with `last: false` are logged at debug level and ignored.

The app sends response text back to Twilio as a Conversation Relay text token message:

```json
{
  "type": "text",
  "token": "The kitchen lights are on.",
  "last": true,
  "lang": "en-US"
}
```

Do not add local TTS generation to this path. Conversation Relay mode should not write caller audio, generated TTS audio, transcripts, or response text to disk.

## Provider Notes

Twilio's current Conversation Relay TwiML supports `ttsProvider`, `voice`, `language`, `ttsLanguage`, `transcriptionLanguage`, and `transcriptionProvider` attributes. Current Twilio docs list `ElevenLabs` as a supported TTS provider and `Deepgram` as the default transcription provider for newer Conversation Relay accounts.

Validate these before relying on a production call flow:

- The Twilio account is onboarded for Conversation Relay.
- The account supports ElevenLabs TTS in Conversation Relay.
- The configured `conversation_relay_voice` is valid for `ElevenLabs`.
- The configured language is valid for both TTS and STT.
- The public websocket endpoint is reachable as `wss://`, not `https://`.

Reference docs:

- https://www.twilio.com/docs/voice/twiml/connect/conversationrelay
- https://www.twilio.com/docs/voice/conversationrelay/websocket-messages

## Local Tunnel Checklist

For development outside a deployed public add-on:

1. Run the add-on or FastAPI app on port `8000`.
2. Start an HTTPS tunnel to port `8000`.
3. Set `public_base_url` to the tunnel base URL.
4. Configure the Twilio phone number webhook to `https://TUNNEL_DOMAIN/incoming_call`.
5. Confirm `/incoming_call` is publicly reachable.
6. For Conversation Relay mode, confirm websocket upgrade support for `/conversation_relay`.

Many tunnel and reverse proxy failures show up as Twilio webhook errors before the app receives anything. Check both Twilio call logs and add-on logs.

## Test Matrix

Minimum Twilio-side tests:

| Mode | Test | Expected result |
| --- | --- | --- |
| `gather` | Call number, enter valid PIN | Call reaches `/start_session` and plays the local TTS prompt. |
| `gather` | Speak command | `/process_command` downloads recording, sends text to Home Assistant, and plays `/audio/*`. |
| `gather` | Enter bad PIN | Twilio redirects to `/incoming_call`. |
| `conversation_relay` | Call number, enter valid PIN | `/start_session` returns `<Connect><ConversationRelay>`. |
| `conversation_relay` | Websocket connects | Logs include `websocket_connected`. |
| `conversation_relay` | Final prompt arrives | Logs include `transcript_text_received` and Home Assistant request timing. |
| `conversation_relay` | Home Assistant responds | App sends a `text` message back to Twilio with `last: true`. |

## Useful Logs

The app emits structured timing logs prefixed with `TIMING`. For Twilio-side development, watch for:

```text
inbound_call_received
pin_prompt_sent
pin_accepted
bridge_mode_selected
conversation_relay_twiml_returned
websocket_connected
transcript_text_received
home_assistant_conversation_request_started
home_assistant_conversation_response_received
response_text_sent_to_conversation_relay
call_ended
```

These logs intentionally avoid PINs, auth tokens, full transcripts, and full response text.

## Troubleshooting

If Twilio never reaches the app:

- Verify the phone number webhook URL is `https://YOUR_PUBLIC_DOMAIN/incoming_call`.
- Verify the reverse proxy forwards `POST` requests.
- Verify the public route is not protected by Home Assistant Ingress authentication.

If DTMF PIN entry fails:

- Confirm `pin_mode: dtmf`.
- Confirm Twilio is posting `Digits` to `/check_pin`.
- Confirm the PIN exists in the admin UI.

If Conversation Relay fails immediately:

- Confirm the Twilio account is enabled for Conversation Relay.
- Confirm the generated websocket URL begins with `wss://`.
- Confirm the tunnel or reverse proxy supports websocket upgrades.
- Temporarily remove `conversation_relay_voice` to let Twilio use the provider default.
- Check Twilio call logs for Conversation Relay error codes.

If Home Assistant responds but the caller hears nothing in Conversation Relay mode:

- Confirm the websocket response message is `{"type":"text","token":"...","last":true}`.
- Confirm `ttsProvider` and `voice` are a valid combination in Twilio.
- Confirm the language is supported by the selected provider.

## Security Notes

- Never hard-code Twilio credentials, PINs, public URLs, or voice IDs into source code.
- Do not log `twilio_auth_token`, `SUPERVISOR_TOKEN`, PIN values, full transcripts, or full Home Assistant responses.
- Keep `/admin` and `/admin/api/*` private behind Home Assistant Ingress.
- Conversation Relay websocket signature validation is still a TODO for hardening before production use.
