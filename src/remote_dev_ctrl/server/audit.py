"""Audit logging for RDC Command Center."""

import hashlib
import hmac
import json
import sqlite3
import os
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel

from .config import get_rdc_home


class AuditAction(str, Enum):
    """Audit event types."""
    # Auth
    AUTH_LOGIN_SUCCESS = "auth.login.success"
    AUTH_LOGIN_FAILED = "auth.login.failed"
    AUTH_TOKEN_CREATED = "auth.token.created"
    AUTH_TOKEN_REVOKED = "auth.token.revoked"
    AUTH_DENIED = "auth.denied"
    
    # Agents
    AGENT_SPAWN = "agent.spawn"
    AGENT_STOP = "agent.stop"
    AGENT_ERROR = "agent.error"
    AGENT_TASK_ASSIGNED = "agent.task.assigned"
    AGENT_RETRY = "agent.retry"
    
    # Tasks
    TASK_CREATED = "task.created"
    TASK_STARTED = "task.started"
    TASK_COMPLETED = "task.completed"
    TASK_FAILED = "task.failed"
    TASK_CANCELLED = "task.cancelled"
    
    # Secrets
    SECRET_READ = "secret.read"
    SECRET_WRITE = "secret.write"
    SECRET_DELETE = "secret.delete"
    
    # Config
    CONFIG_UPDATED = "config.updated"
    PROJECT_ADDED = "project.added"
    PROJECT_REMOVED = "project.removed"
    
    # Channels
    CHANNEL_TELEGRAM_COMMAND = "channel.telegram.command"
    CHANNEL_VOICE_COMMAND = "channel.voice.command"
    CHANNEL_WEBSOCKET_CONNECT = "channel.websocket.connect"
    CHANNEL_WEBSOCKET_DISCONNECT = "channel.websocket.disconnect"
    
    # Security
    SECURITY_RATE_LIMIT = "security.rate_limit"
    SECURITY_BLOCKED_IP = "security.blocked_ip"
    SECURITY_SUSPICIOUS_PROMPT = "security.suspicious_prompt"
    
    # Server
    SERVER_STARTED = "server.started"
    SERVER_STOPPED = "server.stopped"


class AuditEntry(BaseModel):
    """A single audit log entry."""
    id: Optional[int] = None
    timestamp: datetime
    
    # Who
    actor_type: str  # 'user', 'agent', 'system', 'channel'
    actor_id: Optional[str] = None
    actor_ip: Optional[str] = None
    
    # What
    action: str
    resource_type: Optional[str] = None
    resource_id: Optional[str] = None
    
    # Context
    request_id: Optional[str] = None
    channel: Optional[str] = None  # 'api', 'dashboard', 'telegram', 'cli'
    
    # Outcome
    status: str = "success"  # 'success', 'denied', 'error'
    error: Optional[str] = None
    
    # Details
    metadata: Optional[dict[str, Any]] = None
    
    # Integrity
    prev_hash: Optional[str] = None
    entry_hash: Optional[str] = None


