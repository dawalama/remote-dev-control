"""LLM-powered process discovery for projects."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional
from pydantic import BaseModel

from ..llm import ollama_generate


class DiscoveredProcess(BaseModel):
    """A process discovered from project config."""
    name: str  # e.g., "frontend", "backend", "worker"
    command: str  # e.g., "npm run dev", "npm run worker:dev"
    description: str  # What this process does
    default_port: Optional[int] = None  # If it's a server
    cwd: Optional[str] = None  # Subdirectory if any
    kind: str = "service"  # "service" or "command"


class ProjectProcessConfig(BaseModel):
    """Process configuration for a project."""
    project: str
    processes: list[DiscoveredProcess]


def read_project_files(project_path: str) -> dict[str, str]:
    """Read relevant config files from a project."""
    path = Path(project_path)
    files = {}
    
    # Root level files
    for filename in ["package.json", "pyproject.toml", "Cargo.toml", "go.mod", "requirements.txt", "Gemfile", "Makefile", "docker-compose.yml", "docker-compose.yaml", "rdc.yml", "rdc.yaml", ".rdc.yml", ".rdc.yaml", "adt.yml", "adt.yaml", ".adt.yml", ".adt.yaml"]:
        filepath = path / filename
        if filepath.exists():
            try:
                files[filename] = filepath.read_text()[:5000]
            except Exception:
                pass
    
    # Check subdirectories
    for subdir in ["frontend", "backend", "client", "server", "api", "web", "app", "worker", "workers"]:
        subpath = path / subdir
        if subpath.exists():
            for filename in ["package.json", "pyproject.toml"]:
                filepath = subpath / filename
                if filepath.exists():
                    try:
                        files[f"{subdir}/{filename}"] = filepath.read_text()[:3000]
                    except Exception:
                        pass
    
    return files


def load_adt_config(project_path: str) -> list[DiscoveredProcess] | None:
    """Load processes/actions from rdc.yml/adt.yml config file if present.

    Supports three config formats:
    1. ``actions:`` array — new format with ``kind`` per entry (service/command)
    2. ``processes:`` array — legacy format, all entries become kind=service
    3. ``dev:`` flat section — single dev server shorthand
    """
    path = Path(project_path)

    for config_name in ["rdc.yml", "rdc.yaml", ".rdc.yml", ".rdc.yaml", "adt.yml", "adt.yaml", ".adt.yml", ".adt.yaml"]:
        config_path = path / config_name
        if config_path.exists():
            try:
                import yaml
                config = yaml.safe_load(config_path.read_text())

                if not config:
                    continue

                processes = []

                # Format 1: actions array (new — supports kind)
                if "actions" in config:
                    for entry in config["actions"]:
                        kind = entry.get("kind", "service")
                        if kind not in ("service", "command"):
                            continue  # skip unknown kinds (e.g. future workflow)
                        processes.append(DiscoveredProcess(
                            name=entry.get("name", "unnamed"),
                            command=entry.get("command", ""),
                            description=entry.get("description", ""),
                            default_port=entry.get("port"),
                            cwd=entry.get("cwd"),
                            kind=kind,
                        ))

                # Format 2: explicit processes array (legacy — all service)
                elif "processes" in config:
                    for proc in config["processes"]:
                        processes.append(DiscoveredProcess(
                            name=proc.get("name", "unnamed"),
                            command=proc.get("command", ""),
                            description=proc.get("description", ""),
                            default_port=proc.get("port"),
                            cwd=proc.get("cwd"),
                            kind="service",
                        ))

                # Format 3: flat dev section (e.g. dev: {command: ..., port: ...})
                elif "dev" in config and isinstance(config["dev"], dict):
                    dev = config["dev"]
                    if dev.get("command"):
                        project_name = config.get("name", "dev")
                        processes.append(DiscoveredProcess(
                            name=project_name,
                            command=dev["command"],
                            description=config.get("description", f"Dev server for {project_name}"),
                            default_port=dev.get("port"),
                            cwd=dev.get("cwd"),
                            kind="service",
                        ))

                if processes:
                    print(f"Loaded {len(processes)} actions from {config_name}")
                    return processes
            except Exception as e:
                print(f"Failed to load {config_name}: {e}")

    return None


def analyze_with_llm(project_name: str, files: dict[str, str]) -> list[DiscoveredProcess]:
    """Use LLM to analyze project files and discover processes."""
    
    prompt = f"""Analyze this project's configuration files and identify ONLY the long-running dev processes.

