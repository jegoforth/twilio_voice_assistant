"""The Claude line: a second Twilio number that connects a household member
straight to Claude instead of Elspeth.

It is built for talking while driving. Twilio Conversation Relay handles the
audio both ways (Deepgram speech-to-text in, ElevenLabs voice out, the same
setup Elspeth uses), and this module sits in the middle:

    Twilio call -> /claude/incoming_call -> ConversationRelay
        -> /claude/relay websocket -> Claude (streamed) -> spoken reply

Claude's replies are streamed to Twilio token by token, so the caller hears
the first words while the rest is still being written. If the caller starts
talking over Claude, the reply in flight is cancelled at once.

Claude has three tools on the call:

- send_to_project: hands a request to Eric's Claude project by firing a
  Claude Code scheduled task (API trigger). The project's coordinator picks
  it up and starts threads.
- get_project_status: reads back the latest status digest the project
  posted to /claude/project_status.
- end_call: hangs up after Claude says goodbye.

It also has web search, for questions asked on the road.

Everything here is off unless anthropic_api_key is set in the App options.
This module is imported at the bottom of main.py, after every helper it uses
is defined.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import time
import traceback
from dataclasses import dataclass, field
from html import escape

import anthropic
import httpx
from urllib.parse import quote

from fastapi import APIRouter, Form, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response

import main as core

router = APIRouter()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
CLAUDE_LINE_ENABLED = bool(ANTHROPIC_API_KEY)
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "").strip() or "claude-opus-5-5"
CLAUDE_EFFORT = os.getenv("CLAUDE_EFFORT", "").strip().lower() or "low"
if CLAUDE_EFFORT not in {"low", "medium", "high"}:
    CLAUDE_EFFORT = "low"
CLAUDE_FAST_MODE = os.getenv("CLAUDE_FAST_MODE", "false").strip().lower() == "true"
CLAUDE_WEB_SEARCH = os.getenv("CLAUDE_WEB_SEARCH", "true").strip().lower() != "false"
CLAUDE_VOICE = os.getenv("CLAUDE_VOICE", "").strip()
# HA user ids allowed on this line, comma separated. Empty means any caller
# HA Extended User Management recognizes. Unrecognized numbers are always
# turned away: this line has no spoken PIN ladder of its own.
CLAUDE_ALLOWED_USER_IDS = {
    value.strip()
    for value in os.getenv("CLAUDE_ALLOWED_USER_IDS", "").split(",")
    if value.strip()
}
# A call that drops (a dead zone, a tunnel) and is redialed within this many
# minutes picks the conversation back up instead of starting over.
CLAUDE_RESUME_MINUTES = 20
CLAUDE_MAX_HISTORY_MESSAGES = 120

PROJECT_FIRE_URL = os.getenv("CLAUDE_PROJECT_FIRE_URL", "").strip()
PROJECT_FIRE_TOKEN = os.getenv("CLAUDE_PROJECT_FIRE_TOKEN", "").strip()
PROJECT_STATUS_SECRET = os.getenv("CLAUDE_PROJECT_STATUS_SECRET", "").strip()
PROJECT_STATUS_PATH = "/data/claude-project-status.json"
PROJECT_STATUS_MAX_CHARS = 4000

ROUTINE_BETA = "experimental-cc-routine-2026-04-01"
FALLBACK_BETA = "server-side-fallback-2026-07-01"

LINE_TOKEN_TTL_SECONDS = 120
# Caller ID can be spoofed, so by default the caller also says (or keys)
# their HA Extended User Management PIN before Claude picks up. This is done
# by Twilio <Gather> and checked in Home Assistant: the PIN never reaches
# Claude. A dropped call redialed within the resume window skips it.
CLAUDE_REQUIRE_PIN = os.getenv("CLAUDE_REQUIRE_PIN", "true").strip().lower() != "false"
PIN_ATTEMPTS = 3
SPOKEN_DIGITS = {
    "zero": "0", "oh": "0", "o": "0", "one": "1", "won": "1", "two": "2", "to": "2",
    "too": "2", "three": "3", "four": "4", "for": "4", "five": "5", "six": "6",
    "seven": "7", "eight": "8", "ate": "8", "nine": "9",
}

# Said without asking Claude, so hanging up never waits on the API. Kept to
# phrases that can't be an answer to a question Claude asked ("no thanks"
# is left to Claude, since it may mean "don't send that").
HANGUP_PHRASES = {"goodbye", "good bye", "bye", "bye bye", "hang up", "end call", "end the call"}

_client: anthropic.AsyncAnthropic | None = None


def client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY, max_retries=2)
    return _client


# --- Line session tokens ----------------------------------------------------
#
# Signed with a key derived for this line only, so a token minted for an
# Elspeth call can't open a Claude call or the other way around.

def _line_key() -> bytes:
    return hmac.new(
        core.TWILIO_AUTH_TOKEN.encode("utf-8"), b"claude-line-session", hashlib.sha256
    ).digest()


def create_line_token(
    user_id: str,
    user_name: str,
    call_sid: str | None,
    stage: str = "relay",
    **extra,
) -> str:
    """stage "relay" opens /claude/relay; stage "pin" only carries a caller
    from the PIN prompt to /claude/check_pin. Neither works as the other."""
    payload = {
        "user_id": user_id,
        "user_name": user_name,
        "created": int(time.time()),
        "stage": stage,
        **extra,
    }
    if call_sid:
        payload["call_sid"] = call_sid
    body = core.base64url_encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    sig = hmac.new(_line_key(), body.encode("ascii"), hashlib.sha256).digest()
    return f"{body}.{core.base64url_encode(sig)}"


def validate_line_token(token: str | None, stage: str = "relay") -> dict | None:
    if not token or "." not in token:
        return None
    body, sig = token.split(".", 1)
    expected = hmac.new(_line_key(), body.encode("ascii"), hashlib.sha256).digest()
    try:
        if not hmac.compare_digest(core.base64url_decode(sig), expected):
            return None
        payload = json.loads(core.base64url_decode(body).decode("utf-8"))
    except Exception:
        return None
    created = payload.get("created")
    if not isinstance(created, int) or time.time() - created > LINE_TOKEN_TTL_SECONDS:
        return None
    if not payload.get("user_id") or not payload.get("user_name"):
        return None
    if payload.get("stage") != stage:
        return None
    return payload


# --- Conversation state -----------------------------------------------------

@dataclass
class Conversation:
    messages: list = field(default_factory=list)
    last_active: float = field(default_factory=time.time)
    # Set when the call ended on purpose (a goodbye), so the next call
    # starts fresh instead of resuming.
    ended: bool = False


_conversations: dict[str, Conversation] = {}
# When each caller last passed the PIN check, so a redial after a dropped
# call doesn't ask again. Cleared when a call ends with a goodbye.
_pin_verified_at: dict[str, float] = {}


def conversation_for(user_id: str) -> tuple[Conversation, bool]:
    """Return the caller's conversation and whether it was resumed."""
    convo = _conversations.get(user_id)
    fresh_enough = convo and time.time() - convo.last_active < CLAUDE_RESUME_MINUTES * 60
    if (
        convo
        and fresh_enough
        and not convo.ended
        and convo.messages
        and len(convo.messages) < CLAUDE_MAX_HISTORY_MESSAGES
    ):
        convo.last_active = time.time()
        return convo, True
    convo = Conversation()
    _conversations[user_id] = convo
    return convo, False


