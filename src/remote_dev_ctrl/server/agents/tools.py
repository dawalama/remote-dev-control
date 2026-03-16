"""Agent tool definitions and executor.

Tools are defined in OpenAI function calling format and executed
against the project filesystem with sandboxing to project_path.
"""

import asyncio
import json
import os
from pathlib import Path
from typing import Any

# Tools that require user approval before execution
REQUIRES_APPROVAL = {
    "write_file",
    "create_file",
    "edit_file",
    "delete_file",
    "run_command",
}

# Tools that auto-execute without approval
AUTO_APPROVE = {
    "read_file",
    "list_directory",
    "search_files",
    "git_status",
    "git_diff",
}

AGENT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the contents of a file. Returns the full file content with line numbers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path relative to project root",
                    },
                    "offset": {
                        "type": "integer",
                        "description": "Line number to start reading from (1-based). Optional.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of lines to read. Optional.",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write content to a file, creating it if it doesn't exist or overwriting if it does.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path relative to project root",
                    },
                    "content": {
                        "type": "string",
                        "description": "The full content to write to the file",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Replace a specific string in a file. The old_string must be unique in the file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path relative to project root",
                    },
                    "old_string": {
                        "type": "string",
                        "description": "The exact string to find and replace (must be unique in the file)",
                    },
                    "new_string": {
                        "type": "string",
                        "description": "The replacement string",
                    },
                },
                "required": ["path", "old_string", "new_string"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_file",
            "description": "Create a new file with the given content. Fails if the file already exists.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path relative to project root",
                    },
                    "content": {
                        "type": "string",
                        "description": "The content for the new file",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_file",
            "description": "Delete a file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path relative to project root",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": "List files and directories in a path. Returns names with '/' suffix for directories.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Directory path relative to project root. Use '.' for project root.",
                    },
                    "recursive": {
                        "type": "boolean",
                        "description": "If true, list recursively (max 200 entries). Default false.",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": "Search for a pattern in files using grep. Returns matching lines with file paths and line numbers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Search pattern (regex supported)",
                    },
                    "path": {
                        "type": "string",
                        "description": "Directory to search in, relative to project root. Default '.'",
                    },
                    "glob": {
                        "type": "string",
                        "description": "File glob pattern to filter (e.g. '*.py', '*.ts'). Optional.",
                    },
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Run a shell command in the project directory. Use for builds, tests, git operations, etc.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The shell command to execute",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Timeout in seconds. Default 120.",
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_status",
            "description": "Get the git status of the project (modified, staged, untracked files).",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_diff",
            "description": "Get the git diff showing current changes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "staged": {
                        "type": "boolean",
                        "description": "If true, show staged changes only. Default false (shows unstaged).",
                    },
                    "file": {
                        "type": "string",
                        "description": "Optional specific file to diff.",
                    },
                },
            },
        },
    },
]


def _resolve_path(project_path: str, relative_path: str) -> Path:
    """Resolve a relative path within the project, preventing directory traversal."""
    base = Path(project_path).resolve()
    target = (base / relative_path).resolve()
    if not str(target).startswith(str(base)):
        raise ValueError(f"Path '{relative_path}' escapes project directory")
    return target


async def execute_tool(
    name: str,
    args: dict[str, Any],
    project_path: str,
) -> tuple[str, bool]:
    """Execute a tool and return (result_string, is_error).

    All file operations are sandboxed to project_path.
    """
    try:
        if name == "read_file":
            return await _read_file(project_path, **args)
        elif name == "write_file":
            return await _write_file(project_path, **args)
        elif name == "edit_file":
            return await _edit_file(project_path, **args)
        elif name == "create_file":
            return await _create_file(project_path, **args)
        elif name == "delete_file":
            return await _delete_file(project_path, **args)
        elif name == "list_directory":
            return await _list_directory(project_path, **args)
        elif name == "search_files":
            return await _search_files(project_path, **args)
        elif name == "run_command":
            return await _run_command(project_path, **args)
        elif name == "git_status":
            return await _git_status(project_path)
        elif name == "git_diff":
            return await _git_diff(project_path, **args)
        else:
            return f"Unknown tool: {name}", True
    except Exception as e:
        return f"Error: {e}", True


async def _read_file(
    project_path: str, path: str, offset: int | None = None, limit: int | None = None
) -> tuple[str, bool]:
    target = _resolve_path(project_path, path)
    if not target.exists():
        return f"File not found: {path}", True
    if not target.is_file():
        return f"Not a file: {path}", True

    content = target.read_text(errors="replace")
    lines = content.split("\n")

    start = (offset - 1) if offset and offset > 0 else 0
    end = (start + limit) if limit else len(lines)
    selected = lines[start:end]

    numbered = []
    for i, line in enumerate(selected, start=start + 1):
        numbered.append(f"{i:>5}\t{line}")

    return "\n".join(numbered), False


