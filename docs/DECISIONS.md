# Twilio Voice Assistant Decisions

This document records the current product and architecture decisions for the v2.0.0 target architecture.

## Status labels

- **Accepted**: Decision is part of the current target architecture.
- **Proposed**: Decision is likely but still needs validation.
- **Deferred**: Decision is intentionally postponed.
- **Rejected**: Decision was considered and is not part of the target direction.

## Decision 001: Name the new target architecture v2.0.0

**Status:** Accepted

The next targeted architecture is named **v2.0.0**.

v2.0.0 represents a major architectural change from an audio-processing add-on to a text-only voice bridge with external STT/TTS.

### Rationale

The current implementation moves audio into and out of the local environment. This creates latency, file handling, codec, storage, cleanup, public URL, and troubleshooting overhead.

A major version target is appropriate because the primary call path, configuration model, and long-term packaging strategy are changing.

### Consequences

- v1.x remains the compatibility line.
- v2.0.0 documentation should describe the desired architecture, even before all implementation work is complete.
- Migration notes will be required before an actual v2.0.0 release.

## Decision 002: Make the primary bridge text-only

**Status:** Accepted

The v2.0.0 primary path will pass **text only** between the external voice layer and Home Assistant.

```text
External voice layer -> transcript text -> bridge -> Home Assistant Conversation
Home Assistant Conversation -> response text -> bridge -> external voice layer
```

### Rationale

Text is faster and simpler to pass across the network than generated audio files. It also avoids local audio storage, Home Assistant media-source routing, and public generated-audio routes.

### Consequences

- Local WAV/M4A/MP3 processing should be removed from the primary path.
- Existing audio file paths may remain only as fallback behavior.
- Latency measurement becomes easier because the bridge handles discrete text events.

## Decision 003: Use ElevenLabs for TTS

**Status:** Accepted

v2.0.0 will use **ElevenLabs for text-to-speech**.

### Rationale

The Elspeth experience depends heavily on voice quality, personality, and consistency. ElevenLabs is the preferred TTS provider for that experience.

### Consequences

- Twilio-only TTS is not the preferred final experience.
- If Twilio Conversation Relay can provide ElevenLabs TTS with the necessary voice control, it is the preferred v2 path.
- If Conversation Relay cannot provide acceptable ElevenLabs TTS behavior, ElevenLabs Agent integration becomes the next candidate.

## Decision 004: Prefer Twilio Conversation Relay first

**Status:** Proposed

The first v2.0.0 prototype should test Twilio Conversation Relay as the text-only bridge layer, with ElevenLabs TTS configured if supported by the active Twilio account/API.

### Rationale

Conversation Relay appears to match the desired model closely:

```text
Twilio handles phone call, STT, and TTS.
Bridge handles text events.
Home Assistant Conversation remains the brain.
```

This keeps Home Assistant in control of house reasoning and service execution while minimizing local audio handling.

### Consequences

- The project needs a websocket endpoint for Conversation Relay events.
- The bridge must parse transcript events and send response text events.
- The exact Twilio markup and provider/voice settings must be validated during implementation.

## Decision 005: Treat ElevenLabs Agent integration as the secondary path

**Status:** Proposed

ElevenLabs Agent with Twilio integration should be investigated as a secondary v2.0.0 path.

### Rationale

ElevenLabs Agent integration may provide the smoothest realtime voice experience and the best ElevenLabs voice behavior. However, it may also move the main agent loop away from Home Assistant.

The desired architecture keeps Home Assistant Conversation as the source of truth for house context, tools, and service execution.

### Consequences

- ElevenLabs Agents should not replace the Home Assistant conversation brain unless that tradeoff is explicitly accepted later.
- If used, ElevenLabs should call back into the bridge through tools/endpoints that send text to Home Assistant and return text results.
- Session authority, identity, and security should remain in the bridge/Home Assistant side where possible.

## Decision 006: Preserve the existing Gather path as fallback

**Status:** Accepted

The current Gather-based implementation should remain available as a fallback while v2.0.0 is developed.

### Rationale

The existing app works as a baseline and provides a useful recovery path while Conversation Relay and ElevenLabs Agent modes are tested.

### Consequences

- Add a mode setting such as:

```yaml
voice_bridge_mode: gather | conversation_relay | elevenlabs_agent
```

- `gather` should be treated as compatibility mode.
- New work should avoid deepening dependency on generated audio files except for fallback support.

## Decision 007: Prefer DTMF for PIN entry

**Status:** Accepted

PIN entry should use DTMF where possible instead of speech recognition.

### Rationale

PINs are structured numeric input. DTMF is faster, less ambiguous, and less likely to fail than speech-based PIN recognition.

### Consequences

- The PIN prompt can use a short spoken prompt.
- PIN validation can happen before the realtime voice bridge starts.
- Speech-based PIN entry can remain an optional fallback if needed.

## Decision 008: Keep Home Assistant Conversation as the assistant brain

**Status:** Accepted

Home Assistant Conversation remains the core brain for v2.0.0.

### Rationale

