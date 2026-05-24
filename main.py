from fastapi import FastAPI, Form, Request
from fastapi import HTTPException
from fastapi.responses import Response, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import requests
import websockets
import asyncio
import whisper
import re
import os
import json
import traceback
from contextlib import asynccontextmanager
from urllib.parse import quote

app = FastAPI()

DATA_DIR = "/share/twilio_voice_assistant"
AUDIO_DIR = f"{DATA_DIR}/audio"
INGRESS_PROXY_IP = "172.30.32.2"
os.makedirs(AUDIO_DIR, exist_ok=True)
app.mount("/audio", StaticFiles(directory=AUDIO_DIR), name="audio")

# Faster model for phone audio
model = whisper.load_model("tiny")

# Load configuration from environment
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
SUPERVISOR_TOKEN = os.getenv("SUPERVISOR_TOKEN")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
DEBUG = os.getenv("DEBUG", "false").lower() == "true"

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

PIN_MAP_FILE = f"{DATA_DIR}/pin_map.json"
SETTINGS_FILE = f"{DATA_DIR}/settings.json"


class PinRequest(BaseModel):
    pin: str
    user_id: str
    user_name: str | None = None


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
    if isinstance(pin_entry, dict):
        user_name = pin_entry.get("user_name") or pin_entry.get("name")
        if user_name:
            return user_name

    user_id = pin_entry_user_id(pin_entry)
    return user_map.get(user_id) or user_id


PIN_MAP = load_pin_map()

if DEBUG:
    print(f"Loaded PIN_MAP with {len(PIN_MAP)} entries: {PIN_MAP}")
    print(f"Loaded settings: {load_settings()}")


def twiml_response(xml: str):
    return Response(content=xml.strip(), media_type="application/xml")


def public_audio_url(filename: str) -> str:
    return f"{PUBLIC_BASE_URL}/audio/{filename}"


def safe_redirect_to_session(user_id: str, user_name: str | None, message: str):
    encoded_user_id = quote(user_id)
    encoded_user_name = quote(user_name or user_id)
    return twiml_response(f"""
    <Response>
        <Say>{message}</Say>
        <Redirect>/start_session?user_id={encoded_user_id}&amp;user_name={encoded_user_name}</Redirect>
    </Response>
    """)


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
    """Fill missing TTS settings from available Home Assistant engines."""
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


