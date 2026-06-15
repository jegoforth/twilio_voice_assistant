# Twilio Development Guide

This guide covers the Twilio side of development for Twilio Voice Assistant. It is focused on building and testing the public webhook, Caller Access authentication, DTMF PIN fallback, and preferred Conversation Relay bridge.

For the target v2.0.0 architecture, read `docs/ARCHITECTURE.md` first. The current implementation path is Caller Access plus Twilio Conversation Relay. Gather, speech PIN, Whisper, local generated TTS audio files, `/process_command`, and `/audio/*` were removed in version `1.4.0`.

## Auth Direction

The preferred v2 authentication path is caller whitelist matching:

```text
Twilio From number
  -> app normalizes/matches caller against callers / allowed_callers
  -> mapped Home Assistant user
  -> Conversation Relay
```

PIN authentication remains available as a fallback and legacy compatibility path. It is not the preferred v2 path because it adds latency and usually requires an extra call turn before the bridge can start.

Caller ID whitelist matching is useful for routing known callers, but it is not strong authentication by itself. Production deployments still require Twilio webhook signature validation, narrow public routing, careful logging, and a clear policy for unknown callers.

Version `1.4.2` makes secure Conversation Relay the only normal product path. Twilio HTTP request signature validation is enabled for `/incoming_call`, `/check_pin`, and `/start_session` unless the explicit development-only unsigned request bypass is enabled. `/start_session` and Conversation Relay websocket setup are protected with a short-lived signed session token generated only after caller whitelist or PIN authentication.

## Twilio Responsibilities

Twilio is responsible for:

- Receiving the phone call on a Twilio phone number.
- Calling the add-on webhook at `/incoming_call`.
- Sending the caller phone number in the webhook `From` field.
- Sending the called Twilio number in the webhook `To` field.
- Posting follow-up webhook fields such as `CallSid` and `Digits` when PIN fallback is used.
- Connecting to the Conversation Relay websocket, sending transcript events, and speaking text responses with the configured TTS provider.

The app is responsible for:

- Normalizing and matching `From` against canonical `callers` and legacy `allowed_callers`.
- Mapping known callers to Home Assistant users.
- Applying the unknown caller policy.
- Falling back to PIN only when configured to do so.
- Starting Conversation Relay after authentication.

Home Assistant remains responsible for:

- Home Assistant user identity.
- Conversation agent selection.
- Processing transcript text through Home Assistant Conversation.
- Returning response text to the bridge.

## Public Endpoints

Expose only the Conversation Relay routes needed by the add-on.

Common Twilio webhook routes:

```text
/incoming_call
/check_pin
/start_session
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

Preferred Caller Access admin flow:

- Leave add-on config `callers` and `allowed_callers` empty for normal day-to-day use.
- Add users through the Caller Access web UI.
- Caller Access stores admin-managed records in `/share/twilio_voice_assistant/callers.json`.
- Add-on YAML `callers` remains supported but should be considered advanced/manual configuration.
- Legacy `allowed_callers` remains migration/fallback only.
- Legacy separate PIN management is no longer the preferred path.
- PIN fallback is DTMF-only.

Validated Caller Access behavior:

- Caller Access web UI is working.
- Users were removed from add-on config `callers` and `allowed_callers`, then added through the web UI.
- A call from an allowed number skipped PIN and entered Conversation Relay as expected.
- A call from an unlisted number fell back to PIN as expected.
- Correct PIN was accepted and entered Conversation Relay as expected.
- Conversation Relay remains the preferred/default voice bridge.

Advanced/manual unified caller identity config:

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

Legacy migration-only `allowed_callers` single-number entries using `phone_number` still work. `name` is optional there too, but normal configuration should use Caller Access.

```yaml
allowed_callers:
  - name: Eric
    phone_number: "+15551234567"
    ha_user_id: 0123456789abcdef0123456789abcdef
```

Legacy PIN-only auth remains DTMF-only:

```yaml
auth_mode: pin
```

Advanced/manual caller whitelist with PIN fallback using unified caller identity:

```yaml
auth_mode: caller_whitelist_or_pin
unknown_caller_policy: pin_fallback
callers:
  - ha_user_id: 0123456789abcdef0123456789abcdef
    phone_numbers:
      - "+15551234567"
      - "+15559876543"
    pin: "1234"
```

Conversation Relay TTS configuration:

```yaml
conversation_relay_tts_provider: ElevenLabs
conversation_relay_voice: h8eW5xfRUGVJrZhAFxqK
conversation_relay_transcription_provider: Deepgram
conversation_relay_language: en-US
allow_unsigned_twilio_requests_for_dev: false
```

Use `conversation_relay_voice: h8eW5xfRUGVJrZhAFxqK` for the confirmed Elspeth ElevenLabs voice. Leave `conversation_relay_voice` empty only when Twilio should use its provider default. The value `default` is treated as blank and the app omits the `voice` attribute from Conversation Relay TwiML.

- Conversation Relay uses Twilio Conversation Relay TTS settings from add-on config. `conversation_relay_tts_provider` must be a Twilio-supported provider: `ElevenLabs`, `Google`, or `Amazon`.
- Do not use Home Assistant TTS engine IDs as Conversation Relay `ttsProvider` values. `block_elevenlabs` is valid only as a Home Assistant TTS engine ID, not as a Twilio Conversation Relay provider.
- For the validated v2 path, Conversation Relay `ttsProvider` should be `ElevenLabs` and `voice` should be `h8eW5xfRUGVJrZhAFxqK`.
- Conversation Relay does not load Whisper and avoids local audio files plus local STT/TTS processing.
- Whisper is no longer installed by the add-on.
- `allow_unsigned_twilio_requests_for_dev` defaults to `false`. Set it to `true` only for controlled local tests where requests are not signed by Twilio. Never enable it on exposed/public endpoints.

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
  -> signed session token
  -> /start_session
  -> token validation
  -> Conversation Relay
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
  -> signed session token
  -> /start_session
  -> token validation
  -> Conversation Relay
```

