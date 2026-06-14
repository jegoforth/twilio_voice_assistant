# Twilio Voice Assistant Decisions

This document records accepted decisions, proposed decisions, implementation notes, questions, risks, and challenges for the v2.0.0 target architecture.

## Document usage

`docs/ARCHITECTURE.md` is the stable architecture authority for this project. Codex should read it before implementation work and treat it as guardrails.

This file is the right place for accepted decisions, proposed decisions, implementation notes, open questions, risks, and challenges discovered during development.

Codex may update this file to communicate activity, questions, tradeoffs, and implementation discoveries. Codex should not silently change the architectural direction in `docs/ARCHITECTURE.md`; proposed architectural changes should be recorded here first for review.

Focused activity logs and test notes may live in separate markdown files, such as `docs/TWILIO_DEVELOPMENT.md` or future testing documents.

## Status labels

- **Accepted**: Decision is part of the current target architecture.
- **Proposed**: Decision is likely but still needs validation.
- **Deferred**: Decision is intentionally postponed.
- **Rejected**: Decision was considered and is not part of the target direction.

## Decision 001: Name the new target architecture v2.0.0

**Status:** Accepted

The next targeted architecture is named **v2.0.0**.

v2.0.0 represents a major architectural change from an audio-processing add-on to a text-only voice bridge with external STT/TTS.

## Decision 002: Make the primary bridge text-only

**Status:** Accepted

The v2.0.0 primary path will pass **text only** between the external voice layer and Home Assistant.

```text
External voice layer -> transcript text -> bridge -> Home Assistant Conversation
Home Assistant Conversation -> response text -> bridge -> external voice layer
```

Consequences:

- Local WAV/M4A/MP3 processing should be removed from the primary path.
- Existing audio file paths may remain only as fallback behavior.
- Latency measurement becomes easier because the bridge handles discrete text events.

## Decision 003: Use ElevenLabs for TTS

**Status:** Accepted

v2.0.0 will use **ElevenLabs for text-to-speech**.

Consequences:

- Twilio-only TTS is not the preferred final experience.
- If Twilio Conversation Relay can provide ElevenLabs TTS with the necessary voice control, it is the preferred v2 path.
- If Conversation Relay cannot provide acceptable ElevenLabs TTS behavior, ElevenLabs Agent integration becomes the next candidate.

## Decision 004: Prefer Twilio Conversation Relay first

**Status:** Accepted

The first v2.0.0 prototype should test Twilio Conversation Relay as the text-only bridge layer, with ElevenLabs TTS configured if supported by the active Twilio account/API.

Implementation note:

The first prototype now includes:

- `voice_bridge_mode: conversation_relay`.
- `<Connect><ConversationRelay>` TwiML returned after successful authentication.
- A `/conversation_relay` websocket endpoint.
- Final transcript routing to Home Assistant Conversation.
- Text token responses back to Conversation Relay.
- ElevenLabs TTS provider and voice configuration placeholders.

The remaining validation is account-level Twilio behavior: Conversation Relay availability, ElevenLabs TTS support, valid voice IDs, and language/provider compatibility.

## Decision 005: Treat ElevenLabs Agent integration as the secondary path

**Status:** Proposed

ElevenLabs Agent with Twilio integration should be investigated as a secondary v2.0.0 path.

Consequences:

- ElevenLabs Agents should not replace the Home Assistant conversation brain unless that tradeoff is explicitly accepted later.
- If used, ElevenLabs should call back into the bridge through tools/endpoints that send text to Home Assistant and return text results.
- Session authority, identity, and security should remain in the bridge/Home Assistant side where possible.

## Decision 006: Preserve the existing Gather path as fallback

**Status:** Accepted

The current Gather-based implementation should remain available as a fallback while v2.0.0 is developed.

Consequences:

- `voice_bridge_mode` should support `gather | conversation_relay | elevenlabs_agent`.
- `gather` should be treated as compatibility mode.
- New work should avoid deepening dependency on generated audio files except for fallback support.
- `gather` remains the default `voice_bridge_mode` in add-on configuration.

## Decision 007: Keep PIN support as fallback/legacy authentication

**Status:** Accepted

PIN authentication remains supported, but it is no longer the preferred v2 authentication path.

Rationale:

PINs are useful for fallback, unknown caller handling, and compatibility with the existing v1 flow. However, PIN entry adds latency and creates an extra interaction before the assistant can begin.

Known household callers should normally be identified by caller whitelist mapping and routed directly into the selected bridge mode.

Consequences:

- `pin_mode` may support `dtmf` and `speech`, with DTMF preferred when PIN fallback is used.
- PIN fallback may be used for unknown callers when `unknown_caller_policy: pin_fallback` is configured.
- Speech-based PIN entry can remain as a legacy fallback if needed.
- PIN values must not be logged.

## Decision 008: Keep Home Assistant Conversation as the assistant brain

**Status:** Accepted

Home Assistant Conversation remains the core brain for v2.0.0.

Consequences:

- The bridge should send user transcript text into Home Assistant Conversation.
- Home Assistant response text should be returned to the voice layer without local TTS generation.
- Provider-specific agent loops should not bypass Home Assistant for house-control decisions unless explicitly designed as safe shortcuts.

