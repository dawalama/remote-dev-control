"""Configuration management for RDC Command Center."""

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


def _env_with_fallback(new_var: str, old_var: str, default: str | Path | None = None) -> str | None:
    """Check new RDC_* env var first, fall back to legacy ADT_* var."""
    val = os.environ.get(new_var)
    if val is not None:
        return val
    val = os.environ.get(old_var)
    if val is not None:
        return val
    return str(default) if default is not None else None


def get_rdc_home() -> Path:
    """Get the RDC home directory, with fallback to legacy ~/.adt."""
    explicit = _env_with_fallback("RDC_HOME", "ADT_HOME")
    if explicit:
        return Path(explicit)
    rdc_path = Path.home() / ".rdc"
    adt_path = Path.home() / ".adt"
    # Prefer ~/.rdc if it exists, otherwise fall back to ~/.adt if it exists
    if rdc_path.exists():
        return rdc_path
    if adt_path.exists():
        return adt_path
    return rdc_path  # default for fresh installs


# Backward compat aliases
get_adt_home = get_rdc_home
ensure_adt_home = None  # replaced below


def ensure_rdc_home() -> Path:
    """Ensure RDC home directory exists with proper structure."""
    home = get_rdc_home()
    home.mkdir(parents=True, exist_ok=True)
    (home / "data").mkdir(exist_ok=True)
    (home / "logs" / "agents").mkdir(parents=True, exist_ok=True)
    (home / "logs" / "processes").mkdir(parents=True, exist_ok=True)
    return home


ensure_adt_home = ensure_rdc_home


class ProviderConfig(BaseModel):
    """Configuration for an LLM provider."""
    type: str
    api_key: str | None = None
    model: str | None = None
    default: bool = False
    use_for: list[str] = Field(default_factory=list)


class TelegramConfig(BaseModel):
    """Telegram channel configuration."""
    enabled: bool = False
    token: str | None = None
    allowed_users: list[int] = Field(default_factory=list)


class VoiceConfig(BaseModel):
    """Voice channel configuration."""
    enabled: bool = False
    provider: str = "twilio"
    account_sid: str | None = None
    auth_token: str | None = None
    phone_number: str | None = None         # Twilio FROM number
    user_phone_number: str | None = None    # User's phone TO number
    webhook_base_url: str | None = None     # Public URL for Twilio webhooks


class WebConfig(BaseModel):
    """Web dashboard configuration."""
    enabled: bool = True
    port: int = 8421


class ChannelsConfig(BaseModel):
    """Communication channels configuration."""
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    voice: VoiceConfig = Field(default_factory=VoiceConfig)
    web: WebConfig = Field(default_factory=WebConfig)


class BrowserConfig(BaseModel):
    """Browser automation configuration."""
    backend: str = "chrome"  # "chrome" (local, default) or "docker" (legacy browserless)
    headless: bool = True
    chrome_path: str = ""  # auto-detect if empty


class NekoConfig(BaseModel):
    """Neko visual streaming configuration."""
    enabled: bool = False
    image: str = "ghcr.io/m1k1o/neko:firefox"
    port_range: tuple[int, int] = (9000, 9010)


class TerminalConfig(BaseModel):
    """Terminal streaming configuration."""
    enabled: bool = False
    provider: str = "ttyd"
    port_range: tuple[int, int] = (9100, 9110)


class VisualConfig(BaseModel):
    """Visual streaming configuration."""
    neko: NekoConfig = Field(default_factory=NekoConfig)
    terminal: TerminalConfig = Field(default_factory=TerminalConfig)


class EscalationConfig(BaseModel):
    """Escalation rules for agents."""
    stuck_timeout: int = 300  # seconds
    retry_limit: int = 3
    notify_on: list[str] = Field(default_factory=lambda: ["completion", "failure", "blocked"])


class AgentsConfig(BaseModel):
    """Agent orchestration configuration."""
    default_provider: str = "cursor"
    max_concurrent: int = 3
    auto_spawn: bool = False
    escalation: EscalationConfig = Field(default_factory=EscalationConfig)


class ProjectOverride(BaseModel):
    """Per-project configuration overrides."""
    path: Path | None = None
    priority: str = "normal"
    preferred_provider: str | None = None


class TLSConfig(BaseModel):
    """TLS/SSL configuration."""
    enabled: bool = False
    cert_file: str | None = None
    key_file: str | None = None
    

class ServerSettings(BaseModel):
    """Server configuration."""
    host: str = "127.0.0.1"
    port: int = 8420
    secret_key: str | None = None
    tls: TLSConfig = Field(default_factory=TLSConfig)


class CaddyConfig(BaseModel):
    """Caddy reverse proxy configuration for subdomain-based preview URLs."""
    enabled: bool = False
    base_domain: str = "example.com"
    rdc_domain: str = "rdc.example.com"
    admin_port: int = 2019
    listen_port: int = 8888


