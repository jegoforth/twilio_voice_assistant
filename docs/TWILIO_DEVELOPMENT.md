# Twilio Development Guide

This guide covers the Twilio side of development for Twilio Voice Assistant. It is focused on building and testing the public webhook, caller whitelist authentication, legacy PIN fallback, deprecated Gather fallback, and preferred Conversation Relay bridge.

For the target v2.0.0 architecture, read `docs/ARCHITECTURE.md` first. The important direction is that caller whitelist authentication is the preferred v2 path, `conversation_relay` is the default and preferred voice bridge mode, and `gather` remains deprecated fallback compatibility mode.

## Auth Direction

The preferred v2 authentication path is caller whitelist matching:

```text
Twilio From number
  -> app normalizes/matches caller against callers / allowed_callers
  -> mapped Home Assistant user
  -> selected bridge mode
```

PIN authentication remains available as a fallback and legacy compatibility path. It is not the preferred v2 path because it adds latency and usually requires an extra call turn before the bridge can start.

Caller ID whitelist matching is useful for routing known callers, but it is not strong authentication by itself. Production deployments still require Twilio webhook signature validation, narrow public routing, careful logging, and a clear policy for unknown callers.

## Twilio Responsibilities

Twilio is responsible for:

- Receiving the phone call on a Twilio phone number.
- Calling the add-on webhook at `/incoming_call`.
- Sending the caller phone number in the webhook `From` field.
- Sending the called Twilio number in the webhook `To` field.
- Posting follow-up webhook fields such as `CallSid`, `Digits`, and recording URLs when the selected flow requires them.
- In `gather` mode, recording caller audio and fetching generated response audio.
- In `conversation_relay` mode, connecting to the add-on websocket, sending transcript events, and speaking text responses.

The app is responsible for:

- Normalizing and matching `From` against canonical `callers` and legacy `allowed_callers`.
- Mapping known callers to Home Assistant users.
- Applying the unknown caller policy.
- Falling back to PIN only when configured to do so.
- Starting the selected bridge mode after authentication.

Home Assistant remains responsible for:

- Home Assistant user identity.
- Conversation agent selection.
- Processing transcript text through Home Assistant Conversation.
- Returning response text to the bridge.

## Public Endpoints

Expose only the routes needed by the selected mode.

Common Twilio webhook routes:

```text
/incoming_call
/start_session
```

PIN fallback or legacy PIN mode:

```text
/check_pin
```

Gather mode only:

```text
/process_command
/audio/*
```

Conversation Relay mode only:

```text
/conversation_relay
/conversation_relay/status
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

Preferred v2 unified caller identity config:

```yaml
auth_mode: caller_whitelist
unknown_caller_policy: reject
callers:
  - ha_user_id: 0123456789abcdef0123456789abcdef
    phone_numbers:
      - "+15551234567"
      - "+15559876543"
    pin: "1234"
  - ha_user_id: fedcba9876543210fedcba9876543210
    phone_numbers:
      - "+15557654321"
    pin: "5678"
```

The canonical `callers` list is the migration target for caller identity. Each record requires `ha_user_id` and one or more `phone_numbers`; it can also include an optional fallback `pin` and optional `name`. Runtime greetings resolve the Home Assistant user display name from `ha_user_id` when available. The optional configured `name` is only a fallback when Home Assistant user lookup fails. Caller Access stores admin-managed caller records in `/share/twilio_voice_assistant/callers.json` and merges them with add-on config `callers` at runtime. The legacy `allowed_callers` list and old PIN map still work during migration.

Legacy `allowed_callers` single-number entries using `phone_number` still work. `name` is optional there too, but new configuration should use `callers`.

```yaml
allowed_callers:
  - name: Eric
    phone_number: "+15551234567"
    ha_user_id: 0123456789abcdef0123456789abcdef
```

PIN-only legacy auth:

```yaml
auth_mode: pin
pin_mode: dtmf
```

Caller whitelist with PIN fallback using unified caller identity:

```yaml
auth_mode: caller_whitelist_or_pin
unknown_caller_policy: pin_fallback
pin_mode: dtmf
callers:
  - ha_user_id: 0123456789abcdef0123456789abcdef
    phone_numbers:
      - "+15551234567"
      - "+15559876543"
    pin: "1234"
