# Changelog

## 1.5.1

- Fix: an unrecognized caller's opening greeting said "Hello unknown. What
  would you like to do?" -- a leftover placeholder value spoken aloud, and
  a prompt that skipped past the identity ladder entirely. Now opens with
  "Hello, this is Elspeth. To whom am I speaking?" for that case, matching
  the actual voice flow. Known callers are unaffected.

## 1.5.0

- **Requires [HA Extended User Management](https://github.com/jegoforth/ha-extended-user-management)**, a separate Home Assistant integration. Install and configure it (set each household member's `phone_number` and PIN) before this release.
- Caller phone-number matching now goes through HA Extended User
  Management's `find_person_by_phone` service (a `phone_number` profile
  value on the person's own record) instead of this add-on's own
  `callers.json` -- one source of truth shared with the person's PIN.
- Every caller turn is now handed to Elspeth Core via the narrow
  `elspeth_local.twilio_conversation` service (which signs a real per-call
  identity) instead of the generic `conversation/process` REST API, which
  carried no caller identity at all.
- An unrecognized caller is no longer sent to a DTMF PIN prompt
  (`/check_pin`). They are connected straight into Conversation Relay with
  a placeholder identity that resolves to `household_unknown` in Core, and
  Core's own spoken self-declaration + PIN elevation ladder ("To whom am I
  speaking?" / stated name / "Can you verify your PIN?" / spoken PIN)
  handles identity entirely in natural voice -- no DTMF, no PIN stored by
  this add-on at all. Nothing is answered for an unrecognized caller until
  that PIN verifies.
- `AUTH_MODE`, `UNKNOWN_CALLER_POLICY`, `/check_pin`, `prompt_for_pin()`,
  and the Caller Access PIN fields are no longer reached by `/incoming_call`
  and are candidates for removal in a follow-up cleanup pass.

## 1.4.6 - Public Beta Baseline

This is the first public beta shape of Twilio Voice Assistant.

Current product shape:

- Twilio Conversation Relay is the only voice bridge.
- Caller Access UI is the only caller identity management surface.
- Optional DTMF PIN fallback is managed through Caller Access records.
- Home Assistant Conversation remains the assistant brain.
- ElevenLabs can be used through Twilio Conversation Relay for voice playback.
- Twilio HTTP signature validation is enabled by default.
- `/start_session` requires a short-lived signed session token.
- `/conversation_relay` validates session setup before trusting user metadata.
- Public routes are limited to `/incoming_call`, `/check_pin`, `/start_session`, `/conversation_relay`, and `/conversation_relay/status`.

Not included in the public beta path:

- Local Whisper.
- Speech PIN.
- Local generated TTS audio files.
- YAML caller identity configuration.
- Legacy `allowed_callers`.
- Legacy PIN map.
- Public admin access.
