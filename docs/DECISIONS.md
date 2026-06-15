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
- **Superseded**: Decision was valid earlier but has been replaced by a later accepted decision.

## Decision 001: Name the new target architecture v2.0.0

**Status:** Accepted

The next targeted architecture is named **v2.0.0**.

v2.0.0 represents a major architectural change from an audio-processing App to a text-only voice bridge with external STT/TTS.

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

## Decision 006: Preserve the existing local audio prototype path as fallback

**Status:** Superseded by Decision 018

The current local audio prototype-based implementation remained available as a fallback while v2.0.0 was developed. This compatibility decision was superseded after Conversation Relay plus Caller Access passed testing from multiple phones.

Consequences:

- Version `1.4.0` removes local audio prototype, `/process_command`, `/audio/*`, local Whisper, Twilio recording download/transcription, and local generated TTS audio files.
- Conversation Relay is now the only supported bridge mode.

## Decision 007: Keep PIN support as fallback/legacy authentication

**Status:** Accepted

PIN authentication remains supported, but it is no longer the preferred v2 authentication path.

Rationale:

PINs are useful for fallback, unknown caller handling, and compatibility with the existing v1 flow. However, PIN entry adds latency and creates an extra interaction before the assistant can begin.

Known household callers should normally be identified by caller whitelist mapping and routed directly into Conversation Relay.

Consequences:

- PIN fallback is DTMF-only as of version `1.4.0`.
- PIN fallback may be used for unknown callers when `unknown_caller_policy: pin_fallback` is configured.
- Speech-based PIN entry has been removed with the local audio pipeline.
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

The project should remain focused on being a Home Assistant App for the current v2.0.0 hardening work. HACS custom integration work is deferred.

Rationale:

The current runtime is a public webhook/websocket service packaged as a Supervisor App. HACS is for Home Assistant custom integrations under `custom_components/`, not for installing this App service. Keeping both shapes in the same repository creates confusion when adding the GitHub URL as an App repository.

Implementation note:

The earlier HACS skeleton has been removed for now. The repository is structured as a Home Assistant App repository with root `repository.yaml` and the installable App under:

```text
twilio_voice_assistant/
```

HACS integration work can be revisited later if the project needs Home Assistant-native config flows, entities, diagnostics, or options flows separate from the App service.

## Decision 010: Separate integration responsibilities from bridge-service responsibilities

**Status:** Accepted

The HACS integration and the public bridge service should be treated as separate responsibilities, even if they live in the same repository during development.

Possible v2.0.0 deployment shapes:

1. Home Assistant App as the public bridge service.
2. Future HACS integration plus Home Assistant App, if native configuration/entities are needed.
3. Integration-only mode only if safe endpoint exposure is practical.

The safest near-term target is the Home Assistant App as the bridge runtime.

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

Conversation Relay does not invoke local Whisper, Home Assistant TTS, local generated audio files, or `/audio/*` playback. Those legacy paths were removed in version `1.4.0`.

Consequences:

- The websocket handler sends final transcript text to Home Assistant Conversation.
- The websocket handler returns Home Assistant response text to Conversation Relay as text messages.
- Generated speech is delegated to Twilio/ElevenLabs.
- Conversation Relay mode must not write caller audio, generated TTS audio, transient transcripts, or transient response text to disk.
- local audio prototype mode, local recording, transcription, generated audio, and `/audio/*` were removed in version `1.4.0`.

## Decision 016: Prefer caller whitelist authentication for v2

**Status:** Accepted

The preferred v2 authentication path is caller whitelist matching, mapped to Home Assistant users.

```text
Twilio From number
  -> normalize/match against Caller Access records
  -> mapped Home Assistant user
  -> Conversation Relay
```

Rationale:

Known household callers should not need to enter a PIN before every call. Caller whitelist matching reduces call setup latency and allows the bridge to map the session directly to the correct Home Assistant user.

Consequences:

- Add or implement configuration for:

```yaml
auth_mode: caller_whitelist | pin | caller_whitelist_or_pin
unknown_caller_policy: reject | pin_fallback
callers:
  - ha_user_id: 5e738examplehomeassistantuserid
    phone_numbers:
      - "+15551234567"
      - "+15559876543"
    pin: "1234"
```

- Known callers should continue directly to Conversation Relay.
- Unknown callers should either be rejected or sent to PIN fallback, depending on configuration.
- Full caller numbers must not be logged by default.
- Caller ID whitelist matching is convenient but not strong authentication by itself.
- `auth_mode: pin` remains the backward-compatible default for existing App installs.
- Twilio webhook signature validation and Conversation Relay websocket session validation are part of the normal product path.