```

Deprecated fallback bridge mode:

```yaml
voice_bridge_mode: gather
```

Conversation Relay prototype:

```yaml
voice_bridge_mode: conversation_relay
conversation_relay_tts_provider: ElevenLabs
conversation_relay_voice: h8eW5xfRUGVJrZhAFxqK
conversation_relay_transcription_provider: Deepgram
conversation_relay_language: en-US
```

Use `conversation_relay_voice: h8eW5xfRUGVJrZhAFxqK` for the confirmed Elspeth ElevenLabs voice. Leave `conversation_relay_voice` empty only when Twilio should use its provider default. The value `default` is treated as blank and the app omits the `voice` attribute from Conversation Relay TwiML.

Gather mode and Conversation Relay mode use separate TTS configuration:

- Gather mode uses Home Assistant TTS settings from the admin UI, such as a Home Assistant TTS engine ID like `block_elevenlabs`.
- Conversation Relay mode uses Twilio Conversation Relay TTS settings from add-on config. `conversation_relay_tts_provider` must be a Twilio-supported provider: `ElevenLabs`, `Google`, or `Amazon`.
- Do not use Home Assistant TTS engine IDs as Conversation Relay `ttsProvider` values. `block_elevenlabs` is valid only as a Home Assistant TTS engine ID, not as a Twilio Conversation Relay provider.
- For the validated v2 path, Conversation Relay `ttsProvider` should be `ElevenLabs` and `voice` should be `h8eW5xfRUGVJrZhAFxqK`.

The add-on schema accepts `auth_mode`, `unknown_caller_policy`, `callers`, and legacy `allowed_callers` from `twilio_voice_assistant/config.json`. The preferred caller shape is one Home Assistant user ID with a `phone_numbers` list and optional fallback `pin` in `callers`; `name` is optional. The legacy `allowed_callers[*].phone_number` single-value shape remains supported for backward compatibility, and `allowed_callers[*].name` is optional so saved options are not blocked during migration. Caller Access is the preferred admin model for new entries.

Caller Access admin behavior:

- Uses the existing Home Assistant user dropdown.
- Stores only `ha_user_id` as the stable key.
- Resolves and displays Home Assistant user display names dynamically.
- Allows one or more phone numbers per user.
- Allows an optional 4-digit fallback PIN per user.
- Shows existing records with masked phone numbers only.
- Shows only `PIN set` or `No PIN`; PIN values are write-only in the UI.
- Allows deleting admin-managed caller records.
- Shows add-on config `callers` records as read-only config-sourced records.
- Treats Caller Access as the preferred editable source; add-on config `callers` remains supported during migration but should not be edited in two places long term.
- Keeps legacy PIN management collapsed and de-emphasized.

HA user IDs in `callers` or legacy `allowed_callers` should not include angle brackets. Use `5e738...`, not `<5e738...>`.

## Authentication Call Flows

Known caller:

```text
Twilio call
  -> /incoming_call
  -> app reads From
  -> caller whitelist match
  -> map to ha_user_id
  -> /start_session or selected bridge mode
```

Unknown caller with `unknown_caller_policy: reject`:

```text
Twilio call
  -> /incoming_call
  -> app reads From
  -> no caller whitelist match
  -> reject call or polite hangup
```

Unknown caller with `unknown_caller_policy: pin_fallback`:

```text
Twilio call
  -> /incoming_call
  -> app reads From
  -> no caller whitelist match
  -> PIN prompt
  -> /check_pin
  -> /start_session or selected bridge mode
```

Legacy PIN-only:

```text
Twilio call
  -> /incoming_call
  -> PIN prompt
  -> /check_pin
  -> /start_session or selected bridge mode
```

## Gather Mode Build Path

`gather` is deprecated fallback compatibility mode and should remain the regression baseline while it exists.

Known caller flow:

```text
Twilio call
  -> /incoming_call
  -> caller whitelist match
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

PIN fallback flow:

```text
Twilio call
  -> /incoming_call
  -> DTMF PIN Gather
  -> /check_pin
  -> /start_session
  -> gather command loop
```

Use this mode to verify the existing v1 behavior after any Twilio-side change.

Expected TwiML for PIN fallback with `pin_mode: dtmf`:

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

`conversation_relay` is the preferred v2 voice bridge mode after the first stable v2 validation. It depends on the active Twilio account having Conversation Relay enabled. `gather` remains the fallback compatibility mode.

Stable v2 baseline:

