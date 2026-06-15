#!/bin/bash

. /app/venv/bin/activate

# Load addon configuration
CONFIG_PATH=/data/options.json

if [ ! -f "$CONFIG_PATH" ] || [ ! -s "$CONFIG_PATH" ]; then
    echo "ERROR: Configuration not set. Please configure the addon in Home Assistant."
    exit 1
fi

# Use Python to parse and export configuration
python3 << 'EOF'
import json
import os
import sys

try:
    with open('/data/options.json', 'r') as f:
        config = json.load(f)
except Exception as e:
    print(f"ERROR: Failed to read configuration: {e}", file=sys.stderr)
    sys.exit(1)

# Export environment variables
os.environ['TWILIO_ACCOUNT_SID'] = str(config.get('twilio_account_sid', '')).strip()
os.environ['TWILIO_AUTH_TOKEN'] = str(config.get('twilio_auth_token', '')).strip()
public_base_url = str(config.get('public_base_url', '')).strip().rstrip('/')
if public_base_url and not public_base_url.startswith(('http://', 'https://')):
    public_base_url = f'https://{public_base_url}'
os.environ['PUBLIC_BASE_URL'] = public_base_url
os.environ['AUTH_MODE'] = str(
    config.get('auth_mode', 'pin')
).strip().lower()
os.environ['UNKNOWN_CALLER_POLICY'] = str(
    config.get('unknown_caller_policy', 'reject')
).strip().lower()
os.environ['CONVERSATION_RELAY_TTS_PROVIDER'] = str(
    config.get('conversation_relay_tts_provider', 'ElevenLabs')
).strip()
os.environ['CONVERSATION_RELAY_VOICE'] = str(
    config.get('conversation_relay_voice', '')
).strip()
os.environ['CONVERSATION_RELAY_TRANSCRIPTION_PROVIDER'] = str(
    config.get('conversation_relay_transcription_provider', 'Deepgram')
).strip()
os.environ['CONVERSATION_RELAY_LANGUAGE'] = str(
    config.get('conversation_relay_language', 'en-US')
).strip()
os.environ['ALLOW_UNSIGNED_TWILIO_REQUESTS_FOR_DEV'] = str(
    config.get('allow_unsigned_twilio_requests_for_dev', False)
).strip().lower()

os.environ['DEBUG'] = str(config.get('debug', False)).lower()

port = config.get('port', 8000)

# Start uvicorn
import subprocess
cmd = [
    'uvicorn',
    'main:app',
    '--host', '0.0.0.0',
    '--port', str(port)
]

print(f"Starting Twilio Voice Assistant add-on on port {port}")
os.execvp('uvicorn', cmd)
EOF
