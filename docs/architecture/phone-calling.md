# Phone Voice Calling Architecture

## Overview

Phone calling allows users to interact with the RDC Command Center via actual phone calls. The server initiates outbound calls to the user's phone via Twilio, and conversation intelligence comes from the existing nanobot/orchestrator (IntentEngine + ActionExecutor). No browser is needed for the call itself.

## Call Flow

```
Dashboard [phone btn] --> POST /voice/call --> Twilio REST API (outbound call)
                                                      |
                                                user's phone rings
                                                      |
                                          user answers the call
                                                      |
                          Twilio connects --> POST /voice/twilio/incoming (webhook)
                                                      |
                                   TTS greeting --> <Play audio> + <Gather input="speech">
                                                      |
                                              user speaks a command
                                                      |
                          Twilio transcribes --> POST /voice/twilio/gather (webhook)
                                                      |
                                   speech_result --> IntentEngine.process(msg, ctx)
                                                      |
                                              ActionExecutor runs actions
                                                      |
                                   response text --> TTSService.speak() --> audio file
                                                      |
                                              <Play audio_url> + <Gather> --> loop
                                                      |
                                         (user says "goodbye" or hangs up)
                                                      |
                          end_phone_call tool --> <Say goodbye> + <Hangup>
```

## Voice Processing: Gather (Batch) vs Streams (Real-time)

The current implementation uses **Twilio Gather** (batch mode):

| Aspect | Gather (current) | Media Streams (future) |
|--------|-------------------|------------------------|
| How it works | Twilio collects speech, runs built-in STT, POSTs transcript | Twilio streams raw audio frames over WebSocket |
| Latency | ~1-2s STT wait after silence detection | Near real-time, process as user speaks |
| STT provider | Twilio's built-in (Google-based) | Custom: Deepgram, Whisper, etc. |
| Interruption | Not supported (must wait for full utterance) | Barge-in possible |
| Complexity | Simple: just handle POST webhooks | Complex: WebSocket audio pipeline, VAD |
| Audio format | N/A (transcript only) | mulaw 8kHz mono chunks |

**Why Gather first:** Simpler to implement, good enough for command-and-response conversations (~3-7s per turn). Media Streams is the natural upgrade path for lower latency and custom STT.

### Latency Budget (per turn)

| Phase | Duration |
|-------|----------|
| Twilio STT (speech_timeout="auto") | ~1-2s |
| Orchestrator LLM (fast model) | ~1-3s |
| TTS generation (ElevenLabs turbo) | ~1-2s |
| Twilio audio download + playback | ~0.5s |
| **Total** | **~3-7s** |

## Files

| File | Role |
|------|------|
| `channels/phone.py` | PhoneChannel class: call lifecycle, TwiML builders, TTS audio serving, per-call state |
| `app.py` | HTTP endpoints: user-facing (`/voice/call`, `/voice/hangup`, `/voice/call/status`) + Twilio webhooks |
| `intent.py` | `end_phone_call` tool in ORCHESTRATOR_TOOLS + ActionExecutor case |
| `middleware.py` | `/voice/twilio/` added to PUBLIC_PATH_PREFIXES |
| `config.py` | `user_phone_number`, `webhook_base_url` fields on VoiceConfig |
| `dashboard_state.py` | Phone button UI, state management, status polling |
| `admin_page.py` | Phone settings section (user number, webhook URL, Twilio secret status) |
| `pyproject.toml` | `twilio>=9.0.0` optional dependency |

## Key Classes

### `PhoneChannel` (`channels/phone.py`)

Manages the full call lifecycle. Follows the same pattern as `TelegramBot` in `channels/telegram.py`.

```
PhoneChannel
  .__init__(account_sid, auth_token, twilio_number, user_phone, webhook_base_url)
  .start()                              # Create temp audio dir
  .stop()                               # Hangup active calls, cleanup
  .initiate_call(project) -> dict       # Twilio REST: create outbound call
  .hangup(call_sid) -> dict             # Twilio REST: update call to completed
  .handle_incoming(call_sid) -> str     # Return TwiML greeting + <Gather>
  .handle_gather(call_sid, speech) -> str  # Orchestrator -> TwiML response
  .handle_status(call_sid, status)      # Cleanup on call end
  .validate_request(url, params, sig)   # Twilio signature validation
  .get_active_call() -> CallState|None
  .get_call_info() -> dict
```