- Version `1.3.8` is the stable unified-auth Conversation Relay baseline.
- Unified `callers` config works.
- Known caller phone numbers skip PIN and enter Conversation Relay.
- Unlisted callers fall back to PIN when configured.
- Wrong PIN is rejected.
- Correct PIN is accepted.
- Conversation Relay remains the preferred/default voice bridge.
- ElevenLabs Elspeth voice is working through Conversation Relay.
- Gather remains deprecated fallback only.
- Conversation Relay v2 has passed testing from multiple phones.
- Caller whitelist authentication works from multiple allowed callers.
- Unknown caller PIN fallback works.
- Wrong PIN rejection and correct PIN acceptance work.
- Gather compatibility mode remains functional.
- Conversation Relay mode works end-to-end.
- Conversation Relay uses ElevenLabs and the Elspeth voice ID `h8eW5xfRUGVJrZhAFxqK`.
- Conversation Relay sends caller transcript text to Home Assistant Conversation and receives response text successfully.
- The assistant can verify house state through Home Assistant.
- End-call handling works.
- Conversation Relay is much faster than the previous Gather/TTS/audio-file path.
- This version should be treated as the known-good baseline before adding new features.
- Future changes should preserve this path unless explicitly replacing it.
- Caller Access replaces the earlier stopped allowed-caller UI attempt with a focused unified caller management section. Add-on config `callers` remains supported.

Known caller flow:

```text
Twilio call
  -> /incoming_call
  -> caller whitelist match
  -> /start_session
  -> <Connect><ConversationRelay>
  -> wss://YOUR_PUBLIC_DOMAIN/conversation_relay
  -> final transcript text
  -> Home Assistant Conversation
  -> response text
  -> Conversation Relay text message
  -> Twilio / ElevenLabs TTS
```

Unknown caller with PIN fallback:

```text
Twilio call
  -> /incoming_call
  -> no caller whitelist match
  -> PIN prompt
  -> /check_pin
  -> /start_session
  -> <Connect><ConversationRelay>
```

The add-on should return Conversation Relay TwiML only after the caller is authenticated. The websocket URL is derived from `public_base_url` by converting `https://` to `wss://`.

Current generated TwiML shape:

```xml
<Response>
  <Connect action="/conversation_relay/status">
    <ConversationRelay
      url="wss://YOUR_PUBLIC_DOMAIN/conversation_relay"
      welcomeGreeting="Hello USER. What would you like to do?"
      language="en-US"
      ttsProvider="ElevenLabs"
      voice="h8eW5xfRUGVJrZhAFxqK"
      transcriptionProvider="Deepgram">
      <Parameter name="user_id" value="..."/>
      <Parameter name="user_name" value="..."/>
      <Parameter name="conversation_id" value="twilio_..."/>
    </ConversationRelay>
  </Connect>
</Response>
```

The app includes `voice="..."` only when `conversation_relay_voice` is configured and is not `default`. The confirmed Elspeth voice ID is `h8eW5xfRUGVJrZhAFxqK`.

Validated on the active Twilio account: Conversation Relay starts after caller whitelist authentication or successful PIN fallback, uses ElevenLabs with the Elspeth voice ID, sends final caller transcript text to Home Assistant Conversation, receives a Home Assistant response, speaks the response through Conversation Relay, and ends the call correctly through the end-call handling. Latency is much faster than the Gather/TTS/audio-file path.

TODO: Confirm interruption/barge-in behavior and websocket validation details before treating this as production behavior.

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

TODO: Add and document Conversation Relay websocket validation before production use.

## Provider Notes

Working assumptions for the prototype:

- Conversation Relay can connect to the app over `wss://`.
- Conversation Relay sends final caller transcripts as websocket `prompt` messages.
- The app can return text token messages for Twilio to speak.
- ElevenLabs is the target TTS provider for v2.0.0.

TODO: Verify these provider/account details before production:

- The Twilio account is onboarded for Conversation Relay.
- The account supports ElevenLabs TTS in Conversation Relay.
- The configured `conversation_relay_voice` is valid for `ElevenLabs`.
- The configured language is valid for both TTS and STT.
- The desired interruption/barge-in behavior is available and configured correctly.
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
6. Confirm Twilio webhook requests include the expected `From` value.
7. For Conversation Relay mode, confirm websocket upgrade support for `/conversation_relay`.

Many tunnel and reverse proxy failures show up as Twilio webhook errors before the app receives anything. Check both Twilio call logs and add-on logs.

## Test Matrix

Minimum Twilio-side tests:

