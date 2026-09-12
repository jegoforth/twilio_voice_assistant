from fastapi import FastAPI, Form, Request, WebSocket, WebSocketDisconnect
from fastapi import HTTPException
from fastapi.responses import HTMLResponse, Response
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

# Load configuration from environment
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
SUPERVISOR_TOKEN = os.getenv("SUPERVISOR_TOKEN")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
if PUBLIC_BASE_URL and not PUBLIC_BASE_URL.startswith(("http://", "https://")):
    PUBLIC_BASE_URL = f"https://{PUBLIC_BASE_URL}"
DEBUG = os.getenv("DEBUG", "false").lower() == "true"
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
# Empty by default: elspeth_local.twilio_conversation is used, carrying real
# per-caller identity. Set only by a community deployment without
# elspeth_local installed, naming whatever HA conversation agent they want
# calls routed to instead -- see send_to_home_assistant_conversation().
CONVERSATION_AGENT_ID = os.getenv("CONVERSATION_AGENT_ID", "").strip()
ALLOW_UNSIGNED_TWILIO_REQUESTS_FOR_DEV = (
    os.getenv("ALLOW_UNSIGNED_TWILIO_REQUESTS_FOR_DEV", "false").strip().lower()
    in {"1", "true", "yes", "on"}
)
SESSION_TOKEN_TTL_SECONDS = 120

# Placeholder ha_user_id for an unrecognized caller. Deliberately never a
# real HA user UUID, so identity_assertion.py's signer finds no mapping for
# it and elspeth_local's client.py falls back to household_unknown -- Core
# then drives its own self-declaration + PIN elevation ladder from there.
UNKNOWN_CALLER_HA_USER_ID = "twilio_unrecognized_caller"

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


def normalize_ha_user_id(user_id: str | None, config_name: str) -> str:
    normalized = (user_id or "").strip()
    if normalized.startswith("<") and normalized.endswith(">"):
        print(
            f"WARNING: {config_name} ha_user_id should not include angle brackets; "
            "stripping them"
        )
        normalized = normalized[1:-1].strip()
    return normalized




# Phone numbers live in HA Extended User Management (a phone_number profile
# value on the person's own record), the one source of truth shared with the
# PIN itself -- this app used to keep its own separate, plaintext
# callers.json for the same purpose; see find_person_by_phone() below for
# the live lookup.
async def resolve_person_ha_user(person_entity_id: str):
    """Read a person entity's own user_id/friendly_name attributes.

    The person's `user_id` attribute (set when a person entity is linked to
    a real Home Assistant user account) is the same HA user UUID that
    identity_assertion.py's mapping file keys on -- so this is the one
    piece needed to turn "which person entity matched this phone number"
    into "which ha_user_id to hand to elspeth_local.twilio_conversation".
    """
    url = "http://supervisor/core/api/states/" + person_entity_id
    headers = {"Authorization": f"Bearer {SUPERVISOR_TOKEN}"}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(url, headers=headers)
        if response.status_code != 200:
            return None, None
        state = response.json()
    except Exception as e:
        print(f"WARNING: could not fetch {person_entity_id}: {e}")
        return None, None
    attributes = state.get("attributes", {}) if isinstance(state, dict) else {}
    ha_user_id = normalize_ha_user_id(attributes.get("user_id"), "extended_user_management")
    display_name = attributes.get("friendly_name") or person_entity_id
    return (ha_user_id or None), display_name


async def find_person_by_phone(from_number: str | None):
    """Look up the registered HA person for a caller ID via HA Extended
    User Management's find_person_by_phone service.

    Returns (ha_user_id, display_name, normalized_from). ha_user_id is
    None when the number isn't registered to any person, when that person
    has no phone_number set, or when that person entity isn't linked to a
    real HA user account -- all three are treated the same: an unrecognized
    caller.
    """
    normalized_from = normalize_phone_number(from_number)
    if not normalized_from:
        return None, None, normalized_from

    result, error = await ha_websocket_request({
        "type": "call_service",
        "domain": "extended_user_management",
        "service": "find_person_by_phone",
        "service_data": {"phone_number": normalized_from},
        "return_response": True,
    })
    if error:
        print(f"WARNING: find_person_by_phone lookup failed: {error}")
        return None, None, normalized_from

    response = (result or {}).get("response") or {}
    person_entity_id = response.get("person_entity_id")
    if not person_entity_id:
        return None, None, normalized_from

    ha_user_id, display_name = await resolve_person_ha_user(person_entity_id)
    return ha_user_id, display_name, normalized_from