Implementation note:

The implementation reads Caller Access records from `/share/twilio_voice_assistant/callers.json`. Entries are normalized into a flat phone-number lookup, logs use masked caller context, and Conversation Relay starts only after a whitelist match or successful PIN fallback.

`ha_user_id` is the required stable identity key for Caller Access records. Configured `name` is optional and retained only as a fallback label. When the app matches a known caller or accepts a Caller Access PIN, it resolves the Home Assistant user display name from `ha_user_id`; if lookup fails, it falls back to configured `name`, then `ha_user_id`.

Caller Access admin UI is now the management model for unified caller identity. It writes caller records to `/share/twilio_voice_assistant/callers.json`, stores `ha_user_id` as the stable key, resolves the Home Assistant display name dynamically, masks phone numbers in the UI, and treats PIN values as write-only.

Cleanup direction: the normal product path is Caller Access UI -> Conversation Relay -> Home Assistant Conversation -> ElevenLabs voice. local audio prototype/audio/Whisper/TTS-file handling was removed in version `1.4.0` after additional stable use.

Cleanup phase 2: Version `1.3.13` is the cleanup baseline after Caller Access UI validation. Version `1.3.14` is the lightweight Conversation Relay runtime baseline. The app no longer imports or loads Whisper at startup for the normal Conversation Relay path.

Cleanup phase 3 decision: Conversation Relay plus Caller Access is the product path. local audio prototype and `pin_mode: speech` were hidden legacy fallbacks before they were removed.

## Decision 018: Remove deprecated local audio prototype, speech PIN, Whisper, and local audio files

**Status:** Accepted

Version `1.4.0` makes the App a Conversation Relay-only bridge.

Removed runtime paths:

- `voice_bridge_mode` and the `local audio prototype`/`elevenlabs_agent` bridge selector.
- local audio prototype TwiML command loop.
- `/process_command`.
- `/audio/*`.
- Speech PIN mode.
- Local Whisper dependency/import/model loading.
- Twilio recording download/transcription path.
- Home Assistant TTS-to-file call response generation.
- Local generated audio file handling.

Kept runtime paths:

- Caller Access UI and admin-managed `callers` records.
- DTMF PIN fallback.
- Conversation Relay websocket text bridge.
- Home Assistant Conversation as the assistant brain.
- ElevenLabs through Twilio Conversation Relay.

Consequences:

- Normal public routes are `/incoming_call`, `/check_pin`, `/start_session`, `/conversation_relay`, and `/conversation_relay/status`.
- Startup logs should identify the runtime as Conversation Relay-only and report `local_audio_pipeline: removed`.
- The App version is bumped to `1.4.0` because this removes legacy fallback compatibility.

Startup configuration logging now runs from a FastAPI startup hook instead of module import so deployment logs clearly show the active authentication and bridge configuration when the app starts.

Caller whitelist parsing now supports the preferred multi-number shape, where one Home Assistant user has a `phone_numbers` list. The legacy single-value `phone_number` shape remains supported and is normalized into the same flat lookup table internally.

Caller whitelist management is no longer config-only. The previous broken inline allowed-caller form was not reintroduced; instead, the admin page now has a focused Caller Access section backed by `/share/twilio_voice_assistant/callers.json`. Future Home Assistant-native management can still move into a HACS options flow later, but the App admin page now has a safe migration UI for day-to-day caller access.

The earlier issue was App schema/config visibility, not runtime caller matching. The active App manifest is `twilio_voice_assistant/config.json`. The Caller Access UI avoids writing Home Assistant App options directly; it writes caller records to `/share`.

Validation note:

The repository-installed App starts successfully. The admin UI was restored and is functional again. DTMF PIN authentication works. Caller configuration was tested successfully through the standard App config path. A known caller matched caller identity config, skipped PIN, and entered the conversation flow. An unlisted caller with PIN fallback enabled was prompted for PIN; one wrong PIN was rejected as expected, and one correct PIN was accepted and entered conversation as expected.

Caller Access validation: Caller Access web UI is working. Users were added through the web UI. A call from an allowed number skipped PIN and entered Conversation Relay as expected. A call from an unlisted number fell back to PIN as expected. Correct PIN was accepted and entered Conversation Relay as expected. Unified Caller Access is now the configuration path. Conversation Relay remains the preferred/default voice bridge.