| Area | Test | Expected result |
| --- | --- | --- |
| Caller whitelist | Known caller in `callers` | App maps `From` to the configured `ha_user_id` and starts the selected bridge mode. |
| Caller whitelist | One caller entry with multiple `phone_numbers` | Each listed number maps to the same configured `ha_user_id`. |
| Caller whitelist | Legacy `allowed_callers[*].phone_number` entry | Single-number legacy config still maps to the configured `ha_user_id`. |
| Caller whitelist | Unknown caller with `unknown_caller_policy: reject` | App rejects or politely hangs up without starting a bridge session. |
| Caller whitelist | Unknown caller with `unknown_caller_policy: pin_fallback` | App prompts for PIN and continues only after successful PIN validation. |
| Caller whitelist | Phone number logging | Logs mask the caller number and do not emit full E.164 numbers by default. |
| PIN fallback | Valid PIN | Call reaches `/start_session` and starts the selected bridge mode. |
| PIN fallback | Bad PIN | Twilio redirects to `/incoming_call` or reprompts according to the configured policy. |
| `gather` | Known caller, then speak command | `/process_command` downloads recording, sends text to Home Assistant, and plays `/audio/*`. |
| `gather` | PIN fallback, then speak command | Legacy command loop still works after PIN validation. |
| `conversation_relay` | Known caller | `/start_session` returns `<Connect><ConversationRelay>`. |
| `conversation_relay` | PIN fallback caller | Conversation Relay starts only after PIN validation. |
| `conversation_relay` | Websocket connects | Logs include `websocket_connected`. |
| `conversation_relay` | Final prompt arrives | Logs include `transcript_text_received` and Home Assistant request timing. |
| `conversation_relay` | Home Assistant responds | App sends a `text` message back to Twilio with `last: true`. |

## Useful Logs

The app emits structured timing logs prefixed with `TIMING`. For Twilio-side development, watch for:

```text
inbound_call_received
caller_whitelist_matched
unknown_caller_rejected
unknown_caller_pin_fallback
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

These logs avoid PINs, auth tokens, full caller numbers, full transcripts, and full response text by default.

## Troubleshooting

If Twilio never reaches the app:

- Verify the phone number webhook URL is `https://YOUR_PUBLIC_DOMAIN/incoming_call`.
- Verify the reverse proxy forwards `POST` requests.
- Verify the public route is not protected by Home Assistant Ingress authentication.
- Check Twilio debugger and call logs for webhook delivery errors.

If known callers are not recognized:

- Confirm Twilio sends `From` in E.164 format, such as `+15551234567`.
- Confirm `callers[*].phone_numbers` uses E.164 format.
- Legacy `allowed_callers[*].phone_number` entries are still accepted.
- Confirm number normalization does not strip or duplicate the country code.
- Confirm the matching log masks the number but still indicates whether a match happened.

If unknown callers are not handled correctly:

- Confirm `auth_mode`.
- Confirm `unknown_caller_policy`.
- Use `reject` for strict whitelist testing.
- Use `pin_fallback` only when legacy PIN fallback is intended.

If DTMF PIN fallback fails:

- Confirm `auth_mode: pin` or `auth_mode: caller_whitelist_or_pin`.
- Confirm `unknown_caller_policy: pin_fallback` when testing unknown callers.
- Confirm `pin_mode: dtmf`.
- Confirm Twilio is posting `Digits` to `/check_pin`.
- Confirm the PIN exists in the admin UI or configured PIN map.

If Conversation Relay fails immediately:

- Confirm the Twilio account is enabled for Conversation Relay.
- Confirm the generated websocket URL begins with `wss://`.
- Confirm the tunnel or reverse proxy supports websocket upgrades.
- Confirm `conversation_relay_tts_provider` is a Twilio provider: `ElevenLabs`, `Google`, or `Amazon`.
- Do not set `conversation_relay_tts_provider` to a Home Assistant TTS engine ID such as `block_elevenlabs`; Twilio reports this as invalid TTS settings.
- Temporarily remove `conversation_relay_voice` to let Twilio use the provider default.
- Check Twilio call logs for Conversation Relay error codes.
- Treat provider, language, and voice failures as TODO validation items until verified against the active account.

If Home Assistant responds but the caller hears nothing in Conversation Relay mode:

- Confirm the websocket response message is `{"type":"text","token":"...","last":true}`.
- Confirm `ttsProvider` and `voice` are a valid combination in Twilio.
- Confirm the language is supported by the selected provider.

## Security Notes

- Never hard-code Twilio credentials, PINs, public URLs, caller phone numbers, Home Assistant user IDs, or voice IDs into source code.
- Mask phone numbers in logs.
- Do not log full caller numbers by default.
- Do not log `twilio_auth_token`, `SUPERVISOR_TOKEN`, PIN values, full transcripts, or full Home Assistant responses.
- Caller ID whitelist matching is not strong authentication by itself. Caller ID can be spoofed or forwarded in ways that weaken trust.
- Twilio webhook signature validation is required before production use.
- Conversation Relay websocket validation is required before production use.
- Keep `/admin` and `/admin/api/*` private behind Home Assistant Ingress.
