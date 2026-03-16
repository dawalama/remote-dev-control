"""Port management and coordination for multiple projects."""

from __future__ import annotations

import socket
from typing import Optional

from .db.repositories import get_port_repo, resolve_project_id
from .db.models import PortAssignment


RANGE_START = 3000
RANGE_END = 9000
RESERVED_PORTS = [
    5432,   # PostgreSQL
    5433,   # PostgreSQL alt
    6379,   # Redis
    8420,   # RDC server
    27017,  # MongoDB
]


def is_port_available(port: int) -> bool:
    """Check if a port is available for binding."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1)
            s.bind(("127.0.0.1", port))
            return True
    except OSError:
        return False


def find_available_port(start: int, end: int, reserved: list[int]) -> Optional[int]:
    """Find the next available port in range."""
    for port in range(start, end):
        if port not in reserved and is_port_available(port):
            return port
    return None


class PortManager:
    """Manages port assignments across projects. Backed by port_assignments DB table."""

    def __init__(self):
        self._repo = get_port_repo()

    def _resolve_project_id(self, project: str) -> str:
        """Resolve project name to UUID. Returns empty string if not found."""
        return resolve_project_id(project) or ""

    def get_port(self, project: str, service: str) -> Optional[int]:
        """Get assigned port for a project service."""
        project_id = self._resolve_project_id(project)
        if not project_id:
            return None
        assignment = self._repo.get(project_id, service)
        return assignment.port if assignment else None

    def assign_port(
        self,
        project: str,
        service: str,
        preferred: Optional[int] = None,
        force_new: bool = False,
    ) -> int:
        """Assign a port to a project service."""
        project_id = self._resolve_project_id(project)
        if not project_id:
            raise RuntimeError(f"Project not found in DB: {project}")

        # Check if already assigned (skip if forcing new)
        if not force_new:
            existing = self._repo.get(project_id, service)
            if existing:
                # If preferred port differs from existing, update to preferred
                if preferred and existing.port != preferred:
                    self._repo.upsert(project_id, service, preferred)
                    return preferred
                if is_port_available(existing.port):
                    return existing.port

        # Try preferred port
        if preferred and preferred not in RESERVED_PORTS:
            self._repo.upsert(project_id, service, preferred)
            return preferred

        # Find next available (exclude all used ports)
        used = self._repo.used_ports()
        port = find_available_port(
            RANGE_START,
            RANGE_END,
            RESERVED_PORTS + list(used),
        )

        if not port:
            raise RuntimeError("No available ports in range")

        self._repo.upsert(project_id, service, port)
        return port

    def release_port(self, project: str, service: str):
        """Release a port assignment."""
        project_id = self._resolve_project_id(project)
        if project_id:
            self._repo.delete(project_id, service)

    def set_port(self, project: str, service: str, port: int) -> bool:
        """Explicitly set a port for a service. Returns False if port is in use."""
        if port in RESERVED_PORTS:
            return False

        project_id = self._resolve_project_id(project)
        if not project_id:
            return False

        if not is_port_available(port):
            # Check if it's assigned to us
            existing = self._repo.get(project_id, service)
            if not existing or existing.port != port:
                return False

        self._repo.upsert(project_id, service, port)
        return True

    def list_assignments(self, project: Optional[str] = None) -> list[dict]:
        """List all port assignments, optionally filtered by project."""
        if project:
            project_id = self._resolve_project_id(project)
            assignments = self._repo.list(project_id) if project_id else []
        else:
            assignments = self._repo.list()

        # Return dicts with in_use status for API compatibility
        return [
            {
                "project_id": a.project_id,
                "service": a.service,
                "port": a.port,
                "in_use": not is_port_available(a.port),
            }
            for a in assignments
        ]

    def get_project_ports(self, project: str) -> dict[str, int]:
        """Get all port assignments for a project as a dict."""
        project_id = self._resolve_project_id(project)
        if not project_id:
            return {}
        return {a.service: a.port for a in self._repo.list(project_id)}

    def suggest_ports(self, project: str, services: list[str]) -> dict[str, int]:
        """Suggest ports for a list of services (doesn't assign yet)."""
        suggestions = {}
        used = self._repo.used_ports() | set(RESERVED_PORTS)

        current_port = RANGE_START
        for service in services:
            # Check if already assigned
            existing = self.get_port(project, service)
            if existing:
                suggestions[service] = existing
                continue

            # Find next available
            while current_port in used or not is_port_available(current_port):
                current_port += 1
                if current_port >= RANGE_END:
                    break

            if current_port < RANGE_END:
                suggestions[service] = current_port
                used.add(current_port)
                current_port += 1

        return suggestions


# Global port manager
_port_manager: Optional[PortManager] = None


def get_port_manager() -> PortManager:
    """Get the global port manager."""
    global _port_manager
    if _port_manager is None:
        _port_manager = PortManager()
    return _port_manager
