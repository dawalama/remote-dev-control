"""LLM client factory for gwd.

Creates an OpenAI-compatible client from environment variables.
Priority: OPENROUTER_API_KEY > OPENAI_API_KEY > ANTHROPIC_API_KEY > localhost Ollama.
"""

import os

from openai import OpenAI


def create_client(api_key: str | None = None, base_url: str | None = None) -> OpenAI:
    """Create an OpenAI-compatible client.

    If api_key/base_url are provided, uses them directly.
    Otherwise auto-detects from environment variables.
    """
    if api_key:
        return OpenAI(api_key=api_key, base_url=base_url)

    # Auto-detect from env
    or_key = os.getenv("OPENROUTER_API_KEY")
    if or_key:
        return OpenAI(api_key=or_key, base_url="https://openrouter.ai/api/v1")

    oai_key = os.getenv("OPENAI_API_KEY")
    if oai_key:
        return OpenAI(api_key=oai_key)

    anthropic_key = os.getenv("ANTHROPIC_API_KEY")
    if anthropic_key:
        return OpenAI(api_key=anthropic_key, base_url="https://openrouter.ai/api/v1")

    # Fallback to local Ollama
    return OpenAI(api_key="ollama", base_url="http://localhost:11434/v1")


def default_model() -> str:
    """Pick the default model based on which env var is set."""
    if os.getenv("OPENROUTER_API_KEY"):
        return "anthropic/claude-sonnet-4-20250514"
    if os.getenv("OPENAI_API_KEY"):
        return "gpt-4o"
    if os.getenv("ANTHROPIC_API_KEY"):
        return "anthropic/claude-sonnet-4-20250514"
    return "qwen3.5"
