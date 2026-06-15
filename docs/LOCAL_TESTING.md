# Local Testing Checklist

Use this checklist for the next v2.0.0 validation pass. Keep test logs free of full caller phone numbers, PIN values, full transcripts, and full Home Assistant responses.

## Latest Validated Results

- Version `1.4.0` is the Conversation Relay-only baseline.
- Gather, speech PIN, local Whisper, local generated TTS audio files, `/process_command`, and `/audio/*` have been removed from the runtime.
- Normal public routes are `/incoming_call`, `/check_pin`, `/start_session`, `/conversation_relay`, and `/conversation_relay/status`.
- Caller Access, DTMF PIN fallback, Conversation Relay, Home Assistant Conversation, and ElevenLabs through Twilio Conversation Relay remain the supported product path.
- Version `1.3.13` was the cleanup baseline after Caller Access UI validation.
- Version `1.3.14` is the lightweight Conversation Relay runtime baseline.
- Conversation Relay no longer loads Whisper at startup.
- Whisper has now been removed from the add-on dependencies.
- Conversation Relay avoids local audio files and local STT/TTS processing.
- Speech PIN mode has been removed.
- Caller Access web UI is working.
- All users were removed from `callers` and `allowed_callers` in the add-on configuration tab.
- Users were added through the Caller Access web UI.
- A call from an allowed number skipped PIN and entered Conversation Relay as expected.
- A call from an unlisted number fell back to PIN as expected.
- Correct PIN was accepted and entered Conversation Relay as expected.
- Unified Caller Access is now the preferred configuration path.
- Add-on YAML `callers` remains supported but should be considered advanced/manual configuration.
- Legacy `allowed_callers` remains migration/fallback only.
- Legacy separate PIN management is no longer the preferred path.
- Conversation Relay remains the preferred/default voice bridge.
- Version `1.3.8` is the stable unified-auth Conversation Relay baseline.
- Unified `callers` config works through the standard add-on config path.
- Known caller phone numbers in `callers` skip PIN and enter Conversation Relay.
- Unlisted callers fall back to PIN when configured.
- Wrong PIN is rejected.
- Correct PIN is accepted.
- Conversation Relay remains the preferred/default voice bridge.
- ElevenLabs Elspeth voice is working through Conversation Relay.
- Gather has been removed.
- Caller Access is the preferred admin model for unified `callers` records.
- Caller Access stores `ha_user_id`, resolves the Home Assistant display name dynamically, masks phone numbers, and treats PIN values as write-only.
- Legacy PIN management and `allowed_callers` remain migration/fallback only.
- Stable v2 baseline: this version should be treated as the known-good baseline before adding new features.
- Repository-installed add-on starts successfully.
- Admin UI was restored and is functional again.
- DTMF PIN authentication works.
- Allowed caller configuration works through the standard add-on config path.
- Caller whitelist authentication works from multiple allowed callers.
- Conversation Relay v2 has passed testing from multiple phones.
- A call from an allowed number skips PIN and goes straight into conversation.
- A call from an unlisted number with PIN fallback enabled prompts for PIN.
- One wrong PIN was rejected as expected.
- One correct PIN was accepted and entered conversation as expected.
- Gather compatibility mode was removed after the stable Conversation Relay and Caller Access validation baseline.
- Conversation Relay mode works.
- Conversation Relay uses ElevenLabs successfully.
- Conversation Relay uses the Elspeth ElevenLabs voice ID successfully: `h8eW5xfRUGVJrZhAFxqK`.
- Conversation Relay sends caller transcript text to Home Assistant Conversation.
- Home Assistant Conversation returns a response.
- The assistant verified something in the house correctly.
- The call ended correctly through the end-call handling.
- Conversation Relay latency is much faster than the previous Gather/TTS/audio-file path.
- Future changes should preserve this path unless explicitly replacing it.
- Caller Access replaces the earlier stopped allowed-caller UI attempt with a focused unified caller management section.
- Caller Access is preferred for new caller management; add-on config `callers` remains supported.
- Conversation Relay is now the preferred voice bridge mode.

## Startup

- Add-on starts without configuration parsing errors.
- Logs include one `startup_configuration` timing event.
- `startup_configuration` includes:
  - `auth_mode`
  - `unknown_caller_policy`
  - `voice_bridge: conversation_relay_only`
  - `pin_fallback: dtmf`
  - `conversation_relay_tts_provider`
  - `conversation_relay_transcription_provider`
  - `conversation_relay_language`
  - `conversation_relay_voice_configured`
  - `caller_identities_count`
  - `allowed_callers_count`
  - `local_audio_pipeline: removed`
- Startup logs do not include full caller phone numbers.
- Startup logs state that Conversation Relay-only mode is selected and Gather, speech PIN, Whisper, and local audio-file handling are removed.

## Authentication

- The active add-on manifest is `twilio_voice_assistant/config.json`.
- The active schema includes `callers`.
- The active schema includes `allowed_callers`.
- After schema changes, the add-on version is bumped and Home Assistant shows the new version after repository refresh or reinstall.
- Legacy `allowed_callers[*].name` is optional in the schema so existing options can be saved while migrating to `callers`.
- Normal caller identity setup uses Caller Access in the web UI.
- Advanced/manual unified caller identity configuration still works from the standard add-on options/YAML editor:

