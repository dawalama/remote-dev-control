"""Text-to-Speech service with ElevenLabs primary and Deepgram fallback."""

import os
import httpx
from typing import Optional
from dataclasses import dataclass
from enum import Enum

from .db.connection import get_db
from .vault import get_secret


class TTSProvider(str, Enum):
    ELEVENLABS = "elevenlabs"
    DEEPGRAM = "deepgram"
    OPENAI = "openai"
    BROWSER = "browser"


@dataclass
class TTSConfig:
    provider: TTSProvider = TTSProvider.ELEVENLABS
    voice: str = "rachel"
    fallback_provider: TTSProvider = TTSProvider.DEEPGRAM
    fallback_voice: str = "aura-asteria-en"
    
    # ElevenLabs settings
    elevenlabs_model: str = "eleven_turbo_v2_5"
    elevenlabs_stability: float = 0.5
    elevenlabs_similarity: float = 0.75
    
    # Deepgram settings
    deepgram_model: str = "aura-asteria-en"


# ElevenLabs voice IDs
ELEVENLABS_VOICES = {
    "rachel": "21m00Tcm4TlvDq8ikWAM",
    "drew": "29vD33N1CtxCmqQRPOHJ",
    "clyde": "2EiwWnXFnvU5JabPnv8n",
    "paul": "5Q0t7uMcjvnagumLfvZi",
    "domi": "AZnzlk1XvdvUeBnXmlld",
    "dave": "CYw3kZ02Hs0563khs1Fj",
    "fin": "D38z5RcWu1voky8WS1ja",
    "sarah": "EXAVITQu4vr4xnSDxMaL",
    "antoni": "ErXwobaYiN019PkySvjV",
    "thomas": "GBv7mTt0atIp3Br8iCZE",
    "charlie": "IKne3meq5aSn9XLyUdCD",
    "emily": "LcfcDJNUP1GQjkzn1xUU",
    "elli": "MF3mGyEYCl7XYWbV9V6O",
    "callum": "N2lVS1w4EtoT3dr4eOWO",
    "patrick": "ODq5zmih8GrVes37Dizd",
    "harry": "SOYHLrjzK2X1ezoPC6cr",
    "liam": "TX3LPaxmHKxFdv7VOQHJ",
    "dorothy": "ThT5KcBeYPX3keUQqHPh",
    "josh": "TxGEqnHWrfWFTfGW9XjX",
    "arnold": "VR6AewLTigWG4xSOukaG",
    "charlotte": "XB0fDUnXU5powFXDhCwa",
    "matilda": "XrExE9yKIg1WjnnlVkGX",
    "matthew": "Yko7PKs4RNGr13Ozq7Mf",
    "james": "ZQe5CZNOzWyzPSCn5a3c",
    "joseph": "Zlb1dXrM653N07WRdFW3",
    "jessie": "t0jbNlBVZ17f02VDIeMI",
    "michael": "flq6f7yk4E4fJM5XTYuZ",
    "ethan": "g5CIjZEefAph4nQFvHAz",
    "gigi": "jBpfuIE2acCO8z3wKNLl",
    "freya": "jsCqWAovK2LkecY7zXl4",
    "grace": "oWAxZDx7w5VEj9dCyTzz",
    "daniel": "onwK4e9ZLuTAKqWW03F9",
    "serena": "pMsXgVXv3BLzUgSXRplE",
    "adam": "pNInz6obpgDQGcFmaJgB",
    "nicole": "piTKgcLEGmPE4e6mEKli",
    "glinda": "z9fAnlkpzviPz146aGWa",
}

# Deepgram Aura voices
DEEPGRAM_VOICES = {
    "asteria": "aura-asteria-en",      # Female, US English
    "luna": "aura-luna-en",            # Female, US English  
    "stella": "aura-stella-en",        # Female, US English
    "athena": "aura-athena-en",        # Female, UK English
    "hera": "aura-hera-en",            # Female, US English
    "orion": "aura-orion-en",          # Male, US English
    "arcas": "aura-arcas-en",          # Male, US English
    "perseus": "aura-perseus-en",      # Male, US English
    "angus": "aura-angus-en",          # Male, Irish English
    "orpheus": "aura-orpheus-en",      # Male, US English
    "helios": "aura-helios-en",        # Male, UK English
    "zeus": "aura-zeus-en",            # Male, US English
}


