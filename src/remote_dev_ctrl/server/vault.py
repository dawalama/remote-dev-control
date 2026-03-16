"""Secrets management for RDC Command Center.

Uses simple file-based encryption with age or falls back to base64 obfuscation.
"""

import base64
import json
import os
import subprocess
from pathlib import Path

from .config import get_rdc_home


def get_secrets_path() -> Path:
    """Get the secrets file path."""
    return get_rdc_home() / "secrets.json"


def _has_age() -> bool:
    """Check if age encryption tool is available."""
    try:
        subprocess.run(["age", "--version"], capture_output=True, check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def _get_key_path() -> Path:
    """Get the age key file path."""
    return get_rdc_home() / ".age-key"


def _ensure_age_key() -> Path:
    """Ensure an age key exists, create if not."""
    key_path = _get_key_path()
    if not key_path.exists():
        result = subprocess.run(
            ["age-keygen", "-o", str(key_path)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Failed to generate age key: {result.stderr}")
        key_path.chmod(0o600)
    return key_path


class Vault:
    """Simple secrets vault with file-based storage."""
    
    def __init__(self):
        self._secrets: dict[str, str] = {}
        self._loaded = False
        self._use_age = _has_age()
    
    def _load(self) -> None:
        """Load secrets from file."""
        if self._loaded:
            return
        
        path = get_secrets_path()
        if not path.exists():
            self._secrets = {}
            self._loaded = True
            return
        
        content = path.read_text()
        
        if self._use_age:
            # Decrypt with age
            try:
                key_path = _get_key_path()
                if key_path.exists():
                    result = subprocess.run(
                        ["age", "-d", "-i", str(key_path)],
                        input=content,
                        capture_output=True,
                        text=True,
                    )
                    if result.returncode == 0:
                        self._secrets = json.loads(result.stdout)
                        self._loaded = True
                        return
            except Exception:
                pass
        
        # Fallback: base64 decode (obfuscation, not encryption)
        try:
            decoded = base64.b64decode(content).decode()
            self._secrets = json.loads(decoded)
        except Exception:
            # Plain JSON fallback
            self._secrets = json.loads(content)
        
        self._loaded = True
    
    def _save(self) -> None:
        """Save secrets to file."""
        path = get_secrets_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        
        data = json.dumps(self._secrets, indent=2)
        
        if self._use_age:
            # Encrypt with age
            try:
                key_path = _ensure_age_key()
                # Get public key from key file
                key_content = key_path.read_text()
                public_key = None
                for line in key_content.split("\n"):
                    if line.startswith("# public key:"):
                        public_key = line.split(": ")[1].strip()
                        break
                
                if public_key:
                    result = subprocess.run(
                        ["age", "-r", public_key],
                        input=data,
                        capture_output=True,
                        text=True,
                    )
                    if result.returncode == 0:
                        path.write_text(result.stdout)
                        path.chmod(0o600)
                        return
            except Exception:
                pass
        
        # Fallback: base64 encode (obfuscation)
        encoded = base64.b64encode(data.encode()).decode()
        path.write_text(encoded)
        path.chmod(0o600)
    
    def get(self, key: str) -> str | None:
        """Get a secret value."""
        self._load()
        return self._secrets.get(key)
    
    def set(self, key: str, value: str) -> None:
        """Set a secret value."""
        self._load()
        self._secrets[key] = value
        self._save()
    
    def delete(self, key: str) -> bool:
        """Delete a secret. Returns True if it existed."""
        self._load()
        if key in self._secrets:
            del self._secrets[key]
            self._save()
            return True
        return False
    
    def list_keys(self) -> list[str]:
        """List all secret keys (not values)."""
        self._load()
        return list(self._secrets.keys())
    
    def has(self, key: str) -> bool:
        """Check if a secret exists."""
        self._load()
        return key in self._secrets
    
    def export_to_env(self) -> dict[str, str]:
        """Export secrets as environment variables dict."""
        self._load()
        return dict(self._secrets)


# Global vault instance
_vault: Vault | None = None


def get_vault() -> Vault:
    """Get the global vault instance."""
    global _vault
    if _vault is None:
        _vault = Vault()
    return _vault


def get_secret(key: str) -> str | None:
    """Get a secret from the vault."""
    return get_vault().get(key)


def set_secret(key: str, value: str) -> None:
    """Set a secret in the vault."""
    get_vault().set(key, value)


def list_secrets() -> list[str]:
    """List all secret keys (not values)."""
    return get_vault().list_keys()


def resolve_secret_ref(value: str) -> str:
    """Resolve a secret reference like ${SECRET_NAME}.
    
    First checks vault, then environment variables.
    """
    if not (value.startswith("${") and value.endswith("}")):
        return value
    
    key = value[2:-1]
    
    # Check vault first
    secret = get_secret(key)
    if secret:
        return secret
    
    # Fall back to environment
    return os.environ.get(key, "")
