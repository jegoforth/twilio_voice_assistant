# Twilio Voice Assistant Architecture

## Document Role

This document is the stable architecture reference for Twilio Voice Assistant.

It describes the current public beta product:

- Twilio Conversation Relay only.
- Caller Access UI only for caller identity.
- Optional DTMF PIN fallback.
- Home Assistant Conversation as the assistant brain.
- ElevenLabs voice through Twilio Conversation Relay.
- Twilio signature validation enabled by default.
- Protected session startup and websocket setup.
- No local STT/TTS audio pipeline.

Implementation notes, testing activity, risks, and proposed changes belong in `docs/DECISIONS.md`, `docs/TWILIO_DEVELOPMENT.md`, or `docs/LOCAL_TESTING.md`.

## Product Summary

Twilio Voice Assistant is a Home Assistant App that lets a caller use a Twilio phone number to talk to Home Assistant Conversation.

The App receives Twilio webhooks, authenticates the caller, starts Twilio Conversation Relay, sends final transcript text to Home Assistant Conversation, and returns Home Assistant response text to Conversation Relay for speech playback.

```text
Twilio phone call
  -> Twilio Voice webhook
  -> Twilio Voice Assistant App
  -> Caller Access or DTMF PIN fallback
  -> signed session startup
  -> Twilio Conversation Relay websocket
  -> Home Assistant Conversation
  -> text response
  -> Twilio Conversation Relay TTS
  -> caller hears response
```

The App is intentionally text-only after Twilio handles speech recognition. It does not store caller audio, generate local TTS files, or serve local audio files.

## System Boundaries

### Twilio

Twilio is responsible for:

- Receiving and maintaining the phone call.
- Sending the inbound call webhook to `/incoming_call`.
- Providing caller metadata such as `From`, `To`, and `CallSid`.
- Posting DTMF digits to `/check_pin` when PIN fallback is used.
- Connecting to `/conversation_relay` over websocket.
- Sending final transcript text events.
- Speaking response text through Conversation Relay using the configured provider and voice.

### Twilio Voice Assistant App

The App is responsible for:

- Validating Twilio HTTP request signatures.
- Normalizing caller phone numbers.
- Matching known callers against Caller Access records.
- Applying `auth_mode` and `unknown_caller_policy`.
- Validating optional DTMF fallback PINs stored on Caller Access records.
- Creating short-lived signed session tokens.
- Protecting `/start_session`.
- Protecting Conversation Relay websocket setup.
- Routing final transcript text to Home Assistant Conversation.
- Returning Home Assistant response text to Twilio Conversation Relay.
- Logging timings without secrets or sensitive content.

### Home Assistant

Home Assistant is responsible for:

- User identity.
- Conversation agent selection.
- Processing transcript text through Home Assistant Conversation.
- Executing house-control actions.
- Returning response text.

## Caller Identity

Caller Access is the only caller identity management surface.

Caller Access records are stored in:

```text
/share/twilio_voice_assistant/callers.json
```

Each record stores:

- Home Assistant user ID.
- One or more caller phone numbers.
- Optional 4-digit DTMF fallback PIN.
- Optional fallback display name.

The UI resolves the display name from Home Assistant when possible. Saved phone numbers are masked in record lists and logs. Saved PIN values are write-only in the UI.

## Authentication Modes

The App supports:

```yaml
auth_mode: caller_whitelist | pin | caller_whitelist_or_pin
unknown_caller_policy: reject | pin_fallback
```

Recommended beta configuration:

```yaml
auth_mode: caller_whitelist_or_pin
unknown_caller_policy: pin_fallback
```

Known caller flow:

```text
/incoming_call
  -> validate Twilio signature
  -> read Twilio From number
  -> match Caller Access phone number
  -> map to Home Assistant user
  -> create signed session token
  -> /start_session
  -> Conversation Relay
```

Unknown caller with `reject`:

```text
/incoming_call
  -> validate Twilio signature
  -> no Caller Access match
  -> polite rejection
```

Unknown caller with `pin_fallback`:

```text
/incoming_call
  -> validate Twilio signature
  -> no Caller Access match
  -> DTMF PIN prompt
  -> /check_pin
  -> validate Twilio signature
  -> match PIN to Caller Access record
  -> create signed session token
  -> /start_session
  -> Conversation Relay
```

Caller ID matching is a convenience and routing mechanism. It should not be treated as strong authentication by itself.

## Public Routes

Only these routes are intended to be publicly reachable:

```text
/incoming_call
/check_pin
/start_session
/conversation_relay
/conversation_relay/status
```

These routes must not be exposed publicly:

```text
/admin
/admin/api/*
```

The admin UI should be reached through Home Assistant Ingress.

## Session Protection

`/incoming_call` and `/check_pin` are the only routes that create authenticated call sessions.

After a caller is authenticated, the App creates a short-lived signed session token that includes:

- Home Assistant user ID.
- Display name.
- Creation timestamp.
- Call SID when available.

`/start_session` validates that token before returning Conversation Relay TwiML.

The Conversation Relay TwiML passes the same session token as a custom parameter. The websocket setup message must include a valid token before the App trusts user metadata or accepts transcript events.

## Conversation Relay Contract

Conversation Relay connects to:

```text
wss://PUBLIC_BASE_URL/conversation_relay
```

The App handles these event types defensively:

- `setup`: validates the signed session token.
- `prompt`: final caller transcripts are sent to Home Assistant Conversation.
- `dtmf`: currently logged/ignored.
- `interrupt`: currently logged/ignored.
- `error`: logged without sensitive details.

The App returns response text as Conversation Relay text token messages.

## Security Guardrails

- Twilio signature validation is enabled by default.
- Unsigned request bypass is development-only.
- `/start_session` requires a short-lived signed token.
- `/conversation_relay` setup validates the signed token.
- Public route exposure must be minimal.
- `/admin` and `/admin/api/*` must stay private.
- Logs must not include Twilio auth tokens, request signatures, session tokens, PINs, full caller numbers, full transcripts, or full Home Assistant responses.

## Storage

Persistent App data lives under:

```text
/share/twilio_voice_assistant/
```

Current files:

- `settings.json`: selected Home Assistant Conversation agent and admin settings.
- `callers.json`: Caller Access records.

The call path does not write transient caller audio, generated TTS audio, transcripts, or response text to disk.

## Future Work

Potential future work:

- One-time-use session token replay protection.
- Better diagnostics for failed Twilio signature validation.
- More detailed Conversation Relay provider validation.
- Optional Home Assistant-native configuration surfaces if needed.
- Health/latency sensors or diagnostics surfaced in Home Assistant.