Conversation Relay mode was validated successfully. Conversation Relay uses ElevenLabs successfully with a configured Elspeth voice ID. Conversation Relay sends caller transcript text to Home Assistant Conversation, Home Assistant Conversation returns a response, and the assistant verified something in the house correctly. The call ended correctly through the end-call handling. Conversation Relay latency is much faster than the previous local audio prototype/TTS/audio-file path.

Version `1.3.8` was validated as the stable unified-auth Conversation Relay baseline. Unified `callers` config works. Known caller phone numbers skip PIN and enter Conversation Relay. Unlisted callers fall back to PIN when configured. Wrong PIN is rejected, correct PIN is accepted, Conversation Relay remains the preferred/default voice bridge, and ElevenLabs Elspeth voice is working through Conversation Relay.

Stable v2 baseline:

- Conversation Relay v2 has passed testing from multiple phones.
- Caller whitelist authentication works from multiple allowed callers.
- Unknown caller PIN fallback works.
- Wrong PIN rejection and correct PIN acceptance work.
- Conversation Relay mode works end-to-end.
- Conversation Relay uses ElevenLabs and a configured Elspeth voice ID.
- Conversation Relay sends caller transcript text to Home Assistant Conversation and receives response text successfully.
- The assistant can verify house state through Home Assistant.
- End-call handling works.
- Conversation Relay is much faster than the previous local audio prototype/TTS/audio-file path.
- This version should be treated as the known-good baseline before adding new features.
- Future changes should preserve this path unless explicitly replacing it.
- Version `1.4.0` removes local audio prototype, speech PIN, Whisper, local generated TTS audio files, `/process_command`, and `/audio/*`.
- Conversation Relay is now the preferred voice bridge mode.

The attempted inline allowed-caller management work was reverted/stopped. Caller Access supersedes it with a smaller, isolated UI that manages canonical caller records in `/share/twilio_voice_assistant/callers.json` without modifying Home Assistant App options directly. Future caller management can still move into a HACS options flow or separately designed Home Assistant-native UI.

The next validation focus should be interruption/barge-in behavior and broader production hardening.

HA user IDs should not include angle brackets. Use `5e738...`, not `<5e738...>`.

Conversation Relay TTS settings are separate from Home Assistant TTS settings. Home Assistant TTS engine IDs such as `block_elevenlabs` must not be used as Conversation Relay `ttsProvider` values. The runtime now limits Conversation Relay providers to `ElevenLabs`, `Google`, or `Amazon`, falls back to `ElevenLabs` for invalid values, and omits `voice` when `conversation_relay_voice` is blank or `default`.

## Decision 019: Validate Twilio webhooks and signed Conversation Relay sessions

**Status:** Accepted

Version `1.4.1` added the first security hardening layer around the public Conversation Relay path. Version `1.4.2` makes that secure path the production-default posture.

Accepted behavior:

- `allow_unsigned_twilio_requests_for_dev` defaults to `false`.
- `/incoming_call`, `/check_pin`, and `/start_session` validate `X-Twilio-Signature` using `TWILIO_AUTH_TOKEN`.
- Unsigned Twilio requests are accepted only when the explicit development-only bypass is enabled.
- Validation reconstructs the URL from `PUBLIC_BASE_URL`, request path, and query string, then includes form parameters in the signature check.
- Invalid signatures are rejected with a safe HTTP error.
- After caller whitelist or PIN authentication, the app creates a short-lived signed session token.
- `/start_session` validates the session token before returning Conversation Relay TwiML.
- Conversation Relay TwiML includes the same session token as a custom parameter.
- The websocket `setup` message validates the session token before trusting `user_id` or `user_name`.
- Token values, signatures, auth tokens, PINs, full caller numbers, transcripts, and full Home Assistant responses must not be logged.

Implementation tradeoff:

The session token is stateless and HMAC-signed with a secret derived from `TWILIO_AUTH_TOKEN`. It expires quickly, currently 120 seconds. This is sufficient for phase 1 startup protection and avoids adding token storage, but it does not provide one-time-use replay protection within the short TTL.

Local testing:

Controlled unsigned local tests can temporarily set `allow_unsigned_twilio_requests_for_dev: true`. Public or production-like endpoints should never enable this bypass.

## Decision 020: Make Caller Access the single caller identity and PIN source

**Status:** Accepted

Version `1.4.3` removes the remaining legacy caller identity and PIN migration paths.

Removed paths:

- `allowed_callers` App option and schema entries.
- Runtime parsing/loading of `allowed_callers`.
- Separate legacy PIN-map storage and migration loading.
- Legacy PIN Management UI.
- `/admin/api/pins` routes.

