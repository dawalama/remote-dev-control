"""Phone voice calling channel via Twilio.

Enables outbound phone calls from the dashboard — user's phone rings,
they speak commands, the orchestrator processes them, TTS responds.
"""

import asyncio
import io
import logging
import math
import struct
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _safe_voice(method_name: str, *args, **kwargs) -> Any:
    """Run a voice_runtime method by name, swallowing failures.

    Voice telemetry is best-effort observability — a failure here must never
    interrupt the phone call flow (answering, gathering speech, hanging up).
    """
    try:
        from ..voice_runtime import get_voice_runtime

        return getattr(get_voice_runtime(), method_name)(*args, **kwargs)
    except Exception:
        logger.debug("Voice runtime %s failed", method_name, exc_info=True)
        return None


def _generate_chime_wav() -> bytes:
    """Generate a soft repeating chime WAV — a gentle tone + silence gap.

    Produces a ~2.5s clip: 300ms sine fade-in/out at 880Hz, then ~2.2s silence.
    When looped with <Play loop="0">, creates a periodic "ding ... ding ..." effect.
    """
    sample_rate = 8000  # Twilio prefers 8kHz for telephony
    tone_hz = 880.0     # A5 — bright but not harsh
    tone_ms = 300
    silence_ms = 2200
    amplitude = 4000    # Soft volume (16-bit max ~32767)

    tone_samples = int(sample_rate * tone_ms / 1000)
    silence_samples = int(sample_rate * silence_ms / 1000)
    total_samples = tone_samples + silence_samples

    buf = io.BytesIO()
    # WAV header
    data_size = total_samples * 2  # 16-bit mono
    buf.write(b"RIFF")
    buf.write(struct.pack("<I", 36 + data_size))
    buf.write(b"WAVE")
    buf.write(b"fmt ")
    buf.write(struct.pack("<IHHIIHH", 16, 1, 1, sample_rate, sample_rate * 2, 2, 16))
    buf.write(b"data")
    buf.write(struct.pack("<I", data_size))

    # Tone with fade-in/out envelope
    for i in range(tone_samples):
        t = i / sample_rate
        # Envelope: fade in first 30%, fade out last 30%
        fade_in_end = int(tone_samples * 0.3)
        fade_out_start = int(tone_samples * 0.7)
        if i < fade_in_end:
            env = i / fade_in_end
        elif i > fade_out_start:
            env = (tone_samples - i) / (tone_samples - fade_out_start)
        else:
            env = 1.0
        sample = int(amplitude * env * math.sin(2 * math.pi * tone_hz * t))
        buf.write(struct.pack("<h", sample))

    # Silence gap
    buf.write(b"\x00\x00" * silence_samples)

    return buf.getvalue()


TYPE_MODE_EXIT_PHRASES = [
    "exit type mode", "stop type mode", "turn off type mode",
    "chat mode", "stop typing", "disable type mode",
]

# Common STT misheard words — maps wrong word to correct RDC term.
# Applied case-insensitively as whole-word replacements.
import re as _re

_SPEECH_CORRECTIONS: list[tuple[_re.Pattern, str]] = [
    (_re.compile(r"\bcontacts\b", _re.IGNORECASE), "contexts"),
    (_re.compile(r"\bcontact\b", _re.IGNORECASE), "context"),
    (_re.compile(r"\bprocess ease\b", _re.IGNORECASE), "processes"),
    (_re.compile(r"\btask ease\b", _re.IGNORECASE), "tasks"),
    (_re.compile(r"\bterminal s\b", _re.IGNORECASE), "terminals"),
    (_re.compile(r"\bkiosk\b", _re.IGNORECASE), "kiosk"),
    (_re.compile(r"\bkey ask\b", _re.IGNORECASE), "kiosk"),
    (_re.compile(r"\bterminal\s+all\b", _re.IGNORECASE), "terminal"),
    (_re.compile(r"\bfocused\b", _re.IGNORECASE), "focus"),
]


def _correct_speech(text: str) -> str:
    """Apply common STT corrections for RDC-specific vocabulary."""
    for pattern, replacement in _SPEECH_CORRECTIONS:
        text = pattern.sub(replacement, text)
    return text


