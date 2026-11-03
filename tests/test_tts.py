"""Unit tests for TTS service and automatic fallback."""

import asyncio
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
import httpx

from remote_dev_ctrl.server.tts import TTSService, TTSConfig, TTSProvider


def test_tts_config_defaults():
    """Verify default TTS configuration uses Deepgram primary and ElevenLabs fallback."""
    tts = TTSService()
    config = tts.get_config()
    assert config.provider == TTSProvider.DEEPGRAM
    assert config.fallback_provider == TTSProvider.ELEVENLABS


def test_elevenlabs_paid_plan_required_handling():
    """Verify ElevenLabs 402 payment required is mapped to informative error."""
    async def _test():
        tts = TTSService()
        config = TTSConfig()
        
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 402
        mock_response.text = '{"detail":{"type":"payment_required","code":"paid_plan_required","message":"Free users cannot use library voices"}}'

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response
            with patch("remote_dev_ctrl.server.tts.get_secret", return_value="fake_eleven_key"):
                with pytest.raises(ValueError) as excinfo:
                    await tts._elevenlabs("hello", "rachel", config)
                assert "Paid plan required" in str(excinfo.value)

    asyncio.run(_test())


def test_speak_automatic_fallback_on_primary_error():
    """Verify that when primary provider fails, speak() falls back seamlessly."""
    async def _test():
        tts = TTSService()
        
        async def mock_call_provider(provider, text, voice, config):
            if provider == TTSProvider.ELEVENLABS:
                raise ValueError("ElevenLabs: Paid plan required (free tier cannot use library voices via API)")
            elif provider == TTSProvider.DEEPGRAM:
                return b"fake_deepgram_mp3_audio"
            raise ValueError(f"Unknown: {provider}")

        with patch.object(tts, "_call_provider", side_effect=mock_call_provider):
            with patch.object(tts, "get_config", return_value=TTSConfig(
                provider=TTSProvider.ELEVENLABS,
                fallback_provider=TTSProvider.DEEPGRAM
            )):
                audio = await tts.speak("Testing automatic fallback")
                assert audio == b"fake_deepgram_mp3_audio"

    asyncio.run(_test())


def test_speak_smart_alternative_detection_when_fallback_equals_primary():
    """Verify fallback detects alternative provider when fallback_provider is same as primary."""
    async def _test():
        tts = TTSService()

        async def mock_call_provider(provider, text, voice, config):
            if provider == TTSProvider.ELEVENLABS:
                raise ValueError("ElevenLabs: Paid plan required")
            elif provider == TTSProvider.DEEPGRAM:
                return b"recovered_audio"
            raise ValueError(f"Unexpected: {provider}")

        with patch.object(tts, "_call_provider", side_effect=mock_call_provider):
            with patch.object(tts, "get_config", return_value=TTSConfig(
                provider=TTSProvider.ELEVENLABS,
                fallback_provider=TTSProvider.ELEVENLABS  # misconfigured identical fallback
            )):
                with patch("remote_dev_ctrl.server.tts.get_secret", side_effect=lambda k: "fake_deepgram_key" if k == "DEEPGRAM_API_KEY" else None):
                    audio = await tts.speak("Testing smart recovery")
                    assert audio == b"recovered_audio"

    asyncio.run(_test())


def test_speak_raises_comprehensive_error_when_both_fail():
    """Verify that if both primary and fallback fail, both error messages are included."""
    async def _test():
        tts = TTSService()

        async def mock_call_provider(provider, text, voice, config):
            if provider == TTSProvider.ELEVENLABS:
                raise ValueError("Primary ElevenLabs error")
            elif provider == TTSProvider.DEEPGRAM:
                raise ValueError("Fallback Deepgram error")
            raise ValueError("Unknown")

        with patch.object(tts, "_call_provider", side_effect=mock_call_provider):
            with patch.object(tts, "get_config", return_value=TTSConfig(
                provider=TTSProvider.ELEVENLABS,
                fallback_provider=TTSProvider.DEEPGRAM
            )):
                with pytest.raises(ValueError) as excinfo:
                    await tts.speak("Testing both fail")
                assert "Primary TTS (elevenlabs) failed: Primary ElevenLabs error" in str(excinfo.value)
                assert "Fallback TTS (deepgram) failed: Fallback Deepgram error" in str(excinfo.value)

    asyncio.run(_test())