## Decision 009: Defer HACS custom integration work

**Status:** Deferred

The project should remain focused on being a Home Assistant add-on for the current v2.0.0 hardening work. HACS custom integration work is deferred.

Rationale:

The current runtime is a public webhook/websocket service packaged as a Supervisor add-on. HACS is for Home Assistant custom integrations under `custom_components/`, not for installing this add-on service. Keeping both shapes in the same repository creates confusion when adding the GitHub URL as an add-on repository.

Implementation note:

The earlier HACS skeleton has been removed for now. The repository is structured as a Home Assistant add-on repository with root `repository.yaml` and the installable add-on under:

```text
twilio_voice_assistant/
```

HACS integration work can be revisited later if the project needs Home Assistant-native config flows, entities, diagnostics, or options flows separate from the add-on service.

## Decision 010: Separate integration responsibilities from bridge-service responsibilities

**Status:** Accepted

The HACS integration and the public bridge service should be treated as separate responsibilities, even if they live in the same repository during development.

Possible v2.0.0 deployment shapes:

1. Home Assistant add-on as the public bridge service.
2. Future HACS integration plus Home Assistant add-on, if native configuration/entities are needed.
3. Integration-only mode only if safe endpoint exposure is practical.

The safest near-term target is the Home Assistant add-on as the bridge runtime.

## Decision 011: Public routes must be minimal

**Status:** Accepted

Only the routes required by Twilio or ElevenLabs should be exposed publicly.

Consequences:

- Do not expose admin pages publicly.
- Do not expose Home Assistant internal APIs publicly.
- Do not expose diagnostics publicly.
- Do not log secrets, PINs, full caller numbers, full transcripts, or full Home Assistant responses.
- Public route documentation must be maintained as part of each bridge mode.
- Twilio-side public routes and build instructions are documented in `docs/TWILIO_DEVELOPMENT.md`.

## Decision 012: Add latency instrumentation as a first-class feature

**Status:** Accepted

v2.0.0 should track timing across the complete call path.

Track at least:

- inbound call received
- caller whitelist matched
- unknown caller rejected
- unknown caller PIN fallback selected
- PIN prompt sent, if fallback/legacy PIN auth is used
- PIN accepted, if fallback/legacy PIN auth is used
- session created
- voice layer connected
- transcript received
- Home Assistant request started
- Home Assistant response received
- response text sent to voice layer
- call ended

Implementation note:

The bridge now emits structured `TIMING` logs for the prototype call path. Home Assistant diagnostics and sensors remain future work.

## Decision 013: Avoid writing conversation text to disk in the primary path

**Status:** Accepted

The primary call path should not write transient transcript or response text to disk.

Consequences:

- Debug logging should avoid full transcript capture by default.
- Diagnostics may include timings and state, but not necessarily full conversation content.
- If transcript logging is added, it should be explicit, configurable, and documented.

## Decision 014: Keep user identity and session authority in the bridge/Home Assistant side

**Status:** Accepted

The bridge should create and own session identity, including caller ID mapping, PIN validation, and mapped Home Assistant user context.

Consequences:

- Caller ID, PIN mappings, and Home Assistant user mapping should remain in Home Assistant-controlled configuration.
- Provider metadata may be used as input, but authorization decisions remain local.
- ElevenLabs Agent mode, if used, must receive only the session metadata it needs.

## Decision 015: Keep Conversation Relay mode text-only in the bridge

**Status:** Accepted

Conversation Relay mode should not invoke local Whisper, Home Assistant TTS, local generated audio files, or `/audio/*` playback in the primary path.

Consequences:

- The websocket handler sends final transcript text to Home Assistant Conversation.
- The websocket handler returns Home Assistant response text to Conversation Relay as text messages.
- Generated speech is delegated to Twilio/ElevenLabs.
- Conversation Relay mode must not write caller audio, generated TTS audio, transient transcripts, or transient response text to disk.
- Gather mode may continue using local recording, transcription, generated audio, and `/audio/*` as fallback behavior.

## Decision 016: Prefer caller whitelist authentication for v2

**Status:** Accepted

The preferred v2 authentication path is caller whitelist matching, mapped to Home Assistant users.

```text
Twilio From number
  -> normalize/match against allowed_callers
  -> mapped Home Assistant user
  -> selected voice_bridge_mode
```

Rationale:

Known household callers should not need to enter a PIN before every call. Caller whitelist matching reduces call setup latency and allows the bridge to map the session directly to the correct Home Assistant user.

Consequences:

- Add or implement configuration for:

```yaml
auth_mode: caller_whitelist | pin | caller_whitelist_or_pin
unknown_caller_policy: reject | pin_fallback
allowed_callers:
  - name: Eric Goforth
    phone_numbers:
      - "+19013027364"
      - "+1XXXXXXXXXX"
    ha_user_id: "<home_assistant_user_id>"
```