def system_prompt(user_name: str) -> str:
    today = time.strftime("%A, %B %-d, %Y")
    project_line = (
        "You can hand work to Eric's Claude project with send_to_project, and read back "
        "what it last reported with get_project_status."
        if PROJECT_FIRE_URL and PROJECT_FIRE_TOKEN
        else "The project tools are not set up yet; if asked, say so briefly."
    )
    return f"""You are Claude, talking with {user_name} on a phone call. Today is {today}.
{user_name} is usually driving, so this is a hands-free voice conversation.

How to talk:
- Everything you write is spoken aloud by a text-to-speech voice. Write plain spoken sentences: no markdown, lists, headings, emoji, URLs, or code.
- Keep replies short: one to three sentences unless asked for more. Lead with the answer.
- Say numbers, times, and units the way a person would say them.
- Ask at most one question at a time. If a request is unclear, ask one short clarifying question.
- Speech-to-text makes mistakes. If a word seems off, go with the most likely meaning, and check only when it matters.
- Never ask {user_name} to look at a screen, read something, or type while driving.

Tools:
- {project_line}
- Before sending anything to the project, say back the request in one sentence and wait for a yes. Then send it, and confirm in a few words.
- Before a tool call, say a few words first, like "Let me check." so there is no dead air.
- When {user_name} is done, say a short goodbye and call end_call."""


