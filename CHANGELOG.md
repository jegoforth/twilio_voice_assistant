# Changelog

## 1.11.0

- Added a `log_call_transcripts` add-on option (default `false`). When
  enabled, each turn of a call -- what the caller said and what Elspeth
  replied -- is printed to the App log prefixed `TRANSCRIPT` and appended
  to `/data/call-transcripts.jsonl` (same rebuild-surviving volume and
  10MB rotation as the 1.10.4 TIMING log), so a test call can be reviewed
  afterward. Off by default because, unlike the metadata-only
  `log_timing()` events, this records real spoken content; meant to be
  switched on for a testing window and back off afterward.

## 1.10.5

- Fixed two wording issues on the public legal pages that the site
  serves for A2P 10DLC/Toll-Free Verification review: every "Contact"
  section said only "the account holder directly" with no actual
  address, and the homepage/privacy/terms pages described Goforth Home
  as "a private, non-commercial household/organization" -- directly
  contradicting the `SOLE_PROPRIETOR` business type declared on the
  TFV application itself. Found live, 2026-09-23, reviewing a TFV
  rejection (error 30489, "Website Must Be Established and Active")
  that Twilio's stated possible causes list as "lack of contact
  information" and "lack of company services". Contact sections now
  link `admin@goforthha.org` directly; the business-type language now
  matches what's on file with Twilio.

## 1.10.4

- Persist TIMING logs to `/data/timing-log.jsonl` (this add-on's own
  dedicated, rebuild-surviving volume), not just stdout. Found live,
  2026-09-18: a performance review asking for "the longest delays over the
  past few days" turned up exactly one real call's worth of data, because
  two unrelated add-on rebuilds in that same window each silently
  discarded docker's log history for the old container. Rotated at 10MB
  (one backup kept) -- a rolling window for performance review, not a
  permanent record.

## 1.10.3

- Raised the timeout waiting for Core's reply over the held Home Assistant
  websocket from 10s to 25s, and stopped treating a plain timeout the same
  as a dropped connection. Diagnosed live, 2026-09-17: a weather question
  (Core doing a live web search, unlike the near-instant deterministic
  ETA/scheduler paths) took longer than 10s, so `HAConnection.request()`
  assumed the connection had died, reconnected, and resent the identical
  question as an independent second request. Core answered both --
  differently, since each was a fresh live search -- but the caller had
  already been told Elspeth was "temporarily unavailable" by the time
  either came back, and neither answer was ever actually spoken to them.
  Only a genuine send/receive failure now reconnects and retries; a
  timeout fails that turn without resending it.

## 1.10.2

- Set `interruptible="speech"` and `interruptSensitivity="low"` on
  `<ConversationRelay>` instead of leaving them unset (Twilio defaults to
  `interruptible="any"` with high sensitivity). Diagnosed live, 2026-09-16:
  a call over car Bluetooth hands-free audio echoed Elspeth's own TTS back
  through the car's weak echo cancellation, and Twilio treated that faint,
  degraded echo as a new caller utterance, derailing the conversation.
  Requiring recognized speech at low sensitivity keeps real callers just as
  responsive while filtering out that kind of self-echo.

## 1.10.1