class AuditLogger:
    """Append-only audit logger with integrity verification."""
    
    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or get_rdc_home() / "data" / "audit.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._hmac_key = self._get_or_create_hmac_key()
        self._last_hash: Optional[str] = None
        self._init_db()
    
    def _get_or_create_hmac_key(self) -> bytes:
        """Get or create the HMAC key for entry signing."""
        key_path = get_rdc_home() / "data" / ".audit_key"
        if key_path.exists():
            return key_path.read_bytes()
        else:
            key = os.urandom(32)
            key_path.parent.mkdir(parents=True, exist_ok=True)
            key_path.write_bytes(key)
            key_path.chmod(0o600)
            return key
    
    def _init_db(self):
        """Initialize the audit database."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TIMESTAMP NOT NULL,
                    actor_type TEXT NOT NULL,
                    actor_id TEXT,
                    actor_ip TEXT,
                    action TEXT NOT NULL,
                    resource_type TEXT,
                    resource_id TEXT,
                    request_id TEXT,
                    channel TEXT,
                    status TEXT DEFAULT 'success',
                    error TEXT,
                    metadata JSON,
                    prev_hash TEXT,
                    entry_hash TEXT NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_time ON audit_log(timestamp)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_log(actor_type, actor_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_resource ON audit_log(resource_type, resource_id)")
            conn.commit()
            
            # Get last hash for chain integrity
            cursor = conn.execute("SELECT entry_hash FROM audit_log ORDER BY id DESC LIMIT 1")
            row = cursor.fetchone()
            if row:
                self._last_hash = row[0]
    
    def _compute_hash(self, entry: AuditEntry) -> str:
        """Compute HMAC hash for an entry."""
        data = f"{entry.timestamp.isoformat()}:{entry.actor_type}:{entry.actor_id}:{entry.action}:{entry.prev_hash}"
        return hmac.new(self._hmac_key, data.encode(), hashlib.sha256).hexdigest()[:32]
    
    def log(
        self,
        action: AuditAction | str,
        actor_type: str = "system",
        actor_id: Optional[str] = None,
        actor_ip: Optional[str] = None,
        resource_type: Optional[str] = None,
        resource_id: Optional[str] = None,
        request_id: Optional[str] = None,
        channel: Optional[str] = None,
        status: str = "success",
        error: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> AuditEntry:
        """Log an audit event."""
        entry = AuditEntry(
            timestamp=datetime.now(),
            actor_type=actor_type,
            actor_id=actor_id,
            actor_ip=actor_ip,
            action=action.value if isinstance(action, AuditAction) else action,
            resource_type=resource_type,
            resource_id=resource_id,
            request_id=request_id,
            channel=channel,
            status=status,
            error=error,
            metadata=metadata,
            prev_hash=self._last_hash,
        )
        
        entry.entry_hash = self._compute_hash(entry)
        self._last_hash = entry.entry_hash
        
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                INSERT INTO audit_log (
                    timestamp, actor_type, actor_id, actor_ip, action,
                    resource_type, resource_id, request_id, channel,
                    status, error, metadata, prev_hash, entry_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                entry.timestamp.isoformat(),
                entry.actor_type,
                entry.actor_id,
                entry.actor_ip,
                entry.action,
                entry.resource_type,
                entry.resource_id,
                entry.request_id,
                entry.channel,
                entry.status,
                entry.error,
                json.dumps(entry.metadata) if entry.metadata else None,
                entry.prev_hash,
                entry.entry_hash,
            ))
            entry.id = cursor.lastrowid
            conn.commit()
        
        return entry
    
    def query(
        self,
        action: Optional[str] = None,
        actor_type: Optional[str] = None,
        actor_id: Optional[str] = None,
        resource_type: Optional[str] = None,
        resource_id: Optional[str] = None,
        status: Optional[str] = None,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AuditEntry]:
        """Query audit logs with filters."""
        conditions = []
        params = []
        
        if action:
            conditions.append("action = ?")
            params.append(action)
        if actor_type:
            conditions.append("actor_type = ?")
            params.append(actor_type)
        if actor_id:
            conditions.append("actor_id = ?")
            params.append(actor_id)
        if resource_type:
            conditions.append("resource_type = ?")
            params.append(resource_type)
        if resource_id:
            conditions.append("resource_id = ?")
            params.append(resource_id)
        if status:
            conditions.append("status = ?")
            params.append(status)
        if since:
            conditions.append("timestamp >= ?")
            params.append(since.isoformat())
        if until:
            conditions.append("timestamp <= ?")
            params.append(until.isoformat())
        
        where_clause = " AND ".join(conditions) if conditions else "1=1"
        
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(f"""
                SELECT * FROM audit_log
                WHERE {where_clause}
                ORDER BY timestamp DESC
                LIMIT ? OFFSET ?
            """, params + [limit, offset])
            rows = cursor.fetchall()
        
        entries = []
        for row in rows:
            entries.append(AuditEntry(
                id=row["id"],
                timestamp=datetime.fromisoformat(row["timestamp"]),
                actor_type=row["actor_type"],
                actor_id=row["actor_id"],
                actor_ip=row["actor_ip"],
                action=row["action"],
                resource_type=row["resource_type"],
                resource_id=row["resource_id"],
                request_id=row["request_id"],
                channel=row["channel"],
                status=row["status"],
                error=row["error"],
                metadata=json.loads(row["metadata"]) if row["metadata"] else None,
                prev_hash=row["prev_hash"],
                entry_hash=row["entry_hash"],
            ))
        
        return entries
    
    def verify_integrity(self, entries: Optional[list[AuditEntry]] = None) -> tuple[bool, Optional[str]]:
        """Verify the integrity chain of audit entries."""
        if entries is None:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute("SELECT * FROM audit_log ORDER BY id ASC")
                rows = cursor.fetchall()
            
            entries = []
            for row in rows:
                entries.append(AuditEntry(
                    id=row["id"],
                    timestamp=datetime.fromisoformat(row["timestamp"]),
                    actor_type=row["actor_type"],
                    actor_id=row["actor_id"],
                    action=row["action"],
                    prev_hash=row["prev_hash"],
                    entry_hash=row["entry_hash"],
                ))
        
        prev_hash = None
        for entry in entries:
            # Check chain
            if entry.prev_hash != prev_hash:
                return False, f"Chain broken at entry {entry.id}: expected prev_hash {prev_hash}, got {entry.prev_hash}"
            
            # Verify hash
            expected_hash = self._compute_hash(entry)
            if entry.entry_hash != expected_hash:
                return False, f"Invalid hash at entry {entry.id}: expected {expected_hash}, got {entry.entry_hash}"
            
            prev_hash = entry.entry_hash
        
        return True, None
    
    def count(
        self,
        action: Optional[str] = None,
        since: Optional[datetime] = None,
    ) -> int:
        """Count audit entries matching filters."""
        conditions = []
        params = []
        
        if action:
            conditions.append("action = ?")
            params.append(action)
        if since:
            conditions.append("timestamp >= ?")
            params.append(since.isoformat())
        
        where_clause = " AND ".join(conditions) if conditions else "1=1"
        
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(f"SELECT COUNT(*) FROM audit_log WHERE {where_clause}", params)
            return cursor.fetchone()[0]


# Global instance
_audit_logger: Optional[AuditLogger] = None


def get_audit_logger() -> AuditLogger:
    """Get the global audit logger instance."""
    global _audit_logger
    if _audit_logger is None:
        _audit_logger = AuditLogger()
    return _audit_logger


def audit(
    action: AuditAction | str,
    **kwargs,
) -> AuditEntry:
    """Convenience function to log an audit event."""
    return get_audit_logger().log(action, **kwargs)
