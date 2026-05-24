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
os.environ['ELEVEN_API_KEY'] = str(config.get('eleven_api_key', '')).strip()
os.environ['ELEVEN_VOICE_ID'] = str(config.get('eleven_voice_id', '')).strip()
os.environ['PUBLIC_BASE_URL'] = str(config.get('public_base_url', '')).strip().rstrip('/')

# Handle pin_map - could be dict or JSON string
pin_map = config.get('pin_map', {})
if isinstance(pin_map, str):
    try:
        pin_map = json.loads(pin_map)
    except:
        pin_map = {}
        
os.environ['PIN_MAP_JSON'] = json.dumps(pin_map)
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