### `CallState` (per-call dataclass)

```python
@dataclass
class CallState:
    call_sid: str
    project: str | None        # Active project context
    session_id: str            # "phone-{sid[:8]}" for orchestrator
    history: list[dict]        # [{role, content}] conversation turns
    turn_count: int
    started_at: float
```

## Endpoints

### User-facing (require auth token)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/voice/call` | Initiate outbound call. Body: `{project?}`. Returns `{call_sid, status}` |
| POST | `/voice/hangup` | Hang up active call. Body: `{call_sid?}` |
| GET | `/voice/call/status` | Call info: `{active, configured, call_sid?, turn_count?, duration?}` |

### Twilio webhooks (public, signature-validated)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/voice/twilio/incoming` | Call connected. Returns TwiML greeting + Gather |
| POST | `/voice/twilio/gather` | Speech received. Processes via orchestrator, returns TwiML |
| POST | `/voice/twilio/status` | Status callbacks (cleanup on completion/failure) |
| GET | `/voice/twilio/audio/{file}` | Serves TTS audio files for Twilio to play |

## Security

- **Twilio webhooks** are validated via `X-Twilio-Signature` header using `twilio.request_validator.RequestValidator`. This prevents unauthorized invocations.
- **User-facing endpoints** require standard auth token (via AuthMiddleware).
- Webhook paths are in `PUBLIC_PATH_PREFIXES` to bypass token auth (they use signature auth instead).

## TTS Audio Pipeline

1. Orchestrator returns response text
2. `TTSService.speak(text)` generates audio bytes (ElevenLabs primary, Deepgram/OpenAI fallback)
3. Audio saved to temp file in `/tmp/rdc_phone_tts_*/`
4. TwiML `<Play>` points to `{webhook_base_url}/voice/twilio/audio/{filename}`
5. Twilio downloads and plays the audio to the caller
6. Fallback: if TTS fails, uses `<Say voice="Polly.Joanna">` (AWS Polly via Twilio)

## Dashboard Integration

- Phone button (phone icon) in the command bar, next to the mic button
- **Mutually exclusive** with browser mic: clicking phone stops mic, activating mic while phone is active shows a warning
- Active call: green pulsing button with tooltip showing turn count and duration
- Status polling: every 3s during active call via `GET /voice/call/status`
- Config check on page load: button is disabled/dimmed if not configured

## Configuration

### Secrets (via vault: `rdc config set-secret`)

| Secret | Description |
|--------|-------------|
| `TWILIO_ACCOUNT_SID` | Twilio account SID |
| `TWILIO_AUTH_TOKEN` | Twilio auth token |
| `TWILIO_PHONE_NUMBER` | Twilio FROM number (purchased number) |

### Settings (via Admin page, stored in SQLite)

| Setting | Description |
|---------|-------------|
| `phone_user_number` | User's phone number to call (E.164: +1234567890) |
| `phone_webhook_url` | Public URL for Twilio webhooks (e.g., ngrok URL) |

### Lazy initialization

The phone channel can be initialized either:
1. **At startup** via `config.yml` if `channels.voice.enabled: true` and all fields are set
2. **On first call** via `_try_init_phone_channel()` which reads secrets + DB settings

This means users can configure everything via the Admin UI without editing config files.

## Future: Media Streams Upgrade

To reduce latency to <2s per turn:

1. Replace `<Gather>` with `<Stream url="wss://...">` in TwiML
2. Add WebSocket endpoint for bidirectional audio streaming
3. Pipe raw audio to Deepgram streaming STT
4. Stream TTS audio back as mulaw chunks
5. Implement VAD (voice activity detection) for barge-in

This is a natural evolution, not a rewrite. The orchestrator/intent layer stays the same.