def tool_definitions() -> list[dict]:
    tools: list[dict] = []
    if PROJECT_FIRE_URL and PROJECT_FIRE_TOKEN:
        tools.append({
            "name": "send_to_project",
            "description": (
                "Send a request to Eric's Claude project, where a coordinator starts "
                "the work in its own thread. Use it for anything that should happen "
                "after the call: research, writing, code changes, reminders to follow up. "
                "Only call it after the caller has confirmed the request out loud."
            ),
            "eager_input_streaming": True,
            "input_schema": {
                "type": "object",
                "properties": {
                    "request": {
                        "type": "string",
                        "description": (
                            "The request, written out in full so it stands on its own "
                            "without this call's context."
                        ),
                    },
                },
                "required": ["request"],
                "additionalProperties": False,
            },
        })
        tools.append({
            "name": "get_project_status",
            "description": (
                "Read the latest status summary Eric's Claude project posted: which "
                "threads are working, waiting on him, or done."
            ),
            "eager_input_streaming": True,
            "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
        })
    tools.append({
        "name": "end_call",
        "description": "Hang up the call. Say goodbye first, in the same turn.",
        "eager_input_streaming": True,
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    })
    if CLAUDE_WEB_SEARCH:
        tools.append({"type": "web_search_20260209", "name": "web_search", "max_uses": 3})
    return tools


# --- Tools --------------------------------------------------------------------

