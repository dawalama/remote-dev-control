"""Shared project detection helpers for imports and process discovery."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class NodeFramework:
    package_names: tuple[str, ...]
    script_tokens: tuple[str, ...]
    frontend_stack: str
    default_port: int
    direct_command: str | None = None
    config_files: tuple[str, ...] = ()
    deployment: str = "none"


NODE_FRAMEWORKS: tuple[NodeFramework, ...] = (
    NodeFramework(
        package_names=("next",),
        script_tokens=("next",),
        frontend_stack="nextjs",
        default_port=3000,
        direct_command="npx next dev",
        config_files=("next.config.js", "next.config.mjs", "next.config.ts"),
        deployment="vercel",
    ),
    NodeFramework(
        package_names=("vite",),
        script_tokens=("vite",),
        frontend_stack="react",
        default_port=5173,
        direct_command="npx vite --host",
        config_files=("vite.config.js", "vite.config.mjs", "vite.config.ts"),
    ),
    NodeFramework(
        package_names=("vue", "@vue/cli-service"),
        script_tokens=("vue-cli-service",),
        frontend_stack="vue",
        default_port=8080,
        direct_command="npx vue-cli-service serve",
        config_files=("vue.config.js",),
    ),
    NodeFramework(
        package_names=("react-scripts",),
        script_tokens=("react-scripts",),
        frontend_stack="react",
        default_port=3000,
        direct_command="npx react-scripts start",
    ),
)


def load_package_json(path: Path) -> dict[str, Any] | None:
    package_path = path / "package.json"
    if not package_path.exists():
        return None
    try:
        return json.loads(package_path.read_text(errors="ignore"))
    except (json.JSONDecodeError, OSError):
        return None


def package_dependencies(pkg: dict[str, Any]) -> dict[str, str]:
    deps = {
        **pkg.get("dependencies", {}),
        **pkg.get("devDependencies", {}),
    }
    return {str(key).lower(): str(value) for key, value in deps.items()}


def detect_node_framework(
    *,
    project_path: Path | None,
    pkg: dict[str, Any],
    script: str = "",
) -> NodeFramework | None:
    deps = package_dependencies(pkg)
    script_lower = script.lower()

    for framework in NODE_FRAMEWORKS:
        if any(token in script_lower for token in framework.script_tokens):
            return framework

    for framework in NODE_FRAMEWORKS:
        if any(package in deps for package in framework.package_names):
            return framework

    if project_path:
        for framework in NODE_FRAMEWORKS:
            if any((project_path / filename).exists() for filename in framework.config_files):
                return framework

    return None


def detect_script_port(script: str, default: int) -> int:
    port_match = re.search(r"(?:--port|-p)\s+(\d+)", script)
    if port_match:
        return int(port_match.group(1))
    if "5173" in script:
        return 5173
    return default


def direct_node_dev_command(pkg: dict[str, Any], script: str, port: int) -> tuple[str, int] | None:
    framework = detect_node_framework(project_path=None, pkg=pkg, script=script)
    if not framework or not framework.direct_command:
        return None

    resolved_port = detect_script_port(script, framework.default_port)
    if framework.direct_command == "npx vite --host":
        return (framework.direct_command, port if port != 3000 else resolved_port)

    return (framework.direct_command, resolved_port)


def infer_node_profile(path: Path) -> dict[str, Any]:
    pkg = load_package_json(path)
    scripts = pkg.get("scripts", {}) if pkg else {}
    framework = detect_node_framework(
        project_path=path,
        pkg=pkg or {},
        script=str(scripts.get("dev", "")),
    )
    if not pkg and not framework:
        return {}

    deps = package_dependencies(pkg or {})

    profile: dict[str, Any] = {
        "name": pkg.get("name") if pkg else None,
        "description": pkg.get("description") if pkg else None,
        "backend": "express" if "express" in deps else "none",
        "frontend": framework.frontend_stack if framework else "none",
        "database": "postgres" if "prisma" in deps or "@prisma/client" in deps else "none",
        "deployment": framework.deployment if framework else "none",
        "features": [],
    }

    if "@builder.io/react" in deps or "@builder.io/sdk" in deps:
        profile["features"].append("admin")

    return profile
