"""Secret scrubbing for logs and output."""

import re
from typing import Optional

from .vault import get_vault


class SecretScrubber:
    """Scrub secrets from text before logging."""
    
    # Common patterns for secrets
    PATTERNS = [
        # Generic key=value patterns
        r'(?i)(api[_-]?key|apikey|token|secret|password|passwd|pwd|auth|credential)["\']?\s*[=:]\s*["\']?[\w\-\.]+["\']?',
        
        # Bearer tokens
        r'Bearer\s+[\w\-\.]+',
        
        # OpenAI
        r'sk-[a-zA-Z0-9]{20,}',
        
        # Anthropic
        r'sk-ant-[a-zA-Z0-9\-]+',
        
        # GitHub
        r'ghp_[a-zA-Z0-9]{36}',
        r'github_pat_[a-zA-Z0-9_]{22,}',
        r'gho_[a-zA-Z0-9]{36}',
        
        # AWS
        r'AKIA[0-9A-Z]{16}',
        r'(?i)aws[_-]?secret[_-]?access[_-]?key\s*[=:]\s*[\w/\+]+',
        
        # Google
        r'AIza[0-9A-Za-z\-_]{35}',
        
        # Slack
        r'xox[baprs]-[0-9a-zA-Z\-]+',
        
        # Telegram
        r'\d{8,10}:[a-zA-Z0-9_-]{35}',
        
        # Generic long hex strings (potential secrets)
        r'(?<![a-zA-Z0-9])[a-fA-F0-9]{32,}(?![a-zA-Z0-9])',
        
        # Base64 encoded strings that look like secrets (long, no spaces)
        r'(?i)(key|token|secret|password)\s*[=:]\s*["\']?[A-Za-z0-9+/]{40,}={0,2}["\']?',
        
        # Connection strings
        r'(?i)(postgres|mysql|mongodb|redis)://[^\s]+',
        
        # Private keys
        r'-----BEGIN[A-Z ]+PRIVATE KEY-----',
    ]
    
    def __init__(self, replacement: str = "[REDACTED]"):
        self.replacement = replacement
        self._compiled_patterns = [re.compile(p) for p in self.PATTERNS]
        self._known_secrets: set[str] = set()
        self._min_secret_length = 8  # Don't scrub very short strings
    
    def add_known_secret(self, secret: str) -> None:
        """Add a known secret to scrub."""
        if secret and len(secret) >= self._min_secret_length:
            self._known_secrets.add(secret)
    
    def load_secrets_from_vault(self) -> None:
        """Load all secrets from the vault for scrubbing."""
        try:
            vault = get_vault()
            for key in vault.list_keys():
                secret = vault.get(key)
                if secret:
                    self.add_known_secret(secret)
        except Exception:
            pass
    
    def scrub(self, text: str) -> str:
        """Scrub all secrets from text."""
        if not text:
            return text
        
        result = text
        
        # Scrub known secrets first (exact match)
        for secret in self._known_secrets:
            if secret in result:
                result = result.replace(secret, self.replacement)
        
        # Scrub pattern matches
        for pattern in self._compiled_patterns:
            result = pattern.sub(self.replacement, result)
        
        return result
    
    def scrub_dict(self, data: dict, keys_to_scrub: Optional[set[str]] = None) -> dict:
        """Scrub secrets from a dictionary."""
        if keys_to_scrub is None:
            keys_to_scrub = {
                'password', 'passwd', 'pwd', 'secret', 'token', 'api_key',
                'apikey', 'auth', 'credential', 'private_key', 'access_key',
            }
        
        result = {}
        for key, value in data.items():
            key_lower = key.lower()
            
            # Check if key indicates a secret
            if any(s in key_lower for s in keys_to_scrub):
                result[key] = self.replacement
            elif isinstance(value, str):
                result[key] = self.scrub(value)
            elif isinstance(value, dict):
                result[key] = self.scrub_dict(value, keys_to_scrub)
            elif isinstance(value, list):
                result[key] = [
                    self.scrub(v) if isinstance(v, str) else
                    self.scrub_dict(v, keys_to_scrub) if isinstance(v, dict) else v
                    for v in value
                ]
            else:
                result[key] = value
        
        return result


# Global instance
_scrubber: Optional[SecretScrubber] = None


def get_scrubber() -> SecretScrubber:
    """Get the global scrubber instance."""
    global _scrubber
    if _scrubber is None:
        _scrubber = SecretScrubber()
        _scrubber.load_secrets_from_vault()
    return _scrubber


def scrub(text: str) -> str:
    """Convenience function to scrub text."""
    return get_scrubber().scrub(text)


class ScrubberFileWrapper:
    """File-like wrapper that scrubs content before writing.
    
    Note: This wrapper does NOT support fileno() so it cannot be used
    directly with subprocess.Popen stdout/stderr. For subprocess output,
    use scrub_file() after the process completes or read and scrub manually.
    """
    
    def __init__(self, file, scrubber: Optional[SecretScrubber] = None):
        self._file = file
        self._scrubber = scrubber or get_scrubber()
    
    def write(self, data: str) -> int:
        scrubbed = self._scrubber.scrub(data)
        return self._file.write(scrubbed)
    
    def writelines(self, lines):
        for line in lines:
            self.write(line)
    
    def flush(self):
        return self._file.flush()
    
    def close(self):
        return self._file.close()
    
    def fileno(self):
        return self._file.fileno()
    
    def __enter__(self):
        return self
    
    def __exit__(self, *args):
        self.close()


def scrub_file(path: str, scrubber: Optional[SecretScrubber] = None) -> None:
    """Scrub secrets from a file in-place."""
    from pathlib import Path
    
    p = Path(path)
    if not p.exists():
        return
    
    s = scrubber or get_scrubber()
    content = p.read_text()
    scrubbed = s.scrub(content)
    
    if content != scrubbed:
        p.write_text(scrubbed)


def scrub_log_content(content: str) -> str:
    """Scrub secrets from log content before displaying."""
    return get_scrubber().scrub(content)