def log_startup_configuration():
    log_timing(
        "startup_configuration",
        runtime_mode="conversation_relay_only",
        caller_identity_source="ha_extended_user_management",
        unrecognized_caller_auth="spoken_self_declaration_and_pin",
        conversation_relay_tts_provider=CONVERSATION_RELAY_TTS_PROVIDER,
        conversation_relay_transcription_provider=(
            CONVERSATION_RELAY_TRANSCRIPTION_PROVIDER
        ),
        conversation_relay_language=CONVERSATION_RELAY_LANGUAGE,
        conversation_relay_voice_configured=bool(CONVERSATION_RELAY_VOICE),
        conversation_route=("generic_ha_agent" if CONVERSATION_AGENT_ID else "elspeth_local"),
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
    # An unrecognized caller gets no name-based greeting at all -- user_name
    # is only ever the literal placeholder "unknown" for this case, and
    # asking "what would you like to do?" skips straight past the identity
    # ladder Core is about to run. Open with the ladder's own first rung
    # instead, matching the spoken flow exactly.
    welcome_greeting = (
        "Hello, this is Elspeth. To whom am I speaking?"
        if user_id == UNKNOWN_CALLER_HA_USER_ID
        else f"Hello {user_name}. What would you like to do?"
    )
    attrs = {
        "url": websocket_url,
        "welcomeGreeting": welcome_greeting,
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
) -> str:
    """Send text to a plain Home Assistant conversation agent.

    Only used when CONVERSATION_AGENT_ID is set -- a community deployment
    without elspeth_local installed, naming whichever HA conversation agent
    they want calls routed to. Unlike send_to_elspeth_twilio_conversation()
    below, this generic HA conversation/process REST API carries no
    per-caller identity at all (it derives identity, if any, from the
    Supervisor token's own HA user context, not from who is actually on the
    phone), so it cannot distinguish one Twilio caller from another or apply
    a self-declaration/PIN ladder per caller -- that's the tradeoff of not
    having elspeth_local installed.
    """
    ha_url = "http://supervisor/core/api/conversation/process"
    headers = {
        "Authorization": f"Bearer {SUPERVISOR_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "text": text,
        "conversation_id": conversation_id or f"twilio_{user_id}",
        "language": "en",
        "agent_id": CONVERSATION_AGENT_ID,
    }

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
        agent_id=CONVERSATION_AGENT_ID,
        text_length=len(text),
    )

    async with httpx.AsyncClient(timeout=30) as client:
        ha_raw = await client.post(ha_url, json=payload, headers=headers)

    log_timing(
        "home_assistant_conversation_response_received",
        user_id=user_id,
        conversation_id=payload["conversation_id"],
        status_code=ha_raw.status_code,
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


async def send_to_elspeth_twilio_conversation(text: str, user_id: str, conversation_id: str) -> str:
    """Hand one caller turn to Elspeth Core via the narrow, response-only
    elspeth_local.twilio_conversation service.

    This is the default path (used whenever CONVERSATION_AGENT_ID is
    unset). It signs user_id through the same HA-side identity mapping the
    Home Assistant voice channel uses, so Core can tell one Twilio caller
    from another and apply its self-declaration/PIN ladder per caller --
    Core falls back to household_unknown for any unmapped/placeholder id
    (e.g. UNKNOWN_CALLER_HA_USER_ID). send_to_home_assistant_conversation()
    above is the fallback for a community deployment without elspeth_local
    installed, which cannot offer that per-caller identity.
    """
    result, error = await ha_websocket_request({
        "type": "call_service",
        "domain": "elspeth_local",
        "service": "twilio_conversation",
        "service_data": {
            "text": text,
            "conversation_id": conversation_id,
            "ha_user_id": user_id,
        },
        "return_response": True,
    })
    if error:
        raise RuntimeError(f"Elspeth Core request failed: {error}")
    response = (result or {}).get("response") or {}
    reply = response.get("reply")
    if not reply:
        raise RuntimeError("Elspeth Core response was missing reply text")
    return reply


@app.get("/")
async def root():
    return {"status": "ok"}


# A2P 10DLC campaign registration requires live privacy-policy and
# terms-and-conditions URLs. This add-on's public domain (PUBLIC_BASE_URL)
# already exists for Twilio's own webhooks, so these two static pages are
# served from here rather than standing up separate hosting.
_LEGAL_STYLE = """
<style>
  body{font-family:Georgia,'Times New Roman',serif;line-height:1.6;max-width:680px;
       margin:0 auto;padding:2.5rem 1.5rem 4rem;color:#2a2622;background:#faf9f7;}
  h1{font-size:1.6rem;margin-bottom:.25rem;}
  .updated{color:#6b6459;font-size:.9rem;margin-bottom:2rem;}
  h2{font-size:1.1rem;margin-top:2rem;border-bottom:1px solid #e5e0d8;padding-bottom:.3rem;}
  li{margin-bottom:.5rem;}
  .contact{margin-top:2.5rem;padding-top:1rem;border-top:1px solid #e5e0d8;color:#6b6459;font-size:.95rem;}
</style>
"""

_PRIVACY_HTML = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Elspeth Privacy Policy</title>{_LEGAL_STYLE}</head>
<body>
<h1>Privacy Policy — Elspeth Household Assistant</h1>
<p class="updated">Effective September 12, 2026</p>
<p>Elspeth is a personal, non-commercial household assistant project. This policy covers the SMS messaging feature of that project.</p>
<h2>What we collect and why</h2>
<p>We hold the mobile phone number of each household member who has verbally agreed to receive messages from Elspeth, solely for the purpose of sending those messages (for example, status updates or estimated arrival times).</p>
<h2>How your number is used</h2>
<ul>
  <li>Your phone number is used only to send you messages you've agreed to receive from this household assistant.</li>
  <li>We do not sell, rent, trade, or share your mobile phone number or opt-in status with any third party or affiliate for marketing or any other purpose.</li>
  <li>Your number is not used for any purpose outside this household messaging feature.</li>
</ul>
<h2>Message frequency</h2>
<p>Message frequency varies and is occasional, sent only as needed (for example, when a status update is requested). This is not a recurring or scheduled marketing program.</p>
<h2>Message and data rates</h2>
<p>Message and data rates may apply, depending on your mobile carrier and plan.</p>
<h2>Opting out</h2>
<p>Reply <strong>STOP</strong> to any message at any time to opt out of receiving further messages. Reply <strong>HELP</strong> for assistance.</p>
<h2>Contact</h2>
<p class="contact">Questions about this policy can be directed to the account holder directly.</p>
</body></html>
"""

_TERMS_HTML = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Elspeth Terms &amp; Conditions</title>{_LEGAL_STYLE}</head>
<body>
<h1>Terms &amp; Conditions — Elspeth SMS Messaging</h1>
<p class="updated">Effective September 12, 2026</p>
<p>Elspeth is a personal, non-commercial household assistant. This page describes the terms of its SMS messaging feature.</p>
<h2>The service</h2>
<p>Elspeth may send short SMS messages to household members who have verbally agreed to receive them — for example, status updates or estimated arrival times, sent by request or as part of ordinary household use.</p>
<h2>Enrollment</h2>
<p>Only phone numbers belonging to household members who have given explicit verbal consent are enrolled to receive messages. There is no public sign-up; enrollment is managed directly by the account holder.</p>
<h2>Message frequency</h2>
<p>Message frequency varies and is occasional, sent only as needed. This is not a recurring or scheduled marketing program.</p>
<h2>Message and data rates</h2>
<p>Message and data rates may apply, depending on your mobile carrier and plan.</p>
<h2>Opting out and help</h2>
<ul>
  <li>Reply <strong>STOP</strong> to any message at any time to opt out of receiving further messages.</li>
  <li>Reply <strong>HELP</strong> for assistance.</li>
</ul>
<h2>Privacy</h2>
<p>See the <a href="/legal/privacy">Privacy Policy</a> for how your phone number is handled.</p>
<h2>Contact</h2>
<p class="contact">Questions about these terms can be directed to the account holder directly.</p>
</body></html>
"""


_CONSENT_HTML = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Elspeth Consent Evidence</title>{_LEGAL_STYLE}</head>
<body>
<h1>Verbal Consent Evidence — Elspeth SMS Messaging</h1>
<p class="updated">Effective September 12, 2026</p>
<p>Elspeth's SMS feature has no public sign-up form. Consent is collected verbally, in person, between members of the same household. This page documents exactly how that consent is obtained, for verification purposes.</p>
<h2>Who is enrolled</h2>
<p>Only two people: Eric Goforth (the account holder) and Shelley Goforth, members of the same household. No one outside this household is ever enrolled.</p>
<h2>The verbal consent script</h2>
<p>Before enrolling a household member's phone number, the account holder speaks the following disclosure to them directly, in person:</p>
<blockquote>
"I'd like to set up Elspeth, our household assistant, to be able to text you things like my ETA or a status update. You'll get occasional texts, only when something like that comes up -- not a regular schedule. Message and data rates may apply. You can reply STOP at any time to stop receiving these, or HELP if you need assistance. Is that okay with you?"
</blockquote>
<p>Enrollment only proceeds after the household member responds "yes" or otherwise gives clear verbal agreement. That response is the enrollment event; there is no separate confirmation step, since it happens in the same conversation.</p>
<h2>What the household member is told</h2>
<ul>
  <li>The brand/service name: Elspeth.</li>
  <li>What will be sent: occasional status updates or ETA information.</li>
  <li>Message frequency: occasional, not scheduled or recurring.</li>
  <li>That message and data rates may apply.</li>
  <li>How to opt out (reply STOP) and get help (reply HELP).</li>
</ul>
<h2>Related pages</h2>
<p>See the <a href="/legal/privacy">Privacy Policy</a> and <a href="/legal/terms">Terms &amp; Conditions</a> for how phone numbers are handled and the full terms of this feature.</p>
<h2>Contact</h2>
<p class="contact">Questions about this consent process can be directed to the account holder directly.</p>
</body></html>
"""


@app.get("/legal/consent")
async def legal_consent():
    return HTMLResponse(_CONSENT_HTML)


@app.get("/legal/privacy")
async def legal_privacy():
    return HTMLResponse(_PRIVACY_HTML)


@app.get("/legal/terms")
async def legal_terms():
    return HTMLResponse(_TERMS_HTML)


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
    ha_user_id, display_name, normalized_from = await find_person_by_phone(From)
    masked_from = mask_phone_number(normalized_from or From)
    log_timing(
        "inbound_call_received",
        call_sid=CallSid,
        runtime_mode="conversation_relay",
        caller=masked_from,
    )

    if ha_user_id:
        log_timing(
            "caller_registered_number_matched",
            call_sid=CallSid,
            caller=masked_from,
            user_id=ha_user_id,
        )
        return redirect_to_start_session(ha_user_id, display_name, CallSid)

    # Unrecognized number: no DTMF PIN prompt. The caller is connected
    # straight into the same conversation-relay session as a known caller,
    # carrying a placeholder identity that will not map to any real HA
    # user -- Core (elspeth-core/app.py) resolves that to household_unknown
    # and its own self-declaration + PIN elevation ladder takes over from
    # there, spoken and turn-by-turn, exactly like the Home Assistant voice
    # channel: "To whom am I speaking?" / stated name / "Can you verify your
    # PIN?" / spoken PIN. Nothing else is answered until that PIN verifies.
    log_timing(
        "unknown_caller_routed_to_identity_ladder",
        call_sid=CallSid,
        caller=masked_from,
    )
    return redirect_to_start_session(UNKNOWN_CALLER_HA_USER_ID, "unknown", CallSid)


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
                    if CONVERSATION_AGENT_ID:
                        reply = await send_to_home_assistant_conversation(
                            transcript,
                            user_id,
                            conversation_id,
                        )
                    else:
                        reply = await send_to_elspeth_twilio_conversation(
                            transcript,
                            user_id,
                            conversation_id,
                        )
                except Exception:
                    print("Conversation Relay Elspeth Core request failed.")
                    traceback.print_exc()
                    reply = "Sorry, Elspeth is temporarily unavailable."

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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