- Replaced the root path's bare `{"status": "ok"}` JSON response with a
  real HTML homepage for Goforth Home, linking to the legal pages.
  Diagnosed live: Toll-Free Verification rejected the submission with
  "Invalid or Inaccessible Website URL" (error 30473) because the
  submitted BusinessWebsite (this add-on's root) returned a bare JSON
  blob instead of a real webpage. The old JSON health-check response
  moves to `/health`.

## 1.10.0

- Reduced per-turn call latency: the Home Assistant websocket connection
  used to relay each conversational turn to Elspeth Core (`elspeth_local.
  twilio_conversation`) is now held open and reused for the whole call
  (`HAConnection`), instead of reconnecting and re-running the full
  auth_required/auth/auth_ok handshake from scratch on every single
  utterance. The connection auto-reconnects once if it drops mid-call
  (e.g. Home Assistant restarts). One-shot lookups (`find_person_by_phone`,
  the inbound-SMS webhook) are unaffected and still use the original
  one-shot `ha_websocket_request()`.
- Added a configurable `conversation_relay_eot_threshold` option (0.5-0.9,
  default 0.8 -- Twilio's own default, so this changes nothing unless
  explicitly tuned). Controls how confident Twilio needs to be that the
  caller has stopped talking before finalizing their turn; lower values
  trade a small risk of cutting someone off for a faster reply.

## 1.9.4

- Made "Goforth Home" the consistent, primary name across all three legal
  pages (titles, headings, and body copy), with "Elspeth" mentioned only
  as the name of the software Goforth Home operates. Confirmed via the
  Twilio API that the actual registered A2P brand (TrustProduct
  friendly_name) is "Goforth Home" -- every legal page previously led
  with "Elspeth" instead, meaning a reviewer cross-checking the
  registered brand against the campaign's own materials would find no
  match. This was flagged directly by the account holder as a likely
  contributor to repeated CTA-verification rejections.

## 1.9.3

- Reframed `/legal/consent`, `/legal/privacy`, and `/legal/terms` from
  "personal household assistant" language to "private, non-commercial
  organization" language, and updated the consent page's CTA banner to
  list both the original local number and the newly purchased toll-free
  number (+1 833-709-7901), since the household is now pursuing Toll-Free
  Verification as a parallel path alongside the still-failing A2P 10DLC
  campaign.

## 1.9.2

- Added a visible "Text YES to +1 (901) 308-7408..." call-to-action banner
  to the top of `/legal/consent`. Twilio's revise-and-resubmit form
  explicitly asks for a publicly reachable page showing where the opt-in
  CTA is displayed, not just a prose description of the opt-in mechanism
  -- the 1.9.1 rewrite covered the latter but not this.

## 1.9.1

- Rewrote `/legal/consent`, `/legal/privacy`, and `/legal/terms` to describe
  the real text-based opt-in flow (a household member texts START/YES/
  UNSTOP to this number themselves) instead of the verbal-consent script
  they previously documented, which Twilio's CTA verification already
  rejected (error 30909, see 1.9.0). Leaving the old verbal-script wording
  in place would have kept the legal pages contradicting the campaign's
  actual, revised `message_flow`.

## 1.9.0

- Added `/incoming_sms`, the first real inbound-SMS webhook: a household
  member texting YES/START/STOP to the Twilio number now updates their
  own `sms_opted_in` profile value in HA Extended User Management. This
  is the genuine, Twilio-verifiable "Via Text" opt-in flow the A2P 10DLC
  campaign needs -- verbal consent alone did not pass CTA verification
  (error 30909). Twilio's own Advanced Opt-Out still independently
  enforces STOP at the carrier level; this webhook only updates
  Elspeth's own record, which is what lets Elspeth (elspeth-core)
  decide not to attempt a send in the first place. Elspeth's own
  request side can only ever read this field, never write it -- consent
  has to come from the person's own phone, not from anyone else saying
  so on their behalf.

## 1.8.1

- Added `/legal/consent`, documenting the actual verbal consent script
  used to enroll a household member for SMS -- the campaign's first
  A2P 10DLC submission was rejected (error 30909) because TCR couldn't
  verify the described opt-in process from text alone; this page gives
  reviewers something concrete and publicly checkable.

## 1.8.0

- Added `/legal/privacy` and `/legal/terms`, serving a privacy policy and
  terms-and-conditions page for this add-on's SMS-messaging use. Twilio's
  A2P 10DLC campaign registration requires live URLs for both; this
  add-on already has a public domain (`PUBLIC_BASE_URL`) for its own
  webhooks, so these are served from there rather than standing up
  separate hosting.

## 1.5.1

- Fix: an unrecognized caller's opening greeting said "Hello unknown. What
  would you like to do?" -- a leftover placeholder value spoken aloud, and
  a prompt that skipped past the identity ladder entirely. Now opens with
  "Hello, this is Elspeth. To whom am I speaking?" for that case, matching
  the actual voice flow. Known callers are unaffected.

## 1.5.0

- **Requires [HA Extended User Management](https://github.com/jegoforth/ha-extended-user-management)**, a separate Home Assistant integration. Install and configure it (set each household member's `phone_number` and PIN) before this release.
- Caller phone-number matching now goes through HA Extended User
  Management's `find_person_by_phone` service (a `phone_number` profile
  value on the person's own record) instead of this add-on's own
  `callers.json` -- one source of truth shared with the person's PIN.
- Every caller turn is now handed to Elspeth Core via the narrow
  `elspeth_local.twilio_conversation` service (which signs a real per-call
  identity) instead of the generic `conversation/process` REST API, which
  carried no caller identity at all.
- An unrecognized caller is no longer sent to a DTMF PIN prompt
  (`/check_pin`). They are connected straight into Conversation Relay with
  a placeholder identity that resolves to `household_unknown` in Core, and
  Core's own spoken self-declaration + PIN elevation ladder ("To whom am I
  speaking?" / stated name / "Can you verify your PIN?" / spoken PIN)
  handles identity entirely in natural voice -- no DTMF, no PIN stored by
  this add-on at all. Nothing is answered for an unrecognized caller until
  that PIN verifies.
- `AUTH_MODE`, `UNKNOWN_CALLER_POLICY`, `/check_pin`, `prompt_for_pin()`,
  and the Caller Access PIN fields are no longer reached by `/incoming_call`
  and are candidates for removal in a follow-up cleanup pass.

## 1.4.6 - Public Beta Baseline

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
- Speech PIN.
- Local generated TTS audio files.
- YAML caller identity configuration.
- Legacy `allowed_callers`.
- Legacy PIN map.
- Public admin access.
