"""DuckDB-based event store for time-series event logging.

Events are stored in weekly rotating files to keep table size manageable.
"""

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any

import duckdb

from .config import get_rdc_home


class EventStore:
    """Time-series event store using DuckDB with weekly rotation."""
    
    RETENTION_DAYS = 7
    
    def __init__(self):
        self._conn: Optional[duckdb.DuckDBPyConnection] = None
        self._current_file: Optional[Path] = None
        self._ensure_connection()
    
    def _get_events_dir(self) -> Path:
        """Get the events directory."""
        events_dir = get_rdc_home() / "events"
        events_dir.mkdir(parents=True, exist_ok=True)
        return events_dir
    
    def _get_current_db_path(self) -> Path:
        """Get the current week's database file path."""
        # Use ISO week number for rotation
        now = datetime.now()
        week_id = now.strftime("%Y-W%W")
        return self._get_events_dir() / f"events_{week_id}.duckdb"
    
    def _ensure_connection(self) -> duckdb.DuckDBPyConnection:
        """Ensure we have a connection to the current week's database."""
        current_path = self._get_current_db_path()
        
        # Reconnect if week changed
        if self._current_file != current_path:
            if self._conn:
                self._conn.close()
            
            self._conn = duckdb.connect(str(current_path))
            self._current_file = current_path
            self._init_schema()
            
            # Run cleanup on connection
            self._cleanup_old_files()
        
        return self._conn
    
    def _init_schema(self):
        """Initialize the events table schema."""
        # Create sequence for auto-incrementing IDs
        self._conn.execute("""
            CREATE SEQUENCE IF NOT EXISTS events_id_seq START 1
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY DEFAULT nextval('events_id_seq'),
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                session_id VARCHAR,
                client_id VARCHAR,
                client_name VARCHAR,
                event_type VARCHAR NOT NULL,
                direction VARCHAR,
                data JSON,
                project VARCHAR,
                source VARCHAR,
                duration_ms INTEGER
            )
        """)
        
        # Add client columns if they don't exist (for schema migration)
        try:
            self._conn.execute("ALTER TABLE events ADD COLUMN IF NOT EXISTS client_id VARCHAR")
            self._conn.execute("ALTER TABLE events ADD COLUMN IF NOT EXISTS client_name VARCHAR")
        except Exception:
            pass  # Columns may already exist or syntax not supported
        
        # Create indexes for common queries
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp)
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type)
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_events_project ON events(project)
        """)
    
    def _cleanup_old_files(self):
        """Remove database files older than retention period."""
        events_dir = self._get_events_dir()
        cutoff = datetime.now() - timedelta(days=self.RETENTION_DAYS + 7)  # Keep extra week buffer
        
        for f in events_dir.glob("events_*.duckdb*"):
            try:
                # Parse week from filename
                name = f.stem.replace("events_", "").split(".")[0]
                # Convert "2024-W05" format to date
                year, week = name.split("-W")
                file_date = datetime.strptime(f"{year}-W{week}-1", "%Y-W%W-%w")
                
                if file_date < cutoff:
                    f.unlink()
                    print(f"[EventStore] Cleaned up old file: {f.name}")
            except (ValueError, OSError) as e:
                # Skip files that don't match expected format
                pass
    
    def log(
        self,
        event_type: str,
        direction: str = "system",
        data: Optional[Dict[str, Any]] = None,
        session_id: Optional[str] = None,
        client_id: Optional[str] = None,
        client_name: Optional[str] = None,
        project: Optional[str] = None,
        source: str = "server",
        duration_ms: Optional[int] = None,
    ) -> int:
        """Log an event to the store.
        
        Returns the event ID.
        """
        conn = self._ensure_connection()
        
        result = conn.execute("""
            INSERT INTO events (timestamp, session_id, client_id, client_name, event_type, direction, data, project, source, duration_ms)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING id
        """, [
            datetime.now(),
            session_id,
            client_id,
            client_name,
            event_type,
            direction,
            json.dumps(data) if data else None,
            project,
            source,
            duration_ms,
        ]).fetchone()
        
        return result[0] if result else 0
    
    def query(
        self,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        event_type: Optional[str] = None,
        direction: Optional[str] = None,
        project: Optional[str] = None,
        session_id: Optional[str] = None,
        limit: int = 1000,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """Query events with filters.
        
        Defaults to last 30 minutes if no time range specified.
        """
        conn = self._ensure_connection()
        
        # Default to last 30 minutes
        if start_time is None:
            start_time = datetime.now() - timedelta(minutes=30)
        if end_time is None:
            end_time = datetime.now()
        
        conditions = ["timestamp >= ? AND timestamp <= ?"]
        params = [start_time, end_time]
        
        if event_type:
            conditions.append("event_type = ?")
            params.append(event_type)
        
        if direction:
            conditions.append("direction = ?")
            params.append(direction)
        
        if project:
            conditions.append("project = ?")
            params.append(project)
        
        if session_id:
            conditions.append("session_id = ?")
            params.append(session_id)
        
        where_clause = " AND ".join(conditions)
        params.extend([limit, offset])
        
        rows = conn.execute(f"""
            SELECT id, timestamp, session_id, client_id, client_name, event_type, direction, data, project, source, duration_ms
            FROM events
            WHERE {where_clause}
            ORDER BY timestamp DESC
            LIMIT ? OFFSET ?
        """, params).fetchall()
        
        events = []
        for row in rows:
            events.append({
                "id": row[0],
                "timestamp": row[1].isoformat() if row[1] else None,
                "session_id": row[2],
                "client_id": row[3],
                "client_name": row[4],
                "event_type": row[5],
                "direction": row[6],
                "data": json.loads(row[7]) if row[7] else None,
                "project": row[8],
                "source": row[9],
                "duration_ms": row[10],
            })
        
        return events
    
    def query_across_weeks(
        self,
        start_time: datetime,
        end_time: Optional[datetime] = None,
        **kwargs,
    ) -> List[Dict[str, Any]]:
        """Query events across multiple weekly files if needed."""
        if end_time is None:
            end_time = datetime.now()
        
        events_dir = self._get_events_dir()
        all_events = []
        
        # Find all relevant database files
        for f in sorted(events_dir.glob("events_*.duckdb")):
            if f.suffix == ".duckdb" and not f.name.endswith(".wal"):
                try:
                    conn = duckdb.connect(str(f), read_only=True)
                    
                    # Check if table exists
                    tables = conn.execute("SHOW TABLES").fetchall()
                    if not any(t[0] == "events" for t in tables):
                        conn.close()
                        continue
                    
                    # Query this file
                    rows = conn.execute("""
                        SELECT id, timestamp, session_id, client_id, client_name, event_type, direction, data, project, source, duration_ms
                        FROM events
                        WHERE timestamp >= ? AND timestamp <= ?
                        ORDER BY timestamp DESC
                        LIMIT 1000
                    """, [start_time, end_time]).fetchall()
                    
                    for row in rows:
                        all_events.append({
                            "id": row[0],
                            "timestamp": row[1].isoformat() if row[1] else None,
                            "session_id": row[2],
                            "client_id": row[3],
                            "client_name": row[4],
                            "event_type": row[5],
                            "direction": row[6],
                            "data": json.loads(row[7]) if row[7] else None,
                            "project": row[8],
                            "source": row[9],
                            "duration_ms": row[10],
                        })
                    
                    conn.close()
                except Exception as e:
                    print(f"[EventStore] Error reading {f}: {e}")
        
        # Sort by timestamp descending and limit
        all_events.sort(key=lambda e: e["timestamp"] or "", reverse=True)
        limit = kwargs.get("limit", 1000)
        return all_events[:limit]
    
    def get_stats(self, minutes: int = 30) -> Dict[str, Any]:
        """Get event statistics for the given time period."""
        conn = self._ensure_connection()
        start_time = datetime.now() - timedelta(minutes=minutes)
        
        stats = conn.execute("""
            SELECT 
                COUNT(*) as total,
                COUNT(DISTINCT session_id) as sessions,
                COUNT(DISTINCT event_type) as event_types,
                COUNT(CASE WHEN direction = 'sent' THEN 1 END) as sent,
                COUNT(CASE WHEN direction = 'received' THEN 1 END) as received
            FROM events
            WHERE timestamp >= ?
        """, [start_time]).fetchone()
        
        type_counts = conn.execute("""
            SELECT event_type, COUNT(*) as count
            FROM events
            WHERE timestamp >= ?
            GROUP BY event_type
            ORDER BY count DESC
            LIMIT 10
        """, [start_time]).fetchall()
        
        return {
            "total": stats[0],
            "sessions": stats[1],
            "event_types": stats[2],
            "sent": stats[3],
            "received": stats[4],
            "by_type": {row[0]: row[1] for row in type_counts},
            "period_minutes": minutes,
        }
    
    def close(self):
        """Close the database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None
            self._current_file = None


# Global instance
_event_store: Optional[EventStore] = None


def get_event_store() -> EventStore:
    """Get the global event store instance."""
    global _event_store
    if _event_store is None:
        _event_store = EventStore()
    return _event_store


def log_event(
    event_type: str,
    direction: str = "system",
    data: Optional[Dict[str, Any]] = None,
    **kwargs,
) -> int:
    """Convenience function to log an event."""
    return get_event_store().log(event_type, direction, data, **kwargs)