Project: {project_name}

Files:
"""
    for filename, content in files.items():
        prompt += f"\n--- {filename} ---\n{content}\n"
    
    prompt += """

Identify ONLY long-running dev processes (servers, workers, watchers). For each, provide:
1. name: A short unique name (e.g., "frontend", "backend", "worker", "api")
2. command: The npm/python command (e.g., "npm run dev", "npm run worker:dev")
3. description: Brief description
4. default_port: Port number if it's a web server, or null for workers
5. cwd: The subdirectory to run the command from. IMPORTANT: If a package.json or pyproject.toml is in a subdirectory (e.g., frontend/package.json), set cwd to that subdirectory name (e.g., "frontend"). Use null only if running from project root.

EXCLUDE these types of scripts:
- Build scripts (build, compile, bundle)
- Test scripts (test, jest, mocha, cypress)
- Lint/format scripts (lint, eslint, prettier, format)
- One-time scripts (seed, migrate, generate, install, clean)
- Type checking (typecheck, tsc)

INCLUDE only:
- Dev servers (dev, start, serve)
- Workers (worker, worker:dev, queue)
- Watch processes (watch, but only if it's a server)

CRITICAL: Look at the file paths! If you see "frontend/package.json", the cwd should be "frontend".

Return ONLY a valid JSON array with NO duplicates:
[
  {"name": "...", "command": "...", "description": "...", "default_port": ..., "cwd": ...}
]
"""

    try:
        response = ollama_generate(prompt)
        if not response:
            return []

        # Extract JSON from response
        response = response.strip()
        
        # Find JSON array in response
        start = response.find('[')
        end = response.rfind(']') + 1
        
        if start >= 0 and end > start:
            json_str = response[start:end]
            data = json.loads(json_str)
            
            processes = [DiscoveredProcess(**p) for p in data]
            return deduplicate_processes(processes)
    except Exception as e:
        print(f"LLM analysis failed: {e}")
    
    return []


def deduplicate_processes(processes: list[DiscoveredProcess]) -> list[DiscoveredProcess]:
    """Remove duplicate processes - only remove obvious duplicates."""
    seen_names = set()
    seen_commands = set()
    unique = []
    
    # Sort by specificity - prefer processes with specific commands
    def specificity(p):
        score = 0
        if p.default_port:
            score += 5
        if p.cwd:
            score += 3
        # Prefer direct commands over npm wrappers
        if 'uvicorn' in p.command:
            score += 2
        if 'npx' in p.command:
            score += 2
        return score
    
    processes = sorted(processes, key=specificity, reverse=True)
    
    for p in processes:
        name = p.name.lower().strip()
        cmd = p.command.lower().strip()
        
        # Skip if exact same name
        if name in seen_names:
            continue
        
        # Skip if exact same command (normalized)
        cmd_normalized = cmd.replace('npm run ', '').replace('npx ', '')
        if cmd_normalized in seen_commands:
            continue
        
        # Skip obvious non-dev scripts that slipped through
        skip_patterns = ['test', 'lint', 'build', 'seed', 'migrate', 'generate', 'typecheck', 'format', 'clean']
        if any(pattern in name for pattern in skip_patterns):
            continue
        if any(pattern in cmd for pattern in skip_patterns):
            continue
        
        # Skip if name is just a tool name that's redundant with another entry
        # e.g., "uvicorn" when we already have "api" or "backend"
        redundant_tool_names = ['uvicorn', 'vite', 'next', 'node', 'python']
        if name in redundant_tool_names and len(unique) > 0:
            # Check if we already have a backend/api/frontend that covers this
            dominated = False
            for existing in unique:
                existing_name = existing.name.lower()
                # If existing is a semantic name and uses same tool, skip the tool-named one
                if existing_name in ['api', 'backend', 'server'] and 'uvicorn' in name:
                    dominated = True
                if existing_name in ['frontend', 'client', 'web'] and name in ['vite', 'next']:
                    dominated = True
            if dominated:
                continue
        
        seen_names.add(name)
        seen_commands.add(cmd_normalized)
        unique.append(p)
    
    return unique


def analyze_with_heuristics(project_path: str, files: dict[str, str]) -> list[DiscoveredProcess]:
    """Fallback heuristic-based discovery without LLM."""
    processes = []
    path = Path(project_path)
    
    # Check root package.json
    if "package.json" in files:
        try:
            pkg = json.loads(files["package.json"])
            scripts = pkg.get("scripts", {})
            
            for script_name in ["dev", "start", "serve"]:
                if script_name in scripts:
                    processes.append(DiscoveredProcess(
                        name="app",
                        command=f"npm run {script_name}",
                        description=f"Main application ({script_name})",
                        default_port=3000,
                    ))
                    break
            
            # Look for worker scripts
            for script_name, script_cmd in scripts.items():
                if "worker" in script_name.lower():
                    processes.append(DiscoveredProcess(
                        name=script_name.replace(":", "-"),
                        command=f"npm run {script_name}",
                        description=f"Worker process",
                    ))
        except json.JSONDecodeError:
            pass
    
    # Check subdirectories
    for subdir in ["frontend", "backend", "client", "server", "api", "web", "app", "worker", "workers"]:
        pkg_key = f"{subdir}/package.json"
        if pkg_key in files:
            try:
                pkg = json.loads(files[pkg_key])
                scripts = pkg.get("scripts", {})
                
                if "dev" in scripts or "start" in scripts:
                    script = "dev" if "dev" in scripts else "start"
                    port = 3000 if subdir in ["frontend", "client"] else 8000
                    processes.append(DiscoveredProcess(
                        name=subdir,
                        command=f"npm run {script}",
                        description=f"{subdir.title()} server",
                        default_port=port,
                        cwd=subdir,
                    ))
                
                # Check for worker scripts in subdirs
                for script_name in scripts:
                    if "worker" in script_name.lower():
                        processes.append(DiscoveredProcess(
                            name=f"{subdir}-{script_name.replace(':', '-')}",
                            command=f"npm run {script_name}",
                            description=f"Worker in {subdir}",
                            cwd=subdir,
                        ))
            except json.JSONDecodeError:
                pass
        
        # Check for Python
        pyproject_key = f"{subdir}/pyproject.toml"
        if pyproject_key in files or (path / subdir / "main.py").exists():
            port = 8000 if subdir in ["backend", "server", "api"] else 8080
            processes.append(DiscoveredProcess(
                name=subdir,
                command="uvicorn main:app --reload",
                description=f"{subdir.title()} API server",
                default_port=port,
                cwd=subdir,
            ))
    
    return processes


def detect_stack(project_path: str) -> dict:
    """Detect project stack, test framework, and directory layout using heuristics.

    Returns dict with keys: stack, test_command, source_dir, test_dir.
    """
    files = read_project_files(project_path)
    path = Path(project_path)

    stack: list[str] = []
    test_command: str | None = None
    source_dir: str | None = None
    test_dir: str | None = None

    # --- Parse package.json ---
    if "package.json" in files:
        try:
            pkg = json.loads(files["package.json"])
            all_deps = {
                **pkg.get("dependencies", {}),
                **pkg.get("devDependencies", {}),
            }
            dep_map = {
                "react": "react",
                "vue": "vue",
                "next": "next",
                "nuxt": "nuxt",
                "vite": "vite",
                "tailwindcss": "tailwind",
                "express": "express",
                "fastify": "fastify",
                "svelte": "svelte",
                "@angular/core": "angular",
                "typescript": "typescript",
            }
            for dep_key, label in dep_map.items():
                if dep_key in all_deps:
                    stack.append(label)

            # Test framework from deps
            test_dep_map = {
                "jest": "jest",
                "vitest": "vitest",
                "mocha": "mocha",
                "cypress": "cypress",
                "playwright": "playwright",
            }
            for dep_key, label in test_dep_map.items():
                if dep_key in all_deps and not test_command:
                    test_command = f"npx {label}"

            # Test command from scripts
            scripts = pkg.get("scripts", {})
            if "test" in scripts and not test_command:
                test_command = "npm test"
        except json.JSONDecodeError:
            pass

    # --- Parse pyproject.toml ---
    if "pyproject.toml" in files:
        content = files["pyproject.toml"]
        stack.append("python")
        lower = content.lower()
        py_dep_map = {
            "fastapi": "fastapi",
            "django": "django",
            "flask": "flask",
            "sqlalchemy": "sqlalchemy",
            "celery": "celery",
            "pydantic": "pydantic",
        }
        for dep_key, label in py_dep_map.items():
            if dep_key in lower:
                stack.append(label)
        if "pytest" in lower:
            if not test_command:
                test_command = "pytest"

    # --- requirements.txt fallback (Python without pyproject.toml) ---
    if "requirements.txt" in files and "python" not in stack:
        stack.append("python")
        lower = files["requirements.txt"].lower()
        for dep_key, label in {"fastapi": "fastapi", "django": "django", "flask": "flask"}.items():
            if dep_key in lower:
                stack.append(label)
        if "pytest" in lower and not test_command:
            test_command = "pytest"

    # --- Cargo.toml (Rust) ---
    if "Cargo.toml" in files:
        stack.append("rust")
        if not test_command:
            test_command = "cargo test"

    # --- go.mod (Go) ---
    if "go.mod" in files:
        stack.append("go")
        if not test_command:
            test_command = "go test ./..."

    # --- Gemfile (Ruby) ---
    if "Gemfile" in files:
        stack.append("ruby")
        lower = files["Gemfile"].lower()
        if "rails" in lower:
            stack.append("rails")
        if "rspec" in lower and not test_command:
            test_command = "bundle exec rspec"

    # --- Docker ---
    if (path / "Dockerfile").exists() or "docker-compose.yml" in files or "docker-compose.yaml" in files:
        stack.append("docker")

    # --- Detect source and test dirs ---
    for candidate in ["src", "lib", "app"]:
        if (path / candidate).is_dir():
            source_dir = candidate
            break

    for candidate in ["tests", "test", "__tests__"]:
        if (path / candidate).is_dir():
            test_dir = candidate
            break

    # Deduplicate stack preserving order
    seen: set[str] = set()
    unique_stack: list[str] = []
    for s in stack:
        if s not in seen:
            seen.add(s)
            unique_stack.append(s)

    return {
        "stack": unique_stack,
        "test_command": test_command,
        "source_dir": source_dir,
        "test_dir": test_dir,
    }


def discover_processes(project_name: str, project_path: str, use_llm: bool = True) -> list[DiscoveredProcess]:
    """Discover all runnable processes for a project.
    
    Priority:
    1. rdc.yml config file (explicit, user-defined)
    2. LLM analysis (smart discovery)
    3. Heuristics (fallback, merged with LLM results)
    
    Args:
        project_name: Name of the project
        project_path: Path to project root
        use_llm: Whether to use LLM for smart discovery (falls back to heuristics if fails)
    
    Returns:
        List of discovered processes
    """
    # First priority: check for explicit rdc.yml config
    rdc_processes = load_adt_config(project_path)
    if rdc_processes:
        return rdc_processes
    
    # Second priority: LLM + heuristics discovery
    files = read_project_files(project_path)
    
    if not files:
        return []
    
    processes = []
    
    if use_llm:
        processes = analyze_with_llm(project_name, files)
    
    # Always run heuristics to catch anything LLM might have missed
    heuristic_processes = analyze_with_heuristics(project_path, files)
    
    # Merge: add heuristic results that aren't already in LLM results
    llm_names = {p.name.lower() for p in processes}
    llm_cwds = {p.cwd for p in processes if p.cwd}
    
    for hp in heuristic_processes:
        # Add if name not already found and cwd not already covered
        if hp.name.lower() not in llm_names and (not hp.cwd or hp.cwd not in llm_cwds):
            processes.append(hp)
    
    return deduplicate_processes(processes)
