from fastapi import FastAPI, Form
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
from elevenlabs import ElevenLabs

app = FastAPI()
app.mount("/audio", StaticFiles(directory="/share"), name="audio")

# Faster model for phone audio
model = whisper.load_model("tiny")

# Load configuration from environment
ELEVEN_API_KEY = os.getenv("ELEVEN_API_KEY", "")
ELEVEN_VOICE_ID = os.getenv("ELEVEN_VOICE_ID", "")
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
SUPERVISOR_TOKEN = os.getenv("SUPERVISOR_TOKEN")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
DEBUG = os.getenv("DEBUG", "false").lower() == "true"

# Validate required credentials
required_vars = {
    "TWILIO_ACCOUNT_SID": TWILIO_ACCOUNT_SID,
    "TWILIO_AUTH_TOKEN": TWILIO_AUTH_TOKEN,
    "ELEVEN_API_KEY": ELEVEN_API_KEY,
    "ELEVEN_VOICE_ID": ELEVEN_VOICE_ID,
    "PUBLIC_BASE_URL": PUBLIC_BASE_URL,
}

missing_vars = [k for k, v in required_vars.items() if not v]
if missing_vars:
    raise RuntimeError(
        f"Missing required configuration: {', '.join(missing_vars)}. "
        "Please configure the addon with your API credentials."
    )

tts_client = ElevenLabs(api_key=ELEVEN_API_KEY)

PIN_MAP_FILE = "/share/pin_map.json"


class PinRequest(BaseModel):
    pin: str
    user_id: str
    user_name: str | None = None


def load_pin_map():
    """Load PIN map from JSON file"""
    try:
        if os.path.exists(PIN_MAP_FILE):
            with open(PIN_MAP_FILE, "r") as f:
                return json.load(f)
    except Exception as e:
        print(f"WARNING: Could not load PIN map from file: {e}")
    return {}

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


async def fetch_ha_users():
    """Fetch Home Assistant users via the admin-only websocket auth API."""
    if not SUPERVISOR_TOKEN:
        return [], "SUPERVISOR_TOKEN is not set"

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

                await websocket.send(json.dumps({
                    "id": 1,
                    "type": "config/auth/list",
                }))
                users_response = await websocket_recv_json(websocket)
                if not users_response.get("success"):
                    last_error = f"{endpoint}: user list failed: {users_response}"
                    continue

                users = users_response.get("result", [])
                filtered_users = [
                    {
                        "id": user.get("id"),
                        "name": user.get("name") or user.get("username") or user.get("id"),
                    }
                    for user in users
                    if user.get("id") and not user.get("system_generated", False)
                ]
                print(f"Found {len(filtered_users)} users at {endpoint}")
                return filtered_users, None
        except Exception as e:
            last_error = f"{endpoint}: {e}"
            print(f"Error fetching users from {endpoint}: {e}")

    return [], last_error or "Could not fetch users from Home Assistant"


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
    print(f"ELEVEN_VOICE_ID: {ELEVEN_VOICE_ID}")


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


def tts_to_wav(text: str, filename: str) -> str:
    safe_filename = filename.replace(" ", "_")
    output_path = f"/share/{safe_filename}.wav"

    audio = tts_client.text_to_speech.convert(
        voice_id=ELEVEN_VOICE_ID,
        model_id="eleven_multilingual_v2",
        text=text,
        output_format="wav_8000"
    )

    with open(output_path, "wb") as f:
        for chunk in audio:
            f.write(chunk)

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
async def admin_ui():
    """Serve the admin UI"""
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Twilio Voice Assistant - PIN Management</title>
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
            <h1>🔐 PIN Management</h1>
            
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
            async function loadUsers() {
                try {
                    const res = await fetch('/admin/api/users');
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
                    const res = await fetch('/admin/api/pins');
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
                    const res = await fetch('/admin/api/pins', {
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
                    const res = await fetch(`/admin/api/pins/${pin}`, { method: 'DELETE' });
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

            loadUsers();
            loadPins();
            setInterval(loadPins, 5000);
        </script>
    </body>
    </html>
    """


@app.get("/admin/api/users")
async def get_users():
    """Get list of Home Assistant users"""
    users, error = await fetch_ha_users()
    response = {"users": users}
    if error:
        response["error"] = error
    return response


@app.get("/admin/api/pins")
async def get_pins():
    """Get current PIN mappings"""
    global PIN_MAP
    PIN_MAP = load_pin_map()

    users, error = await fetch_ha_users()
    user_map = {user["id"]: user["name"] for user in users}
    if error:
        print(f"Could not fetch user names for PIN list: {error}")

    return {"pins": PIN_MAP, "userMap": user_map}


@app.post("/admin/api/pins")
async def add_pin(request: PinRequest):
    """Add a PIN mapping"""
    global PIN_MAP

    pin = request.pin
    user_id = request.user_id

    pin = pin.strip()
    if not pin or not pin.isdigit() or len(pin) != 4:
        return JSONResponse({"error": "PIN must be 4 digits"}, status_code=400)

    if not user_id:
        return JSONResponse({"error": "User ID required"}, status_code=400)

    PIN_MAP[pin] = {
        "user_id": user_id,
        "user_name": request.user_name or user_id,
    }
    if save_pin_map(PIN_MAP):
        return {
            "success": True,
            "pin": pin,
            "user_id": user_id,
            "user_name": request.user_name,
        }
    else:
        return JSONResponse({"error": "Could not save PIN"}, status_code=500)


@app.delete("/admin/api/pins/{pin}")
async def delete_pin(pin: str):
    """Delete a PIN mapping"""
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

        prompt_audio = tts_to_wav(
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

        payload = {
            "text": spoken_text,
            "conversation_id": f"twilio_{user_id}",
            "language": "en",
            "agent_id": "conversation.openai_conversation"
        }

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

        reply_audio = tts_to_wav(reply, f"{CallSid}_reply")
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
async def debug():
    """Debug endpoint - check configuration"""
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