class TTSService:
    """TTS service with automatic fallback."""
    
    def __init__(self):
        self._config: Optional[TTSConfig] = None
    
    def get_config(self) -> TTSConfig:
        """Load TTS config from database."""
        if self._config:
            return self._config
        
        config = TTSConfig()
        
        try:
            db = get_db("rdc")
            cursor = db.execute("SELECT key, value FROM settings WHERE key LIKE 'tts_%'")
            rows = cursor.fetchall()
            
            for row in rows:
                key = row["key"].replace("tts_", "")
                value = row["value"]
                
                if key == "provider":
                    config.provider = TTSProvider(value)
                elif key == "voice":
                    config.voice = value
                elif key == "fallback_provider":
                    config.fallback_provider = TTSProvider(value)
                elif key == "fallback_voice":
                    config.fallback_voice = value
                elif key == "elevenlabs_model":
                    config.elevenlabs_model = value
                elif key == "elevenlabs_stability":
                    config.elevenlabs_stability = float(value)
                elif key == "elevenlabs_similarity":
                    config.elevenlabs_similarity = float(value)
                elif key == "deepgram_model":
                    config.deepgram_model = value
        except Exception:
            pass
        
        self._config = config
        return config
    
    def set_config(self, **kwargs) -> TTSConfig:
        """Update TTS config in database."""
        db = get_db("rdc")
        
        for key, value in kwargs.items():
            if value is not None:
                db.execute(
                    "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
                    (f"tts_{key}", str(value))
                )
        db.commit()
        
        # Clear cached config
        self._config = None
        return self.get_config()
    
    def get_all_settings(self) -> dict:
        """Get all settings as a dict."""
        db = get_db("rdc")
        cursor = db.execute("SELECT key, value FROM settings")
        return {row["key"]: row["value"] for row in cursor.fetchall()}
    
    def set_setting(self, key: str, value: str):
        """Set a single setting."""
        db = get_db("rdc")
        db.execute(
            "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
            (key, value)
        )
        db.commit()
        self._config = None
    
    async def speak(self, text: str, voice: Optional[str] = None, provider: Optional[str] = None) -> bytes:
        """Convert text to speech, with automatic fallback."""
        config = self.get_config()
        
        use_provider = TTSProvider(provider) if provider else config.provider
        use_voice = voice or config.voice
        
        try:
            if use_provider == TTSProvider.ELEVENLABS:
                return await self._elevenlabs(text, use_voice, config)
            elif use_provider == TTSProvider.DEEPGRAM:
                return await self._deepgram(text, use_voice, config)
            elif use_provider == TTSProvider.OPENAI:
                return await self._openai(text, use_voice)
            else:
                raise ValueError(f"Unsupported provider: {use_provider}")
                
        except Exception as e:
            error_str = str(e).lower()
            
            # Check for quota/credit errors
            if any(x in error_str for x in ["quota", "credit", "limit", "exceeded", "insufficient"]):
                # Try fallback
                if config.fallback_provider and config.fallback_provider != use_provider:
                    return await self._fallback(text, config)
            
            raise
    
    async def _fallback(self, text: str, config: TTSConfig) -> bytes:
        """Use fallback provider."""
        if config.fallback_provider == TTSProvider.DEEPGRAM:
            return await self._deepgram(text, config.fallback_voice, config)
        elif config.fallback_provider == TTSProvider.OPENAI:
            return await self._openai(text, "nova")
        elif config.fallback_provider == TTSProvider.ELEVENLABS:
            return await self._elevenlabs(text, config.fallback_voice, config)
        else:
            raise ValueError("No fallback available")
    
    async def _elevenlabs(self, text: str, voice: str, config: TTSConfig) -> bytes:
        """ElevenLabs TTS."""
        api_key = get_secret("ELEVENLABS_API_KEY") or os.getenv("ELEVENLABS_API_KEY")
        if not api_key:
            raise ValueError("ELEVENLABS_API_KEY not set")
        
        voice_id = ELEVENLABS_VOICES.get(voice.lower(), ELEVENLABS_VOICES["rachel"])
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
        
        headers = {
            "Accept": "audio/mpeg",
            "Content-Type": "application/json",
            "xi-api-key": api_key,
        }
        
        payload = {
            "text": text[:5000],
            "model_id": config.elevenlabs_model,
            "voice_settings": {
                "stability": config.elevenlabs_stability,
                "similarity_boost": config.elevenlabs_similarity,
            }
        }
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, json=payload, headers=headers)
            
            if response.status_code == 401:
                raise ValueError("ElevenLabs: Invalid API key")
            elif response.status_code == 429 or "quota" in response.text.lower():
                raise ValueError("ElevenLabs: Quota exceeded")
            elif response.status_code != 200:
                raise ValueError(f"ElevenLabs error: {response.text}")
            
            return response.content
    
    async def _deepgram(self, text: str, voice: str, config: TTSConfig) -> bytes:
        """Deepgram Aura TTS."""
        api_key = get_secret("DEEPGRAM_API_KEY") or os.getenv("DEEPGRAM_API_KEY")
        if not api_key:
            raise ValueError("DEEPGRAM_API_KEY not set. Set with: rdc config set-secret DEEPGRAM_API_KEY <key>")
        
        # Map voice name to model
        model = DEEPGRAM_VOICES.get(voice.lower(), voice if voice.startswith("aura-") else config.deepgram_model)
        
        url = f"https://api.deepgram.com/v1/speak?model={model}"
        
        headers = {
            "Authorization": f"Token {api_key}",
            "Content-Type": "application/json",
        }
        
        payload = {"text": text[:2000]}  # Deepgram limit
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, json=payload, headers=headers)
            
            if response.status_code == 401:
                raise ValueError("Deepgram: Invalid API key")
            elif response.status_code == 402:
                raise ValueError("Deepgram: Insufficient credits")
            elif response.status_code != 200:
                raise ValueError(f"Deepgram error: {response.text}")
            
            return response.content
    
    async def _openai(self, text: str, voice: str) -> bytes:
        """OpenAI TTS."""
        try:
            from openai import OpenAI
        except ImportError:
            raise ValueError("OpenAI not installed")
        
        api_key = get_secret("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY not set")
        
        openai_voices = ["alloy", "echo", "fable", "onyx", "nova", "shimmer"]
        if voice not in openai_voices:
            voice = "nova"
        
        client = OpenAI(api_key=api_key)
        
        response = client.audio.speech.create(
            model="tts-1",
            voice=voice,
            input=text[:4096],
            response_format="mp3",
        )
        
        return response.content


# Global service instance
_tts_service: Optional[TTSService] = None


def get_tts_service() -> TTSService:
    """Get the global TTS service."""
    global _tts_service
    if _tts_service is None:
        _tts_service = TTSService()
    return _tts_service
