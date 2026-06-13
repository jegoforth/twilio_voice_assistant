# Twilio Voice Assistant Architecture

## Document role and ownership

This document is the stable architectural north star for the Twilio Voice Assistant project.

It defines the intended v2.0.0 direction, system boundaries, preferred call/authentication flows, and guardrails that implementation work should follow.

Ownership model:

- Eric and ChatGPT own the architecture direction in this file.
- Codex should treat this file as authoritative guardrails before making implementation changes.
- Codex should not use this file as a scratchpad, progress log, or open-question tracker.
- Implementation discoveries, testing notes, uncertainties, and proposed changes should be recorded in `docs/DECISIONS.md` or focused development/testing markdown files.
- If implementation work reveals that this architecture is wrong or incomplete, Codex should document the question or proposed change elsewhere first instead of silently rewriting this file.

Related working documents:

```text
docs/DECISIONS.md              Accepted decisions, proposed decisions, questions, and implementation notes.
docs/TWILIO_DEVELOPMENT.md     Twilio-side setup, provider validation, public routes, and test matrix.
README.md                      User-facing installation and current stable usage notes.
```

## Version target

This document describes the targeted **v2.0.0 architecture** for the Twilio Voice Assistant project.

The current implementation is a Home Assistant add-on that receives Twilio calls, authenticates the caller, records or receives caller audio, performs speech-to-text, sends text to Home Assistant Conversation, generates text-to-speech audio, and exposes generated audio back to Twilio.

The v2.0.0 target changes the project from an audio-processing add-on into a **text-only voice bridge** between an external phone/speech provider and Home Assistant.

## Current implementation status

As of the first v2.0.0 prototype phase, the repository includes:

- `voice_bridge_mode` configuration with `gather` as the default.
- Optional `conversation_relay` and reserved `elevenlabs_agent` bridge modes.
- `pin_mode` configuration with DTMF as the default and the legacy spoken-PIN recording path still available.
- Conversation Relay configuration placeholders for TTS provider, voice, transcription provider, and language.
- A Conversation Relay TwiML path after successful authentication.
- A public `/conversation_relay` websocket endpoint for Conversation Relay events.
- Shared Home Assistant Conversation request handling for Gather and Conversation Relay paths.
- Structured timing logs for inbound calls, PIN flow, bridge selection, Conversation Relay connection, transcript handling, Home Assistant request/response, outbound response text, and call end.
- An initial HACS custom integration skeleton under `custom_components/twilio_voice_assistant/`.
- Twilio-side development notes in `docs/TWILIO_DEVELOPMENT.md`.

The Conversation Relay path is still experimental. It is ready for account-level validation with Twilio Conversation Relay and ElevenLabs TTS, but it should not yet be treated as a production replacement for `gather`.

## v2.0.0 goals

1. Use **ElevenLabs for text-to-speech**.
2. Avoid passing, storing, transcoding, or serving WAV, M4A, MP3, or other call audio files from the local network whenever possible.
3. Keep Home Assistant Conversation as the source of truth for the assistant's reasoning, house context, and service execution.
4. Move call audio handling, speech-to-text, and text-to-speech to the external voice layer.
5. Pass only text and session metadata between the external voice layer and the Home Assistant side.
6. Prefer caller whitelist authentication mapped to Home Assistant users.
7. Preserve PIN authentication as fallback/legacy behavior.
8. Preserve user identity mapping, logging, and safe public exposure boundaries.
9. Evolve the project toward a **full HACS-compliant Home Assistant custom integration**.
10. Keep the current add-on path available as a fallback or compatibility bridge until the v2 integration is complete.

## Architectural principle

Audio should stay outside the Home Assistant network whenever possible.

The Home Assistant side should receive text, send text to the selected Home Assistant conversation agent, receive text back, and return that text to the external voice layer for speech playback.

```text
Caller audio
  -> Twilio / ElevenLabs voice layer
  -> transcribed text
  -> Twilio Voice Assistant v2 bridge
  -> Home Assistant Conversation
  -> response text
  -> Twilio Voice Assistant v2 bridge
  -> Twilio / ElevenLabs TTS
  -> caller hears speech
```

Authentication and authorization should stay under Home Assistant/bridge control. External voice providers may supply caller metadata, but they should not become the source of truth for house identity or permission decisions.

