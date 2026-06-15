from fastapi import FastAPI, Form, Request, WebSocket, WebSocketDisconnect
from fastapi import HTTPException
from fastapi.responses import Response, HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel
import httpx
import websockets
import asyncio
import base64
import hashlib
import hmac
import re
import os
import json
import traceback
import time
from contextlib import asynccontextmanager
from html import escape
from urllib.parse import quote

DATA_DIR = "/share/twilio_voice_assistant"
INGRESS_PROXY_IP = "172.30.32.2"

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
ALLOW_UNSIGNED_TWILIO_REQUESTS_FOR_DEV = (
    os.getenv("ALLOW_UNSIGNED_TWILIO_REQUESTS_FOR_DEV", "false").strip().lower()
    in {"1", "true", "yes", "on"}
)
SESSION_TOKEN_TTL_SECONDS = 120

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


def base64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def base64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def twilio_public_url(request: Request) -> str:
    query = request.url.query
    url = f"{PUBLIC_BASE_URL}{request.url.path}"
    if query:
        url = f"{url}?{query}"
    return url


def compute_twilio_signature(url: str, params: dict[str, str]) -> str:
    signed_data = url + "".join(
        f"{key}{params[key]}"
        for key in sorted(params)
    )
    digest = hmac.new(
        TWILIO_AUTH_TOKEN.encode("utf-8"),
        signed_data.encode("utf-8"),
        hashlib.sha1,
    ).digest()
    return base64.b64encode(digest).decode("ascii")


async def validate_twilio_http_request(
    request: Request,
    route: str,
    call_sid: str | None = None,
):
    """Validate Twilio webhook signatures without logging secrets or PINs."""
    if ALLOW_UNSIGNED_TWILIO_REQUESTS_FOR_DEV:
        log_timing(
            "twilio_signature_validation",
            route=route,
            result="dev_bypass",
            call_sid=call_sid,
        )
        return

    form = await request.form()
    params = {
        key: str(value)
        for key, value in form.multi_items()
    }
    expected_signature = compute_twilio_signature(
        twilio_public_url(request),
        params,
    )
    received_signature = request.headers.get("x-twilio-signature", "")
    is_valid = hmac.compare_digest(received_signature, expected_signature)
    log_timing(
        "twilio_signature_validation",
        route=route,
        result="valid" if is_valid else "invalid",
        call_sid=call_sid or params.get("CallSid"),
    )
    if not is_valid:
        raise HTTPException(status_code=403, detail="Invalid Twilio signature")