The project's value comes from connecting remote phone access to the same Home Assistant assistant context, memory, tools, users, and service execution that power the in-home Elspeth experience.

### Consequences

- The bridge should send user transcript text into Home Assistant Conversation.
- Home Assistant response text should be returned to the voice layer without local TTS generation.
- Provider-specific agent loops should not bypass Home Assistant for house-control decisions unless explicitly designed as safe shortcuts.

## Decision 009: Move toward a full HACS-compliant integration

**Status:** Accepted

The project should evolve toward a **full HACS-compliant Home Assistant custom integration**.

### Rationale

A HACS integration provides a more native Home Assistant installation and configuration experience than an add-on alone. It also allows the project to expose entities, diagnostics, repairs, config flows, options flows, and services in a Home Assistant-native way.

### Consequences

- The target integration should live under:

```text
custom_components/twilio_voice_assistant/
```

- The repository should include HACS metadata such as `hacs.json` when ready.
- The integration should include a valid `manifest.json` with required Home Assistant and HACS fields.
- The project may still need an add-on or external bridge service for public webhook and websocket endpoints.

## Decision 010: Separate integration responsibilities from bridge-service responsibilities

**Status:** Accepted

The HACS integration and the public bridge service should be treated as separate responsibilities, even if they live in the same repository during development.

### Rationale

A Home Assistant custom integration is ideal for configuration, diagnostics, entities, and services. A separately packaged bridge service or add-on may still be the safest place to host public Twilio/ElevenLabs webhook and websocket endpoints.

### Consequences

Possible v2.0.0 deployment shapes:

1. HACS integration plus Home Assistant add-on.
2. HACS integration plus external bridge service.
3. Integration-only mode if safe endpoint exposure is practical.

The safest near-term target is HACS integration plus bridge service/add-on.

## Decision 011: Public routes must be minimal

**Status:** Accepted

Only the routes required by Twilio or ElevenLabs should be exposed publicly.

### Rationale

The bridge needs to be reachable by external call providers, but admin and configuration functions must stay private.

### Consequences

- Do not expose admin pages publicly.
- Do not expose Home Assistant internal APIs publicly.
- Do not expose diagnostics publicly.
- Do not log secrets or PINs.
- Public route documentation must be maintained as part of each bridge mode.

## Decision 012: Add latency instrumentation as a first-class feature

**Status:** Accepted

v2.0.0 should track timing across the complete call path.

### Rationale

The main product goal is a faster, more natural phone-based assistant. Latency needs to be visible in logs and diagnostics so changes can be measured instead of guessed.

### Consequences

Track at least:

- inbound call received
- PIN prompt sent
- PIN accepted
- session created
- voice layer connected
- transcript received
- Home Assistant request started
- Home Assistant response received
- response text sent to voice layer
- call ended

Optional Home Assistant sensors can expose recent latency metrics.

## Decision 013: Avoid writing conversation text to disk in the primary path

**Status:** Accepted

The primary call path should not write transient transcript or response text to disk.

### Rationale

The v2.0.0 bridge should be fast and privacy-conscious. Transient conversation text should move through memory and logs should be configurable.

### Consequences

- Debug logging should avoid full transcript capture by default.
- Diagnostics may include timings and state, but not necessarily full conversation content.
- If transcript logging is added, it should be explicit, configurable, and documented.

## Decision 014: Keep user identity and session authority in the bridge/Home Assistant side

**Status:** Accepted

The bridge should create and own session identity, including caller ID mapping, PIN validation, and mapped Home Assistant user context.

### Rationale

Voice providers should not become the source of truth for house identity, authorization, or service permissions.

### Consequences

- Caller ID, PIN mappings, and Home Assistant user mapping should remain in Home Assistant-controlled configuration.
- Provider metadata may be used as input, but authorization decisions remain local.
- ElevenLabs Agent mode, if used, must receive only the session metadata it needs.

## Open questions

1. What exact Twilio Conversation Relay attributes are required to select ElevenLabs TTS and the desired Elspeth voice?
2. Does the active Twilio account support Conversation Relay and ElevenLabs TTS provider configuration?
3. Can Conversation Relay provide the desired interruption/barge-in behavior?
4. Can ElevenLabs Agent integration preserve Home Assistant as the source of truth for conversation and service execution?
5. Should the HACS integration and add-on remain in the same repository or eventually split?
6. What entities should the HACS integration expose by default?
7. How should secrets be shared between the HACS integration and bridge service/add-on?

## Immediate implementation plan

1. Add `voice_bridge_mode` configuration with `gather` as the default.
2. Add a Conversation Relay websocket endpoint.
3. Build a fixed-response Conversation Relay prototype.
4. Wire transcript text to Home Assistant Conversation.
5. Return Home Assistant response text to Conversation Relay.
6. Validate ElevenLabs TTS configuration through Twilio.
7. Add latency instrumentation.
8. Add initial HACS integration skeleton.
9. Move configuration and diagnostics into the HACS integration.
10. Keep the current add-on as the bridge runtime until an integration-only deployment is proven safe.