Kept paths:

- Caller Access UI.
- Admin-managed Caller Access records in `/share/twilio_voice_assistant/callers.json`.
- Optional DTMF fallback PIN stored on Caller Access records.
- `auth_mode` and `unknown_caller_policy`.
- Twilio signature validation, signed `/start_session`, and Conversation Relay websocket session validation.

Rationale:

Caller Access has been validated as the working admin model. Keeping `allowed_callers`, the old PIN map, and the legacy PIN UI created multiple places to manage the same identity and fallback behavior. Removing those paths makes the normal product path easier to explain and harder to misconfigure.

Consequences:

- Normal setup uses Caller Access.
- Fallback PINs are write-only in the Caller Access UI and are not displayed after save.
- Unknown caller PIN fallback validates against PINs on Caller Access records.
- If no Caller Access PINs are configured, unknown caller PIN fallback fails safely.
- Existing deployments using `allowed_callers`, App YAML `callers`, or the separate old PIN map need to recreate records in Caller Access.

## Decision 021: Remove App YAML callers

**Status:** Accepted

Version `1.4.4` removes the remaining App YAML `callers` option from `twilio_voice_assistant/config.json`, `run.sh`, and runtime loading.

Rationale:

Caller Access has been validated and is the intended product surface. Keeping a second YAML caller source made identity management harder to explain and could create duplicate or conflicting records.

Consequences:

- Caller Access records in `/share/twilio_voice_assistant/callers.json` are the only caller identity source.
- Fallback PINs are managed only through Caller Access records.
- The admin UI no longer shows read-only config-sourced caller records.
- The App options editor no longer exposes caller identity records.
- Existing deployments that still have YAML `callers` must recreate those records through Caller Access before relying on caller whitelist or PIN fallback behavior.

## Decision 022: Present the current design as the public beta product

**Status:** Accepted

Version `1.4.5` makes the public README describe the current validated product directly instead of presenting the private development migration history.

Current public beta product surface:

- Twilio Conversation Relay only.
- Caller Access UI only for caller identity.
- Optional DTMF PIN fallback.
- Home Assistant Conversation text bridge.
- ElevenLabs through Twilio Conversation Relay.
- Twilio signature validation enabled by default.
- Protected `/start_session`.
- Protected `/conversation_relay` session setup.
- No local STT/TTS audio pipeline.

Rationale:

New beta users are not migrating from the earlier private development paths. Setup documentation should prioritize what to install, expose, configure, and test now. Historical details belong in `CHANGELOG.md` and this decision log.

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

1. Which production Conversation Relay voice IDs should be recommended for new testers, if any?
2. Does the active Twilio account support Conversation Relay and ElevenLabs TTS provider configuration?
3. Can Conversation Relay provide the desired interruption/barge-in behavior?
4. Can ElevenLabs Agent integration preserve Home Assistant as the source of truth for conversation and service execution?
5. Should the HACS integration and App remain in the same repository or eventually split?
6. If HACS work returns later, what entities should the integration expose by default?
7. If HACS work returns later, how should secrets be shared between the integration and bridge service/App?
8. Should the stateless session token gain one-time-use replay protection after the short-TTL hardening proves stable?
9. What is the final storage model for caller whitelist configuration in the App and future HACS integration?
10. What would a future HACS options flow need to manage caller whitelist entries cleanly without overloading the App admin page?

## Immediate implementation plan

1. Add `voice_bridge_mode` configuration with `conversation_relay` as the default. **Done, then removed in `1.4.0` when Conversation Relay became the only bridge.**
2. Add a Conversation Relay websocket endpoint. **Done.**
3. Build a text bridge Conversation Relay prototype. **Done.**
4. Wire transcript text to Home Assistant Conversation. **Done.**
5. Return Home Assistant response text to Conversation Relay. **Done.**
6. Add caller whitelist authentication mapped to Home Assistant users. **Done.**
7. Keep PIN authentication as fallback/legacy behavior. **Done.**
8. Validate Conversation Relay with ElevenLabs TTS configuration through Twilio. **Done.**
9. Add latency instrumentation. **Done for logs; diagnostics remain future work.**
10. Structure the repository as a valid Home Assistant App repository. **Done.**
11. Move configuration and diagnostics into a future HACS integration. **Deferred.**
12. Keep the current App as the bridge runtime until an integration-only deployment is proven safe. **Current approach.**
13. Migrate caller identity to Caller Access and remove legacy `allowed_callers` plus separate PIN UI compatibility. **Done in `1.4.3`.**