```yaml
auth_mode: caller_whitelist
unknown_caller_policy: reject
callers:
  - ha_user_id: 5e738examplehomeassistantuserid
    phone_numbers:
      - "+15551234567"
      - "+15559876543"
    pin: "1234"
```

- Optional `name` remains accepted as a fallback if Home Assistant user display-name lookup fails:

```yaml
callers:
  - name: Eric Goforth
    ha_user_id: 5e738examplehomeassistantuserid
    phone_numbers:
      - "+15551234567"
```

- Legacy migration-only `allowed_callers` single-number caller whitelist configuration still works:

```yaml
auth_mode: caller_whitelist
unknown_caller_policy: reject
allowed_callers:
  - name: Eric Goforth
    ha_user_id: 5e738examplehomeassistantuserid
    phone_number: "+15551234567"
```

- HA user IDs in `callers` or legacy `allowed_callers` should not include angle brackets. Use `5e738...`, not `<5e738...>`.
- `auth_mode: pin` still prompts for PIN on `/incoming_call`.
- `auth_mode: pin` still reaches `/start_session` after a valid PIN.
- `auth_mode: caller_whitelist` rejects an unknown caller when `unknown_caller_policy: reject`.
- `auth_mode: caller_whitelist_or_pin` sends an unknown caller to PIN fallback when `unknown_caller_policy: pin_fallback`.
- A known caller in `callers` reaches `/start_session` without PIN.
- A known caller in `callers` can omit `name`; the greeting uses the Home Assistant user display name when available.
- A known caller configured with preferred `phone_numbers` reaches `/start_session` from each listed number.
- A known caller configured with legacy `phone_number` still reaches `/start_session`.
- Tested successfully: an allowed caller matched the config, skipped PIN, and entered the conversation flow.
- Caller numbers are masked in logs.

## Caller Access Admin UI

- Existing conversation agent dropdown still works.
- Home Assistant TTS engine, language, and voice dropdowns are removed because Conversation Relay uses Twilio Conversation Relay TTS settings.
- Caller Access user dropdown loads Home Assistant users.
- Adding a caller with one phone number works.
- Adding a caller with multiple phone numbers works.
- Adding a caller with a fallback PIN works.
- Existing admin-managed `callers` records show in the Caller Access list.
- Existing add-on config `callers` records show in the Caller Access list as read-only config-sourced records.
- Caller Access is the preferred editable source; add-on config `callers` remains supported during migration but should not be edited in two places long term.
- Saved caller records show Home Assistant display names and masked phone numbers only.
- Saved caller records show only `PIN set` or `No PIN`; PIN values are not displayed.
- Deleting an admin-managed caller access record works.
- Legacy PIN management is collapsed and de-emphasized.
- Legacy `allowed_callers` and old PIN map records continue to work at runtime during migration.
- Normal validation should use Caller Access records, not add-on YAML caller records.

## Normal Test Matrix

- Conversation Relay is the only supported voice bridge.
- Default startup does not load Whisper.
- Caller Access UI loads.
- Existing Caller Access records show.
- Add/delete Caller Access record still works.
- Allowed number skips PIN.
- Unlisted number gets DTMF PIN fallback.
- Wrong PIN is rejected.
- Correct PIN is accepted.
- Conversation Relay still uses ElevenLabs Elspeth voice.
- End-call handling still works.
- `/start_session` returns Conversation Relay TwiML only after caller whitelist match or successful PIN validation.
- Known failure to avoid: `block_elevenlabs` is a Home Assistant TTS engine ID and must not be used as Conversation Relay `ttsProvider`.
- Conversation Relay `ttsProvider` defaults to `ElevenLabs` and is limited to `ElevenLabs`, `Google`, or `Amazon`.
- Conversation Relay `ttsProvider` should be `ElevenLabs`.
- Conversation Relay `voice` should use `h8eW5xfRUGVJrZhAFxqK` for Elspeth.
- Conversation Relay TwiML omits `voice` when `conversation_relay_voice` is blank or `default`.
- Conversation Relay websocket sends final transcript text to Home Assistant Conversation without local TTS generation.
- Conversation Relay mode does not write caller audio, generated TTS audio, transient transcripts, or transient response text to disk.
- Conversation Relay mode does not mount or use `/audio/*`.
- Whisper is not imported, loaded, or installed by the add-on.
- Conversation Relay mode with caller whitelist authentication and ElevenLabs TTS through Twilio Conversation Relay is validated on the active Twilio account.

## Removed Legacy Route Checks

- `/process_command` is no longer a public route.
- `/audio/*` is no longer mounted.
- `voice_bridge_mode` is no longer a normal add-on option.
- `pin_mode` is no longer a normal add-on option; PIN fallback is DTMF only.

## Security TODOs Before Production

- Add Twilio webhook signature validation.
- Add Conversation Relay websocket validation.
- Validate Twilio webhook and Conversation Relay websocket signatures before production.