## v1.x architecture summary

The v1.x design is useful and functional, but it carries too much media handling inside the local environment.

```text
Twilio call
  -> Home Assistant add-on webhook
  -> PIN validation
  -> record or receive call audio
  -> local or configured STT
  -> Home Assistant Conversation
  -> configured Home Assistant TTS
  -> generated audio file
  -> public /audio route
  -> Twilio plays generated audio
```

### v1.x drawbacks

- Call audio may need to cross into the local network.
- Generated speech audio must be written, exposed, fetched, and cleaned up.
- Public routes must serve generated media.
- Latency accumulates at each blocking step.
- File permissions, media paths, codec compatibility, and public URL handling become core failure points.
- The app behaves like a media pipeline instead of a conversation bridge.

## v2.0.0 target architecture

The v2.0.0 target separates the system into three layers.

### 1. External voice layer

Responsible for:

- Receiving and maintaining the phone call.
- Providing inbound caller metadata such as Twilio `From`, `To`, and `CallSid`.
- Handling caller audio.
- Performing speech-to-text.
- Performing text-to-speech using ElevenLabs.
- Streaming or playing the spoken response back to the caller.

Preferred candidates:

- Twilio Conversation Relay with ElevenLabs TTS, if it provides the required voice configuration and text-only application protocol.
- ElevenLabs Agents with Twilio integration, if Conversation Relay cannot provide the required ElevenLabs TTS control or latency.

### 2. Twilio Voice Assistant bridge

Responsible for:

- Receiving the inbound Twilio webhook.
- Normalizing and matching caller phone numbers against an allowed caller list.
- Mapping known callers to Home Assistant users.
- Applying unknown caller policy.
- Supporting PIN fallback/legacy authentication when configured.
- Creating a conversation session.
- Receiving transcribed text events.
- Sending text to Home Assistant Conversation.
- Returning response text to the external voice layer.
- Logging latency and session events without leaking secrets or full caller numbers.
- Enforcing public/private route boundaries.

The bridge should not:

- Store caller audio.
- Generate TTS audio locally in the v2 primary path.
- Serve generated audio files in the v2 primary path.
- Require Home Assistant media-source routes for normal Conversation Relay operation.

### 3. Home Assistant integration layer

The v2.0.0 long-term goal is a HACS-compliant custom integration that manages configuration and Home Assistant-facing behavior.

Responsible for:

- Config flow setup.
- Options flow for Twilio, ElevenLabs, and conversation bridge settings.
- Conversation agent selection.
- Caller whitelist configuration.
- User/PIN/caller identity mapping.
- Diagnostics.
- Repairs and setup validation.
- Services for test calls, session inspection, and optional administrative actions.
- Entities or sensors exposing bridge health, last call status, and latency metrics.

## Preferred v2.0.0 authentication model

The preferred v2 authentication path is caller whitelist matching.

```text
Twilio From number
  -> bridge normalizes caller number
  -> bridge matches against allowed_callers
  -> bridge maps caller to Home Assistant user
  -> bridge starts selected voice_bridge_mode
```

PIN authentication remains available as fallback/legacy behavior. It should not be the preferred v2 path because it adds latency and an extra conversational turn before the assistant can begin.

Supported authentication modes should be:

```yaml
auth_mode: caller_whitelist | pin | caller_whitelist_or_pin
unknown_caller_policy: reject | pin_fallback
```

Example allowed caller shape:

```yaml
allowed_callers:
  - name: Eric Goforth
    phone_number: "+19013027364"
    ha_user_id: "<home_assistant_user_id>"
```

Caller ID whitelist matching is convenient for household use, but it is not strong authentication by itself. Caller ID can be spoofed, forwarded, or transformed by carrier behavior. Production-sensitive deployments should use Twilio webhook validation, narrow public routing, careful logging, and optionally PIN fallback or confirmation for high-impact actions.

## Preferred v2.0.0 call flow

Known caller:

