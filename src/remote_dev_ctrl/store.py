"""Persistent storage for configuration and index."""

import json
from pathlib import Path

from .models import GlobalConfig, KnowledgeNode, ProjectConfig


_NEW_CONFIG_DIR = Path.home() / ".config" / "remote-dev-ctrl"
_OLD_CONFIG_DIR = Path.home() / ".config" / "agent-dev-tool"
CONFIG_FILE = "config.json"
INDEX_FILE = "index.json"


def _resolve_config_dir() -> Path:
    """Return the config dir, falling back to legacy path if new one doesn't exist."""
    if _NEW_CONFIG_DIR.exists():
        return _NEW_CONFIG_DIR
    if _OLD_CONFIG_DIR.exists():
        return _OLD_CONFIG_DIR
    return _NEW_CONFIG_DIR  # default for fresh installs


# Resolved at import time for backward compat, but re-checked in load_config
DEFAULT_CONFIG_DIR = _resolve_config_dir()


def ensure_config_dir() -> Path:
    DEFAULT_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    return DEFAULT_CONFIG_DIR


def load_config() -> GlobalConfig:
    config_path = DEFAULT_CONFIG_DIR / CONFIG_FILE
    if config_path.exists():
        data = json.loads(config_path.read_text())
        return GlobalConfig(**data)
    return GlobalConfig()


def save_config(config: GlobalConfig) -> None:
    ensure_config_dir()
    config_path = DEFAULT_CONFIG_DIR / CONFIG_FILE
    
    # Convert to JSON-serializable dict
    data = json.loads(config.model_dump_json())
    config_path.write_text(json.dumps(data, indent=2))


def load_index() -> KnowledgeNode | None:
    index_path = DEFAULT_CONFIG_DIR / INDEX_FILE
    if index_path.exists():
        data = json.loads(index_path.read_text())
        return KnowledgeNode(**data)
    return None


def save_index(index: KnowledgeNode) -> None:
    ensure_config_dir()
    index_path = DEFAULT_CONFIG_DIR / INDEX_FILE
    
    data = json.loads(index.model_dump_json())
    index_path.write_text(json.dumps(data, indent=2))


def get_index_path() -> Path:
    return DEFAULT_CONFIG_DIR / INDEX_FILE


def get_config_path() -> Path:
    return DEFAULT_CONFIG_DIR / CONFIG_FILE
