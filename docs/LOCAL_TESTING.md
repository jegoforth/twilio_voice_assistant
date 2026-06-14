# Local Testing Checklist

Use this checklist for the next v2.0.0 validation pass. Keep test logs free of full caller phone numbers, PIN values, full transcripts, and full Home Assistant responses.

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
  - `allowed_callers_count`
- Startup logs do not include full caller phone numbers.

## Authentication

- The active add-on manifest is `twilio_voice_assistant/config.json`.
- The active schema includes `allowed_callers`.
- After schema changes, the add-on version is bumped and Home Assistant shows the new version after repository refresh or reinstall.
- Preferred multi-number caller whitelist configuration works from the standard add-on options/YAML editor:

```yaml
auth_mode: caller_whitelist
unknown_caller_policy: reject
allowed_callers:
  - name: Eric Goforth
    ha_user_id: "<home_assistant_user_id>"
    phone_numbers:
      - "+19013027364"
      - "+1XXXXXXXXXX"
```

- Legacy single-number caller whitelist configuration still works:

```yaml
auth_mode: caller_whitelist
unknown_caller_policy: reject
allowed_callers:
  - name: Eric Goforth
    ha_user_id: "<home_assistant_user_id>"
    phone_number: "+19013027364"
```

- `auth_mode: pin` still prompts for PIN on `/incoming_call`.
- `auth_mode: pin` still reaches `/start_session` after a valid PIN.
- `auth_mode: caller_whitelist` rejects an unknown caller when `unknown_caller_policy: reject`.
- `auth_mode: caller_whitelist_or_pin` sends an unknown caller to PIN fallback when `unknown_caller_policy: pin_fallback`.
- A known caller in `allowed_callers` reaches `/start_session` without PIN.
- A known caller configured with preferred `phone_numbers` reaches `/start_session` from each listed number.
- A known caller configured with legacy `phone_number` still reaches `/start_session`.
- The admin UI does not include allowed-caller management.
- Caller numbers are masked in logs.

## Bridge Modes

- `voice_bridge_mode: gather` remains the default compatibility path.
- Gather mode still records caller commands, processes them through Home Assistant Conversation, and plays generated `/audio/*` responses after authentication.
- `voice_bridge_mode: conversation_relay` returns Conversation Relay TwiML only after caller whitelist match or successful PIN validation.
- Conversation Relay websocket sends final transcript text to Home Assistant Conversation without local TTS generation.
- Conversation Relay mode does not write caller audio, generated TTS audio, transient transcripts, or transient response text to disk.

## Security TODOs Before Production

- Add Twilio webhook signature validation.
- Add Conversation Relay websocket validation.
- Validate Twilio webhook and Conversation Relay websocket signatures before production.
