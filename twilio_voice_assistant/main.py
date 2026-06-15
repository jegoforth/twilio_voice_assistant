from fastapi import FastAPI, Form, Request, WebSocket, WebSocketDisconnect
from fastapi import HTTPException
from fastapi.responses import Response, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import requests
import httpx
import websockets
import asyncio
import re
import os
import json
import traceback
import time
from contextlib import asynccontextmanager
from html import escape
from urllib.parse import quote

DATA_DIR = "/share/twilio_voice_assistant"
AUDIO_DIR = f"{DATA_DIR}/audio"
INGRESS_PROXY_IP = "172.30.32.2"
WHISPER_MODEL = None

# Load configuration from environment
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
SUPERVISOR_TOKEN = os.getenv("SUPERVISOR_TOKEN")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
if PUBLIC_BASE_URL and not PUBLIC_BASE_URL.startswith(("http://", "https://")):
    PUBLIC_BASE_URL = f"https://{PUBLIC_BASE_URL}"
DEBUG = os.getenv("DEBUG", "false").lower() == "true"
AUTH_MODE = os.getenv("AUTH_MODE", "pin").strip().lower()
UNKNOWN_CALLER_POLICY = os.getenv("UNKNOWN_CALLER_POLICY", "reject").strip().lower()
CALLERS_JSON = os.getenv("CALLERS_JSON", "[]")
ALLOWED_CALLERS_JSON = os.getenv("ALLOWED_CALLERS_JSON", "[]")
VOICE_BRIDGE_MODE = os.getenv("VOICE_BRIDGE_MODE", "conversation_relay").strip().lower()
CONVERSATION_RELAY_TTS_PROVIDER = os.getenv(
    "CONVERSATION_RELAY_TTS_PROVIDER", "ElevenLabs"
).strip()
CONVERSATION_RELAY_VOICE = os.getenv("CONVERSATION_RELAY_VOICE", "").strip()
CONVERSATION_RELAY_TRANSCRIPTION_PROVIDER = os.getenv(
    "CONVERSATION_RELAY_TRANSCRIPTION_PROVIDER", "Deepgram"
).strip()
CONVERSATION_RELAY_LANGUAGE = os.getenv(
    "CONVERSATION_RELAY_LANGUAGE", "en-US"
).strip()
PIN_MODE = os.getenv("PIN_MODE", "dtmf").strip().lower()

SUPPORTED_AUTH_MODES = {
    "caller_whitelist",
    "pin",
    "caller_whitelist_or_pin",
}
if AUTH_MODE not in SUPPORTED_AUTH_MODES:
    print(
        "WARNING: Unsupported auth_mode "
        f"{AUTH_MODE!r}; falling back to 'pin'"
    )
    AUTH_MODE = "pin"

SUPPORTED_UNKNOWN_CALLER_POLICIES = {
    "reject",
    "pin_fallback",
}
if UNKNOWN_CALLER_POLICY not in SUPPORTED_UNKNOWN_CALLER_POLICIES:
    print(
        "WARNING: Unsupported unknown_caller_policy "
        f"{UNKNOWN_CALLER_POLICY!r}; falling back to 'reject'"
    )
    UNKNOWN_CALLER_POLICY = "reject"

SUPPORTED_VOICE_BRIDGE_MODES = {
    "gather",
    "conversation_relay",
    "elevenlabs_agent",
}
if VOICE_BRIDGE_MODE not in SUPPORTED_VOICE_BRIDGE_MODES:
    print(
        "WARNING: Unsupported voice_bridge_mode "
        f"{VOICE_BRIDGE_MODE!r}; falling back to 'conversation_relay'"
    )
    VOICE_BRIDGE_MODE = "conversation_relay"

SUPPORTED_CONVERSATION_RELAY_TTS_PROVIDERS = {
    "ElevenLabs",
    "Google",
    "Amazon",
}
if CONVERSATION_RELAY_TTS_PROVIDER not in SUPPORTED_CONVERSATION_RELAY_TTS_PROVIDERS:
    print(
        "WARNING: Unsupported conversation_relay_tts_provider "
        f"{CONVERSATION_RELAY_TTS_PROVIDER!r}; falling back to 'ElevenLabs'"
    )
    CONVERSATION_RELAY_TTS_PROVIDER = "ElevenLabs"

if CONVERSATION_RELAY_VOICE.lower() == "default":
    CONVERSATION_RELAY_VOICE = ""

# Validate required credentials
required_vars = {
    "TWILIO_ACCOUNT_SID": TWILIO_ACCOUNT_SID,
    "TWILIO_AUTH_TOKEN": TWILIO_AUTH_TOKEN,
    "PUBLIC_BASE_URL": PUBLIC_BASE_URL,
}

missing_vars = [k for k, v in required_vars.items() if not v]
if missing_vars:
    raise RuntimeError(
        f"Missing required configuration: {', '.join(missing_vars)}. "
        "Please configure the addon with your API credentials."
    )

# Current Caller Access storage. This is the preferred admin-managed identity path.
SETTINGS_FILE = f"{DATA_DIR}/settings.json"
CALLERS_FILE = f"{DATA_DIR}/callers.json"

# Legacy PIN map storage remains for migration and unknown-caller fallback.
PIN_MAP_FILE = f"{DATA_DIR}/pin_map.json"


def log_timing(event: str, **fields):
    """Emit structured timing logs without secrets, PINs, or transcript text."""
    payload = {
        "event": event,
        "ts": round(time.time(), 3),
        **fields,
    }
    print("TIMING " + json.dumps(payload, sort_keys=True))


def debug_log(event: str, **fields):
    if DEBUG:
        print("DEBUG " + json.dumps({"event": event, **fields}, sort_keys=True))


def get_whisper_model():
    """Lazy-load Whisper only for deprecated Gather or speech-PIN paths."""
    global WHISPER_MODEL
    if WHISPER_MODEL is None:
        print(
            "WARNING: Loading local Whisper model for deprecated "
            "Gather/speech-PIN audio transcription path."
        )
        import whisper

        WHISPER_MODEL = whisper.load_model("tiny")
    return WHISPER_MODEL


def mask_phone_number(phone_number: str | None) -> str:
    """Mask caller numbers in logs while preserving enough context to debug."""
    if not phone_number:
        return "unknown"
    digits = re.sub(r"\D", "", phone_number)
    if len(digits) <= 4:
        return "****"
    return f"+***{digits[-4:]}"


def normalize_phone_number(phone_number: str | None) -> str | None:
    """Normalize common Twilio caller IDs to E.164 where possible."""
    if not phone_number:
        return None
    stripped = phone_number.strip()
    digits = re.sub(r"\D", "", stripped)
    if not digits:
        return None
    if stripped.startswith("+"):
        return f"+{digits}"
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    return f"+{digits}"


def caller_phone_numbers(caller: dict) -> list[str]:
    """Return legacy and preferred caller numbers without logging them."""
    raw_numbers = []
    legacy_number = caller.get("phone_number")
    if legacy_number:
        raw_numbers.append(legacy_number)

    phone_numbers = caller.get("phone_numbers") or []
    if isinstance(phone_numbers, str):
        raw_numbers.append(phone_numbers)
    elif isinstance(phone_numbers, list):
        raw_numbers.extend(phone_numbers)

    return [number for number in raw_numbers if isinstance(number, str)]


def load_json_list(value: str, config_name: str) -> list:
    try:
        parsed = json.loads(value)
    except Exception as e:
        print(f"WARNING: Could not parse {config_name}: {e}")
        return []

    if not isinstance(parsed, list):
        print(f"WARNING: {config_name} must be a list; ignoring configured value")
        return []
    return parsed


def normalize_ha_user_id(user_id: str | None, config_name: str) -> str:
    normalized = (user_id or "").strip()
    if normalized.startswith("<") and normalized.endswith(">"):
        print(
            f"WARNING: {config_name} ha_user_id should not include angle brackets; "
            "stripping them"
        )
        normalized = normalized[1:-1].strip()
    return normalized


def normalize_callers_for_lookup(callers, config_name: str):
    """Parse caller identity records into a flat phone-number lookup."""
    if not isinstance(callers, list):
        print(f"WARNING: {config_name} must be a list; ignoring configured value")
        return [], 0

    normalized_callers = []
    valid_caller_records = 0
    for caller in callers:
        if not isinstance(caller, dict):
            continue
        ha_user_id = normalize_ha_user_id(caller.get("ha_user_id"), config_name)
        if not ha_user_id:
            continue
        caller_name = (caller.get("name") or "").strip()
        normalized_numbers = {
            normalized_number
            for raw_number in caller_phone_numbers(caller)
            if (normalized_number := normalize_phone_number(raw_number))
        }
        if not normalized_numbers:
            continue

        valid_caller_records += 1
        for normalized_number in normalized_numbers:
            normalized_callers.append({
                "name": caller_name,
                "phone_number": normalized_number,
                "ha_user_id": ha_user_id,
            })
    return normalized_callers, valid_caller_records


def load_config_caller_identity_records():
    return load_json_list(CALLERS_JSON, "callers")


def load_admin_caller_identity_records():
    try:
        if os.path.exists(CALLERS_FILE):
            with open(CALLERS_FILE, "r") as f:
                callers = json.load(f)
            if isinstance(callers, list):
                return callers
            print("WARNING: callers admin storage must be a list; ignoring saved value")
    except Exception as e:
        print(f"WARNING: Could not load callers admin storage: {e}")
    return []


