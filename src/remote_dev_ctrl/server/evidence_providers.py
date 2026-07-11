"""Optional evidence capture backends for supervised work Sessions."""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


class EvidenceProvider(Protocol):
    id: str
    label: str

    def inspect(self, project_path: Path | None) -> dict[str, Any]: ...


@dataclass
class NativeEvidenceProvider:
    id: str = "native"
    label: str = "RDC native evidence"

    def inspect(self, project_path: Path | None) -> dict[str, Any]:
        return {
            "id": self.id, "label": self.label, "installed": True, "ready": True,
            "status": "ready", "detail": "Git, verification, browser, approval, and lifecycle evidence.",
            "setup_command": None,
            "capabilities": ["git", "verification", "browser", "approvals", "lineage"],
        }


@dataclass
class EntireEvidenceProvider:
    id: str = "entire"
    label: str = "Entire checkpoints"

    def inspect(self, project_path: Path | None) -> dict[str, Any]:
        executable = shutil.which("entire")
        base = {
            "id": self.id, "label": self.label, "installed": bool(executable), "ready": False,
            "status": "not_installed" if not executable else "needs_setup",
            "detail": "Entire captures agent transcripts, tool calls, file attribution, checkpoints, and resume context.",
            "setup_command": "entire enable", "capabilities": ["transcript", "tool_calls", "checkpoints", "rewind", "resume"],
        }
        if not executable:
            base["detail"] = "Entire CLI is not installed. Native RDC evidence remains available."
            base["setup_command"] = "brew install entireio/tap/entire"
            return base
        if project_path is None:
            base["detail"] = "Select a project to check whether Entire is enabled."
            return base
        try:
            result = subprocess.run(
                [executable, "status", "--json"], cwd=project_path, text=True,
                capture_output=True, timeout=15, check=False,
            )
            payload = json.loads(result.stdout) if result.stdout.strip().startswith(("{", "[")) else {}
            combined = f"{result.stdout}\n{result.stderr}".lower()
            enabled = result.returncode == 0 and "not set up" not in combined and "disabled" not in combined
            if isinstance(payload, dict):
                enabled = bool(payload.get("enabled", enabled))
            if enabled:
                base.update({"ready": True, "status": "ready", "detail": "Enabled for this project; agent hooks will capture Entire session context."})
            else:
                base["detail"] = "Installed, but not enabled for this project. Enable it from the project directory."
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
            base["detail"] = f"Entire status could not be read: {exc}"
        return base


PROVIDERS: tuple[EvidenceProvider, ...] = (NativeEvidenceProvider(), EntireEvidenceProvider())


def list_evidence_provider_readiness(project_path: Path | None = None) -> list[dict[str, Any]]:
    return [provider.inspect(project_path) for provider in PROVIDERS]


def active_evidence_provider_refs(project_path: Path | None = None) -> list[dict[str, str]]:
    return [
        {"id": item["id"], "label": item["label"]}
        for item in list_evidence_provider_readiness(project_path)
        if item["ready"]
    ]


def enable_entire(project_path: Path, agent: str) -> dict[str, Any]:
    """Enable Entire explicitly and locally-first for one existing Git project."""
    executable = shutil.which("entire")
    if not executable:
        raise RuntimeError("Entire CLI is not installed")
    agent_names = {"claude": "claude-code", "gwd": "gemini"}
    entire_agent = agent_names.get(agent, agent)
    result = subprocess.run(
        [
            executable, "enable", "--agent", entire_agent, "--project",
            "--no-init-repo", "--skip-push-sessions", "--telemetry=false",
        ],
        cwd=project_path, text=True, capture_output=True, timeout=120, check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "Entire setup failed").strip()
        raise RuntimeError(detail[-2000:])
    return {"enabled": True, "agent": entire_agent, "output": result.stdout.strip()[-4000:]}
