# Release Procedure

Every released add-on version must map to one immutable Git commit.

1. Update `twilio_voice_assistant/config.json` and `CHANGELOG.md` in a release PR.
2. Merge the PR and record the resulting commit SHA.
3. Create and push an annotated tag matching the add-on version: `vX.Y.Z`.
4. Verify `git rev-list -n 1 vX.Y.Z` is the intended release commit.
5. Build with `SOURCE_VERSION=X.Y.Z` and `SOURCE_REVISION=<full SHA>` so container environment/logs expose the source identity.
6. Confirm the running add-on reports the expected version and its startup log reports the same source revision.
7. Never reuse a version or move a published tag.

## One-time exception: 2026 history rewrite

When `1.12.0` was released, the repository history was rewritten to remove personal data (names, phone numbers, email addresses) from earlier commits. Every commit ID changed, and every existing tag from `v1.5.0` to `v1.11.0` was moved to the rewritten commit with the same content. Each tag still marks the same release source; only its SHA changed. SHAs recorded before the rewrite no longer resolve. This is the only time published tags have been moved.

## Historical version 1.4.6

Version `1.4.6` appears in `config.json` from the repository's first commit and remains unchanged through current `main`. Production reports `1.4.6`, but no historical tag, source SHA, or build metadata proves which commit was deployed.

`f25bc4179f16939184708a50505d7502c24b16d8` is the best-supported candidate only because it is the current public-beta repository head and its add-on metadata/changelog match the running version. Confidence is low: Home Assistant compares add-on versions, not source contents, and every commit from `781ff56` through `f25bc41` advertises `1.4.6`. Treat the historical deployment as commit-unresolved; do not create a retroactive `v1.4.6` tag without stronger evidence.
