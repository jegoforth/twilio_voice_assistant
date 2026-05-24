# Twilio Voice Assistant

Home Assistant add-on that receives Twilio phone calls, authenticates callers with a configured PIN, and sends spoken commands to Home Assistant Assist / Conversation.

## Configuration

- `twilio_account_sid`: Twilio Account SID.
- `twilio_auth_token`: Twilio Auth Token.
- `eleven_api_key`: ElevenLabs API key.
- `eleven_voice_id`: ElevenLabs voice ID.
- `public_base_url`: Public HTTPS base URL Twilio can reach for generated audio, without a trailing slash.
- `debug`: Enables additional logging.

PINs are managed from the add-on web UI at `/admin`.