async def send_to_project(request_text: str, user_name: str) -> str:
    stamp = time.strftime("%Y-%m-%d %H:%M %Z")
    text = (
        f"Voice request from {user_name}, spoken on the Claude phone line at {stamp}. "
        f"It was transcribed by speech-to-text and confirmed out loud before sending.\n\n"
        f"{request_text}"
    )
    async with httpx.AsyncClient(timeout=10) as http:
        response = await http.post(
            PROJECT_FIRE_URL,
            headers={
                "Authorization": f"Bearer {PROJECT_FIRE_TOKEN}",
                "anthropic-beta": ROUTINE_BETA,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            json={"text": text},
        )
    core.log_timing("claude_project_fire", status_code=response.status_code)
    if response.status_code >= 300:
        return f"Sending failed with HTTP {response.status_code}. Tell the caller it didn't go through."
    return "Sent. The project has it and will start on it."


def read_project_status() -> str:
    try:
        with open(PROJECT_STATUS_PATH, "r", encoding="utf-8") as handle:
            status = json.load(handle)
    except FileNotFoundError:
        return "The project hasn't posted a status yet."
    except Exception:
        return "The status file couldn't be read."
    age_minutes = int((time.time() - status.get("updated_at", 0)) / 60)
    return f"Posted {age_minutes} minutes ago: {status.get('summary', '')}"


async def run_tool(name: str, tool_input, user_name: str) -> tuple[str, bool]:
    """Run one client tool. Returns (result text, is_error)."""
    if not isinstance(tool_input, dict):
        return "Invalid tool input.", True
    if name == "send_to_project":
        request_text = tool_input.get("request")
        if not isinstance(request_text, str) or not request_text.strip():
            return "Invalid tool input: request must be a non-empty string.", True
        try:
            return await send_to_project(request_text.strip(), user_name), False
        except Exception as exc:
            return f"Sending failed ({type(exc).__name__}). Tell the caller it didn't go through.", True
    if name == "get_project_status":
        return read_project_status(), False
    if name == "end_call":
        return "Hanging up after this turn.", False
    return f"Unknown tool {name}.", True


# --- The call -----------------------------------------------------------------

class Speaker:
    """Streams Claude's text to Conversation Relay as it arrives."""

    def __init__(self, websocket: WebSocket):
        self.websocket = websocket
        self.spoke_this_turn = False

    async def say(self, text: str, last: bool = False):
        if not text and not last:
            return
        self.spoke_this_turn = self.spoke_this_turn or bool(text.strip())
        await self.websocket.send_text(json.dumps({
            "type": "text",
            "token": text,
            "last": last,
            "lang": core.CONVERSATION_RELAY_LANGUAGE,
        }))


async def claude_turn(convo: Conversation, speaker: Speaker, user_name: str) -> bool:
    """Run Claude until it stops calling tools. Returns True to hang up.

    Messages are appended to convo.messages only when a full assistant turn
    (and its tool results) is done, so a turn cancelled by an interruption
    never leaves half a tool exchange in the history.
    """
    hang_up = False
    json_retries = 0
    tools = tool_definitions()
    started = time.monotonic()
    first_token_logged = False

    while True:
        speaker.spoke_this_turn = False
        params = dict(
            model=CLAUDE_MODEL,
            max_tokens=2048,
            system=system_prompt(user_name),
            messages=convo.messages,
            tools=tools,
            output_config={"effort": CLAUDE_EFFORT},
            cache_control={"type": "ephemeral"},
            betas=[FALLBACK_BETA],
            fallbacks="default",
        )
        if CLAUDE_FAST_MODE:
            params["speed"] = "fast"
        try:
            async with client().beta.messages.stream(**params) as stream:
                async for event in stream:
                    if event.type == "text":
                        if not first_token_logged:
                            first_token_logged = True
                            core.log_timing(
                                "claude_first_token",
                                ms=int((time.monotonic() - started) * 1000),
                            )
                        await speaker.say(event.text)
                    elif (
                        event.type == "content_block_start"
                        and getattr(event.content_block, "type", "") == "server_tool_use"
                        and not speaker.spoke_this_turn
                    ):
                        await speaker.say("Let me look that up. ")
                response = await stream.get_final_message()
            json_retries = 0
        except ValueError:
            # Tool input JSON the SDK couldn't parse at all. Nothing ran;
            # ask again, a bounded number of times.
            json_retries += 1
            if json_retries > 2:
                raise
            continue

        convo.messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "pause_turn":
            continue
        if response.stop_reason == "refusal":
            if not speaker.spoke_this_turn:
                await speaker.say("Sorry, I can't help with that one.")
            break

        tool_uses = [block for block in response.content if block.type == "tool_use"]
        if not tool_uses:
            break
        if response.stop_reason == "max_tokens":
            # A tool input cut off mid-way. Don't run it; say so and stop.
            convo.messages.pop()
            await speaker.say("Sorry, I lost my train of thought. Could you say that again?")
            break

        results = []
        for block in tool_uses:
            if block.name == "end_call":
                hang_up = True
            text, is_error = await run_tool(block.name, block.input, user_name)
            result = {"type": "tool_result", "tool_use_id": block.id, "content": text}
            if is_error:
                result["is_error"] = True
            results.append(result)
        convo.messages.append({"role": "user", "content": results})
        if hang_up:
            break

    await speaker.say("", last=True)
    return hang_up


def is_hangup_phrase(text: str) -> bool:
    return core.normalize_command(text) in HANGUP_PHRASES


async def end_call(websocket: WebSocket, reason: str):
    await websocket.send_text(json.dumps({
        "type": "end",
        "handoffData": json.dumps({"reason": reason}),
    }))


async def lookup_caller(from_number: str | None):
    """Return (person_entity_id, ha_user_id, display_name, normalized_from)
    for a caller, via HA Extended User Management's find_person_by_phone."""
    normalized_from = core.normalize_phone_number(from_number)
    if not normalized_from:
        return None, None, None, normalized_from
    result, error = await core.ha_websocket_request({
        "type": "call_service",
        "domain": "extended_user_management",
        "service": "find_person_by_phone",
        "service_data": {"phone_number": normalized_from},
        "return_response": True,
    })
    if error:
        print(f"WARNING: find_person_by_phone lookup failed: {error}")
        return None, None, None, normalized_from
    person_entity_id = ((result or {}).get("response") or {}).get("person_entity_id")
    if not person_entity_id:
        return None, None, None, normalized_from
    ha_user_id, display_name = await core.resolve_person_ha_user(person_entity_id)
    return person_entity_id, ha_user_id, display_name, normalized_from


async def verify_pin(person_entity_id: str, pin: str) -> tuple[bool, bool]:
    """Check a PIN in Home Assistant. Returns (verified, locked_out)."""
    result, error = await core.ha_websocket_request({
        "type": "call_service",
        "domain": "extended_user_management",
        "service": "verify_pin",
        "service_data": {"person_entity_id": person_entity_id, "pin": pin},
        "return_response": True,
    })
    if error:
        print(f"WARNING: verify_pin failed: {error}")
        return False, False
    response = (result or {}).get("response") or {}
    return bool(response.get("verified")), bool(response.get("locked_out"))


def pin_from_speech(speech: str | None, digits: str | None) -> str:
    """Turn keyed digits or a spoken PIN ("one two three four", "1234",
    "twelve thirty four" is not supported) into a digit string."""
    if digits and digits.strip("#*").isdigit():
        return digits.strip("#*")
    out = []
    for word in core.normalize_command(speech or "").replace("-", " ").split():
        if word.isdigit():
            out.append(word)
        elif word in SPOKEN_DIGITS:
            out.append(SPOKEN_DIGITS[word])
    return "".join(out)


def pin_prompt_twiml(token: str, prompt: str) -> Response:
    action = f"/claude/check_pin?t={quote(token)}"
    return core.twiml_response(f"""
    <Response>
        <Gather input="speech dtmf" action="{escape(action, quote=True)}" method="POST"
                timeout="6" speechTimeout="auto" finishOnKey="#" hints="zero, one, two, three, four, five, six, seven, eight, nine">
            <Say>{escape(prompt)}</Say>
        </Gather>
        <Say>I didn't catch that. Goodbye.</Say>
        <Hangup/>
    </Response>
    """)


def connect_twiml(ha_user_id: str, user_name: str, call_sid: str | None) -> Response:
    convo, resumed = conversation_for(ha_user_id)
    greeting = (
        f"Hey {user_name}, we got cut off. Picking up where we left off."
        if resumed
        else f"Hey {user_name}, it's Claude. What's up?"
    )
    token = create_line_token(ha_user_id, user_name, call_sid)
    # CLAUDE_VOICE, when set, gives this line its own voice so Claude
    # doesn't sound like Elspeth.
    attrs = core.relay_attributes(
        core.public_websocket_url("/claude/relay"), greeting, voice=CLAUDE_VOICE or None
    )
    return core.twiml_response(f"""
    <Response>
        <Connect action="/conversation_relay/status">
            <ConversationRelay {attrs}>
                <Parameter name="session_token" value="{escape(token, quote=True)}"/>
            </ConversationRelay>
        </Connect>
    </Response>
    """)


def recently_verified(ha_user_id: str) -> bool:
    return time.time() - _pin_verified_at.get(ha_user_id, 0) < CLAUDE_RESUME_MINUTES * 60


def mark_call_ended(ha_user_id: str):
    convo = _conversations.get(ha_user_id)
    if convo:
        convo.ended = True
    _pin_verified_at.pop(ha_user_id, None)


@router.post("/claude/incoming_call")
async def claude_incoming_call(
    request: Request,
    From: str = Form(None),
    CallSid: str = Form(None),
):
    await core.validate_twilio_http_request(request, route="/claude/incoming_call", call_sid=CallSid)
    if not CLAUDE_LINE_ENABLED:
        return core.polite_hangup("This line isn't set up yet. Goodbye.")

    person_entity_id, ha_user_id, display_name, normalized_from = await lookup_caller(From)
    masked_from = core.mask_phone_number(normalized_from or From)
    allowed = bool(ha_user_id) and (
        not CLAUDE_ALLOWED_USER_IDS or ha_user_id in CLAUDE_ALLOWED_USER_IDS
    )
    core.log_timing(
        "claude_line_call_received",
        call_sid=CallSid,
        caller=masked_from,
        allowed=allowed,
    )
    if not allowed:
        return core.polite_hangup("Sorry, this line only takes calls from known numbers. Goodbye.")

    user_name = display_name or "there"
    if CLAUDE_REQUIRE_PIN and not recently_verified(ha_user_id):
        token = create_line_token(
            ha_user_id, user_name, CallSid, stage="pin",
            person_entity_id=person_entity_id, attempt=1,
        )
        return pin_prompt_twiml(token, f"Hi {user_name}. What's your PIN?")
    return connect_twiml(ha_user_id, user_name, CallSid)


@router.post("/claude/check_pin")
async def claude_check_pin(
    request: Request,
    t: str | None = None,
    SpeechResult: str = Form(None),
    Digits: str = Form(None),
    CallSid: str = Form(None),
):
    await core.validate_twilio_http_request(request, route="/claude/check_pin", call_sid=CallSid)
    payload = validate_line_token(t, stage="pin")
    if not payload or not payload.get("person_entity_id"):
        return core.polite_hangup("Sorry, that took too long. Please call again.")
    if payload.get("call_sid") and CallSid and payload["call_sid"] != CallSid:
        return core.polite_hangup("Goodbye.")

    ha_user_id = payload["user_id"]
    user_name = payload["user_name"]
    attempt = int(payload.get("attempt") or 1)
    pin = pin_from_speech(SpeechResult, Digits)
    verified, locked_out = (False, False)
    if pin:
        verified, locked_out = await verify_pin(payload["person_entity_id"], pin)
    core.log_timing(
        "claude_line_pin_check",
        call_sid=CallSid,
        attempt=attempt,
        verified=verified,
        locked_out=locked_out,
        heard_digits=len(pin),
    )
    if verified:
        _pin_verified_at[ha_user_id] = time.time()
        return connect_twiml(ha_user_id, user_name, CallSid)
    if locked_out or attempt >= PIN_ATTEMPTS:
        return core.polite_hangup("Sorry, I couldn't verify that. Goodbye.")
    token = create_line_token(
        ha_user_id, user_name, payload.get("call_sid"), stage="pin",
        person_entity_id=payload["person_entity_id"], attempt=attempt + 1,
    )
    return pin_prompt_twiml(token, "That didn't match. Try once more, or key it in.")


@router.websocket("/claude/relay")
async def claude_relay(websocket: WebSocket):
    await websocket.accept()
    user_id = None
    user_name = "there"
    call_sid = None
    convo: Conversation | None = None
    speaker = Speaker(websocket)
    current: asyncio.Task | None = None
    # The text the caller actually heard before cutting in, kept so Claude
    # knows where it was interrupted.
    heard_before_interrupt: str | None = None

    async def respond():
        try:
            hang_up = await claude_turn(convo, speaker, user_name)
            if hang_up:
                mark_call_ended(user_id)
                await end_call(websocket, "claude_ended_call")
        except asyncio.CancelledError:
            raise
        except Exception:
            print("Claude line turn failed.")
            traceback.print_exc()
            await speaker.say("Sorry, I hit a problem on my end. Try that again?", last=True)

    async def cancel_current():
        nonlocal current
        if current and not current.done():
            current.cancel()
            try:
                await current
            except (asyncio.CancelledError, Exception):
                pass
        current = None

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                continue
            kind = message.get("type")

            if kind == "setup":
                params = message.get("customParameters") or {}
                payload = validate_line_token(params.get("session_token"))
                if not payload:
                    core.log_timing("session_token_validation", route="/claude/relay", result="invalid")
                    await websocket.close(code=1008)
                    return
                user_id = payload["user_id"]
                user_name = payload["user_name"]
                call_sid = payload.get("call_sid")
                # connect_twiml already chose to resume or start fresh.
                convo = _conversations.get(user_id) or conversation_for(user_id)[0]
                continue

            if convo is None:
                await websocket.close(code=1008)
                return

            if kind == "interrupt":
                heard_before_interrupt = message.get("utteranceUntilInterrupt") or ""
                await cancel_current()
                continue

            if kind != "prompt" or not message.get("last"):
                continue

            transcript = (message.get("voicePrompt") or "").strip()
            if not transcript:
                continue
            await cancel_current()
            convo.last_active = time.time()
            core.log_call_transcript(
                call_sid=call_sid, user_id=user_id, conversation_id="claude_line",
                speaker="caller", text=transcript,
            )

            if is_hangup_phrase(transcript):
                mark_call_ended(user_id)
                await speaker.say("Talk soon. Bye.", last=True)
                await end_call(websocket, "caller_ended_call")
                continue

            # If the caller cut in, say how much of the reply they heard, so
            # Claude doesn't assume all of it landed. This goes on the new
            # user turn rather than rewriting the assistant turn: history
            # stays append-only, which keeps thinking blocks valid.
            content = transcript
            if heard_before_interrupt is not None:
                content = (
                    f"[I cut in while you were talking. I heard up to: "
                    f"\"{heard_before_interrupt}\"]\n{transcript}"
                )
                heard_before_interrupt = None
            convo.messages.append({"role": "user", "content": content})
            current = asyncio.create_task(respond())

    except WebSocketDisconnect:
        core.log_timing("call_ended", route="/claude/relay", reason="websocket_disconnect")
    except Exception:
        print("Exception in Claude line websocket:")
        traceback.print_exc()
    finally:
        await cancel_current()
        if convo:
            convo.last_active = time.time()


@router.post("/claude/project_status")
async def claude_project_status(request: Request):
    """The project posts a short spoken-style status digest here, which
    get_project_status reads back on the next call."""
    if not PROJECT_STATUS_SECRET:
        raise HTTPException(status_code=404)
    supplied = request.headers.get("authorization", "")
    if not hmac.compare_digest(supplied, f"Bearer {PROJECT_STATUS_SECRET}"):
        raise HTTPException(status_code=401)
    try:
        body = await request.json()
        summary = str(body.get("summary", "")).strip()
    except Exception:
        raise HTTPException(status_code=400, detail="Expected JSON with a summary field")
    if not summary:
        raise HTTPException(status_code=400, detail="summary is empty")
    status = {"updated_at": int(time.time()), "summary": summary[:PROJECT_STATUS_MAX_CHARS]}
    tmp_path = PROJECT_STATUS_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(status, handle)
    os.replace(tmp_path, PROJECT_STATUS_PATH)
    core.log_timing("claude_project_status_updated", length=len(status["summary"]))
    return {"ok": True}