@dataclass
class GatherResult:
    """Result of an async gather processing task."""
    twiml: str | None = None
    ready: bool = False
    error: str | None = None


@dataclass
class CallState:
    """Per-call conversation state."""
    call_sid: str
    project: str | None = None
    session_id: str = ""
    turn_count: int = 0
    started_at: float = field(default_factory=time.time)
    paired_client_id: str | None = None
    type_mode: bool = False
    type_mode_target: str | None = None  # "terminal" or future input targets
    pending_result: GatherResult | None = None  # Async LLM result
    voice_session_id: str | None = None

    def __post_init__(self):
        if not self.session_id:
            self.session_id = f"phone-{self.call_sid[:8]}"


class PhoneChannel:
    """Twilio phone call channel for voice interaction with the orchestrator."""

    def __init__(
        self,
        account_sid: str,
        auth_token: str,
        twilio_number: str,
        user_phone: str,
        webhook_base_url: str,
    ):
        self.account_sid = account_sid
        self.auth_token = auth_token
        self.twilio_number = twilio_number
        self.user_phone = user_phone
        self.webhook_base_url = webhook_base_url.rstrip("/")

        self._client = None  # Lazy Twilio REST client
        self._validator = None  # Lazy request validator
        self._calls: dict[str, CallState] = {}
        self._pairings: dict[str, str] = {}  # call_sid → client_id
        self._audio_dir: Path | None = None
        self._cleanup_task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self):
        """Create temp audio dir, generate chime, and start background cleanup."""
        self._audio_dir = Path(tempfile.mkdtemp(prefix="rdc_phone_tts_"))
        # Generate the processing chime once and reuse for all calls
        chime_path = self._audio_dir / "chime.wav"
        chime_path.write_bytes(_generate_chime_wav())
        self._chime_url = f"{self.webhook_base_url}/voice/twilio/audio/chime.wav"
        logger.info("Phone channel started, audio dir: %s", self._audio_dir)

    async def stop(self):
        """Hang up active calls and clean up."""
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
        # Hang up any active calls
        for sid in list(self._calls):
            try:
                await self.hangup(sid)
            except Exception:
                logger.debug("Error hanging up %s on stop", sid, exc_info=True)
        self._calls.clear()
        # Clean audio dir
        if self._audio_dir and self._audio_dir.exists():
            import shutil
            shutil.rmtree(self._audio_dir, ignore_errors=True)

    # ------------------------------------------------------------------
    # Twilio client (lazy)
    # ------------------------------------------------------------------

    def _get_client(self):
        if self._client is not None:
            return self._client
        from twilio.rest import Client
        self._client = Client(self.account_sid, self.auth_token)
        return self._client

    def _get_validator(self):
        if self._validator is not None:
            return self._validator
        from twilio.request_validator import RequestValidator
        self._validator = RequestValidator(self.auth_token)
        return self._validator

    def validate_request(self, url: str, params: dict, signature: str) -> bool:
        """Validate a Twilio webhook signature."""
        return self._get_validator().validate(url, params, signature)

    # ------------------------------------------------------------------
    # Call management
    # ------------------------------------------------------------------

    async def initiate_call(self, project: str | None = None, client_id: str | None = None) -> dict:
        """Place an outbound call to the user's phone.

        If *client_id* is provided the call is auto-paired with that dashboard
        client so voice commands can control the UI immediately.

        Clears any stale calls/pairings from previous sessions first.
        """
        # Clear stale calls and pairings from previous sessions
        for stale in self._calls.values():
            _safe_voice("end_session", stale.voice_session_id)
        self._calls.clear()
        self._pairings.clear()

        client = self._get_client()

        def _create():
            return client.calls.create(
                to=self.user_phone,
                from_=self.twilio_number,
                url=f"{self.webhook_base_url}/voice/twilio/incoming",
                status_callback=f"{self.webhook_base_url}/voice/twilio/status",
                status_callback_event=["initiated", "ringing", "answered", "completed"],
                status_callback_method="POST",
            )

        call = await asyncio.get_event_loop().run_in_executor(None, _create)
        state = CallState(call_sid=call.sid, project=project)
        self._calls[call.sid] = state

        voice_session = _safe_voice(
            "begin_session",
            transport="phone",
            channel="phone",
            client_id=client_id,
            project=project,
            external_id=call.sid,
            state="connecting",
        )
        if voice_session:
            state.voice_session_id = voice_session.id

        # Auto-pair with the requesting client
        if client_id:
            self.pair(call.sid, client_id)

        logger.info("Initiated call %s to %s (project=%s, paired=%s)", call.sid, self.user_phone, project, client_id)
        return {"call_sid": call.sid, "status": call.status}

    async def hangup(self, call_sid: str) -> dict:
        """Hang up an active call. Notifies paired client and broadcasts state."""
        # Capture paired client before cleanup
        call = self._calls.get(call_sid)
        paired_client = call.paired_client_id if call else self._pairings.get(call_sid)

        client = self._get_client()

        def _update():
            return client.calls(call_sid).update(status="completed")

        try:
            twilio_call = await asyncio.get_event_loop().run_in_executor(None, _update)
            result = {"call_sid": call_sid, "status": twilio_call.status}
        except Exception as e:
            logger.warning("Hangup failed for %s: %s", call_sid, e)
            result = {"call_sid": call_sid, "status": "error", "error": str(e)}

        # Notify paired client that the call ended
        if paired_client:
            try:
                from ..state_machine import get_state_machine
                sm = get_state_machine()
                await sm.send_to_client(paired_client, {
                    "type": "phone_unpaired",
                    "call_sid": call_sid,
                    "reason": "hangup",
                })
            except Exception:
                logger.debug("Failed to notify paired client on hangup", exc_info=True)

        # Cleanup
        _safe_voice("end_session", call.voice_session_id if call else None)
        self._pairings.pop(call_sid, None)
        self._calls.pop(call_sid, None)

        # Broadcast state so all clients see the call ended
        try:
            from ..state_machine import get_state_machine
            sm = get_state_machine()
            await sm._broadcast_state()
        except Exception:
            logger.debug("Failed to broadcast state after hangup", exc_info=True)

        return result

    def get_active_call(self) -> Optional[CallState]:
        """Get the active call state, if any."""
        if self._calls:
            return next(iter(self._calls.values()))
        return None

    def get_call_info(self) -> dict:
        """Get info about the current call for the dashboard."""
        call = self.get_active_call()
        if not call:
            return {"configured": True, "active": False}
        elapsed = int(time.time() - call.started_at)
        return {
            "configured": True,
            "active": True,
            "call_sid": call.call_sid,
            "project": call.project,
            "turn_count": call.turn_count,
            "duration": elapsed,
            "paired_client_id": call.paired_client_id,
            "type_mode": call.type_mode,
            "type_mode_target": call.type_mode_target,
        }

    # ------------------------------------------------------------------
    # Pairing
    # ------------------------------------------------------------------

    def pair(self, call_sid: str, client_id: str) -> bool:
        """Pair a call with a dashboard client. Returns True on success.

        If already paired with a different client, the old pairing is replaced.
        Returns the old client_id (if any) so callers can notify it.
        """
        call = self._calls.get(call_sid)
        if not call:
            return False
        old_client = call.paired_client_id
        call.paired_client_id = client_id
        self._pairings[call_sid] = client_id
        if old_client and old_client != client_id:
            logger.info("Re-paired call %s: %s → %s", call_sid, old_client, client_id)
        else:
            logger.info("Paired call %s with client %s", call_sid, client_id)
        return True

    def unpair(self, call_sid: str) -> bool:
        """Remove pairing for a call. Returns True on success."""
        call = self._calls.get(call_sid)
        if not call:
            return False
        old = call.paired_client_id
        call.paired_client_id = None
        self._pairings.pop(call_sid, None)
        logger.info("Unpaired call %s (was %s)", call_sid, old)
        return True

    def get_paired_client(self, call_sid: str) -> str | None:
        """Get the client_id paired with this call."""
        return self._pairings.get(call_sid)

    def get_call_for_client(self, client_id: str) -> Optional[CallState]:
        """Find the call paired with a given client_id."""
        for sid, cid in self._pairings.items():
            if cid == client_id:
                return self._calls.get(sid)
        return None

    def is_paired(self, call_sid: str) -> bool:
        """Check whether a call is currently paired."""
        return call_sid in self._pairings

    # ------------------------------------------------------------------
    # TwiML builders
    # ------------------------------------------------------------------

    def _twiml_gather(self, play_url: str | None = None, say_text: str | None = None) -> str:
        """Build TwiML with <Gather> for speech input + audio/say response."""
        from twilio.twiml.voice_response import VoiceResponse, Gather

        resp = VoiceResponse()

        if play_url:
            resp.play(play_url)
        elif say_text:
            resp.say(say_text, voice="Polly.Joanna")

        gather = Gather(
            input="speech",
            action=f"{self.webhook_base_url}/voice/twilio/gather",
            method="POST",
            speech_timeout="auto",
            language="en-US",
        )
        gather.say("", voice="Polly.Joanna")  # Silence prompt
        resp.append(gather)

        # On silence timeout, redirect back to gather (keep call alive)
        resp.redirect(f"{self.webhook_base_url}/voice/twilio/incoming", method="POST")

        return str(resp)

    def _twiml_hangup(self, play_url: str | None = None, say_text: str | None = None) -> str:
        """Build TwiML that plays audio/says text then hangs up."""
        from twilio.twiml.voice_response import VoiceResponse

        resp = VoiceResponse()
        if play_url:
            resp.play(play_url)
        elif say_text:
            resp.say(say_text, voice="Polly.Joanna")
        resp.hangup()
        return str(resp)

    # ------------------------------------------------------------------
    # Webhook handlers
    # ------------------------------------------------------------------

    async def handle_incoming(self, call_sid: str) -> str:
        """Handle the initial call connect — return greeting TwiML.

        Uses <Say> directly (no TTS) for instant response to avoid Twilio's 15s timeout.
        Only greets on first connect; silence-timeout redirects get a silent re-gather.
        Auto-pairs with the first connected dashboard client.
        """
        call = self._calls.get(call_sid)
        if call:
            # Outbound call already registered by initiate_call() — provide
            # greeting on first connect (turn_count == 0), silent re-gather
            # on subsequent silence-timeout reconnects.
            if call.turn_count == 0:
                paired = call.paired_client_id
                if call.voice_session_id:
                    _safe_voice("update_session", call.voice_session_id, state="listening")
                greeting = "Connected. How can I help?"
                if paired:
                    # Notify paired client about the connection
                    try:
                        from ..state_machine import get_state_machine
                        sm = get_state_machine()
                        await sm.send_to_client(paired, {
                            "type": "phone_paired",
                            "call_sid": call_sid,
                            "client_id": paired,
                        })
                    except Exception:
                        pass
                return self._twiml_gather(say_text=greeting)
            return self._twiml_gather()

        # Inbound call (not initiated from dashboard)
        # Clear stale calls/pairings from previous sessions
        for stale in self._calls.values():
            _safe_voice("end_session", stale.voice_session_id)
        self._calls.clear()
        self._pairings.clear()

        call = CallState(call_sid=call_sid)
        self._calls[call_sid] = call

        # Auto-pair with first connected client
        paired_client_id = None
        try:
            from ..state_machine import get_state_machine
            sm = get_state_machine()
            clients = sm.get_connected_clients()
            if clients:
                # Prefer desktop, then mobile
                desktop = [c for c in clients if c["client_id"].startswith("desktop-")]
                target = desktop[0] if desktop else clients[0]
                paired_client_id = target["client_id"]
                self.pair(call_sid, target["client_id"])
                await sm.send_to_client(target["client_id"], {
                    "type": "phone_paired",
                    "call_sid": call_sid,
                    "client_id": target["client_id"],
                })
        except Exception:
            pass  # Pairing is best-effort

        greeting = "Thanks for calling, how can I help?"
        voice_session = _safe_voice(
            "begin_session",
            transport="phone",
            channel="phone",
            client_id=paired_client_id,
            external_id=call_sid,
            state="listening",
        )
        if voice_session:
            call.voice_session_id = voice_session.id
        return self._twiml_gather(say_text=greeting)

    async def handle_gather(self, call_sid: str, speech_result: str) -> str:
        """Process speech input — returns fast TwiML, processes LLM in background.

        For instant operations (type mode), returns TwiML directly.
        For LLM operations, kicks off background task and returns a redirect
        to the result-polling endpoint.
        """
        call = self._calls.get(call_sid)
        if not call:
            call = CallState(call_sid=call_sid)
            self._calls[call_sid] = call

        call.turn_count += 1

        # Correct common STT misheard words before processing
        speech_result = _correct_speech(speech_result)

        session = _safe_voice("get_by_external_id", call_sid)
        if session:
            call.voice_session_id = session.id
            _safe_voice(
                "update_session",
                session.id,
                state="processing",
                transcript=speech_result,
                increment_turn=True,
            )

        print(f"[PHONE] Turn {call.turn_count}: speech={speech_result!r} project={call.project}")

        # Type mode: bypass LLM, send raw text to paired client (instant)
        if call.type_mode and call.paired_client_id:
            speech_lower = speech_result.lower().strip()
            if any(phrase in speech_lower for phrase in TYPE_MODE_EXIT_PHRASES):
                call.type_mode = False
                call.type_mode_target = None
                from ..state_machine import get_state_machine
                sm = get_state_machine()
                await sm.send_to_client(call.paired_client_id, {
                    "type": "phone_type_mode",
                    "enabled": False,
                })
                return self._twiml_gather(say_text="Type mode off. Back to chat.")

            # Send raw text to paired client
            from ..state_machine import get_state_machine
            sm = get_state_machine()
            await sm.send_to_client(call.paired_client_id, {
                "type": "phone_type",
                "text": speech_result,
                "target": call.type_mode_target or "terminal",
            })
            return self._twiml_gather()

        # Guard against duplicate gather processing — if a previous LLM task
        # is still running, don't kick off another one for the same call.
        if call.pending_result and not call.pending_result.ready:
            logger.warning("[PHONE] Ignoring duplicate gather for call %s (previous still pending)", call.call_sid)
            return self._twiml_processing_redirect(call.call_sid)

        # LLM processing: kick off in background, return redirect to polling endpoint
        call.pending_result = GatherResult()
        asyncio.ensure_future(self._process_gather_async(call, speech_result))

        # Return TwiML that says "One moment" then redirects to the result endpoint
        return self._twiml_processing_redirect(call_sid)

    def _twiml_processing_redirect(self, call_sid: str) -> str:
        """Return TwiML that plays a single chime then redirects to the result endpoint.

        The chime WAV is ~2.5s (300ms tone + silence). After it finishes,
        Twilio follows the <Redirect> to check if the LLM result is ready.
        If not ready, get_pending_result returns another chime+redirect — creating
        a polling loop that sounds like "ding ... ding ... ding ..." until the
        response arrives.
        """
        from twilio.twiml.voice_response import VoiceResponse

        resp = VoiceResponse()
        resp.play(self._chime_url)
        resp.redirect(
            f"{self.webhook_base_url}/voice/twilio/result/{call_sid}",
            method="POST",
        )
        return str(resp)

    def get_pending_result(self, call_sid: str) -> str:
        """Check if LLM result is ready, return appropriate TwiML."""
        call = self._calls.get(call_sid)
        if not call:
            return self._twiml_gather(say_text="Sorry, the call was lost.")

        result = call.pending_result
        if not result or not result.ready:
            # Not ready yet — play another chime and redirect back (polling loop)
            return self._twiml_processing_redirect(call_sid)

        # Result is ready — clear it and return
        twiml = result.twiml or self._twiml_gather(say_text="Done.")
        call.pending_result = None
        if call.voice_session_id:
            _safe_voice("update_session", call.voice_session_id, state="listening")
        return twiml

    async def _process_gather_async(self, call: CallState, speech_result: str):
        """Background LLM processing for a gather. Stores result in call.pending_result."""
        try:
            from ..intent import (
                get_intent_engine, get_action_executor, build_orchestrator_context,
                load_nanobot_config, log_nanobot_interaction,
            )

            cfg = load_nanobot_config()
            if not cfg.get("enabled", True):
                call.pending_result = GatherResult(
                    twiml=self._twiml_gather(say_text="The orchestrator is currently disabled."),
                    ready=True,
                )
                return

            engine = get_intent_engine()
            executor = get_action_executor()

            channel = "phone_paired" if call.paired_client_id else "phone"

            # Resolve the paired client's current project from state machine
            effective_project = call.project
            effective_client_id = call.session_id
            if call.paired_client_id:
                effective_client_id = call.paired_client_id
                try:
                    from ..state_machine import get_state_machine
                    sm = get_state_machine()
                    # Use the client WS registry to find the *active* session_id,
                    # then look up that session's project. This avoids matching
                    # stale sessions from old connections.
                    entry = sm._client_websockets.get(call.paired_client_id)
                    if entry and entry.get("session_id"):
                        sess = sm._sessions.get(entry["session_id"])
                        if sess and sess.project:
                            effective_project = sess.project
                            logger.info("[PHONE] Resolved project=%r from paired client session %s", effective_project, entry["session_id"])
                        else:
                            logger.info("[PHONE] Paired client session %s has project=%r, using call.project=%r", entry.get("session_id"), sess.project if sess else None, call.project)
                    else:
                        logger.info("[PHONE] No active WS entry for paired client %s", call.paired_client_id)
                except Exception:
                    logger.debug("Failed to resolve paired client project", exc_info=True)

            ctx = build_orchestrator_context(effective_project, call.session_id, channel, client_id=effective_client_id)
            logger.info("[PHONE] Turn %d: speech=%r channel=%s, calling orchestrator...", call.turn_count, speech_result, channel)

            # Process via LLM FIRST, then save turns (same order as /orchestrator endpoint).
            # Saving the user turn before process() causes duplication because
            # engine.process() loads thread_turns from DB AND appends the current
            # message — resulting in the LLM seeing the user message twice.
            result = await engine.process(speech_result, ctx)
            logger.info("[PHONE] Response: %r, actions=%s", result.response[:100], [a.name for a in result.actions])

            # Execute actions
            executed = []
            end_call = False
            for action in result.actions:
                outcome = await executor.execute(action.name, action.params, ctx)
                executed.append(outcome)
                if outcome.get("action") == "end_phone_call":
                    end_call = True

            # Track project selection across turns
            for outcome in executed:
                if outcome.get("action") == "select_project" and outcome.get("project"):
                    call.project = outcome["project"]
                    effective_project = outcome["project"]

            # Dispatch client-side actions to paired dashboard client
            if call.paired_client_id:
                client_actions = [o for o in executed if o.get("type") == "client"]
                if client_actions:
                    from ..state_machine import get_state_machine
                    sm = get_state_machine()
                    await sm.send_to_client(call.paired_client_id, {
                        "type": "phone_action",
                        "actions": client_actions,
                        "call_sid": call.call_sid,
                    })

            response_text = result.response or "Done."
            if call.voice_session_id:
                _safe_voice(
                    "update_session",
                    call.voice_session_id,
                    state="speaking",
                    project=effective_project,
                    response=response_text,
                )

            # SMS fallback: if not paired and there are client-side actions, send mobile link
            if not call.paired_client_id:
                client_actions = [o for o in executed if o.get("type") == "client"]
                if client_actions:
                    mobile_url = f"{self.webhook_base_url}/mobile"
                    try:
                        await self.send_sms(f"RDC Mobile Dashboard: {mobile_url}")
                        response_text += " I've sent you a link to the mobile dashboard — open it to pair and control from your phone."
                    except Exception:
                        logger.warning("SMS send failed", exc_info=True)
                        response_text += " I tried to send you a link but the text message failed. Open the mobile dashboard manually."
            # Save both turns to conversation thread (user + assistant)
            try:
                from ..conversation import get_conversation_manager
                conv_mgr = get_conversation_manager()
                thread_id = conv_mgr.get_or_create_thread(effective_project)
                conv_mgr.append_turn(thread_id, "user", speech_result, channel="phone", client_id=call.session_id)
                conv_mgr.append_turn(thread_id, "assistant", response_text, channel="phone", client_id=call.session_id, actions=executed)
            except Exception:
                logger.debug("Failed to save phone conversation turns", exc_info=True)

            # Log interaction (fire-and-forget)
            try:
                log_nanobot_interaction(
                    channel="phone",
                    project=effective_project,
                    message=speech_result,
                    response=response_text,
                    actions=executed,
                    model=result.usage.get("model", "unknown"),
                    prompt_tokens=result.usage.get("prompt_tokens", 0),
                    completion_tokens=result.usage.get("completion_tokens", 0),
                    duration_ms=result.usage.get("duration_ms", 0),
                )
            except Exception:
                logger.debug("Failed to log phone interaction", exc_info=True)

            if end_call:
                twiml = self._twiml_hangup(say_text=response_text)
                call.pending_result = GatherResult(twiml=twiml, ready=True)
                asyncio.get_event_loop().call_later(5, lambda: self._calls.pop(call.call_sid, None))
            else:
                call.pending_result = GatherResult(
                    twiml=self._twiml_gather(say_text=response_text),
                    ready=True,
                )

        except Exception as exc:
            logger.error("Phone gather handler error: %s", exc, exc_info=True)
            if call.voice_session_id:
                _safe_voice("update_session", call.voice_session_id, error=str(exc))
            call.pending_result = GatherResult(
                twiml=self._twiml_gather(say_text="Sorry, something went wrong. Please try again."),
                ready=True,
                error=str(exc),
            )

    async def handle_status(self, call_sid: str, call_status: str):
        """Handle Twilio status callback — cleanup on completion.

        Notifies the paired client that the call has ended so the browser
        doesn't stay stuck in a "paired" state.
        """
        logger.info("Call %s status: %s", call_sid, call_status)
        if call_status in ("completed", "failed", "busy", "no-answer", "canceled"):
            # Notify paired client before cleanup
            call = self._calls.get(call_sid)
            paired_client = call.paired_client_id if call else self._pairings.get(call_sid)
            if paired_client:
                try:
                    from ..state_machine import get_state_machine
                    sm = get_state_machine()
                    await sm.send_to_client(paired_client, {
                        "type": "phone_unpaired",
                        "call_sid": call_sid,
                        "reason": call_status,
                    })
                except Exception:
                    logger.debug("Failed to notify paired client on call end", exc_info=True)

            self._pairings.pop(call_sid, None)
            _safe_voice(
                "end_session",
                call.voice_session_id if call else None,
                error=call_status if call_status != "completed" else None,
            )
            self._calls.pop(call_sid, None)

    # ------------------------------------------------------------------
    # SMS
    # ------------------------------------------------------------------

    async def send_sms(self, body: str) -> bool:
        """Send an SMS to the user's phone via Twilio.

        Raises on failure so callers can handle it (e.g. tell the user).
        """
        client = self._get_client()

        def _send():
            msg = client.messages.create(
                to=self.user_phone,
                from_=self.twilio_number,
                body=body,
            )
            logger.info("SMS sent to %s (sid=%s, status=%s)", self.user_phone, msg.sid, msg.status)

        await asyncio.get_event_loop().run_in_executor(None, _send)
        return True

    # ------------------------------------------------------------------
    # TTS audio generation
    # ------------------------------------------------------------------

    async def _generate_audio(self, text: str) -> str | None:
        """Generate TTS audio file and return its serving URL, or None on failure."""
        try:
            from ..tts import get_tts_service
            tts = get_tts_service()
            audio_bytes = await tts.speak(text)

            if not audio_bytes or not self._audio_dir:
                return None

            filename = f"{uuid.uuid4().hex}.mp3"
            filepath = self._audio_dir / filename
            filepath.write_bytes(audio_bytes)

            return f"{self.webhook_base_url}/voice/twilio/audio/{filename}"
        except Exception:
            logger.debug("TTS generation failed, falling back to <Say>", exc_info=True)
            return None

    def get_audio_file(self, filename: str) -> Path | None:
        """Get the path to a TTS audio file for serving."""
        if not self._audio_dir:
            return None
        filepath = self._audio_dir / filename
        if filepath.exists() and filepath.parent == self._audio_dir:
            return filepath
        return None

    def cleanup_old_audio(self, max_age_seconds: int = 300):
        """Remove audio files older than max_age_seconds."""
        if not self._audio_dir or not self._audio_dir.exists():
            return
        now = time.time()
        for f in self._audio_dir.iterdir():
            if f.is_file() and (now - f.stat().st_mtime) > max_age_seconds:
                try:
                    f.unlink()
                except OSError:
                    pass


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_phone_channel: Optional[PhoneChannel] = None


def get_phone_channel() -> Optional[PhoneChannel]:
    return _phone_channel


def set_phone_channel(channel: Optional[PhoneChannel]):
    global _phone_channel
    _phone_channel = channel