Legacy PIN-only:

```text
Twilio call
  -> /incoming_call
  -> PIN prompt
  -> /check_pin
  -> signed session token
  -> /start_session
  -> token validation
  -> Conversation Relay
```

## Removed Legacy Paths

Version `1.4.0` removes Gather, speech PIN, local Whisper, local generated TTS audio files, `/process_command`, and `/audio/*`. Legacy `allowed_callers` and the separate legacy PIN map still load for migration/fallback configuration, but normal setup should use Caller Access.

Expected TwiML for DTMF PIN fallback:

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

Conversation Relay is the only supported voice bridge. It depends on the active Twilio account having Conversation Relay enabled.

Stable v2 baseline:

- Version `1.3.13` was the cleanup baseline after Caller Access UI validation.
- Version `1.3.14` is the lightweight Conversation Relay runtime baseline.
- Conversation Relay no longer loads Whisper at startup.
- Version `1.4.0` is the Conversation Relay-only baseline and removes Gather, speech PIN, local Whisper, local generated TTS audio files, `/process_command`, and `/audio/*`.
- Version `1.3.8` is the stable unified-auth Conversation Relay baseline.
- Unified `callers` config works.
- Known caller phone numbers skip PIN and enter Conversation Relay.
- Unlisted callers fall back to PIN when configured.
- Wrong PIN is rejected.
- Correct PIN is accepted.
- Conversation Relay remains the preferred/default voice bridge.
- ElevenLabs Elspeth voice is working through Conversation Relay.
- Conversation Relay v2 has passed testing from multiple phones.
- Caller whitelist authentication works from multiple allowed callers.
- Unknown caller PIN fallback works.
- Wrong PIN rejection and correct PIN acceptance work.
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

`/start_session` is not an open user-ID endpoint. After caller whitelist or PIN authentication, the app redirects Twilio to `/start_session` with a short-lived signed `session_token`. The token includes `user_id`, `user_name`, creation time, and `CallSid` when available. `/start_session` validates the token before returning Conversation Relay TwiML and passes the same token to Conversation Relay as a custom parameter.

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
      <Parameter name="session_token" value="..."/>
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

- `setup`: validates `session_token` from TwiML custom parameters before trusting session metadata.
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
| Caller whitelist | Known caller in `callers` | App maps `From` to the configured `ha_user_id` and starts Conversation Relay. |
| Caller whitelist | One caller entry with multiple `phone_numbers` | Each listed number maps to the same configured `ha_user_id`. |
| Caller whitelist | Legacy `allowed_callers[*].phone_number` entry | Single-number legacy config still maps to the configured `ha_user_id`. |
| Caller whitelist | Unknown caller with `unknown_caller_policy: reject` | App rejects or politely hangs up without starting a bridge session. |
| Caller whitelist | Unknown caller with `unknown_caller_policy: pin_fallback` | App prompts for PIN and continues only after successful PIN validation. |
| Caller whitelist | Phone number logging | Logs mask the caller number and do not emit full E.164 numbers by default. |
| PIN fallback | Valid PIN | Call reaches `/start_session` and starts Conversation Relay. |
| PIN fallback | Bad PIN | Twilio redirects to `/incoming_call` or reprompts according to the configured policy. |
| `conversation_relay` | Known caller | `/start_session` returns `<Connect><ConversationRelay>`. |
| `conversation_relay` | PIN fallback caller | Conversation Relay starts only after PIN validation. |
| Security | Unsigned webhook with validation enabled | `/incoming_call`, `/check_pin`, and `/start_session` reject the request. |
| Security | Manual `/start_session` without token | Request is rejected. |
| Security | Conversation Relay setup without token | Websocket closes without accepting user metadata. |
| `conversation_relay` | Websocket connects | Logs include `websocket_connected`. |
| `conversation_relay` | Final prompt arrives | Logs include `transcript_text_received` and Home Assistant request timing. |
| `conversation_relay` | Home Assistant responds | App sends a `text` message back to Twilio with `last: true`. |
| Startup | Conversation Relay-only runtime | Startup logs say secure Conversation Relay-only mode is selected and local audio-file handling is removed. |
| Removed routes | `/process_command` or `/audio/*` | Routes are not documented as public endpoints and are no longer mounted. |

## Useful Logs

The app emits structured timing logs prefixed with `TIMING`. For Twilio-side development, watch for:

```text
twilio_signature_validation
session_token_validation
inbound_call_received
caller_whitelist_matched
unknown_caller_rejected
unknown_caller_pin_fallback
pin_prompt_sent
pin_accepted
conversation_relay_session_started
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

If signature validation fails:

- Confirm `public_base_url` exactly matches the public URL Twilio uses, including scheme and host.
- Confirm the Twilio phone number webhook uses `POST`.
- Confirm the reverse proxy preserves the path and query string.
- Confirm `twilio_auth_token` matches the active Twilio auth token.
- For controlled local unsigned requests only, temporarily set `allow_unsigned_twilio_requests_for_dev: true`.

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
- Twilio webhook signature validation is enabled by default and should stay enabled in production.
- `/start_session` requires a short-lived signed session token and should reject manual requests without one.
- Conversation Relay websocket setup validates the same session token before trusting user identity parameters.
- Keep `/admin` and `/admin/api/*` private behind Home Assistant Ingress.
