# Local Testing Checklist

Use this checklist for the current Conversation Relay-only App. Keep test logs free of full caller phone numbers, PIN values, session tokens, full transcripts, and full Home Assistant responses.

## Latest Validated Results

- Version `1.4.3` makes Caller Access the single source of truth for caller identity and fallback PINs.
- Caller Access web UI is working.
- Caller Access records in `/share/twilio_voice_assistant/callers.json` are the only caller identity source.
- Optional DTMF fallback PINs are managed only on Caller Access records.
- Conversation Relay is the only supported voice bridge.
- DTMF PIN fallback remains supported through Caller Access record PINs.
- Twilio HTTP signature validation is enabled by default for `/incoming_call`, `/check_pin`, and `/start_session`.
- `/start_session` is protected by a short-lived signed session token.
- Conversation Relay websocket setup validates the same session token before trusting custom user metadata.
- Normal public routes are `/incoming_call`, `/check_pin`, `/start_session`, `/conversation_relay`, and `/conversation_relay/status`.
- Conversation Relay uses ElevenLabs successfully with a configured Conversation Relay voice ID.
- Conversation Relay sends caller transcript text to Home Assistant Conversation and receives response text successfully.
- End-call handling works.
- Conversation Relay latency is much faster than the previous local audio-file path.

## Startup

- App starts without configuration parsing errors.
- Logs include one `startup_configuration` timing event.
- `startup_configuration` includes:
  - `auth_mode`
  - `unknown_caller_policy`
  - `runtime_mode: conversation_relay_only`
  - `pin_fallback: dtmf`
  - `conversation_relay_tts_provider`
  - `conversation_relay_transcription_provider`
  - `conversation_relay_language`
  - `conversation_relay_voice_configured`
  - `caller_identity_source: caller_access`
  - `caller_access_records_count`
  - `local_audio_pipeline: removed`
  - `twilio_signature_validation_enabled`
  - `dev_unsigned_request_bypass_enabled`
  - `session_token_ttl_seconds`
- Startup logs do not include full caller phone numbers.
- Startup logs state that secure Conversation Relay-only mode is selected and local audio-file handling is removed.

## Authentication

- The active App manifest is `twilio_voice_assistant/config.json`.
- Normal caller identity setup uses Caller Access in the web UI.
- Fallback PINs are configured on Caller Access records.
- HA user IDs should not include angle brackets. Use the raw Home Assistant user ID value, not `<home_assistant_user_id>`.
- `auth_mode: pin` prompts for DTMF PIN on `/incoming_call`.
- `auth_mode: pin` reaches `/start_session` after a valid Caller Access PIN.
- `auth_mode: caller_whitelist` rejects an unknown caller when `unknown_caller_policy: reject`.
- `auth_mode: caller_whitelist_or_pin` sends an unknown caller to DTMF PIN fallback when `unknown_caller_policy: pin_fallback`.
- A known Caller Access number reaches `/start_session` without PIN.
- A known caller can omit `name`; the greeting uses the Home Assistant user display name when available.
- A known caller configured with multiple `phone_numbers` reaches `/start_session` from each listed number.
- Caller numbers are masked in logs.
- Unsigned Twilio requests can be allowed only for controlled local tests with `allow_unsigned_twilio_requests_for_dev: true`; production should keep the default `false`.
- Manual `/start_session` requests without a valid session token are rejected.

## Caller Access Admin UI

- Existing conversation agent dropdown works.
- Caller Access user dropdown loads Home Assistant users.
- Adding a caller with one phone number works.
- Adding a caller with multiple phone numbers works.
- Adding a caller with a fallback PIN works.
- Existing admin-managed Caller Access records show in the Caller Access list.
- Saved caller records show Home Assistant display names and masked phone numbers only.
- Saved caller records show only `PIN set` or `No PIN`; PIN values are not displayed.
- Deleting an admin-managed Caller Access record works.
- Normal validation should use Caller Access records.

## Normal Test Matrix

- Conversation Relay is the only supported voice bridge.
- Caller Access UI loads.
- Existing Caller Access records show.
- Add/delete Caller Access record works.
- Allowed Caller Access number skips PIN.
- Unlisted number gets DTMF PIN fallback when configured.
- Wrong PIN is rejected.
- Correct Caller Access PIN is accepted and starts Conversation Relay as the mapped Home Assistant user.
- If no Caller Access PINs are configured, unknown caller PIN fallback fails safely.
- Conversation Relay uses the configured ElevenLabs voice.
- End-call handling works.
- `/start_session` returns Conversation Relay TwiML only after caller whitelist match or successful PIN validation.
- `/start_session` rejects requests without a valid short-lived session token.
- Conversation Relay websocket setup validates the session token before accepting transcript messages.
- Unsigned `/incoming_call`, `/check_pin`, and `/start_session` requests are rejected when `allow_unsigned_twilio_requests_for_dev: false`.
- Known failure to avoid: `block_elevenlabs` is a Home Assistant TTS engine ID and must not be used as Conversation Relay `ttsProvider`.
- Conversation Relay `ttsProvider` should be `ElevenLabs`.
- Conversation Relay `voice` should use a voice ID that is valid for the active Twilio account and selected provider.
- Conversation Relay TwiML omits `voice` when `conversation_relay_voice` is blank or `default`.
- Conversation Relay websocket sends final transcript text to Home Assistant Conversation without local TTS generation.
- Conversation Relay mode does not write caller audio, generated TTS audio, transient transcripts, or transient response text to disk.
- No local STT/TTS audio pipeline is initialized.

## Security Validation

- Confirm startup logs show `twilio_signature_validation_enabled: true`.
- Confirm startup logs show `dev_unsigned_request_bypass_enabled: false`.
- Call from an allowed number and confirm it skips PIN and reaches Conversation Relay.
- Call from an unlisted number and confirm DTMF PIN fallback works.
- Confirm wrong PIN is rejected and correct PIN is accepted.
- Confirm Conversation Relay websocket logs a valid session token validation event during setup.
- Confirm end-call phrase still hangs up.
- Attempt to hit `/start_session` manually without a valid token and confirm it is rejected.
- If practical, send an unsigned `/incoming_call` request and confirm it is rejected when validation is enabled.
- Keep `/admin` and `/admin/api/*` private behind Home Assistant Ingress.