def create_session_token(
    user_id: str,
    user_name: str,
    call_sid: str | None = None,
) -> str:
    payload = {
        "user_id": user_id,
        "user_name": user_name,
        "created": int(time.time()),
    }
    if call_sid:
        payload["call_sid"] = call_sid

    payload_json = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    encoded_payload = base64url_encode(payload_json.encode("utf-8"))
    signature = hmac.new(
        TWILIO_AUTH_TOKEN.encode("utf-8"),
        encoded_payload.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return f"{encoded_payload}.{base64url_encode(signature)}"


def validate_session_token(token: str | None) -> tuple[dict | None, str | None]:
    if not token or "." not in token:
        return None, "missing"

    encoded_payload, encoded_signature = token.split(".", 1)
    expected_signature = hmac.new(
        TWILIO_AUTH_TOKEN.encode("utf-8"),
        encoded_payload.encode("ascii"),
        hashlib.sha256,
    ).digest()

    try:
        received_signature = base64url_decode(encoded_signature)
    except Exception:
        return None, "bad_signature_encoding"

    if not hmac.compare_digest(received_signature, expected_signature):
        return None, "bad_signature"

    try:
        payload = json.loads(base64url_decode(encoded_payload).decode("utf-8"))
    except Exception:
        return None, "bad_payload"

    created = payload.get("created")
    if not isinstance(created, int):
        return None, "missing_created"
    if time.time() - created > SESSION_TOKEN_TTL_SECONDS:
        return None, "expired"

    if not payload.get("user_id") or not payload.get("user_name"):
        return None, "missing_identity"

    return payload, None


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
    return load_admin_caller_identity_records()


def load_caller_access_lookup():
    """Build the effective caller lookup from Caller Access and import records."""
    caller_records = load_caller_identity_records()
    callers, caller_count = normalize_callers_for_lookup(caller_records, "callers")
    return callers, caller_count


def load_caller_access_pins():
    pin_lookup = {}
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
        pin_lookup[pin] = {
            "user_id": ha_user_id,
            "user_name": caller_name,
        }
    return pin_lookup


CALLER_ACCESS_LOOKUP, CALLER_ACCESS_RECORD_COUNT = load_caller_access_lookup()


def find_caller_access_record(from_number: str | None):
    global CALLER_ACCESS_LOOKUP, CALLER_ACCESS_RECORD_COUNT
    CALLER_ACCESS_LOOKUP, CALLER_ACCESS_RECORD_COUNT = load_caller_access_lookup()
    normalized_from = normalize_phone_number(from_number)
    if not normalized_from:
        return None, normalized_from
    for caller in CALLER_ACCESS_LOOKUP:
        if caller["phone_number"] == normalized_from:
            return caller, normalized_from
    return None, normalized_from


def log_startup_configuration():
    log_timing(
        "startup_configuration",
        auth_mode=AUTH_MODE,
        unknown_caller_policy=UNKNOWN_CALLER_POLICY,
        runtime_mode="conversation_relay_only",
        pin_fallback="dtmf",
        conversation_relay_tts_provider=CONVERSATION_RELAY_TTS_PROVIDER,
        conversation_relay_transcription_provider=(
            CONVERSATION_RELAY_TRANSCRIPTION_PROVIDER
        ),
        conversation_relay_language=CONVERSATION_RELAY_LANGUAGE,
        conversation_relay_voice_configured=bool(CONVERSATION_RELAY_VOICE),
        caller_identity_source="caller_access",
        caller_access_records_count=CALLER_ACCESS_RECORD_COUNT,
        local_audio_pipeline="removed",
        twilio_signature_validation_enabled=(
            not ALLOW_UNSIGNED_TWILIO_REQUESTS_FOR_DEV
        ),
        dev_unsigned_request_bypass_enabled=(
            ALLOW_UNSIGNED_TWILIO_REQUESTS_FOR_DEV
        ),
        session_token_ttl_seconds=SESSION_TOKEN_TTL_SECONDS,
    )
    print(
        "INFO: Secure Conversation Relay-only mode selected; "
        "local audio-file handling is removed."
    )
    if ALLOW_UNSIGNED_TWILIO_REQUESTS_FOR_DEV:
        print(
            "WARNING: Development-only unsigned Twilio request bypass is enabled. "
            "Do not expose public endpoints with this setting enabled."
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    log_startup_configuration()
    yield


app = FastAPI(lifespan=lifespan)


# Request models for Caller Access and admin settings.
class CallerAccessRequest(BaseModel):
    ha_user_id: str
    phone_numbers: list[str]
    pin: str | None = None


class SettingsRequest(BaseModel):
    conversation_agent_id: str | None = None


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


def load_settings():
    """Load admin settings from JSON file."""
    defaults = {
        "conversation_agent_id": "",
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


def caller_access_pin_user_id(pin_entry):
    if isinstance(pin_entry, dict):
        return pin_entry.get("user_id")
    return pin_entry


def caller_access_pin_user_name(pin_entry, user_map):
    user_id = caller_access_pin_user_id(pin_entry)
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


if DEBUG:
    print(f"Loaded Caller Access PIN entries: {len(load_caller_access_pins())}")
    print(f"Loaded settings: {load_settings()}")


def twiml_response(xml: str):
    return Response(content=xml.strip(), media_type="application/xml")


def public_websocket_url(path: str) -> str:
    base = PUBLIC_BASE_URL
    if base.startswith("https://"):
        base = "wss://" + base[len("https://"):]
    elif base.startswith("http://"):
        base = "ws://" + base[len("http://"):]
    return f"{base}{path}"


def polite_hangup(message: str = "Goodbye."):
    return twiml_response(f"""
    <Response>
        <Say>{message}</Say>
        <Hangup/>
    </Response>
    """)


def prompt_for_pin(call_sid: str | None = None):
    # Fallback auth path. Caller Access normally bypasses this for known callers.
    log_timing("pin_prompt_sent", call_sid=call_sid, pin_method="dtmf")
    return twiml_response("""
    <Response>
        <Gather input="dtmf" numDigits="4" timeout="10" action="/check_pin" method="POST">
            <Say>Please enter your four digit PIN.</Say>
        </Gather>
        <Say>I did not receive a PIN. Goodbye.</Say>
        <Hangup/>
    </Response>
    """)


def redirect_to_start_session(
    user_id: str,
    user_name: str | None,
    call_sid: str | None = None,
):
    session_token = create_session_token(user_id, user_name or user_id, call_sid)
    encoded_token = quote(session_token)
    return twiml_response(f"""
    <Response>
        <Redirect>/start_session?session_token={encoded_token}</Redirect>
    </Response>
    """)


def conversation_relay_twiml(
    user_id: str,
    user_name: str,
    session_token: str,
) -> str:
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
                <Parameter name="session_token" value="{escape(session_token, quote=True)}"/>
            </ConversationRelay>
        </Connect>
    </Response>
    """


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
    digits = normalize_digits(digits)
    pin_lookup = load_caller_access_pins()

    if DEBUG:
        print(f"PIN received with {len(digits)} digits")

    pin_entry = pin_lookup.get(digits)
    if pin_entry:
        users, error = await fetch_ha_users()
        if error:
            print(f"Could not resolve user name after PIN auth: {error}")

        user_map = {user["id"]: user["name"] for user in users}
        user_id = caller_access_pin_user_id(pin_entry)
        user_name = caller_access_pin_user_name(pin_entry, user_map)
        session_token = create_session_token(user_id, user_name, call_sid)
        encoded_session_token = quote(session_token)
        log_timing(
            "pin_accepted",
            call_sid=call_sid,
            user_id=user_id,
            runtime_mode="conversation_relay",
        )
        return twiml_response(f"""
        <Response>
            <Say>Thank you. Connecting you now.</Say>
            <Redirect>/start_session?session_token={encoded_session_token}</Redirect>
        </Response>
        """)

    return twiml_response("""
    <Response>
        <Say>Incorrect PIN. Please try again.</Say>
        <Redirect>/incoming_call</Redirect>
    </Response>
    """)


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
                <button onclick="saveSettings()">Save Settings</button>
                <div class="status" id="settingsStatus"></div>
            </div>
            
            <div class="form-section">
                <h2 style="font-size: 18px; margin-bottom: 15px; color: #333;">Caller Access</h2>
                <p class="muted" style="margin-bottom: 15px;">Preferred and only caller identity source. Add known callers and optional fallback PINs here.</p>
                <div class="form-group">
                    <label for="callerUser">Home Assistant User:</label>
                    <select id="callerUser">
                        <option value="">-- Loading users --</option>
                    </select>
                </div>
                <div class="form-group">
                    <label for="callerPhoneNumbers">Phone Numbers (one per line, E.164 preferred):</label>
                    <textarea id="callerPhoneNumbers" placeholder="+1XXXXXXXXXX&#10;+1YYYYYYYYYY"></textarea>
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

        </div>

        <script>
            const ADMIN_API_BASE = __ADMIN_API_BASE__;
            let appSettings = {};
            let haUsers = [];

            async function loadSettings() {
                try {
                    const res = await fetch(`${ADMIN_API_BASE}/settings`);
                    appSettings = await res.json();
                    await loadConversationAgents();
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

            async function saveSettings() {
                const status = document.getElementById('settingsStatus');
                const settings = {
                    conversation_agent_id: document.getElementById('conversationAgent').value
                };

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
                    const select = document.getElementById('callerUser');
                    select.innerHTML = '<option value="">-- Select user --</option>';
                    if (data.users && data.users.length > 0) {
                        data.users.forEach(user => {
                            const opt = document.createElement('option');
                            opt.value = user.id;
                            opt.textContent = user.name;
                            select.appendChild(opt);
                        });
                    } else {
                        select.innerHTML = '<option value="">No users found</option>';
                    }
                } catch (err) {
                    console.error('Error loading users:', err);
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
                        sourceBadge.textContent = 'Caller Access';
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
    }

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


@app.get("/admin/api/caller-access")
async def get_caller_access(request: Request):
    """Get caller access records without exposing full phone numbers or PINs."""
    require_ingress(request)
    users, error = await fetch_ha_users()
    user_map = {user["id"]: user["name"] for user in users}
    if error:
        print(f"Could not fetch user names for caller access list: {error}")

    admin_records = [
        caller_record_display(record, index, "admin", user_map)
        for index, record in enumerate(load_admin_caller_identity_records())
        if isinstance(record, dict)
    ]
    return {"callers": admin_records}


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
    request: Request,
    From: str = Form(None),
    To: str = Form(None),
    CallSid: str = Form(None),
):
    await validate_twilio_http_request(
        request,
        route="/incoming_call",
        call_sid=CallSid,
    )
    caller, normalized_from = find_caller_access_record(From)
    masked_from = mask_phone_number(normalized_from or From)
    log_timing(
        "inbound_call_received",
        call_sid=CallSid,
        runtime_mode="conversation_relay",
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
        return redirect_to_start_session(caller["ha_user_id"], user_name, CallSid)

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
    request: Request,
    CallSid: str | None = Form(None),
    Digits: str | None = Form(None),
):
    try:
        await validate_twilio_http_request(
            request,
            route="/check_pin",
            call_sid=CallSid,
        )
        if Digits:
            return await handle_pin_digits(Digits, call_sid=CallSid)

        return twiml_response("""
        <Response>
            <Say>Sorry, I did not receive your PIN. Please try again.</Say>
            <Redirect>/incoming_call</Redirect>
        </Response>
        """)

    except HTTPException:
        raise
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
    request: Request,
    session_token: str | None = None,
):
    try:
        await validate_twilio_http_request(
            request,
            route="/start_session",
        )
        session_payload, token_error = validate_session_token(session_token)
        if token_error or not session_payload:
            log_timing(
                "session_token_validation",
                route="/start_session",
                result="invalid",
                reason=token_error,
            )
            raise HTTPException(status_code=403, detail="Invalid session")

        user_id = session_payload["user_id"]
        user_name = session_payload["user_name"]
        call_sid = session_payload.get("call_sid")
        log_timing(
            "session_token_validation",
            route="/start_session",
            result="valid",
            call_sid=call_sid,
        )
        log_timing(
            "conversation_relay_session_started",
            runtime_mode="conversation_relay",
            user_id=user_id,
        )
        log_timing(
            "conversation_relay_twiml_returned",
            user_id=user_id,
            tts_provider=CONVERSATION_RELAY_TTS_PROVIDER,
            transcription_provider=CONVERSATION_RELAY_TRANSCRIPTION_PROVIDER,
            language=CONVERSATION_RELAY_LANGUAGE,
            conversation_relay_voice_configured=bool(CONVERSATION_RELAY_VOICE),
        )
        return twiml_response(
            conversation_relay_twiml(user_id, user_name, session_token)
        )

    except HTTPException:
        raise
    except Exception:
        print("Exception in start_session:")
        traceback.print_exc()
        return twiml_response("""
        <Response>
            <Say>Sorry, there was a problem starting the assistant session.</Say>
            <Hangup/>
        </Response>
        """)


@app.websocket("/conversation_relay")
async def conversation_relay_websocket(websocket: WebSocket):
    # Preferred v2 path: receive final text transcripts, call HA Conversation,
    # and return response text without local media file handling.
    await websocket.accept()
    log_timing("websocket_connected", runtime_mode="conversation_relay")

    user_id = "unknown"
    user_name = "unknown"
    conversation_id = None
    session_valid = False

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
                session_token = custom_parameters.get("session_token")
                session_payload, token_error = validate_session_token(session_token)
                if token_error or not session_payload:
                    log_timing(
                        "session_token_validation",
                        route="/conversation_relay",
                        result="invalid",
                        reason=token_error,
                    )
                    await websocket.send_text(json.dumps({
                        "type": "error",
                        "message": "Invalid session.",
                    }))
                    await websocket.close(code=1008)
                    return

                user_id = session_payload["user_id"]
                user_name = session_payload["user_name"]
                conversation_id = (
                    custom_parameters.get("conversation_id")
                    or f"twilio_{user_id}"
                )
                session_valid = True
                log_timing(
                    "session_token_validation",
                    route="/conversation_relay",
                    result="valid",
                    call_sid=session_payload.get("call_sid"),
                )
                debug_log(
                    "conversation_relay_setup",
                    user_id=user_id,
                    has_conversation_id=bool(conversation_id),
                )
                continue

            if message_type == "prompt":
                if not session_valid:
                    debug_log("conversation_relay_prompt_before_valid_session")
                    await websocket.close(code=1008)
                    return

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