def download_twilio_recording(recording_url: str, audio_path: str) -> bool:
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
            input[type="text"], select {
                width: 100%;
                padding: 10px;
                border: 1px solid #ddd;
                border-radius: 4px;
                font-size: 14px;
                font-family: inherit;
            }
            input[type="text"]:focus, select:focus {
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
            </div>

            <div class="pins-list">
                <h2 style="font-size: 18px; margin-bottom: 15px; color: #333;">Current PINs</h2>
                <div id="pinsList">
                    <p style="color: #999; text-align: center; padding: 20px;">Loading...</p>
                </div>
            </div>
        </div>

        <script>
            const ADMIN_API_BASE = __ADMIN_API_BASE__;
            let appSettings = {};
            let ttsEngines = [];

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
                    const select = document.getElementById('user');
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
                    document.getElementById('user').innerHTML = '<option value="">Error loading users</option>';
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

                    let html = '';
                    for (const [pin, pinEntry] of Object.entries(data.pins)) {
                        const userId = typeof pinEntry === 'object' ? pinEntry.user_id : pinEntry;
                        const savedName = typeof pinEntry === 'object' ? pinEntry.user_name : null;
                        const userName = savedName || data.userMap?.[userId] || userId;
                        html += `
                            <div class="pin-item">
                                <div class="pin-info">
                                    <strong>${pin}</strong>
                                    <div class="pin-user">${userName}</div>
                                </div>
                                <button class="delete" onclick="deletePin('${pin}')">Delete</button>
                            </div>
                        `;
                    }
                    list.innerHTML = html;
                } catch (err) {
                    console.error('Error loading pins:', err);
                    document.getElementById('pinsList').innerHTML = '<p style="color: #999; text-align: center;">Error loading PINs</p>';
                }
            }

            async function addPin() {
                const pin = document.getElementById('pin').value.trim();
                const userId = document.getElementById('user').value;
                const status = document.getElementById('status');

                if (!pin || !userId) {
                    status.className = 'status error';
                    status.textContent = '❌ Please enter PIN and select a user';
                    return;
                }

                if (!/^[0-9]{4}$/.test(pin)) {
                    status.className = 'status error';
                    status.textContent = '❌ PIN must be exactly 4 digits';
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
                        status.textContent = '✓ PIN added successfully';
                        document.getElementById('pin').value = '';
                        document.getElementById('user').value = '';
                        setTimeout(() => { status.style.display = 'none'; }, 3000);
                        loadPins();
                    } else {
                        const error = await res.text();
                        status.className = 'status error';
                        status.textContent = '❌ Error: ' + error;
                    }
                } catch (err) {
                    status.className = 'status error';
                    status.textContent = '❌ Error: ' + err.message;
                }
            }

            async function deletePin(pin) {
                if (!confirm(`Delete PIN ${pin}?`)) return;
                
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
            loadPins();
            setInterval(loadPins, 5000);
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


# Twilio webhook endpoints
@app.post("/incoming_call")
async def incoming_call(
    From: str = Form(None),
    To: str = Form(None),
    CallSid: str = Form(None),
):
    return twiml_response("""
    <Response>
        <Say>Please say your four digit PIN after the beep.</Say>
        <Record
            maxLength="4"
            timeout="3"
            action="/check_pin"
            playBeep="true"
            trim="do-not-trim"
        />
    </Response>
    """)


@app.post("/check_pin")
async def check_pin(
    RecordingUrl: str = Form(...),
    CallSid: str = Form(...),
):
    global PIN_MAP
    PIN_MAP = load_pin_map()
    
    try:
        audio_path = f"/tmp/{CallSid}_pin.mp3"

        if not download_twilio_recording(RecordingUrl, audio_path):
            return twiml_response("""
            <Response>
                <Say>Sorry, I could not hear your PIN. Please try again.</Say>
                <Redirect>/incoming_call</Redirect>
            </Response>
            """)

        result = model.transcribe(
            audio_path,
            no_speech_threshold=0.4,
            condition_on_previous_text=False
        )
        spoken_text = result.get("text", "").strip()
        digits = normalize_digits(spoken_text)

        print("PIN spoken text:", spoken_text)
        print("PIN normalized digits:", digits)

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

        prompt_audio = await tts_to_audio(
            f"Hello {user_name}. What would you like to do?",
            f"{user_id}_prompt",
        )

        filename = os.path.basename(prompt_audio)
        public_url = public_audio_url(filename)

        return twiml_response(f"""
        <Response>
            <Play>{public_url}</Play>
                <Record
                    maxLength="7"
                    timeout="2"
                    action="/process_command?user_id={encoded_user_id}&amp;user_name={encoded_user_name}"
                    playBeep="false"
                    trim="do-not-trim"
                />
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
            return safe_redirect_to_session(
                user_id,
                user_name,
                "Sorry, I did not hear a command."
            )

        result = model.transcribe(
            audio_path,
            no_speech_threshold=0.4,
            condition_on_previous_text=False
        )
        spoken_text = result.get("text", "").strip()

        print("Command spoken text:", spoken_text)

        if not spoken_text:
            return safe_redirect_to_session(
                user_id,
                user_name,
                "Sorry, I did not hear a command."
            )

        ha_url = "http://supervisor/core/api/conversation/process"
        headers = {
            "Authorization": f"Bearer {SUPERVISOR_TOKEN}",
            "Content-Type": "application/json",
        }

        settings = load_settings()
        payload = {
            "text": spoken_text,
            "conversation_id": f"twilio_{user_id}",
            "language": "en",
        }
        if settings.get("conversation_agent_id"):
            payload["agent_id"] = settings["conversation_agent_id"]

        print("Sending to Home Assistant:", payload)

        ha_raw = requests.post(
            ha_url,
            json=payload,
            headers=headers,
            timeout=30,
        )

        print("HA status:", ha_raw.status_code)
        print("HA content-type:", ha_raw.headers.get("content-type"))
        print("HA body:", ha_raw.text[:1000])

        if ha_raw.status_code < 200 or ha_raw.status_code >= 300:
            return safe_redirect_to_session(
                user_id,
                user_name,
                "Sorry, Home Assistant returned an error."
            )

        try:
            ha_response = ha_raw.json()
        except Exception:
            print("Home Assistant did not return valid JSON.")
            traceback.print_exc()
            return safe_redirect_to_session(
                user_id,
                user_name,
                "Sorry, Home Assistant did not return a valid response."
            )

        reply = (
            ha_response
            .get("response", {})
            .get("speech", {})
            .get("plain", {})
            .get("speech")
        )

        if not reply:
            print("Unexpected HA response shape:", ha_response)
            reply = "I heard you, but Home Assistant did not provide a spoken response."

        print("HA reply:", reply)

        reply_audio = await tts_to_audio(reply, f"{CallSid}_reply")
        filename = os.path.basename(reply_audio)
        public_url = public_audio_url(filename)

        return twiml_response(f"""
        <Response>
            <Play>{public_url}</Play>
                <Record
                    maxLength="7"
                    timeout="2"
                    action="/process_command?user_id={encoded_user_id}&amp;user_name={encoded_user_name}"
                    playBeep="false"
                    trim="do-not-trim"
                />
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