def save_admin_caller_identity_records(callers):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(CALLERS_FILE, "w") as f:
            json.dump(callers, f, indent=2)
        return True
    except Exception as e:
        print(f"ERROR: Could not save callers admin storage: {e}")
        return False


def load_caller_identity_records():
    return load_config_caller_identity_records() + load_admin_caller_identity_records()


def load_legacy_allowed_caller_records():
    # Legacy add-on YAML shape. Keep parsing for migration compatibility only.
    return load_json_list(ALLOWED_CALLERS_JSON, "allowed_callers")


def load_allowed_callers():
    """Build the effective caller whitelist from canonical and legacy config."""
    caller_records = load_caller_identity_records()
    legacy_records = load_legacy_allowed_caller_records()
    callers, caller_count = normalize_callers_for_lookup(caller_records, "callers")
    legacy_callers, legacy_count = normalize_callers_for_lookup(
        legacy_records,
        "allowed_callers",
    )
    return callers + legacy_callers, caller_count + legacy_count


def load_caller_identity_pin_map():
    pin_map = {}
    for caller in load_caller_identity_records():
        if not isinstance(caller, dict):
            continue
        pin = str(caller.get("pin", "")).strip()
        if not pin:
            continue
        if not pin.isdigit() or len(pin) != 4:
            print("WARNING: callers PIN entries must be 4 digits; ignoring one entry")
            continue
        ha_user_id = normalize_ha_user_id(caller.get("ha_user_id"), "callers")
        if not ha_user_id:
            continue
        caller_name = (caller.get("name") or "").strip()
        pin_map[pin] = {
            "user_id": ha_user_id,
            "user_name": caller_name,
        }
    return pin_map


def load_effective_pin_map():
    """Use legacy/admin PINs plus canonical caller PINs during migration."""
    pin_map = load_pin_map()
    pin_map.update(load_caller_identity_pin_map())
    return pin_map


ALLOWED_CALLERS, ALLOWED_CALLER_RECORD_COUNT = load_allowed_callers()
CALLER_IDENTITY_RECORD_COUNT = len(load_caller_identity_records())


def find_allowed_caller(from_number: str | None):
    global ALLOWED_CALLERS, ALLOWED_CALLER_RECORD_COUNT, CALLER_IDENTITY_RECORD_COUNT
    ALLOWED_CALLERS, ALLOWED_CALLER_RECORD_COUNT = load_allowed_callers()
    CALLER_IDENTITY_RECORD_COUNT = len(load_caller_identity_records())
    normalized_from = normalize_phone_number(from_number)
    if not normalized_from:
        return None, normalized_from
    for caller in ALLOWED_CALLERS:
        if caller["phone_number"] == normalized_from:
            return caller, normalized_from
    return None, normalized_from


