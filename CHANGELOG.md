# Changelog

## 1.4.5 - Public Beta Baseline

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
- Twilio Gather command loop.
- Speech PIN.
- Local generated TTS audio files.
- YAML caller identity configuration.
- Legacy `allowed_callers`.
- Legacy PIN map.
- Public admin access.
