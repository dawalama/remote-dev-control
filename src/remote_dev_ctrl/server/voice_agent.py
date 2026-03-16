"""LiveKit voice agent for RDC with VNC screen awareness."""

from __future__ import annotations

import asyncio
import base64
import os
from typing import Optional

from dotenv import load_dotenv
from livekit import agents, rtc
from livekit.agents import AgentSession, Agent, JobContext
from livekit.plugins import openai, silero
from livekit.plugins.turn_detector.multilingual import MultilingualModel

load_dotenv()


class ADTAssistant(Agent):
    """Voice assistant that can see and discuss VNC screen content."""
    
    def __init__(self, vnc_manager=None):
        self._vnc_manager = vnc_manager
        self._last_screenshot: Optional[bytes] = None
        self._active_session_id: Optional[str] = None
        
        super().__init__(
            instructions="""You are an AI development assistant integrated with RDC (Remote Dev Ctrl).
You can see the user's application preview through VNC screenshots.
When the user asks about what's on screen, describes a bug, or wants feedback on the UI,
you should analyze the current screenshot.

You help with:
- Debugging UI issues visible on screen
- Discussing layout and design improvements
- Identifying errors shown in the browser
- General development questions

Be concise but helpful. When referencing something on screen, be specific about location.
If you can't see the screen or need a fresh view, ask the user to share a screenshot."""
        )
    
    async def capture_screen(self) -> Optional[str]:
        """Capture current VNC screen and return as base64."""
        if not self._vnc_manager or not self._active_session_id:
            return None
        
        try:
            screenshot = self._vnc_manager.capture_screenshot(self._active_session_id)
            if screenshot:
                self._last_screenshot = screenshot
                return base64.b64encode(screenshot).decode('utf-8')
        except Exception:
            pass
        return None
    
    def set_active_session(self, session_id: str):
        """Set the active VNC session to monitor."""
        self._active_session_id = session_id


def create_agent_session(vnc_manager=None) -> tuple[AgentSession, ADTAssistant]:
    """Create a new agent session with VNC awareness."""
    assistant = ADTAssistant(vnc_manager=vnc_manager)
    
    session = AgentSession(
        stt="deepgram/nova-3",  # Fast STT
        llm="openai/gpt-4o-mini",  # Multimodal capable
        tts="openai/tts-1",  # Good quality TTS
        vad=silero.VAD.load(),
        turn_detection=MultilingualModel(),
    )
    
    return session, assistant


async def run_voice_agent(ctx: JobContext, vnc_manager=None):
    """Run the voice agent in a LiveKit room."""
    session, assistant = create_agent_session(vnc_manager)
    
    await session.start(
        room=ctx.room,
        agent=assistant,
    )
    
    await session.generate_reply(
        instructions="Greet the user and let them know you can see their screen preview if they have one active."
    )


class VoiceAgentManager:
    """Manages LiveKit voice agent lifecycle."""
    
    def __init__(self, vnc_manager=None):
        self._vnc_manager = vnc_manager
        self._server_process = None
        self._agent_process = None
        self._livekit_url = os.getenv("LIVEKIT_URL", "ws://localhost:7880")
        self._api_key = os.getenv("LIVEKIT_API_KEY", "devkey")
        self._api_secret = os.getenv("LIVEKIT_API_SECRET", "secret")
    
    async def start_livekit_server(self) -> bool:
        """Start the LiveKit server in dev mode."""
        import subprocess
        
        try:
            # Check if already running
            result = subprocess.run(
                ["lsof", "-ti:7880"],
                capture_output=True,
                text=True
            )
            if result.returncode == 0 and result.stdout.strip():
                return True  # Already running
            
            # Start LiveKit server
            self._server_process = subprocess.Popen(
                ["livekit-server", "--dev", "--bind", "0.0.0.0"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            
            # Wait for it to be ready
            await asyncio.sleep(2)
            return True
        except Exception as e:
            print(f"Failed to start LiveKit server: {e}")
            return False
    
    def stop_livekit_server(self):
        """Stop the LiveKit server."""
        if self._server_process:
            self._server_process.terminate()
            self._server_process = None
    
    def get_room_token(self, room_name: str, participant_name: str) -> str:
        """Generate a token for joining a LiveKit room."""
        from livekit import api
        
        token = api.AccessToken(self._api_key, self._api_secret)
        token.with_identity(participant_name)
        token.with_name(participant_name)
        token.with_grants(api.VideoGrants(
            room_join=True,
            room=room_name,
        ))
        
        return token.to_jwt()
    
    def get_connection_info(self, room_name: str = "rdc-voice") -> dict:
        """Get connection info for the frontend."""
        return {
            "url": self._livekit_url.replace("ws://", "wss://").replace(":7880", ":7880"),
            "token": self.get_room_token(room_name, "user"),
            "room": room_name,
        }


# CLI entry point for running the agent standalone
if __name__ == "__main__":
    from livekit.agents import cli
    
    server = agents.AgentServer()
    
    @server.rtc_session()
    async def entrypoint(ctx: JobContext):
        await run_voice_agent(ctx)
    
    cli.run_app(server)