def log_startup_configuration():
    log_timing(
        "startup_configuration",
        auth_mode=AUTH_MODE,
        unknown_caller_policy=UNKNOWN_CALLER_POLICY,
        voice_bridge_mode=VOICE_BRIDGE_MODE,
        pin_mode=PIN_MODE,
        conversation_relay_tts_provider=CONVERSATION_RELAY_TTS_PROVIDER,
        conversation_relay_transcription_provider=(
            CONVERSATION_RELAY_TRANSCRIPTION_PROVIDER
        ),
        conversation_relay_language=CONVERSATION_RELAY_LANGUAGE,
        conversation_relay_voice_configured=bool(CONVERSATION_RELAY_VOICE),
        caller_identities_count=CALLER_IDENTITY_RECORD_COUNT,
        allowed_callers_count=ALLOWED_CALLER_RECORD_COUNT,
        local_whisper_loaded=False,
        local_audio_route_enabled=VOICE_BRIDGE_MODE == "gather",
    )
    if VOICE_BRIDGE_MODE == "gather":
        print(
            "WARNING: voice_bridge_mode=gather is hidden legacy fallback behavior "
            "and may be removed in a future cleanup; "
            "conversation_relay is the preferred v2 voice bridge. "
            "Local Whisper/audio pipeline will be lazy-loaded on first use."
        )
    else:
        print(
            "INFO: Conversation Relay mode selected; local Whisper/audio pipeline "
            "is not initialized at startup."
        )
    if PIN_MODE == "speech":
        print(
            "WARNING: pin_mode=speech is hidden legacy fallback behavior and may "
            "be removed in a future cleanup; it will lazy-load local Whisper if "
            "PIN fallback is used."
        )
    print("TODO: Add Twilio webhook signature validation before production use.")
    print("TODO: Add Conversation Relay websocket validation before production use.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    log_startup_configuration()
    yield


app = FastAPI(lifespan=lifespan)
if VOICE_BRIDGE_MODE == "gather":
    # Deprecated Gather fallback audio route. The Conversation Relay path does
    # not generate or serve local audio files.
    os.makedirs(AUDIO_DIR, exist_ok=True)
    app.mount("/audio", StaticFiles(directory=AUDIO_DIR), name="audio")


# Request models for current Caller Access plus legacy PIN/admin settings.
class PinRequest(BaseModel):
    pin: str
    user_id: str
    user_name: str | None = None


class CallerAccessRequest(BaseModel):
    ha_user_id: str
    phone_numbers: list[str]
    pin: str | None = None


class SettingsRequest(BaseModel):
    conversation_agent_id: str | None = None
    tts_engine_id: str | None = None
    tts_language: str | None = None
    tts_voice: str | None = None


def require_ingress(request: Request):
    """Restrict admin UI/API to Home Assistant Ingress."""
    client_host = request.client.host if request.client else ""
    has_ingress_header = (
        request.headers.get("x-ingress-path")
        or request.headers.get("x-supervisor-ingress")
    )
    if client_host == INGRESS_PROXY_IP and has_ingress_header:
        return
    raise HTTPException(status_code=404)


def load_pin_map():
    """Load PIN map from JSON file"""
    try:
        migrate_legacy_file("/share/pin_map.json", PIN_MAP_FILE)
        if os.path.exists(PIN_MAP_FILE):
            with open(PIN_MAP_FILE, "r") as f:
                return json.load(f)
    except Exception as e:
        print(f"WARNING: Could not load PIN map from file: {e}")
    return {}


def load_settings():
    """Load admin settings from JSON file."""
    defaults = {
        "conversation_agent_id": "",
        "tts_engine_id": "",
        "tts_language": "",
        "tts_voice": "",
    }
    try:
        migrate_legacy_file("/share/twilio_voice_assistant_settings.json", SETTINGS_FILE)
        if os.path.exists(SETTINGS_FILE):
            with open(SETTINGS_FILE, "r") as f:
                settings = json.load(f)
            return {**defaults, **settings}
    except Exception as e:
        print(f"WARNING: Could not load settings from file: {e}")
    return defaults


def migrate_legacy_file(old_path, new_path):
    if os.path.exists(new_path) or not os.path.exists(old_path):
        return
    try:
        os.makedirs(os.path.dirname(new_path), exist_ok=True)
        with open(old_path, "r") as src:
            data = json.load(src)
        with open(new_path, "w") as dest:
            json.dump(data, dest, indent=2)
        os.remove(old_path)
        print(f"Migrated {old_path} to {new_path}")
    except Exception as e:
        print(f"WARNING: Could not migrate {old_path}: {e}")


def save_settings(settings):
    """Save admin settings to JSON file."""
    try:
        os.makedirs("/share", exist_ok=True)
        with open(SETTINGS_FILE, "w") as f:
            json.dump(settings, f, indent=2)
        return True
    except Exception as e:
        print(f"ERROR: Could not save settings: {e}")
        return False


def save_pin_map(pin_map):
    """Save PIN map to JSON file"""
    try:
        os.makedirs("/share", exist_ok=True)
        with open(PIN_MAP_FILE, "w") as f:
            json.dump(pin_map, f, indent=2)
        return True
    except Exception as e:
        print(f"ERROR: Could not save PIN map: {e}")
        return False


def caller_record_display(record: dict, index: int | None, source: str, user_map: dict):
    user_id = normalize_ha_user_id(record.get("ha_user_id"), source)
    configured_name = (record.get("name") or "").strip()
    display_name = user_map.get(user_id) or configured_name or user_id
    normalized_numbers = [
        normalized
        for raw_number in caller_phone_numbers(record)
        if (normalized := normalize_phone_number(raw_number))
    ]
    return {
        "index": index,
        "source": source,
        "ha_user_id": user_id,
        "display_name": display_name,
        "masked_phone_numbers": [
            mask_phone_number(number)
            for number in normalized_numbers
        ],
        "pin_set": bool(str(record.get("pin", "")).strip()),
        "deletable": source == "admin",
    }


def normalize_caller_access_request(caller_request: CallerAccessRequest):
    user_id = normalize_ha_user_id(caller_request.ha_user_id, "callers")
    if not user_id:
        return None, "Home Assistant user is required"

    normalized_numbers = []
    for phone_number in caller_request.phone_numbers or []:
        normalized_number = normalize_phone_number(phone_number)
        if normalized_number and normalized_number not in normalized_numbers:
            normalized_numbers.append(normalized_number)

    if not normalized_numbers:
        return None, "At least one phone number is required"

    pin = str(caller_request.pin or "").strip()
    if pin and (not pin.isdigit() or len(pin) != 4):
        return None, "Fallback PIN must be exactly 4 digits"

    record = {
        "ha_user_id": user_id,
        "phone_numbers": normalized_numbers,
    }
    if pin:
        record["pin"] = pin
    return record, None


@asynccontextmanager
async def websocket_connect(url: str):
    """Connect to Home Assistant websocket across supported websockets versions."""
    headers = {"Authorization": f"Bearer {SUPERVISOR_TOKEN}"}
    try:
        async with websockets.connect(url, additional_headers=headers) as websocket:
            yield websocket
    except TypeError:
        async with websockets.connect(url, extra_headers=headers) as websocket:
            yield websocket


async def websocket_recv_json(websocket):
    return json.loads(await asyncio.wait_for(websocket.recv(), timeout=10))


async def ha_websocket_request(message):
    """Send one request to Home Assistant's websocket API."""
    if not SUPERVISOR_TOKEN:
        return None, "SUPERVISOR_TOKEN is not set"

    endpoints_to_try = [
        "ws://supervisor/core/websocket",
        "ws://supervisor/core/api/websocket",
        "ws://homeassistant:8123/api/websocket",
        "ws://localhost:8123/api/websocket",
    ]

    last_error = None
    for endpoint in endpoints_to_try:
        try:
            async with websocket_connect(endpoint) as websocket:
                auth_required = await websocket_recv_json(websocket)
                if auth_required.get("type") != "auth_required":
                    last_error = (
                        f"{endpoint}: expected auth_required, got {auth_required}"
                    )
                    continue

                await websocket.send(json.dumps({
                    "type": "auth",
                    "access_token": SUPERVISOR_TOKEN,
                }))
                auth_response = await websocket_recv_json(websocket)
                if auth_response.get("type") != "auth_ok":
                    last_error = f"{endpoint}: authentication failed: {auth_response}"
                    continue

                payload = {"id": 1, **message}
                await websocket.send(json.dumps(payload))
                response = await websocket_recv_json(websocket)
                if not response.get("success"):
                    last_error = f"{endpoint}: request failed: {response}"
                    continue

                return response.get("result"), None
        except Exception as e:
            last_error = f"{endpoint}: {e}"
            print(f"Error sending websocket request to {endpoint}: {e}")

    return None, last_error or "Could not call Home Assistant websocket API"


async def fetch_ha_users():
    """Fetch Home Assistant users via the admin-only websocket auth API."""
    users, error = await ha_websocket_request({"type": "config/auth/list"})
    if error:
        return [], error

    users = users or []
    filtered_users = [
        {
            "id": user.get("id"),
            "name": user.get("name") or user.get("username") or user.get("id"),
        }
        for user in users
        if user.get("id") and not user.get("system_generated", False)
    ]
    print(f"Found {len(filtered_users)} users")
    return filtered_users, None


async def fetch_conversation_agents():
    """Fetch available Home Assistant conversation agents."""
    result, error = await ha_websocket_request({"type": "conversation/agent/list"})
    if error:
        return [], error

    raw_agents = result.get("agents", result) if isinstance(result, dict) else result
    agents = []
    for agent in raw_agents:
        if isinstance(agent, str):
            agents.append({"id": agent, "name": agent})
            continue
        if not isinstance(agent, dict):
            continue
        agent_id = agent.get("id") or agent.get("agent_id")
        if agent_id:
            agents.append({
                "id": agent_id,
                "name": agent.get("name") or agent_id,
            })
    return agents, None


async def fetch_tts_engines():
    """Fetch available Home Assistant TTS engines."""
    result, error = await ha_websocket_request({"type": "tts/engine/list"})
    if error:
        return [], error

    providers = result.get("providers", []) if isinstance(result, dict) else []
    engines = []
    for provider in providers:
        if not isinstance(provider, dict):
            continue
        engine_id = provider.get("engine_id")
        if engine_id and not provider.get("deprecated", False):
            engines.append({
                "id": engine_id,
                "name": provider.get("name") or engine_id,
                "supported_languages": provider.get("supported_languages") or [],
            })
    return engines, None


async def fetch_tts_voices(engine_id: str, language: str):
    """Fetch available voices for a Home Assistant TTS engine and language."""
    if not engine_id or not language:
        return [], None

    result, error = await ha_websocket_request({
        "type": "tts/engine/voices",
        "engine_id": engine_id,
        "language": language,
    })
    if error:
        return [], error

    raw_voices = result.get("voices", []) if isinstance(result, dict) else []
    voices = []
    for voice in raw_voices:
        if isinstance(voice, str):
            voices.append({"id": voice, "name": voice})
        elif isinstance(voice, dict):
            voice_id = voice.get("voice_id") or voice.get("id") or voice.get("name")
            if voice_id:
                voices.append({
                    "id": voice_id,
                    "name": voice.get("name") or voice_id,
                })
    return voices, None


def pin_entry_user_id(pin_entry):
    if isinstance(pin_entry, dict):
        return pin_entry.get("user_id")
    return pin_entry


def pin_entry_user_name(pin_entry, user_map):
    user_id = pin_entry_user_id(pin_entry)
    resolved_user_name = user_map.get(user_id)
    if resolved_user_name:
        return resolved_user_name

    if isinstance(pin_entry, dict):
        user_name = pin_entry.get("user_name") or pin_entry.get("name")
        if user_name:
            return user_name

    return user_id


async def resolve_ha_user_display_name(
    user_id: str,
    configured_name: str | None = None,
) -> str:
    users, error = await fetch_ha_users()
    if error:
        print(f"Could not resolve Home Assistant user display name: {error}")
        return configured_name or user_id

    user_map = {user["id"]: user["name"] for user in users}
    return user_map.get(user_id) or configured_name or user_id


PIN_MAP = load_pin_map()

if DEBUG:
    print(f"Loaded PIN_MAP with {len(PIN_MAP)} entries")
    print(f"Loaded settings: {load_settings()}")


def twiml_response(xml: str):
    return Response(content=xml.strip(), media_type="application/xml")


def public_audio_url(filename: str) -> str:
    # Deprecated Gather fallback only. Conversation Relay never serves local audio.
    return f"{PUBLIC_BASE_URL}/audio/{filename}"


def public_websocket_url(path: str) -> str:
    base = PUBLIC_BASE_URL
    if base.startswith("https://"):
        base = "wss://" + base[len("https://"):]
    elif base.startswith("http://"):
        base = "ws://" + base[len("http://"):]
    return f"{base}{path}"


def safe_redirect_to_session(user_id: str, user_name: str | None, message: str):
    encoded_user_id = quote(user_id)
    encoded_user_name = quote(user_name or user_id)
    return twiml_response(f"""
    <Response>
        <Say>{message}</Say>
        <Redirect>/start_session?user_id={encoded_user_id}&amp;user_name={encoded_user_name}</Redirect>
    </Response>
    """)


def polite_hangup(message: str = "Goodbye."):
    return twiml_response(f"""
    <Response>
        <Say>{message}</Say>
        <Hangup/>
    </Response>
    """)


def prompt_for_pin(call_sid: str | None = None):
    # Legacy/fallback auth path. Caller Access should normally bypass this for known callers.
    if PIN_MODE == "dtmf":
        log_timing("pin_prompt_sent", call_sid=call_sid, pin_mode="dtmf")
        return twiml_response("""
        <Response>
            <Gather input="dtmf" numDigits="4" timeout="10" action="/check_pin" method="POST">
                <Say>Please enter your four digit PIN.</Say>
            </Gather>
            <Say>I did not receive a PIN. Goodbye.</Say>
            <Hangup/>
        </Response>
        """)

    log_timing("pin_prompt_sent", call_sid=call_sid, pin_mode="speech")
    return twiml_response("""
    <Response>
        <Say>Please say your four digit PIN slowly, one digit at a time, after the beep.</Say>
        <Pause length="1"/>
        <Record
            maxLength="5"
            timeout="4"
            action="/check_pin"
            playBeep="true"
            trim="do-not-trim"
        />
    </Response>
    """)


def redirect_to_start_session(user_id: str, user_name: str | None):
    encoded_user_id = quote(user_id)
    encoded_user_name = quote(user_name or user_id)
    return twiml_response(f"""
    <Response>
        <Redirect>/start_session?user_id={encoded_user_id}&amp;user_name={encoded_user_name}</Redirect>
    </Response>
    """)


def record_command_twiml(encoded_user_id: str, encoded_user_name: str) -> str:
    # Deprecated Gather fallback command loop.
    return f"""
        <Record
            maxLength="10"
            timeout="5"
            action="/process_command?user_id={encoded_user_id}&amp;user_name={encoded_user_name}"
            playBeep="true"
            trim="do-not-trim"
        />
        <Say>I did not hear anything. Goodbye.</Say>
        <Hangup/>
    """


def conversation_relay_twiml(user_id: str, user_name: str) -> str:
    # Preferred v2 path: text-only bridge with Twilio Conversation Relay.
    websocket_url = public_websocket_url("/conversation_relay")
    attrs = {
        "url": websocket_url,
        "welcomeGreeting": f"Hello {user_name}. What would you like to do?",
        "language": CONVERSATION_RELAY_LANGUAGE,
        "ttsProvider": CONVERSATION_RELAY_TTS_PROVIDER,
        "transcriptionProvider": CONVERSATION_RELAY_TRANSCRIPTION_PROVIDER,
    }
    if CONVERSATION_RELAY_VOICE:
        attrs["voice"] = CONVERSATION_RELAY_VOICE

    attr_text = " ".join(
        f'{name}="{escape(value, quote=True)}"'
        for name, value in attrs.items()
        if value
    )
    conversation_id = f"twilio_{user_id}"

    return f"""
    <Response>
        <Connect action="/conversation_relay/status">
            <ConversationRelay {attr_text}>
                <Parameter name="user_id" value="{escape(user_id, quote=True)}"/>
                <Parameter name="user_name" value="{escape(user_name, quote=True)}"/>
                <Parameter name="conversation_id" value="{escape(conversation_id, quote=True)}"/>
            </ConversationRelay>
        </Connect>
    </Response>
    """


def extension_from_content_type(content_type: str | None) -> str:
    if not content_type:
        return "mp3"
    if "wav" in content_type:
        return "wav"
    if "mpeg" in content_type or "mp3" in content_type:
        return "mp3"
    if "ogg" in content_type:
        return "ogg"
    return "mp3"


async def resolve_tts_settings(settings):
    """Fill missing TTS settings for deprecated Gather fallback audio."""
    resolved = dict(settings)
    engines, error = await fetch_tts_engines()
    if error:
        print(f"Could not fetch TTS engines: {error}")
        return resolved

    selected_engine = None
    if resolved.get("tts_engine_id"):
        selected_engine = next(
            (engine for engine in engines if engine["id"] == resolved["tts_engine_id"]),
            None,
        )
    if selected_engine is None and engines:
        selected_engine = engines[0]
        resolved["tts_engine_id"] = selected_engine["id"]

    if not resolved.get("tts_language") and selected_engine:
        languages = selected_engine.get("supported_languages") or []
        if isinstance(languages, list) and languages:
            resolved["tts_language"] = languages[0]
        else:
            resolved["tts_language"] = "en-US"

    return resolved


async def tts_to_audio(text: str, filename: str) -> str:
    # Deprecated Gather fallback only. Conversation Relay returns text to Twilio.
    settings = await resolve_tts_settings(load_settings())
    engine_id = settings.get("tts_engine_id")
    language = settings.get("tts_language")
    voice = settings.get("tts_voice")

    if not engine_id:
        raise RuntimeError("No Home Assistant TTS engine is configured or available.")

    payload = {
        "engine_id": engine_id,
        "message": text,
        "cache": True,
    }
    if language:
        payload["language"] = language
    if voice:
        payload["options"] = {"voice": voice}

    response = requests.post(
        "http://supervisor/core/api/tts_get_url",
        headers={
            "Authorization": f"Bearer {SUPERVISOR_TOKEN}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=30,
    )

    print("HA TTS status:", response.status_code)
    print("HA TTS body:", response.text[:500])

    if response.status_code < 200 or response.status_code >= 300:
        raise RuntimeError(f"Home Assistant TTS returned {response.status_code}")

    tts_response = response.json()
    tts_path = tts_response.get("path")
    if not tts_path:
        raise RuntimeError(f"Home Assistant TTS response missing path: {tts_response}")

    audio_url = f"http://supervisor/core{tts_path}"
    audio_response = requests.get(
        audio_url,
        headers={"Authorization": f"Bearer {SUPERVISOR_TOKEN}"},
        timeout=30,
    )

    if audio_response.status_code < 200 or audio_response.status_code >= 300:
        raise RuntimeError(
            f"Could not download Home Assistant TTS audio: {audio_response.status_code}"
        )

    extension = extension_from_content_type(audio_response.headers.get("content-type"))
    safe_filename = re.sub(r"[^a-zA-Z0-9_.-]", "_", filename)
    os.makedirs(AUDIO_DIR, exist_ok=True)
    output_path = f"{AUDIO_DIR}/{safe_filename}.{extension}"

    with open(output_path, "wb") as f:
        f.write(audio_response.content)

    return output_path


def normalize_digits(text: str) -> str:
    text = text.lower()

    replacements = {
        "zero": "0",
        "oh": "0",
        "one": "1",
        "won": "1",
        "two": "2",
        "too": "2",
        "to": "2",
        "three": "3",
        "four": "4",
        "for": "4",
        "five": "5",
        "six": "6",
        "seven": "7",
        "eight": "8",
        "ate": "8",
        "nine": "9",
    }

    for word, digit in replacements.items():
        text = re.sub(rf"\b{word}\b", digit, text)

    return re.sub(r"[^0-9]", "", text)


def normalize_command(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s']", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def is_end_call_phrase(text: str) -> bool:
    command = normalize_command(text)
    if not command:
        return False

    exact_phrases = {
        "bye",
        "bye bye",
        "goodbye",
        "good bye",
        "hang up",
        "end call",
        "end the call",
        "disconnect",
        "that's all",
        "that is all",
        "that's all i needed",
        "that is all i needed",
        "that's all i need",
        "that is all i need",
        "i'm done",
        "i am done",
        "all done",
        "no thank you",
        "no thanks",
    }
    if command in exact_phrases:
        return True

    starts_with_phrases = (
        "goodbye ",
        "good bye ",
        "hang up ",
        "end the call ",
        "that's all ",
        "that is all ",
        "i'm done ",
        "i am done ",
    )
    return command.startswith(starts_with_phrases)


async def send_to_home_assistant_conversation(
    text: str,
    user_id: str,
    conversation_id: str | None = None,
):
    """Send text to Home Assistant Conversation and return spoken reply text."""
    ha_url = "http://supervisor/core/api/conversation/process"
    headers = {
        "Authorization": f"Bearer {SUPERVISOR_TOKEN}",
        "Content-Type": "application/json",
    }

    settings = load_settings()
    payload = {
        "text": text,
        "conversation_id": conversation_id or f"twilio_{user_id}",
        "language": "en",
    }
    if settings.get("conversation_agent_id"):
        payload["agent_id"] = settings["conversation_agent_id"]

    log_timing(
        "home_assistant_conversation_request_started",
        user_id=user_id,
        conversation_id=payload["conversation_id"],
        text_length=len(text),
    )
    debug_log(
        "home_assistant_conversation_request",
        user_id=user_id,
        conversation_id=payload["conversation_id"],
        has_agent_id=bool(payload.get("agent_id")),
        text_length=len(text),
    )

    async with httpx.AsyncClient(timeout=30) as client:
        ha_raw = await client.post(
            ha_url,
            json=payload,
            headers=headers,
        )

    log_timing(
        "home_assistant_conversation_response_received",
        user_id=user_id,
        conversation_id=payload["conversation_id"],
        status_code=ha_raw.status_code,
    )
    debug_log(
        "home_assistant_conversation_response",
        status_code=ha_raw.status_code,
        content_type=ha_raw.headers.get("content-type"),
    )

    if ha_raw.status_code < 200 or ha_raw.status_code >= 300:
        raise RuntimeError(f"Home Assistant returned {ha_raw.status_code}")

    try:
        ha_response = ha_raw.json()
    except Exception as exc:
        raise RuntimeError("Home Assistant did not return valid JSON") from exc

    reply = (
        ha_response
        .get("response", {})
        .get("speech", {})
        .get("plain", {})
        .get("speech")
    )

    if not reply:
        debug_log("home_assistant_unexpected_response_shape")
        reply = "I heard you, but Home Assistant did not provide a spoken response."

    return reply


async def handle_pin_digits(digits: str, call_sid: str | None = None):
    global PIN_MAP
    PIN_MAP = load_effective_pin_map()
    digits = normalize_digits(digits)

    if DEBUG:
        print(f"PIN received with {len(digits)} digits")

    pin_entry = PIN_MAP.get(digits)
    if pin_entry:
        users, error = await fetch_ha_users()
        if error:
            print(f"Could not resolve user name after PIN auth: {error}")

        user_map = {user["id"]: user["name"] for user in users}
        user_id = pin_entry_user_id(pin_entry)
        user_name = pin_entry_user_name(pin_entry, user_map)
        encoded_user_id = quote(user_id)
        encoded_user_name = quote(user_name)
        log_timing(
            "pin_accepted",
            call_sid=call_sid,
            user_id=user_id,
            bridge_mode=VOICE_BRIDGE_MODE,
        )
        return twiml_response(f"""
        <Response>
            <Say>Thank you. Connecting you now.</Say>
            <Redirect>/start_session?user_id={encoded_user_id}&amp;user_name={encoded_user_name}</Redirect>
        </Response>
        """)

    return twiml_response("""
    <Response>
        <Say>Incorrect PIN. Please try again.</Say>
        <Redirect>/incoming_call</Redirect>
    </Response>
    """)


def download_twilio_recording(recording_url: str, audio_path: str) -> bool:
    # Deprecated local recording path for Gather command audio and speech PIN.
    # Conversation Relay does not download caller recordings.
    try:
        audio_url = recording_url + ".mp3"
        print(f"Downloading Twilio recording: {audio_url}")

        r = requests.get(
            audio_url,
            auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN),
            timeout=20,
        )

        print("Twilio recording status:", r.status_code)
        print("Twilio recording content-type:", r.headers.get("content-type"))

        if r.status_code != 200:
            print("Twilio recording download failed:", r.text[:500])
            return False

        with open(audio_path, "wb") as f:
            f.write(r.content)

        return True

    except Exception:
        print("Exception downloading Twilio recording:")
        traceback.print_exc()
        return False


# Admin UI endpoints
@app.get("/")
async def root():
    return RedirectResponse(url="/admin")


@app.get("/admin", response_class=HTMLResponse)
async def admin_ui(request: Request):
    """Serve the admin UI"""
    require_ingress(request)
    ingress_path = request.headers.get("x-ingress-path", "").rstrip("/")
    admin_api_base = f"{ingress_path}/admin/api" if ingress_path else "/admin/api"
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Twilio Voice Assistant - Admin</title>
        <style>
            * { margin: 0; padding: 0; box-sizing: border-box; }
            body {
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                min-height: 100vh;
                padding: 20px;
            }
            .container {
                max-width: 600px;
                margin: 0 auto;
                background: white;
                border-radius: 8px;
                box-shadow: 0 10px 40px rgba(0, 0, 0, 0.2);
                padding: 30px;
            }
            h1 {
                color: #333;
                margin-bottom: 30px;
                font-size: 28px;
            }
            .form-section {
                margin-bottom: 30px;
                padding: 20px;
                background: #f8f9fa;
                border-radius: 6px;
            }
            .form-group {
                margin-bottom: 15px;
            }
            label {
                display: block;
                margin-bottom: 5px;
                font-weight: 600;
                color: #555;
                font-size: 14px;
            }
            input[type="text"], select, textarea {
                width: 100%;
                padding: 10px;
                border: 1px solid #ddd;
                border-radius: 4px;
                font-size: 14px;
                font-family: inherit;
            }
            textarea {
                min-height: 86px;
                resize: vertical;
            }
            input[type="text"]:focus, select:focus, textarea:focus {
                outline: none;
                border-color: #667eea;
                box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1);
            }
            button {
                background: #667eea;
                color: white;
                padding: 10px 20px;
                border: none;
                border-radius: 4px;
                font-size: 14px;
                font-weight: 600;
                cursor: pointer;
                transition: background 0.2s;
            }
            button:hover {
                background: #5568d3;
            }
            button.delete {
                background: #dc3545;
                padding: 6px 12px;
                font-size: 12px;
            }
            button.delete:hover {
                background: #c82333;
            }
            .pins-list {
                margin-top: 20px;
            }
            .pin-item {
                display: flex;
                justify-content: space-between;
                align-items: center;
                padding: 12px;
                background: white;
                border: 1px solid #ddd;
                border-radius: 4px;
                margin-bottom: 10px;
            }
            .pin-item strong {
                color: #667eea;
                min-width: 80px;
            }
            .pin-info {
                flex: 1;
            }
            .pin-user {
                color: #666;
            }
            .muted {
                color: #777;
                font-size: 13px;
            }
            .badge {
                display: inline-block;
                padding: 2px 8px;
                border-radius: 12px;
                background: #e9ecef;
                color: #555;
                font-size: 12px;
                margin-right: 6px;
            }
            details summary {
                cursor: pointer;
                font-weight: 700;
                color: #333;
                margin-bottom: 15px;
            }
            .status {
                padding: 8px 12px;
                border-radius: 4px;
                font-size: 12px;
                margin-top: 10px;
                display: none;
            }
            .status.success {
                background: #d4edda;
                color: #155724;
                display: block;
            }
            .status.error {
                background: #f8d7da;
                color: #721c24;
                display: block;
            }
            .loading {
                opacity: 0.6;
                cursor: wait;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Twilio Voice Assistant</h1>

            <div class="form-section">
                <h2 style="font-size: 18px; margin-bottom: 15px; color: #333;">Assistant Settings</h2>
                <div class="form-group">
                    <label for="conversationAgent">Conversation Agent:</label>
                    <select id="conversationAgent">
                        <option value="">-- Loading agents --</option>
                    </select>
                </div>
                <div class="form-group">
                    <label for="ttsEngine">TTS Engine:</label>
                    <select id="ttsEngine" onchange="onTtsEngineChanged()">
                        <option value="">-- Loading TTS engines --</option>
                    </select>
                </div>
                <div class="form-group">
                    <label for="ttsLanguage">TTS Language:</label>
                    <select id="ttsLanguage" onchange="loadVoices()">
                        <option value="">-- Select TTS engine first --</option>
                    </select>
                </div>
                <div class="form-group">
                    <label for="ttsVoice">TTS Voice:</label>
                    <select id="ttsVoice">
                        <option value="">Default voice</option>
                    </select>
                </div>
                <button onclick="saveSettings()">Save Settings</button>
                <div class="status" id="settingsStatus"></div>
            </div>
            
            <div class="form-section">
                <h2 style="font-size: 18px; margin-bottom: 15px; color: #333;">Caller Access</h2>
                <p class="muted" style="margin-bottom: 15px;">Preferred path for known callers. Add new or editable users here. Add-on config caller records are still honored during migration and appear below as read-only Config records.</p>
                <div class="form-group">
                    <label for="callerUser">Home Assistant User:</label>
                    <select id="callerUser">
                        <option value="">-- Loading users --</option>
                    </select>
                </div>
                <div class="form-group">
                    <label for="callerPhoneNumbers">Phone Numbers (one per line, E.164 preferred):</label>
                    <textarea id="callerPhoneNumbers" placeholder="+15551234567&#10;+15559876543"></textarea>
                </div>
                <div class="form-group">
                    <label for="callerPin">Fallback PIN (optional, 4 digits):</label>
                    <input type="text" id="callerPin" placeholder="1234" maxlength="4" pattern="[0-9]*">
                </div>
                <button onclick="addCallerAccess()">Add Caller Access</button>
                <div class="status" id="callerAccessStatus"></div>
                <div class="pins-list">
                    <h3 style="font-size: 16px; margin: 20px 0 15px; color: #333;">Current Caller Access</h3>
                    <div id="callerAccessList">
                        <p style="color: #999; text-align: center; padding: 20px;">Loading...</p>
                    </div>
                </div>
            </div>

            <details class="form-section">
                <summary>Legacy PIN Management</summary>
                <p class="muted" style="margin-bottom: 15px;">Migration fallback only. Prefer Caller Access for new entries.</p>
                <div class="form-group">
                    <label for="pin">PIN (4 digits):</label>
                    <input type="text" id="pin" placeholder="1234" maxlength="4" pattern="[0-9]*">
                </div>
                <div class="form-group">
                    <label for="user">Home Assistant User:</label>
                    <select id="user">
                        <option value="">-- Loading users --</option>
                    </select>
                </div>
                <button onclick="addPin()">Add PIN</button>
                <div class="status" id="status"></div>
                <p class="muted" style="margin-top: 15px;">Saved legacy PIN values are not displayed. Use Caller Access for new or editable access records.</p>
            </details>
        </div>

        <script>
            const ADMIN_API_BASE = __ADMIN_API_BASE__;
            let appSettings = {};
            let ttsEngines = [];
            let haUsers = [];

            async function loadSettings() {
                try {
                    const res = await fetch(`${ADMIN_API_BASE}/settings`);
                    appSettings = await res.json();
                    await Promise.all([loadConversationAgents(), loadTtsEngines()]);
                } catch (err) {
                    console.error('Error loading settings:', err);
                    const status = document.getElementById('settingsStatus');
                    status.className = 'status error';
                    status.textContent = 'Error loading settings: ' + err.message;
                }
            }

            async function loadConversationAgents() {
                const res = await fetch(`${ADMIN_API_BASE}/conversation-agents`);
                const data = await res.json();
                const select = document.getElementById('conversationAgent');
                select.innerHTML = '<option value="">Home Assistant default</option>';

                if (data.agents && data.agents.length > 0) {
                    data.agents.forEach(agent => {
                        const opt = document.createElement('option');
                        opt.value = agent.id;
                        opt.textContent = agent.name;
                        select.appendChild(opt);
                    });
                }

                select.value = appSettings.conversation_agent_id || '';
            }

            async function loadTtsEngines() {
                const res = await fetch(`${ADMIN_API_BASE}/tts-engines`);
                const data = await res.json();
                ttsEngines = data.engines || [];
                const select = document.getElementById('ttsEngine');
                select.innerHTML = '<option value="">-- Select TTS engine --</option>';

                ttsEngines.forEach(engine => {
                    const opt = document.createElement('option');
                    opt.value = engine.id;
                    opt.textContent = engine.name;
                    select.appendChild(opt);
                });

                select.value = appSettings.tts_engine_id || '';
                onTtsEngineChanged();
            }

            function onTtsEngineChanged() {
                const engineId = document.getElementById('ttsEngine').value;
                const languageSelect = document.getElementById('ttsLanguage');
                const engine = ttsEngines.find(item => item.id === engineId);
                languageSelect.innerHTML = '';

                if (!engine) {
                    languageSelect.innerHTML = '<option value="">-- Select TTS engine first --</option>';
                    document.getElementById('ttsVoice').innerHTML = '<option value="">Default voice</option>';
                    return;
                }

                const languages = Array.isArray(engine.supported_languages) ? engine.supported_languages : [];
                if (languages.length > 0) {
                    languages.forEach(language => {
                        const opt = document.createElement('option');
                        opt.value = language;
                        opt.textContent = language;
                        languageSelect.appendChild(opt);
                    });
                } else {
                    const opt = document.createElement('option');
                    opt.value = appSettings.tts_language || 'en-US';
                    opt.textContent = appSettings.tts_language || 'en-US';
                    languageSelect.appendChild(opt);
                }

                languageSelect.value = appSettings.tts_language || languageSelect.options[0]?.value || '';
                loadVoices();
            }

            async function loadVoices() {
                const engineId = document.getElementById('ttsEngine').value;
                const language = document.getElementById('ttsLanguage').value;
                const select = document.getElementById('ttsVoice');
                select.innerHTML = '<option value="">Default voice</option>';

                if (!engineId || !language) return;

                try {
                    const res = await fetch(`${ADMIN_API_BASE}/tts-voices?engine_id=${encodeURIComponent(engineId)}&language=${encodeURIComponent(language)}`);
                    const data = await res.json();
                    if (data.voices && data.voices.length > 0) {
                        data.voices.forEach(voice => {
                            const opt = document.createElement('option');
                            opt.value = voice.id;
                            opt.textContent = voice.name;
                            select.appendChild(opt);
                        });
                    }
                    select.value = appSettings.tts_voice || '';
                } catch (err) {
                    console.error('Error loading TTS voices:', err);
                }
            }

            async function saveSettings() {
                const status = document.getElementById('settingsStatus');
                const settings = {
                    conversation_agent_id: document.getElementById('conversationAgent').value,
                    tts_engine_id: document.getElementById('ttsEngine').value,
                    tts_language: document.getElementById('ttsLanguage').value,
                    tts_voice: document.getElementById('ttsVoice').value
                };

                if (!settings.tts_engine_id) {
                    status.className = 'status error';
                    status.textContent = 'Please select a TTS engine';
                    return;
                }

                try {
                    const res = await fetch(`${ADMIN_API_BASE}/settings`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(settings)
                    });

                    if (res.ok) {
                        appSettings = settings;
                        status.className = 'status success';
                        status.textContent = 'Settings saved successfully';
                        setTimeout(() => { status.style.display = 'none'; }, 3000);
                    } else {
                        const error = await res.text();
                        status.className = 'status error';
                        status.textContent = 'Error: ' + error;
                    }
                } catch (err) {
                    status.className = 'status error';
                    status.textContent = 'Error: ' + err.message;
                }
            }

            async function loadUsers() {
                try {
                    const res = await fetch(`${ADMIN_API_BASE}/users`);
                    const data = await res.json();
                    haUsers = data.users || [];
                    const selects = [
                        document.getElementById('user'),
                        document.getElementById('callerUser')
                    ];
                    selects.forEach(select => {
                        select.innerHTML = '<option value="">-- Select user --</option>';
                    });
                    if (data.users && data.users.length > 0) {
                        data.users.forEach(user => {
                            selects.forEach(select => {
                                const opt = document.createElement('option');
                                opt.value = user.id;
                                opt.textContent = user.name;
                                select.appendChild(opt);
                            });
                        });
                    } else {
                        selects.forEach(select => {
                            select.innerHTML = '<option value="">No users found</option>';
                        });
                    }
                } catch (err) {
                    console.error('Error loading users:', err);
                    document.getElementById('user').innerHTML = '<option value="">Error loading users</option>';
                    document.getElementById('callerUser').innerHTML = '<option value="">Error loading users</option>';
                }
            }

            async function loadCallerAccess() {
                try {
                    const res = await fetch(`${ADMIN_API_BASE}/caller-access`);
                    const data = await res.json();
                    const list = document.getElementById('callerAccessList');
                    list.innerHTML = '';

                    if (!data.callers || data.callers.length === 0) {
                        list.innerHTML = '<p style="color: #999; text-align: center; padding: 20px;">No caller access records configured yet</p>';
                        return;
                    }

                    data.callers.forEach(record => {
                        const item = document.createElement('div');
                        item.className = 'pin-item';
                        const info = document.createElement('div');
                        info.className = 'pin-info';
                        const title = document.createElement('strong');
                        title.textContent = record.display_name || record.ha_user_id;
                        info.appendChild(title);
                        const numbers = document.createElement('div');
                        numbers.className = 'pin-user';
                        numbers.textContent = (record.masked_phone_numbers || []).join(', ') || 'No phone numbers';
                        info.appendChild(numbers);
                        const meta = document.createElement('div');
                        meta.className = 'muted';
                        const pinBadge = document.createElement('span');
                        pinBadge.className = 'badge';
                        pinBadge.textContent = record.pin_set ? 'PIN set' : 'No PIN';
                        meta.appendChild(pinBadge);
                        const sourceBadge = document.createElement('span');
                        sourceBadge.className = 'badge';
                        sourceBadge.textContent = record.source === 'admin' ? 'Caller Access' : 'Config read-only';
                        meta.appendChild(sourceBadge);
                        info.appendChild(meta);
                        item.appendChild(info);

                        if (record.deletable) {
                            const button = document.createElement('button');
                            button.className = 'delete';
                            button.textContent = 'Delete';
                            button.addEventListener('click', () => deleteCallerAccess(record.index));
                            item.appendChild(button);
                        }

                        list.appendChild(item);
                    });
                } catch (err) {
                    console.error('Error loading caller access:', err);
                    document.getElementById('callerAccessList').innerHTML = '<p style="color: #999; text-align: center;">Error loading caller access</p>';
                }
            }

            async function addCallerAccess() {
                const userId = document.getElementById('callerUser').value;
                const phoneNumbers = document.getElementById('callerPhoneNumbers').value
                    .split('\\n')
                    .map(item => item.trim())
                    .filter(Boolean);
                const pin = document.getElementById('callerPin').value.trim();
                const status = document.getElementById('callerAccessStatus');
                status.style.display = '';

                if (!userId || phoneNumbers.length === 0) {
                    status.className = 'status error';
                    status.textContent = 'Select a user and enter at least one phone number';
                    return;
                }

                if (pin && !/^[0-9]{4}$/.test(pin)) {
                    status.className = 'status error';
                    status.textContent = 'Fallback PIN must be exactly 4 digits';
                    return;
                }

                try {
                    const res = await fetch(`${ADMIN_API_BASE}/caller-access`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            ha_user_id: userId,
                            phone_numbers: phoneNumbers,
                            pin: pin || null
                        })
                    });

                    if (res.ok) {
                        status.className = 'status success';
                        status.textContent = 'Caller access saved';
                        document.getElementById('callerUser').value = '';
                        document.getElementById('callerPhoneNumbers').value = '';
                        document.getElementById('callerPin').value = '';
                        setTimeout(() => { status.style.display = 'none'; }, 3000);
                        loadCallerAccess();
                    } else {
                        const error = await res.text();
                        status.className = 'status error';
                        status.textContent = 'Error: ' + error;
                    }
                } catch (err) {
                    status.className = 'status error';
                    status.textContent = 'Error: ' + err.message;
                }
            }

            async function deleteCallerAccess(recordIndex) {
                if (!confirm('Delete this caller access record?')) return;
                try {
                    const res = await fetch(`${ADMIN_API_BASE}/caller-access/${recordIndex}`, { method: 'DELETE' });
                    if (res.ok) {
                        loadCallerAccess();
                    } else {
                        alert('Could not delete caller access record');
                    }
                } catch (err) {
                    alert('Error deleting caller access: ' + err.message);
                }
            }

            async function loadPins() {
                try {
                    const res = await fetch(`${ADMIN_API_BASE}/pins`);
                    const data = await res.json();
                    const list = document.getElementById('pinsList');
                    
                    if (!data.pins || Object.keys(data.pins).length === 0) {
                        list.innerHTML = '<p style="color: #999; text-align: center; padding: 20px;">No PINs configured yet</p>';
                        return;
                    }

                    list.innerHTML = '';
                    for (const [pin, pinEntry] of Object.entries(data.pins)) {
                        const userId = typeof pinEntry === 'object' ? pinEntry.user_id : pinEntry;
                        const savedName = typeof pinEntry === 'object' ? pinEntry.user_name : null;
                        const userName = savedName || data.userMap?.[userId] || userId;
                        const item = document.createElement('div');
                        item.className = 'pin-item';
                        const info = document.createElement('div');
                        info.className = 'pin-info';
                        const title = document.createElement('strong');
                        title.textContent = 'PIN set';
                        info.appendChild(title);
                        const user = document.createElement('div');
                        user.className = 'pin-user';
                        user.textContent = userName;
                        info.appendChild(user);
                        item.appendChild(info);
                        const button = document.createElement('button');
                        button.className = 'delete';
                        button.textContent = 'Delete';
                        button.addEventListener('click', () => deletePin(pin));
                        item.appendChild(button);
                        list.appendChild(item);
                    }
                } catch (err) {
                    console.error('Error loading pins:', err);
                    document.getElementById('pinsList').innerHTML = '<p style="color: #999; text-align: center;">Error loading PINs</p>';
                }
            }

            async function addPin() {
                const pin = document.getElementById('pin').value.trim();
                const userId = document.getElementById('user').value;
                const status = document.getElementById('status');
                status.style.display = '';

                if (!pin || !userId) {
                    status.className = 'status error';
                    status.textContent = 'Please enter PIN and select a user';
                    return;
                }

                if (!/^[0-9]{4}$/.test(pin)) {
                    status.className = 'status error';
                    status.textContent = 'PIN must be exactly 4 digits';
                    return;
                }

                try {
                    const select = document.getElementById('user');
                    const userName = select.options[select.selectedIndex]?.textContent || userId;
                    const res = await fetch(`${ADMIN_API_BASE}/pins`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ pin, user_id: userId, user_name: userName })
                    });

                    if (res.ok) {
                        status.className = 'status success';
                        status.textContent = 'PIN added successfully';
                        document.getElementById('pin').value = '';
                        document.getElementById('user').value = '';
                        setTimeout(() => { status.style.display = 'none'; }, 3000);
                    } else {
                        const error = await res.text();
                        status.className = 'status error';
                        status.textContent = 'Error: ' + error;
                    }
                } catch (err) {
                    status.className = 'status error';
                    status.textContent = 'Error: ' + err.message;
                }
            }

            async function deletePin(pin) {
                if (!confirm('Delete this legacy PIN?')) return;
                
                try {
                    const res = await fetch(`${ADMIN_API_BASE}/pins/${pin}`, { method: 'DELETE' });
                    if (res.ok) {
                        loadPins();
                    }
                } catch (err) {
                    alert('Error deleting PIN: ' + err.message);
                }
            }

            document.getElementById('pin').addEventListener('keypress', (e) => {
                if (e.key === 'Enter') addPin();
            });

            loadSettings();
            loadUsers();
            loadCallerAccess();
            setInterval(loadCallerAccess, 5000);
        </script>
    </body>
    </html>
    """
    return html.replace("__ADMIN_API_BASE__", json.dumps(admin_api_base))


@app.get("//admin", response_class=HTMLResponse)
async def admin_ui_double_slash(request: Request):
    return await admin_ui(request)


@app.get("/admin/api/users")
async def get_users(request: Request):
    """Get list of Home Assistant users"""
    require_ingress(request)
    users, error = await fetch_ha_users()
    response = {"users": users}
    if error:
        response["error"] = error
    return response


@app.get("/admin/api/settings")
async def get_settings(request: Request):
    """Get assistant settings"""
    require_ingress(request)
    return load_settings()


@app.post("/admin/api/settings")
async def update_settings(settings_request: SettingsRequest, request: Request):
    """Save assistant settings"""
    require_ingress(request)
    settings = {
        "conversation_agent_id": settings_request.conversation_agent_id or "",
        "tts_engine_id": settings_request.tts_engine_id or "",
        "tts_language": settings_request.tts_language or "",
        "tts_voice": settings_request.tts_voice or "",
    }
    if not settings["tts_engine_id"]:
        return JSONResponse({"error": "TTS engine required"}, status_code=400)

    if save_settings(settings):
        return {"success": True, "settings": settings}
    return JSONResponse({"error": "Could not save settings"}, status_code=500)


@app.get("/admin/api/conversation-agents")
async def get_conversation_agents(request: Request):
    """Get available Home Assistant conversation agents"""
    require_ingress(request)
    agents, error = await fetch_conversation_agents()
    response = {"agents": agents}
    if error:
        response["error"] = error
    return response


@app.get("/admin/api/tts-engines")
async def get_tts_engines(request: Request):
    """Get available Home Assistant TTS engines"""
    require_ingress(request)
    engines, error = await fetch_tts_engines()
    response = {"engines": engines}
    if error:
        response["error"] = error
    return response


@app.get("/admin/api/tts-voices")
async def get_tts_voices(engine_id: str, language: str, request: Request):
    """Get available voices for a Home Assistant TTS engine"""
    require_ingress(request)
    voices, error = await fetch_tts_voices(engine_id, language)
    response = {"voices": voices}
    if error:
        response["error"] = error
    return response


@app.get("/admin/api/pins")
async def get_pins(request: Request):
    """Get current PIN mappings"""
    require_ingress(request)
    global PIN_MAP
    PIN_MAP = load_pin_map()

    users, error = await fetch_ha_users()
    user_map = {user["id"]: user["name"] for user in users}
    if error:
        print(f"Could not fetch user names for PIN list: {error}")

    return {"pins": PIN_MAP, "userMap": user_map}


@app.post("/admin/api/pins")
async def add_pin(pin_request: PinRequest, request: Request):
    """Add a PIN mapping"""
    require_ingress(request)
    global PIN_MAP

    pin = pin_request.pin
    user_id = pin_request.user_id

    pin = pin.strip()
    if not pin or not pin.isdigit() or len(pin) != 4:
        return JSONResponse({"error": "PIN must be 4 digits"}, status_code=400)

    if not user_id:
        return JSONResponse({"error": "User ID required"}, status_code=400)

    PIN_MAP[pin] = {
        "user_id": user_id,
        "user_name": pin_request.user_name or user_id,
    }
    if save_pin_map(PIN_MAP):
        return {
            "success": True,
            "pin": pin,
            "user_id": user_id,
            "user_name": pin_request.user_name,
        }
    else:
        return JSONResponse({"error": "Could not save PIN"}, status_code=500)


@app.delete("/admin/api/pins/{pin}")
async def delete_pin(pin: str, request: Request):
    """Delete a PIN mapping"""
    require_ingress(request)
    global PIN_MAP
    
    if pin in PIN_MAP:
        del PIN_MAP[pin]
        if save_pin_map(PIN_MAP):
            return {"success": True}
    
    return JSONResponse({"error": "PIN not found"}, status_code=404)


@app.get("/admin/api/caller-access")
async def get_caller_access(request: Request):
    """Get caller access records without exposing full phone numbers or PINs."""
    require_ingress(request)
    users, error = await fetch_ha_users()
    user_map = {user["id"]: user["name"] for user in users}
    if error:
        print(f"Could not fetch user names for caller access list: {error}")

    config_records = [
        caller_record_display(record, None, "config", user_map)
        for record in load_config_caller_identity_records()
        if isinstance(record, dict)
    ]
    admin_records = [
        caller_record_display(record, index, "admin", user_map)
        for index, record in enumerate(load_admin_caller_identity_records())
        if isinstance(record, dict)
    ]
    return {"callers": config_records + admin_records}


@app.post("/admin/api/caller-access")
async def add_caller_access(
    caller_request: CallerAccessRequest,
    request: Request,
):
    """Add an admin-managed unified caller access record."""
    require_ingress(request)
    record, error = normalize_caller_access_request(caller_request)
    if error:
        return JSONResponse({"error": error}, status_code=400)

    callers = load_admin_caller_identity_records()
    callers.append(record)
    if save_admin_caller_identity_records(callers):
        return {"success": True}
    return JSONResponse({"error": "Could not save caller access"}, status_code=500)


@app.delete("/admin/api/caller-access/{record_index}")
async def delete_caller_access(record_index: int, request: Request):
    """Delete an admin-managed caller access record."""
    require_ingress(request)
    callers = load_admin_caller_identity_records()
    if record_index < 0 or record_index >= len(callers):
        return JSONResponse({"error": "Caller access record not found"}, status_code=404)

    callers.pop(record_index)
    if save_admin_caller_identity_records(callers):
        return {"success": True}
    return JSONResponse({"error": "Could not save caller access"}, status_code=500)


# Twilio webhook endpoints
@app.post("/incoming_call")
async def incoming_call(
    From: str = Form(None),
    To: str = Form(None),
    CallSid: str = Form(None),
):
    caller, normalized_from = find_allowed_caller(From)
    masked_from = mask_phone_number(normalized_from or From)
    log_timing(
        "inbound_call_received",
        call_sid=CallSid,
        bridge_mode=VOICE_BRIDGE_MODE,
        auth_mode=AUTH_MODE,
        caller=masked_from,
    )

    if AUTH_MODE == "pin":
        return prompt_for_pin(call_sid=CallSid)

    if caller:
        user_name = await resolve_ha_user_display_name(
            caller["ha_user_id"],
            caller.get("name"),
        )
        log_timing(
            "caller_whitelist_matched",
            call_sid=CallSid,
            caller=masked_from,
            user_id=caller["ha_user_id"],
        )
        return redirect_to_start_session(caller["ha_user_id"], user_name)

    if (
        AUTH_MODE == "caller_whitelist_or_pin"
        or UNKNOWN_CALLER_POLICY == "pin_fallback"
    ):
        log_timing(
            "unknown_caller_pin_fallback",
            call_sid=CallSid,
            caller=masked_from,
        )
        return prompt_for_pin(call_sid=CallSid)

    log_timing(
        "unknown_caller_rejected",
        call_sid=CallSid,
        caller=masked_from,
    )
    return polite_hangup("Sorry, this number is not authorized. Goodbye.")


@app.post("/check_pin")
async def check_pin(
    RecordingUrl: str | None = Form(None),
    CallSid: str | None = Form(None),
    Digits: str | None = Form(None),
):
    global PIN_MAP
    PIN_MAP = load_effective_pin_map()
    
    try:
        if Digits:
            return await handle_pin_digits(Digits, call_sid=CallSid)

        if not RecordingUrl or not CallSid:
            return twiml_response("""
            <Response>
                <Say>Sorry, I did not receive your PIN. Please try again.</Say>
                <Redirect>/incoming_call</Redirect>
            </Response>
            """)

        audio_path = f"/tmp/{CallSid}_pin.mp3"

        if not download_twilio_recording(RecordingUrl, audio_path):
            return twiml_response("""
            <Response>
                <Say>Sorry, I could not hear your PIN. Please try again.</Say>
                <Redirect>/incoming_call</Redirect>
            </Response>
            """)

        result = get_whisper_model().transcribe(
            audio_path,
            no_speech_threshold=0.4,
            condition_on_previous_text=False
        )
        spoken_text = result.get("text", "").strip()

        debug_log("pin_speech_transcribed", text_length=len(spoken_text))
        return await handle_pin_digits(spoken_text, call_sid=CallSid)

    except Exception:
        print("Exception in check_pin:")
        traceback.print_exc()
        return twiml_response("""
        <Response>
            <Say>Sorry, there was a problem checking your PIN. Please try again.</Say>
            <Redirect>/incoming_call</Redirect>
        </Response>
        """)


@app.api_route("/start_session", methods=["GET", "POST"])
async def start_session(
    user_id: str | None = None,
    user_name: str | None = None,
    user: str | None = None,
):
    try:
        # user is kept for compatibility with sessions started by older redirects.
        user_id = user_id or user
        if not user_id:
            return twiml_response("""
            <Response>
                <Say>Sorry, there was a problem identifying your user.</Say>
                <Hangup/>
            </Response>
            """)
        user_name = user_name or user_id
        encoded_user_id = quote(user_id)
        encoded_user_name = quote(user_name)
        log_timing(
            "bridge_mode_selected",
            bridge_mode=VOICE_BRIDGE_MODE,
            user_id=user_id,
        )

        if VOICE_BRIDGE_MODE == "conversation_relay":
            log_timing(
                "conversation_relay_twiml_returned",
                user_id=user_id,
                tts_provider=CONVERSATION_RELAY_TTS_PROVIDER,
                transcription_provider=CONVERSATION_RELAY_TRANSCRIPTION_PROVIDER,
                language=CONVERSATION_RELAY_LANGUAGE,
                conversation_relay_voice_configured=bool(CONVERSATION_RELAY_VOICE),
            )
            return twiml_response(conversation_relay_twiml(user_id, user_name))

        if VOICE_BRIDGE_MODE == "elevenlabs_agent":
            print("WARNING: elevenlabs_agent mode is configured but not implemented yet.")
            return polite_hangup(
                "Sorry, ElevenLabs Agent mode is not available yet. Goodbye."
            )

        # Deprecated Gather/audio-file fallback. Keep for compatibility until removal
        # is explicitly planned after more stable Conversation Relay use.
        prompt_audio = await tts_to_audio(
            f"Hello {user_name}. What would you like to do?",
            f"{user_id}_prompt",
        )

        filename = os.path.basename(prompt_audio)
        public_url = public_audio_url(filename)
        record_twiml = record_command_twiml(encoded_user_id, encoded_user_name)

        return twiml_response(f"""
        <Response>
            <Play>{public_url}</Play>
            {record_twiml}
        </Response>
        """)

    except Exception:
        print("Exception in start_session:")
        traceback.print_exc()
        return twiml_response("""
        <Response>
            <Say>Sorry, there was a problem starting the assistant session.</Say>
            <Hangup/>
        </Response>
        """)


@app.post("/process_command")
async def process_command(
    RecordingUrl: str = Form(...),
    CallSid: str = Form(...),
    user_id: str | None = None,
    user_name: str | None = None,
    user: str | None = None,
):
    # Deprecated Gather/audio/Whisper command processing path.
    # Conversation Relay should remain the normal product path.
    try:
        user_id = user_id or user
        if not user_id:
            return twiml_response("""
            <Response>
                <Say>Sorry, there was a problem identifying your user.</Say>
                <Hangup/>
            </Response>
            """)
        user_name = user_name or user_id
        encoded_user_id = quote(user_id)
        encoded_user_name = quote(user_name)
        audio_path = f"/tmp/{CallSid}_cmd.mp3"

        if not download_twilio_recording(RecordingUrl, audio_path):
            return safe_redirect_to_session(
                user_id,
                user_name,
                "Sorry, I could not hear your request. Please try again."
            )

        # Prevent Whisper from transcribing empty files
        if os.path.getsize(audio_path) < 2000:
            return polite_hangup("I did not hear anything. Goodbye.")

        result = get_whisper_model().transcribe(
            audio_path,
            no_speech_threshold=0.4,
            condition_on_previous_text=False
        )
        spoken_text = result.get("text", "").strip()

        debug_log("command_transcribed", text_length=len(spoken_text))

        if not spoken_text:
            return polite_hangup("I did not hear anything. Goodbye.")

        if is_end_call_phrase(spoken_text):
            return polite_hangup("Understood. Goodbye.")

        try:
            reply = await send_to_home_assistant_conversation(spoken_text, user_id)
        except Exception:
            print("Home Assistant Conversation request failed.")
            traceback.print_exc()
            return safe_redirect_to_session(
                user_id,
                user_name,
                "Sorry, Home Assistant returned an error."
            )

        debug_log("home_assistant_reply_ready", text_length=len(reply))

        reply_audio = await tts_to_audio(reply, f"{CallSid}_reply")
        filename = os.path.basename(reply_audio)
        public_url = public_audio_url(filename)
        record_twiml = record_command_twiml(encoded_user_id, encoded_user_name)

        return twiml_response(f"""
        <Response>
            <Play>{public_url}</Play>
            {record_twiml}
        </Response>
        """)

    except Exception:
        print("Exception in process_command:")
        traceback.print_exc()
        return safe_redirect_to_session(
            user_id,
            user_name,
            "Sorry, something went wrong processing your request."
        )


@app.websocket("/conversation_relay")
async def conversation_relay_websocket(websocket: WebSocket):
    # Preferred v2 path: receive final text transcripts, call HA Conversation,
    # and return response text without local TTS/audio file handling.
    await websocket.accept()
    log_timing("websocket_connected", bridge_mode="conversation_relay")

    user_id = "unknown"
    user_name = "unknown"
    conversation_id = None

    try:
        while True:
            raw_message = await websocket.receive_text()
            try:
                message = json.loads(raw_message)
            except json.JSONDecodeError:
                debug_log("conversation_relay_invalid_json")
                continue

            message_type = message.get("type", "unknown")
            debug_log("conversation_relay_message_received", message_type=message_type)

            if message_type == "setup":
                custom_parameters = message.get("customParameters") or {}
                user_id = custom_parameters.get("user_id") or user_id
                user_name = custom_parameters.get("user_name") or user_name
                conversation_id = (
                    custom_parameters.get("conversation_id")
                    or f"twilio_{user_id}"
                )
                debug_log(
                    "conversation_relay_setup",
                    user_id=user_id,
                    has_conversation_id=bool(conversation_id),
                )
                continue

            if message_type == "prompt":
                # Twilio Conversation Relay currently sends caller transcripts as
                # prompt messages with voicePrompt and last fields. Keep this
                # parser narrow and logged because the prototype schema may evolve.
                transcript = (message.get("voicePrompt") or "").strip()
                is_final = bool(message.get("last"))
                if not is_final:
                    debug_log(
                        "conversation_relay_partial_transcript",
                        text_length=len(transcript),
                    )
                    continue

                log_timing(
                    "transcript_text_received",
                    user_id=user_id,
                    conversation_id=conversation_id,
                    text_length=len(transcript),
                )

                if not transcript:
                    continue

                if is_end_call_phrase(transcript):
                    await websocket.send_text(json.dumps({
                        "type": "text",
                        "token": "Understood. Goodbye.",
                        "last": True,
                    }))
                    await websocket.send_text(json.dumps({
                        "type": "end",
                        "handoffData": json.dumps({"reason": "caller_ended_call"}),
                    }))
                    log_timing("response_text_sent_to_conversation_relay", user_id=user_id)
                    continue

                try:
                    reply = await send_to_home_assistant_conversation(
                        transcript,
                        user_id,
                        conversation_id=conversation_id,
                    )
                except Exception:
                    print("Conversation Relay Home Assistant request failed.")
                    traceback.print_exc()
                    reply = "Sorry, Home Assistant returned an error."

                await websocket.send_text(json.dumps({
                    "type": "text",
                    "token": reply,
                    "last": True,
                    "lang": CONVERSATION_RELAY_LANGUAGE,
                }))
                log_timing(
                    "response_text_sent_to_conversation_relay",
                    user_id=user_id,
                    conversation_id=conversation_id,
                    text_length=len(reply),
                )
                continue

            if message_type == "error":
                print("Conversation Relay error message received.")
                continue

            if message_type in {"dtmf", "interrupt"}:
                continue

            debug_log("conversation_relay_unhandled_message", message_type=message_type)

    except WebSocketDisconnect:
        log_timing(
            "call_ended",
            user_id=user_id,
            conversation_id=conversation_id,
            reason="websocket_disconnect",
        )
    except Exception:
        print("Exception in Conversation Relay websocket:")
        traceback.print_exc()
        log_timing(
            "call_ended",
            user_id=user_id,
            conversation_id=conversation_id,
            reason="websocket_error",
        )


@app.post("/conversation_relay/status")
async def conversation_relay_status(
    CallSid: str | None = Form(None),
    SessionId: str | None = Form(None),
    SessionStatus: str | None = Form(None),
):
    log_timing(
        "call_ended",
        call_sid=CallSid,
        session_id=SessionId,
        session_status=SessionStatus,
    )
    return twiml_response("""
    <Response>
        <Hangup/>
    </Response>
    """)


@app.get("/admin/debug")
async def debug(request: Request):
    """Debug endpoint - check configuration"""
    require_ingress(request)
    token_set = bool(SUPERVISOR_TOKEN)
    users, error = await fetch_ha_users()
    
    return {
        "supervisor_token_set": token_set,
        "user_count": len(users),
        "user_error": error,
        "debug_mode": DEBUG
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