```text
1. Caller dials Twilio number.
2. Twilio sends inbound call webhook to the public bridge endpoint.
3. Bridge reads Twilio From number.
4. Bridge normalizes and matches caller against allowed_callers.
5. Bridge maps caller to a Home Assistant user.
6. Bridge starts the selected voice_bridge_mode.
7. External voice layer performs STT and sends transcript text to the bridge.
8. Bridge sends transcript text to Home Assistant Conversation.
9. Home Assistant returns response text.
10. Bridge returns response text to the external voice layer.
11. External voice layer speaks response using ElevenLabs TTS.
12. Bridge logs timing and session state without writing audio to disk.
```

Unknown caller with `unknown_caller_policy: reject`:

```text
1. Caller dials Twilio number.
2. Bridge reads Twilio From number.
3. Bridge finds no allowed caller match.
4. Bridge rejects the call or politely hangs up.
5. No bridge session starts.
```

Unknown caller with `unknown_caller_policy: pin_fallback`:

```text
1. Caller dials Twilio number.
2. Bridge reads Twilio From number.
3. Bridge finds no allowed caller match.
4. Bridge prompts for PIN.
5. Bridge validates PIN and maps the session to a Home Assistant user.
6. Bridge starts the selected voice_bridge_mode.
```

## Candidate implementation modes

### Mode 1: `gather`

Compatibility mode based on the current implementation.

Use for:

- Fallback.
- Regression testing.
- Environments where Conversation Relay or ElevenLabs Agent routing is unavailable.

Expected status in v2.0.0:

- Supported as legacy fallback.
- Not the preferred architecture.
- Current default mode to avoid breaking existing deployments.

### Mode 2: `conversation_relay`

Preferred first v2.0.0 prototype.

Use for:

- Text-only bridge between Twilio and Home Assistant.
- Twilio-hosted STT.
- ElevenLabs TTS through Twilio Conversation Relay, if supported by the active Twilio configuration.

Expected status in v2.0.0:

- Primary target if ElevenLabs TTS control is sufficient.
- Current experimental prototype behind `voice_bridge_mode: conversation_relay`.
- Uses Twilio-hosted STT and returns text token messages for Twilio/ElevenLabs TTS.
- Does not generate local TTS audio or write caller audio in the Conversation Relay path.

### Mode 3: `elevenlabs_agent`

Secondary v2.0.0 experiment.

Use for:

- ElevenLabs-owned realtime voice agent loop.
- Twilio call integration through ElevenLabs.
- Tool calls from ElevenLabs back into the Home Assistant bridge.

Expected status in v2.0.0:

- Candidate if Conversation Relay cannot provide the desired ElevenLabs voice behavior.
- Requires careful design so Home Assistant remains the house-control source of truth.
- Current mode value is reserved, but the call path is not implemented yet.

## HACS-compliant integration goal

v2.0.0 should move the user-facing Home Assistant install experience toward a HACS-compatible custom integration.

The target repository shape should include a single integration under:

```text
custom_components/twilio_voice_assistant/
```

Expected integration files include:

```text
custom_components/twilio_voice_assistant/__init__.py
custom_components/twilio_voice_assistant/manifest.json
custom_components/twilio_voice_assistant/config_flow.py
custom_components/twilio_voice_assistant/const.py
custom_components/twilio_voice_assistant/coordinator.py
custom_components/twilio_voice_assistant/diagnostics.py
custom_components/twilio_voice_assistant/repairs.py
custom_components/twilio_voice_assistant/sensor.py
custom_components/twilio_voice_assistant/services.yaml
custom_components/twilio_voice_assistant/translations/en.json
README.md
hacs.json
```

The current skeleton includes:

```text
custom_components/twilio_voice_assistant/__init__.py
custom_components/twilio_voice_assistant/manifest.json
custom_components/twilio_voice_assistant/config_flow.py
custom_components/twilio_voice_assistant/const.py
hacs.json
```

HACS publication requirements should be validated against the current HACS documentation before release. HACS integration repositories require one integration per repository, required integration files under `custom_components/INTEGRATION_NAME/`, and a `manifest.json` with required keys including `domain`, `documentation`, `issue_tracker`, `codeowners`, `name`, and `version`.

## Add-on versus integration split

A HACS custom integration cannot directly replace every role of a Home Assistant add-on. The integration runs inside Home Assistant, while an add-on is useful for a separately packaged web service.

The v2.0.0 goal is therefore:

```text
HACS custom integration
  -> Home Assistant configuration, entities, diagnostics, services, and user-facing setup

Optional bridge service / add-on
  -> public webhook and websocket endpoints when needed
```