class Config(BaseModel):
    """Main RDC configuration."""
    server: ServerSettings = Field(default_factory=ServerSettings)
    projects_dir: str | None = None  # Base directory for new projects (default: ~/projects)
    providers: dict[str, ProviderConfig] = Field(default_factory=dict)
    channels: ChannelsConfig = Field(default_factory=ChannelsConfig)
    visual: VisualConfig = Field(default_factory=VisualConfig)
    browser: BrowserConfig = Field(default_factory=BrowserConfig)
    agents: AgentsConfig = Field(default_factory=AgentsConfig)
    projects: dict[str, ProjectOverride] = Field(default_factory=dict)
    caddy: CaddyConfig = Field(default_factory=CaddyConfig)

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        """Load configuration from file."""
        if path is None:
            path = get_rdc_home() / "config.yml"

        if not path.exists():
            return cls()

        with open(path) as f:
            data = yaml.safe_load(f) or {}

        # Resolve environment variables
        data = _resolve_env_vars(data)

        # Compat: accept legacy adt_domain field
        if "caddy" in data and "adt_domain" in data["caddy"] and "rdc_domain" not in data["caddy"]:
            data["caddy"]["rdc_domain"] = data["caddy"].pop("adt_domain")

        return cls.model_validate(data)
    
    def save(self, path: Path | None = None) -> None:
        """Save configuration to file."""
        if path is None:
            path = get_rdc_home() / "config.yml"

        path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(path, "w") as f:
            yaml.dump(self.model_dump(mode="json"), f, default_flow_style=False, sort_keys=False)
    
    def get_provider(self, name: str | None = None) -> ProviderConfig | None:
        """Get a provider by name, or the default provider."""
        if name:
            return self.providers.get(name)
        
        # Find default
        for provider in self.providers.values():
            if provider.default:
                return provider
        
        # Return first if no default
        if self.providers:
            return next(iter(self.providers.values()))
        
        return None


def _resolve_env_vars(data: Any) -> Any:
    """Recursively resolve ${VAR} references in config."""
    if isinstance(data, str):
        if data.startswith("${") and data.endswith("}"):
            var_name = data[2:-1]
            return os.environ.get(var_name, "")
        return data
    elif isinstance(data, dict):
        return {k: _resolve_env_vars(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [_resolve_env_vars(v) for v in data]
    return data


def create_default_config() -> Config:
    """Create a default configuration."""
    return Config(
        server=ServerSettings(),
        providers={
            "cursor": ProviderConfig(
                type="cursor-agent",
                default=True,
            ),
            "ollama": ProviderConfig(
                type="ollama",
                model="qwen3.5",
                use_for=["quick tasks", "low cost"],
            ),
        },
        channels=ChannelsConfig(
            web=WebConfig(enabled=True),
        ),
        agents=AgentsConfig(
            default_provider="cursor",
            max_concurrent=3,
        ),
    )


def get_default_config_template() -> str:
    """Get the default config file template with comments."""
    return '''# RDC (Remote Dev Ctrl) Configuration
# Environment variables can be referenced as ${VAR_NAME}

server:
  host: "127.0.0.1"
  port: 8420
  # secret_key: ${RDC_SECRET_KEY}

# LLM Providers - agents pick based on task type
# 
# cursor-agent: Uses your existing Cursor authentication (no API key needed)
# anthropic: Requires ANTHROPIC_API_KEY
# ollama: Local, free, no API key needed
#
providers:
  cursor:
    type: cursor-agent
    default: true
    # No API key needed - uses your Cursor login
  
  # claude:
  #   type: anthropic
  #   api_key: ${ANTHROPIC_API_KEY}
  #   model: claude-sonnet-4-20250514
  #   use_for: ["complex reasoning", "architecture"]
  
  # openai:
  #   type: openai
  #   api_key: ${OPENAI_API_KEY}
  #   model: gpt-4o
  #   use_for: ["general tasks"]
  
  # gemini:
  #   type: google
  #   api_key: ${GEMINI_API_KEY}
  #   model: gemini-2.0-flash
  #   use_for: ["large context", "multimodal"]
  
  ollama:
    type: ollama
    model: qwen3.5
    # No API key needed - runs locally
    use_for: ["quick tasks", "low cost"]

# Communication Channels
channels:
  telegram:
    enabled: false
    # token: ${TELEGRAM_BOT_TOKEN}
    # allowed_users: [123456789]  # Your Telegram user ID
  
  voice:
    enabled: false
    # provider: twilio
    # account_sid: ${TWILIO_SID}
    # auth_token: ${TWILIO_TOKEN}
    # phone_number: "+1234567890"
  
  web:
    enabled: true
    port: 8421

# Browser Automation
browser:
  backend: chrome     # "chrome" (local, default) or "docker" (legacy browserless)
  headless: true
  # chrome_path: ""   # auto-detect Chrome/Chromium

# Visual Streaming (legacy)
visual:
  neko:
    enabled: false
    image: "ghcr.io/m1k1o/neko:firefox"
    port_range: [9000, 9010]

  terminal:
    enabled: false
    provider: ttyd
    port_range: [9100, 9110]

# Agent Defaults
agents:
  default_provider: cursor
  max_concurrent: 3
  auto_spawn: false
  escalation:
    stuck_timeout: 300  # seconds before escalating
    retry_limit: 3
    notify_on: [completion, failure, blocked]

# Project Overrides (optional - projects are auto-discovered from rdc list)
# projects:
#   documaker:
#     priority: high
#     preferred_provider: cursor
'''