async def _write_file(project_path: str, path: str, content: str) -> tuple[str, bool]:
    target = _resolve_path(project_path, path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    return f"Written {len(content)} bytes to {path}", False


async def _edit_file(
    project_path: str, path: str, old_string: str, new_string: str
) -> tuple[str, bool]:
    target = _resolve_path(project_path, path)
    if not target.exists():
        return f"File not found: {path}", True

    content = target.read_text()
    count = content.count(old_string)
    if count == 0:
        return f"String not found in {path}", True
    if count > 1:
        return f"String appears {count} times in {path} — must be unique. Provide more context.", True

    new_content = content.replace(old_string, new_string, 1)
    target.write_text(new_content)
    return f"Edited {path}", False


async def _create_file(project_path: str, path: str, content: str) -> tuple[str, bool]:
    target = _resolve_path(project_path, path)
    if target.exists():
        return f"File already exists: {path}", True
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    return f"Created {path} ({len(content)} bytes)", False


async def _delete_file(project_path: str, path: str) -> tuple[str, bool]:
    target = _resolve_path(project_path, path)
    if not target.exists():
        return f"File not found: {path}", True
    if not target.is_file():
        return f"Not a file: {path}", True
    target.unlink()
    return f"Deleted {path}", False


async def _list_directory(
    project_path: str, path: str = ".", recursive: bool = False
) -> tuple[str, bool]:
    target = _resolve_path(project_path, path)
    if not target.exists():
        return f"Directory not found: {path}", True
    if not target.is_dir():
        return f"Not a directory: {path}", True

    entries = []
    if recursive:
        for p in sorted(target.rglob("*")):
            if len(entries) >= 200:
                entries.append("... (truncated at 200 entries)")
                break
            rel = p.relative_to(target)
            # Skip hidden dirs like .git
            if any(part.startswith(".") for part in rel.parts):
                continue
            suffix = "/" if p.is_dir() else ""
            entries.append(f"{rel}{suffix}")
    else:
        for p in sorted(target.iterdir()):
            if p.name.startswith("."):
                continue
            suffix = "/" if p.is_dir() else ""
            entries.append(f"{p.name}{suffix}")

    return "\n".join(entries) if entries else "(empty directory)", False


async def _search_files(
    project_path: str, pattern: str, path: str = ".", glob: str | None = None
) -> tuple[str, bool]:
    target = _resolve_path(project_path, path)
    cmd = ["grep", "-rn", "--include", glob or "*", "-E", pattern, str(target)]
    if not glob:
        cmd = ["grep", "-rn", "-E", pattern, str(target)]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=project_path,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        output = stdout.decode(errors="replace")

        # Make paths relative to project
        base = str(Path(project_path).resolve())
        output = output.replace(base + "/", "")

        lines = output.strip().split("\n")
        if len(lines) > 100:
            lines = lines[:100]
            lines.append(f"... ({len(output.strip().split(chr(10)))} total matches, showing first 100)")

        return "\n".join(lines) if lines[0] else "No matches found", False
    except asyncio.TimeoutError:
        return "Search timed out after 30 seconds", True


async def _run_command(
    project_path: str, command: str, timeout: int = 120
) -> tuple[str, bool]:
    timeout = min(timeout, 300)  # Cap at 5 minutes

    proc = await asyncio.create_subprocess_shell(
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=project_path,
        env={**os.environ, "TERM": "dumb", "NO_COLOR": "1"},
    )

    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        output = stdout.decode(errors="replace")
        # Truncate very long output
        if len(output) > 30000:
            output = output[:15000] + "\n\n... (output truncated) ...\n\n" + output[-15000:]
        is_error = proc.returncode != 0
        prefix = f"Exit code: {proc.returncode}\n" if is_error else ""
        return prefix + output, is_error
    except asyncio.TimeoutError:
        proc.kill()
        return f"Command timed out after {timeout} seconds", True


async def _git_status(project_path: str) -> tuple[str, bool]:
    return await _run_command(project_path, "git status --short", timeout=10)


async def _git_diff(
    project_path: str, staged: bool = False, file: str | None = None
) -> tuple[str, bool]:
    cmd = "git diff"
    if staged:
        cmd += " --staged"
    if file:
        # Validate file path
        _resolve_path(project_path, file)
        cmd += f" -- {file}"
    return await _run_command(project_path, cmd, timeout=10)