Possible deployment models:

1. **Integration plus existing add-on**: HACS integration configures and monitors the add-on.
2. **Integration plus external bridge service**: HACS integration talks to a separately hosted bridge.
3. **Integration-only where feasible**: only if Home Assistant can safely and reliably expose the required endpoints without violating security or maintainability goals.

The safest near-term design is integration plus add-on/service, with the integration becoming the user's primary configuration surface.

## Security boundaries

Publicly reachable routes should be minimal and mode-aware.

Common public Twilio routes:

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

Admin, diagnostics, settings, token inspection, and Home Assistant internal APIs must not be exposed directly to the public internet.

Secrets, PINs, full caller numbers, full transcripts, and full Home Assistant responses must not be logged by default.

Twilio webhook signature validation and Conversation Relay websocket validation are required before production use.

## Latency instrumentation

v2.0.0 should log timing for each call stage:

- inbound call received
- caller whitelist matched
- unknown caller rejected
- unknown caller PIN fallback selected
- PIN prompt sent, if PIN fallback/legacy auth is used
- PIN accepted, if PIN fallback/legacy auth is used
- bridge session created
- external voice layer connected
- transcript text received
- Home Assistant request started
- Home Assistant response received
- response text returned to voice layer
- call ended

These timings should be exposed through diagnostics and optionally through Home Assistant sensors.

## Configuration targets

Likely v2.0.0 configuration fields:

```yaml
auth_mode: caller_whitelist | pin | caller_whitelist_or_pin
unknown_caller_policy: reject | pin_fallback
allowed_callers:
  - name: Eric Goforth
    phone_number: "+19013027364"
    ha_user_id: "<home_assistant_user_id>"
voice_bridge_mode: gather | conversation_relay | elevenlabs_agent
conversation_agent: <home_assistant_conversation_agent_id>
tts_provider: elevenlabs
public_base_url: https://assistant.example.com
pin_mode: dtmf | speech
latency_logging: true
```

Implemented add-on configuration fields:

```yaml
voice_bridge_mode: gather | conversation_relay | elevenlabs_agent
conversation_relay_tts_provider: ElevenLabs
conversation_relay_voice: ""
conversation_relay_transcription_provider: Deepgram
conversation_relay_language: en-US
pin_mode: dtmf | speech
```

Caller whitelist configuration is documented as the preferred v2 target and should be implemented next. Additional provider-specific fields may be required after validating Twilio Conversation Relay and ElevenLabs Agent APIs.

## Migration strategy

1. Preserve v1.x behavior as `gather` mode. **Implemented.**
2. Add `conversation_relay` mode behind a feature flag. **Implemented.**
3. Build a minimal text bridge prototype. **Implemented for final transcript to Home Assistant response.**
4. Wire transcript text into Home Assistant Conversation. **Implemented.**
5. Return Home Assistant response text to the external voice layer. **Implemented through Conversation Relay text messages.**
6. Add ElevenLabs TTS configuration. **Implemented as Conversation Relay provider/voice configuration placeholders.**
7. Add latency logs and diagnostics. **Timing logs implemented; diagnostics remain future work.**
8. Introduce HACS custom integration structure. **Initial skeleton implemented.**
9. Add caller whitelist authentication mapped to Home Assistant users. **Next.**
10. Validate Conversation Relay and ElevenLabs behavior against the active Twilio account. **Next after auth hardening.**
11. Move setup and monitoring into the HACS integration. **Future.**
12. Keep the add-on or bridge service only for public webhook/websocket duties if still required. **Future.**

## Release definition for v2.0.0

v2.0.0 should be considered complete when:

- Normal call flow is text-only between the external voice layer and Home Assistant.
- ElevenLabs is used for TTS.
- Local audio file generation is not part of the primary call path.
- Home Assistant Conversation remains the assistant brain.
- Caller whitelist authentication can map known callers to Home Assistant users.
- PIN authentication remains available as fallback/legacy behavior.
- The project has a HACS-compliant custom integration structure or a documented migration branch toward that structure.
- The public/private route boundary is documented and enforced.
- Latency metrics are captured for the full conversation path.
- The v1 `gather` path remains available as a fallback or is clearly deprecated with migration instructions.
