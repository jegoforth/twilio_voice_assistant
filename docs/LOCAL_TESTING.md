# Local Testing Checklist

Use this checklist for the next v2.0.0 validation pass. Keep test logs free of full caller phone numbers, PIN values, full transcripts, and full Home Assistant responses.

## Latest Validated Results

- Version `1.3.8` is the stable unified-auth Conversation Relay baseline.
- Unified `callers` config works through the standard add-on config path.
- Known caller phone numbers in `callers` skip PIN and enter Conversation Relay.
- Unlisted callers fall back to PIN when configured.
- Wrong PIN is rejected.
- Correct PIN is accepted.
- Conversation Relay remains the preferred/default voice bridge.
- ElevenLabs Elspeth voice is working through Conversation Relay.
- Gather remains deprecated fallback only.
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
- Gather compatibility mode remains functional.
- Conversation Relay mode works.
- Conversation Relay uses ElevenLabs successfully.
- Conversation Relay uses the Elspeth ElevenLabs voice ID successfully: `h8eW5xfRUGVJrZhAFxqK`.
- Conversation Relay sends caller transcript text to Home Assistant Conversation.
- Home Assistant Conversation returns a response.
- The assistant verified something in the house correctly.
- The call ended correctly through the end-call handling.
- Conversation Relay latency is much faster than the previous Gather/TTS/audio-file path.
- Future changes should preserve this path unless explicitly replacing it.
- Do not reintroduce custom allowed-caller admin UI work yet.
- Allowed caller management remains config-based for now.
- Gather remains fallback compatibility mode.
- Conversation Relay is now the preferred voice bridge mode.

## Startup

- Add-on starts without configuration parsing errors.
- Logs include one `startup_configuration` timing event.
- `startup_configuration` includes:
  - `auth_mode`
  - `unknown_caller_policy`
  - `voice_bridge_mode`
  - `pin_mode`
  - `conversation_relay_tts_provider`
  - `conversation_relay_transcription_provider`
  - `conversation_relay_language`
  - `conversation_relay_voice_configured`
  - `caller_identities_count`
  - `allowed_callers_count`
- Startup logs do not include full caller phone numbers.

## Authentication

- The active add-on manifest is `twilio_voice_assistant/config.json`.
- The active schema includes `callers`.
- The active schema includes `allowed_callers`.
- After schema changes, the add-on version is bumped and Home Assistant shows the new version after repository refresh or reinstall.
- Preferred unified caller identity configuration works from the standard add-on options/YAML editor:

```yaml
auth_mode: caller_whitelist
unknown_caller_policy: reject
callers:
  - name: Eric Goforth
    ha_user_id: 5e738examplehomeassistantuserid
    phone_numbers:
      - "+19013027364"
      - "+1XXXXXXXXXX"
    pin: "1234"
```

- Legacy `allowed_callers` single-number caller whitelist configuration still works:

```yaml
auth_mode: caller_whitelist
unknown_caller_policy: reject
allowed_callers:
  - name: Eric Goforth
    ha_user_id: 5e738examplehomeassistantuserid
    phone_number: "+19013027364"
```

- HA user IDs in `callers` or legacy `allowed_callers` should not include angle brackets. Use `5e738...`, not `<5e738...>`.
- `auth_mode: pin` still prompts for PIN on `/incoming_call`.
- `auth_mode: pin` still reaches `/start_session` after a valid PIN.
- `auth_mode: caller_whitelist` rejects an unknown caller when `unknown_caller_policy: reject`.
- `auth_mode: caller_whitelist_or_pin` sends an unknown caller to PIN fallback when `unknown_caller_policy: pin_fallback`.
- A known caller in `callers` reaches `/start_session` without PIN.
- A known caller configured with preferred `phone_numbers` reaches `/start_session` from each listed number.
- A known caller configured with legacy `phone_number` still reaches `/start_session`.
- Tested successfully: an allowed caller matched the config, skipped PIN, and entered the conversation flow.
- The admin UI does not include allowed-caller management.
- Caller numbers are masked in logs.

## Bridge Modes

- `voice_bridge_mode: conversation_relay` is the default and preferred v2 path.
- `voice_bridge_mode: gather` is deprecated fallback compatibility mode.
- Gather remains fallback compatibility mode.
- Gather mode still records caller commands, processes them through Home Assistant Conversation, and plays generated `/audio/*` responses after authentication.
- Conversation Relay is now the preferred voice bridge mode.
- `voice_bridge_mode: conversation_relay` returns Conversation Relay TwiML only after caller whitelist match or successful PIN validation.
- Known failure to avoid: `block_elevenlabs` is a Home Assistant TTS engine ID and must not be used as Conversation Relay `ttsProvider`.
- Conversation Relay `ttsProvider` defaults to `ElevenLabs` and is limited to `ElevenLabs`, `Google`, or `Amazon`.
- Conversation Relay `ttsProvider` should be `ElevenLabs`.
- Conversation Relay `voice` should use `h8eW5xfRUGVJrZhAFxqK` for Elspeth.
- Conversation Relay TwiML omits `voice` when `conversation_relay_voice` is blank or `default`.
- Conversation Relay websocket sends final transcript text to Home Assistant Conversation without local TTS generation.
- Conversation Relay mode does not write caller audio, generated TTS audio, transient transcripts, or transient response text to disk.
- Conversation Relay mode with caller whitelist authentication and ElevenLabs TTS through Twilio Conversation Relay is validated on the active Twilio account.

## Security TODOs Before Production

- Add Twilio webhook signature validation.
- Add Conversation Relay websocket validation.
- Validate Twilio webhook and Conversation Relay websocket signatures before production.