- Known callers should continue directly to the selected bridge mode.
- Unknown callers should either be rejected or sent to PIN fallback, depending on configuration.
- Full caller numbers must not be logged by default.
- Caller ID whitelist matching is convenient but not strong authentication by itself.
- `auth_mode: pin` remains the backward-compatible default for existing add-on installs.
- Twilio webhook signature validation and Conversation Relay websocket validation remain required before production use.

Implementation note:

The implementation reads `allowed_callers` from the standard Home Assistant add-on configuration path. Entries are normalized into the same flat phone-number lookup, logs use masked caller context, and the selected bridge mode starts only after a whitelist match or successful PIN fallback.

Startup configuration logging now runs from a FastAPI startup hook instead of module import so deployment logs clearly show the active authentication and bridge configuration when the app starts.

Caller whitelist parsing now supports the preferred multi-number shape, where one Home Assistant user has a `phone_numbers` list. The legacy single-value `phone_number` shape remains supported and is normalized into the same flat lookup table internally.

Caller whitelist management is config-based for now. The inline admin UI should not be expanded to manage nested caller records because that couples authentication configuration to a fragile custom form and distracts from the standard add-on options path. Future management belongs in a HACS options flow or in a separately designed UI if the project later needs Home Assistant-native management.

The immediate issue was add-on schema/config visibility, not runtime caller matching. The active add-on manifest is `twilio_voice_assistant/config.json`, and `allowed_callers` is present in that schema. If Home Assistant does not show nested `phone_numbers` cleanly in the visual options editor, use the add-on YAML/options editor with the documented examples instead of adding another inline admin form.

Validation note:

The repository-installed add-on starts successfully. The admin UI was restored and is functional again. DTMF PIN authentication works. Allowed caller configuration was tested successfully through the standard add-on config path. A known caller matched `allowed_callers`, skipped PIN, and entered the conversation flow. An unlisted caller with PIN fallback enabled was prompted for PIN; one wrong PIN was rejected as expected, and one correct PIN was accepted and entered conversation as expected. Gather compatibility mode remains functional.

The attempted inline admin UI caller-management work was reverted/stopped. Allowed caller management remains config-based for now. Do not reintroduce the custom allowed-caller admin UI at this stage. Future caller management should be handled by a HACS options flow or separately designed UI.

Next validation target: Conversation Relay mode with caller whitelist authentication, and ElevenLabs TTS through Twilio Conversation Relay if supported by the active Twilio account.

## Decision 017: Treat ARCHITECTURE.md as the stable guardrail document

**Status:** Accepted

`docs/ARCHITECTURE.md` is the stable, human-owned architecture document. Codex should follow it as guardrails and should not use it as a running development journal.

Rationale:

The architecture file should remain readable, stable, and intentional. Codex needs a place to communicate implementation questions, challenges, discoveries, and testing activity without accidentally turning the architecture document into a noisy work log.

Consequences:

- ChatGPT/Eric own most updates to `docs/ARCHITECTURE.md`.
- Codex should read `docs/ARCHITECTURE.md` before implementation work.
- Codex should record implementation notes, proposed decision changes, and open questions in `docs/DECISIONS.md` unless instructed otherwise.
- Codex may use focused markdown files for testing and operational notes, such as `docs/TWILIO_DEVELOPMENT.md` or future test reports.
- If Codex believes the architecture should change, it should document the proposed change in `docs/DECISIONS.md` first.

## Open questions

1. What exact ElevenLabs voice ID should be used for the desired Elspeth voice in Conversation Relay?
2. Does the active Twilio account support Conversation Relay and ElevenLabs TTS provider configuration?
3. Can Conversation Relay provide the desired interruption/barge-in behavior?
4. Can ElevenLabs Agent integration preserve Home Assistant as the source of truth for conversation and service execution?
5. Should the HACS integration and add-on remain in the same repository or eventually split?
6. If HACS work returns later, what entities should the integration expose by default?
7. If HACS work returns later, how should secrets be shared between the integration and bridge service/add-on?
8. Should Conversation Relay websocket signature validation be implemented before broader testing or before production release?
9. What is the final storage model for caller whitelist configuration in the add-on and future HACS integration?
10. What would a future HACS options flow need to manage caller whitelist entries cleanly without overloading the add-on admin page?

## Immediate implementation plan

1. Add `voice_bridge_mode` configuration with `gather` as the default. **Done.**
2. Add a Conversation Relay websocket endpoint. **Done.**
3. Build a text bridge Conversation Relay prototype. **Done.**
4. Wire transcript text to Home Assistant Conversation. **Done.**
5. Return Home Assistant response text to Conversation Relay. **Done.**
6. Add caller whitelist authentication mapped to Home Assistant users. **Done.**
7. Keep PIN authentication as fallback/legacy behavior. **Done.**
8. Validate Conversation Relay with ElevenLabs TTS configuration through Twilio. **Next.**
9. Add latency instrumentation. **Done for logs; diagnostics remain future work.**
10. Structure the repository as a valid Home Assistant add-on repository. **Done.**
11. Move configuration and diagnostics into a future HACS integration. **Deferred.**
12. Keep the current add-on as the bridge runtime until an integration-only deployment is proven safe. **Current approach.**
