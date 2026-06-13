# Twilio Voice Assistant Architecture

## Version target

This document describes the targeted **v2.0.0 architecture** for the Twilio Voice Assistant project.

The current implementation is a Home Assistant add-on that receives Twilio calls, authenticates the caller, records or receives caller audio, performs speech-to-text, sends text to Home Assistant Conversation, generates text-to-speech audio, and exposes generated audio back to Twilio.

The v2.0.0 target changes the project from an audio-processing add-on into a **text-only voice bridge** between an external phone/speech provider and Home Assistant.

## v2.0.0 goals

1. Use **ElevenLabs for text-to-speech**.
2. Avoid passing, storing, transcoding, or serving WAV, M4A, MP3, or other call audio files from the local network whenever possible.
3. Keep Home Assistant Conversation as the source of truth for the assistant's reasoning, house context, and service execution.
4. Move call audio handling, speech-to-text, and text-to-speech to the external voice layer.
5. Pass only text and session metadata between the external voice layer and the Home Assistant side.
6. Preserve PIN/caller validation, user identity mapping, logging, and safe public exposure boundaries.
7. Evolve the project toward a **full HACS-compliant Home Assistant custom integration**.
8. Keep the current add-on path available as a fallback or compatibility bridge until the v2 integration is complete.

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
- Validating the caller and PIN.
- Creating a conversation session.
- Mapping the caller to a Home Assistant user where possible.
- Receiving transcribed text events.
- Sending text to Home Assistant Conversation.
- Returning response text to the external voice layer.
- Logging latency and session events.
- Enforcing public/private route boundaries.

The bridge should not:

- Store caller audio.
- Generate TTS audio locally.
- Serve generated audio files.
- Require Home Assistant media-source routes for normal operation.

### 3. Home Assistant integration layer

The v2.0.0 long-term goal is a HACS-compliant custom integration that manages configuration and Home Assistant-facing behavior.

Responsible for:

- Config flow setup.
- Options flow for Twilio, ElevenLabs, and conversation bridge settings.
- Conversation agent selection.
- User/PIN/caller identity mapping.
- Diagnostics.
- Repairs and setup validation.
- Services for test calls, session inspection, and optional administrative actions.
- Entities or sensors exposing bridge health, last call status, and latency metrics.

## Preferred v2.0.0 call flow

```text
1. Caller dials Twilio number.
2. Twilio sends inbound call webhook to the public bridge endpoint.
3. Bridge prompts for PIN using DTMF where possible.
4. Bridge validates PIN and maps the session to a Home Assistant user.
5. Bridge returns TwiML that connects the call to the selected external voice layer.
6. External voice layer performs STT and sends transcript text to the bridge.
7. Bridge sends transcript text to Home Assistant Conversation.
8. Home Assistant returns response text.
9. Bridge returns response text to the external voice layer.
10. External voice layer speaks response using ElevenLabs TTS.
11. Bridge logs timing and session state without writing audio to disk.
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

### Mode 2: `conversation_relay`

Preferred first v2.0.0 prototype.

Use for:

- Text-only bridge between Twilio and Home Assistant.
- Twilio-hosted STT.
- ElevenLabs TTS through Twilio Conversation Relay, if supported by the active Twilio configuration.

Expected status in v2.0.0:

- Primary target if ElevenLabs TTS control is sufficient.

### Mode 3: `elevenlabs_agent`

Secondary v2.0.0 experiment.

Use for:

- ElevenLabs-owned realtime voice agent loop.
- Twilio call integration through ElevenLabs.
- Tool calls from ElevenLabs back into the Home Assistant bridge.

Expected status in v2.0.0:

- Candidate if Conversation Relay cannot provide the desired ElevenLabs voice behavior.
- Requires careful design so Home Assistant remains the house-control source of truth.

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

HACS publication requirements should be validated against the current HACS documentation before release. At the time this architecture was written, HACS integration repositories require one integration per repository, required integration files under `custom_components/INTEGRATION_NAME/`, and a `manifest.json` with required keys including `domain`, `documentation`, `issue_tracker`, `codeowners`, `name`, and `version`.

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

Publicly reachable routes should be minimal.

Allowed public routes should be limited to the specific Twilio or ElevenLabs webhook and websocket endpoints required for call operation.

Admin, diagnostics, settings, token inspection, and Home Assistant internal APIs must not be exposed directly to the public internet.

Secrets must remain in Home Assistant storage or add-on configuration and must not be logged.

## Latency instrumentation

v2.0.0 should log timing for each call stage:

- inbound call received
- PIN prompt sent
- PIN accepted
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
voice_bridge_mode: gather | conversation_relay | elevenlabs_agent
conversation_agent: <home_assistant_conversation_agent_id>
tts_provider: elevenlabs
public_base_url: https://assistant.example.com
require_pin: true
pin_mode: dtmf
caller_id_mapping: enabled
latency_logging: true
```

Additional provider-specific fields may be required after validating Twilio Conversation Relay and ElevenLabs Agent APIs.

## Migration strategy

1. Preserve v1.x behavior as `gather` mode.
2. Add `conversation_relay` mode behind a feature flag.
3. Build a minimal text echo prototype.
4. Wire transcript text into Home Assistant Conversation.
5. Return Home Assistant response text to the external voice layer.
6. Add ElevenLabs TTS configuration.
7. Add latency logs and diagnostics.
8. Introduce HACS custom integration structure.
9. Move setup and monitoring into the HACS integration.
10. Keep the add-on or bridge service only for public webhook/websocket duties if still required.

## Release definition for v2.0.0

v2.0.0 should be considered complete when:

- Normal call flow is text-only between the external voice layer and Home Assistant.
- ElevenLabs is used for TTS.
- Local audio file generation is not part of the primary call path.
- Home Assistant Conversation remains the assistant brain.
- The project has a HACS-compliant custom integration structure or a documented migration branch toward that structure.
- The public/private route boundary is documented and enforced.
- Latency metrics are captured for the full conversation path.
- The v1 `gather` path remains available as a fallback or is clearly deprecated with migration instructions.
