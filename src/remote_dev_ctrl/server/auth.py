"""Authentication and authorization for RDC Command Center."""

import base64
import hashlib
import hmac
import json
import secrets
import sqlite3
import time
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Optional

from pydantic import BaseModel

from .config import get_rdc_home

_WS_TICKET_SECRET = secrets.token_bytes(32)


def issue_websocket_ticket(token_info: "TokenInfo", path: str, ttl_seconds: int = 60) -> str:
    """Issue a short-lived ticket bound to one WebSocket path."""
    payload = {
        "sub": token_info.id,
        "path": path,
        "exp": int(time.time()) + min(max(ttl_seconds, 10), 120),
        "nonce": secrets.token_hex(8),
    }
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode()
    ).decode().rstrip("=")
    signature = hmac.new(_WS_TICKET_SECRET, encoded.encode(), hashlib.sha256).digest()
    signed = base64.urlsafe_b64encode(signature).decode().rstrip("=")
    return f"{encoded}.{signed}"


def validate_websocket_ticket(ticket: str, path: str) -> "TokenInfo | None":
    """Validate a ticket's signature, expiry, path binding, and token status."""
    try:
        encoded, signed = ticket.split(".", 1)
        expected = base64.urlsafe_b64encode(
            hmac.new(_WS_TICKET_SECRET, encoded.encode(), hashlib.sha256).digest()
        ).decode().rstrip("=")
        if not hmac.compare_digest(signed, expected):
            return None
        payload = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        if payload.get("path") != path or int(payload.get("exp", 0)) < int(time.time()):
            return None
        info = get_auth_manager().get_token_by_id(str(payload.get("sub", "")))
        if not info or info.revoked or (info.expires_at and info.expires_at < datetime.now()):
            return None
        return info
    except (ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None


class Role(str, Enum):
    """User roles with different permission levels."""
    ADMIN = "admin"
    OPERATOR = "operator"
    VIEWER = "viewer"
    AGENT = "agent"


class Permission(str, Enum):
    """Available permissions."""
    # Admin only
    TOKENS_MANAGE = "tokens.manage"
    CONFIG_WRITE = "config.write"
    SECRETS_MANAGE = "secrets.manage"
    SYSTEM_CONTROL = "system.control"
    SETTINGS_READ = "settings.read"
    
    # Operator+
    AGENTS_SPAWN = "agents.spawn"
    AGENTS_STOP = "agents.stop"
    TASKS_CREATE = "tasks.create"
    TASKS_CANCEL = "tasks.cancel"
    TERMINALS_READ = "terminals.read"
    TERMINALS_CONTROL = "terminals.control"
    PROCESSES_CONTROL = "processes.control"
    BROWSER_CONTROL = "browser.control"
    CONTEXT_WRITE = "context.write"
    PROJECTS_CONTROL = "projects.control"
    VOICE_CONTROL = "voice.control"
    SESSIONS_CONTROL = "sessions.control"
    STATE_CONTROL = "state.control"
    
    # Viewer+
    AGENTS_READ = "agents.read"
    TASKS_READ = "tasks.read"
    LOGS_READ = "logs.read"
    STATUS_READ = "status.read"
    PROJECTS_READ = "projects.read"
    PROCESSES_READ = "processes.read"
    BROWSER_READ = "browser.read"
    CONTEXT_READ = "context.read"
    SESSIONS_READ = "sessions.read"
    WORKERS_READ = "workers.read"
    
    # Agent (for agent-to-server)
    HEARTBEAT = "heartbeat"
    TASK_UPDATE = "task.update"
    LOGS_WRITE = "logs.write"


ROLE_PERMISSIONS: dict[Role, set[Permission]] = {
    Role.ADMIN: set(Permission),  # All permissions
    Role.OPERATOR: {
        Permission.AGENTS_SPAWN,
        Permission.AGENTS_STOP,
        Permission.TASKS_CREATE,
        Permission.TASKS_CANCEL,
        Permission.AGENTS_READ,
        Permission.TASKS_READ,
        Permission.LOGS_READ,
        Permission.STATUS_READ,
        Permission.PROJECTS_READ,
        Permission.PROCESSES_READ,
        Permission.PROCESSES_CONTROL,
        Permission.TERMINALS_READ,
        Permission.TERMINALS_CONTROL,
        Permission.BROWSER_READ,
        Permission.BROWSER_CONTROL,
        Permission.CONTEXT_READ,
        Permission.CONTEXT_WRITE,
        Permission.PROJECTS_CONTROL,
        Permission.VOICE_CONTROL,
        Permission.SETTINGS_READ,
        Permission.SESSIONS_READ,
        Permission.SESSIONS_CONTROL,
        Permission.WORKERS_READ,
        Permission.STATE_CONTROL,
    },
    Role.VIEWER: {
        Permission.AGENTS_READ,
        Permission.TASKS_READ,
        Permission.LOGS_READ,
        Permission.STATUS_READ,
        Permission.PROJECTS_READ,
        Permission.PROCESSES_READ,
        Permission.TERMINALS_READ,
        Permission.BROWSER_READ,
        Permission.CONTEXT_READ,
        Permission.SETTINGS_READ,
        Permission.SESSIONS_READ,
        Permission.WORKERS_READ,
    },
    Role.AGENT: {
        Permission.HEARTBEAT,
        Permission.TASK_UPDATE,
        Permission.LOGS_WRITE,
        Permission.STATUS_READ,
    },
}


class TokenInfo(BaseModel):
    """Information about an API token."""
    id: str
    name: str
    role: Role
    created_at: datetime
    expires_at: Optional[datetime] = None
    last_used_at: Optional[datetime] = None
    revoked: bool = False
    device_name: Optional[str] = None
    parent_token_id: Optional[str] = None


class AuthManager:
    """Manages authentication tokens and authorization."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or get_rdc_home() / "data" / "auth.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @staticmethod
    def _row_to_token_info(row) -> "TokenInfo":
        """Convert a sqlite3.Row to a TokenInfo."""
        return TokenInfo(
            id=row["id"],
            name=row["name"],
            role=Role(row["role"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            expires_at=datetime.fromisoformat(row["expires_at"]) if row["expires_at"] else None,
            last_used_at=datetime.fromisoformat(row["last_used_at"]) if row["last_used_at"] else None,
            revoked=bool(row["revoked"]),
            device_name=row["device_name"] if row["device_name"] else None,
            parent_token_id=row["parent_token_id"] if row["parent_token_id"] else None,
        )
    
    def _init_db(self):
        """Initialize the auth database."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tokens (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    token_hash TEXT NOT NULL,
                    role TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    expires_at TIMESTAMP,
                    last_used_at TIMESTAMP,
                    revoked BOOLEAN DEFAULT FALSE,
                    created_by TEXT
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_tokens_hash ON tokens(token_hash)
            """)
            # Add columns for paired device support (backwards compat)
            for col, coltype in [("device_name", "TEXT"), ("parent_token_id", "TEXT")]:
                try:
                    conn.execute(f"ALTER TABLE tokens ADD COLUMN {col} {coltype}")
                except sqlite3.OperationalError:
                    pass  # column already exists
            conn.commit()
    
    def _hash_token(self, token: str) -> str:
        """Hash a token for storage."""
        return hashlib.sha256(token.encode()).hexdigest()
    
    def create_token(
        self,
        name: str,
        role: Role = Role.OPERATOR,
        expires_in_days: Optional[int] = None,
        created_by: Optional[str] = None,
    ) -> tuple[str, TokenInfo]:
        """Create a new API token. Returns (plain_token, token_info)."""
        token_id = secrets.token_hex(8)
        plain_token = f"rdc_{secrets.token_urlsafe(32)}"
        token_hash = self._hash_token(plain_token)
        
        now = datetime.now()
        expires_at = None
        if expires_in_days:
            expires_at = now + timedelta(days=expires_in_days)
        
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO tokens (id, name, token_hash, role, created_at, expires_at, created_by)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (token_id, name, token_hash, role.value, now.isoformat(), 
                  expires_at.isoformat() if expires_at else None, created_by))
            conn.commit()
        
        info = TokenInfo(
            id=token_id,
            name=name,
            role=role,
            created_at=now,
            expires_at=expires_at,
        )
        
        return plain_token, info
    
    def create_paired_token(
        self,
        parent_token: str,
        device_name: str = "Unknown Device",
    ) -> tuple[str, TokenInfo]:
        """Create a child token for a paired device. Returns (plain_token, token_info)."""
        parent_info = self.validate_token(parent_token)
        if not parent_info:
            raise ValueError("Invalid parent token")

        token_id = secrets.token_hex(8)
        plain_token = f"rdc_{secrets.token_urlsafe(32)}"
        token_hash = self._hash_token(plain_token)

        now = datetime.now()
        name = f"Paired: {device_name}"

        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO tokens (id, name, token_hash, role, created_at, created_by, device_name, parent_token_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (token_id, name, token_hash, parent_info.role.value, now.isoformat(),
                  parent_info.id, device_name, parent_info.id))
            conn.commit()

        info = TokenInfo(
            id=token_id,
            name=name,
            role=parent_info.role,
            created_at=now,
            device_name=device_name,
            parent_token_id=parent_info.id,
        )
        return plain_token, info

    def list_paired_sessions(self) -> list[TokenInfo]:
        """List all active paired device sessions."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT * FROM tokens
                WHERE parent_token_id IS NOT NULL AND NOT revoked
                ORDER BY created_at DESC
            """)
            return [self._row_to_token_info(row) for row in cursor.fetchall()]

    def list_paired_sessions_for_token(self, token_info: "TokenInfo") -> list["TokenInfo"]:
        """Scoped listing: master sees children it created, child sees only itself."""
        if token_info.parent_token_id:
            # Child token — return only itself
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(
                    "SELECT * FROM tokens WHERE id = ? AND NOT revoked",
                    (token_info.id,),
                )
                row = cursor.fetchone()
                return [self._row_to_token_info(row)] if row else []
        else:
            # Master token — return children it created
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(
                    "SELECT * FROM tokens WHERE parent_token_id = ? AND NOT revoked ORDER BY created_at DESC",
                    (token_info.id,),
                )
                return [self._row_to_token_info(row) for row in cursor.fetchall()]

    def get_token_by_id(self, token_id: str) -> Optional["TokenInfo"]:
        """Look up a token by its DB id."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("SELECT * FROM tokens WHERE id = ?", (token_id,))
            row = cursor.fetchone()
            return self._row_to_token_info(row) if row else None

    def validate_token(self, token: str) -> Optional[TokenInfo]:
        """Validate a token and return its info if valid."""
        if not token:
            return None
        
        # Strip "Bearer " prefix if present
        if token.startswith("Bearer "):
            token = token[7:]
        
        token_hash = self._hash_token(token)
        
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT * FROM tokens WHERE token_hash = ?
            """, (token_hash,))
            row = cursor.fetchone()
        
        if not row:
            return None
        
        # Check if revoked
        if row["revoked"]:
            return None
        
        # Check if expired
        if row["expires_at"]:
            expires = datetime.fromisoformat(row["expires_at"])
            if expires < datetime.now():
                return None
        
        # Update last used
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                UPDATE tokens SET last_used_at = ? WHERE id = ?
            """, (datetime.now().isoformat(), row["id"]))
            conn.commit()
        
        info = self._row_to_token_info(row)
        info.last_used_at = datetime.now()
        info.revoked = False
        return info

    def has_permission(self, token_info: TokenInfo, permission: Permission) -> bool:
        """Check if a token has a specific permission."""
        if not token_info:
            return False
        return permission in ROLE_PERMISSIONS.get(token_info.role, set())
    
    def list_tokens(self) -> list[TokenInfo]:
        """List all tokens (without the actual token values)."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT * FROM tokens ORDER BY created_at DESC
            """)
            rows = cursor.fetchall()
        
        return [self._row_to_token_info(row) for row in rows]
    
    def revoke_token(self, token_id: str) -> bool:
        """Revoke a token by its ID."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                UPDATE tokens SET revoked = TRUE WHERE id = ?
            """, (token_id,))
            conn.commit()
            return cursor.rowcount > 0
    
    def delete_token(self, token_id: str) -> bool:
        """Permanently delete a token."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                DELETE FROM tokens WHERE id = ?
            """, (token_id,))
            conn.commit()
            return cursor.rowcount > 0
    
    def has_any_tokens(self) -> bool:
        """Check if any tokens exist (for first-run setup)."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM tokens WHERE NOT revoked")
            count = cursor.fetchone()[0]
        return count > 0
    
    def create_initial_admin_token(self) -> Optional[tuple[str, TokenInfo]]:
        """Create initial admin token if none exist."""
        if self.has_any_tokens():
            return None
        return self.create_token("Initial Admin Token", role=Role.ADMIN)


# Global instance
_auth_manager: Optional[AuthManager] = None


def get_auth_manager() -> AuthManager:
    """Get the global auth manager instance."""
    global _auth_manager
    if _auth_manager is None:
        _auth_manager = AuthManager()
    return _auth_manager
