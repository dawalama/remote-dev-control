"""Smart diff-aware commit planning for completed Goal sessions.

Replaces naive "echo the goal as a single commit message" behavior with:
1. Actual working-tree diff inspection (tracked modifications, additions, deletions, renames, untracked files).
2. Succinct Conventional Commit messages explaining *what changed* rather than repeating the Goal prompt.
3. Support for splitting changes into multiple logical, atomic commits when appropriate.
4. Graceful 3-tier synthesis: configured LLM API -> fast read-only local CLI -> deterministic diff heuristic.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_MAX_DIFF_CHARS = 14000
_MAX_UNTRACKED_FILE_CHARS = 1500
_MAX_UNTRACKED_FILES_PREVIEW = 6
_SUBJECT_LIMIT = 72
_GENERIC_DIRS = frozenset({
    "src", "lib", "app", "pkg", "internal", "packages", "frontend", "backend",
    "server", "client", "web", "components", "features", "modules", "utils",
    "helpers", "common", "shared", "tests", "test", "docs", "remote_dev_ctrl",
})


@dataclass
class ChangedFile:
    path: str
    status: str  # "A" | "M" | "D" | "R"
    old_path: str | None = None


@dataclass
class WorkingTreeSnapshot:
    files: list[ChangedFile] = field(default_factory=list)
    diff_stat: str = ""
    diff_text: str = ""
    untracked_previews: dict[str, str] = field(default_factory=dict)
    fingerprint: str = ""

    @property
    def paths(self) -> list[str]:
        return [f.path for f in self.files]


def _is_rdc_internal(path: str) -> bool:
    clean = path.strip().lstrip("./")
    return clean == ".rdc" or clean.startswith(".rdc/")


def sanitize_commit_message(message: str) -> str:
    """Strip Co-Authored-By trailers and normalize whitespace."""
    lines = [
        line.rstrip()
        for line in (message or "").splitlines()
        if not line.strip().lower().startswith("co-authored-by:")
    ]
    cleaned = "\n".join(lines).strip()
    if not cleaned:
        return ""
    parts = cleaned.splitlines()
    subject = re.sub(r"\s+", " ", parts[0]).strip().rstrip(".")
    if len(subject) > _SUBJECT_LIMIT:
        subject = subject[: _SUBJECT_LIMIT - 1].rstrip() + "…"
    rest = "\n".join(parts[1:]).strip()
    return f"{subject}\n\n{rest}" if rest else subject


def collect_working_tree_changes(repo_path: Path) -> WorkingTreeSnapshot:
    """Inspect all uncommitted changes (staged, unstaged, untracked) excluding `.rdc`."""
    status_res = subprocess.run(
        ["git", "status", "--porcelain=v1", "-uall", "-z"],
        cwd=repo_path,
        capture_output=True,
        timeout=15,
        check=False,
    )
    files: list[ChangedFile] = []
    if status_res.returncode == 0 and status_res.stdout:
        tokens = status_res.stdout.decode("utf-8", errors="replace").split("\0")
        idx = 0
        while idx < len(tokens):
            entry = tokens[idx]
            idx += 1
            if not entry or len(entry) < 4:
                continue
            xy = entry[:2]
            path = entry[3:]
            old_path: str | None = None
            if "R" in xy or "C" in xy:
                if idx < len(tokens):
                    old_path = tokens[idx]
                    idx += 1
            if _is_rdc_internal(path):
                continue
            if xy == "??" or "A" in xy:
                st = "A"
            elif "R" in xy or "C" in xy:
                st = "R"
            elif "D" in xy:
                st = "D"
            else:
                st = "M"
            files.append(ChangedFile(path=path, status=st, old_path=old_path))

    if not files:
        return WorkingTreeSnapshot()

    def _git_text(*args: str) -> str:
        res = subprocess.run(
            ["git", *args],
            cwd=repo_path,
            text=True,
            capture_output=True,
            timeout=15,
            check=False,
        )
        return res.stdout if res.returncode == 0 else ""

    has_head = subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD"],
        cwd=repo_path,
        capture_output=True,
        timeout=10,
        check=False,
    ).returncode == 0

    path_args = [f.path for f in files]
    if has_head:
        diff_stat = _git_text("diff", "--stat", "HEAD", "--", *path_args).strip()
        raw_diff = _git_text("diff", "--unified=2", "HEAD", "--", *path_args)
    else:
        diff_stat = _git_text("diff", "--cached", "--stat", "--", *path_args).strip()
        raw_diff = _git_text("diff", "--cached", "--unified=2", "--", *path_args)

    diff_text = raw_diff[:_MAX_DIFF_CHARS]
    if len(raw_diff) > _MAX_DIFF_CHARS:
        diff_text += "\n... [diff truncated]"

    # Preview new untracked/added files not already present in diff_text
    untracked_previews: dict[str, str] = {}
    for cf in files:
        if len(untracked_previews) >= _MAX_UNTRACKED_FILES_PREVIEW:
            break
        if cf.status != "A" or f"b/{cf.path}" in diff_text:
            continue
        full = (repo_path / cf.path)
        if not full.is_file():
            continue
        try:
            raw_bytes = full.read_bytes()[:_MAX_UNTRACKED_FILE_CHARS]
            if b"\x00" in raw_bytes:
                continue
            untracked_previews[cf.path] = raw_bytes.decode("utf-8", errors="replace")
        except OSError:
            continue

    fp_input = "\n".join(f"{f.status}:{f.path}:{f.old_path or ''}" for f in files) + "\n" + diff_stat
    fingerprint = hashlib.sha256(fp_input.encode("utf-8", errors="replace")).hexdigest()[:16]

    return WorkingTreeSnapshot(
        files=files,
        diff_stat=diff_stat,
        diff_text=diff_text,
        untracked_previews=untracked_previews,
        fingerprint=fingerprint,
    )


def _classify_area(path: str) -> str:
    lower = path.lower()
    parts = [p.lower() for p in Path(path).parts]
    name = Path(path).name.lower()
    if (
        "tests" in parts
        or "test" in parts
        or "__tests__" in parts
        or name.startswith("test_")
        or name.endswith("_test.py")
        or ".test." in name
        or ".spec." in name
    ):
        return "tests"
    if (
        "docs" in parts
        or lower.endswith(".md")
        or name.startswith(("readme", "changelog", "license"))
    ):
        return "docs"
    if (
        name in {
            "pyproject.toml", "package.json", "package-lock.json", "pnpm-lock.yaml",
            "uv.lock", "cargo.toml", "go.mod", "dockerfile", "makefile",
        }
        or ".github" in parts
        or lower.endswith((".toml", ".yaml", ".yml", ".ini", ".cfg"))
    ):
        return "config"
    if (
        "frontend" in parts
        or "web" in parts
        or "ui" in parts
        or "client" in parts
        or lower.endswith((".tsx", ".jsx", ".css", ".scss", ".html", ".svelte", ".vue"))
    ):
        return "ui"
    if (
        "server" in parts
        or "backend" in parts
        or "api" in parts
        or "src" in parts
        or lower.endswith((".py", ".go", ".rs", ".java", ".rb", ".ts", ".js"))
    ):
        return "backend"
    return "other"


def _infer_scope(paths: list[str], area: str) -> str | None:
    """Pick a crisp scope name from file paths (e.g. 'sessions', 'ui', 'auth', 'api')."""
    if not paths:
        return None
    if area in {"docs", "tests"}:
        return None

    candidates: list[str] = []
    for p in paths:
        parts = list(Path(p).parts)
        for seg in reversed(parts[:-1]):
            clean = seg.lower().strip(".")
            if clean and clean not in _GENERIC_DIRS:
                candidates.append(clean)
                break
        else:
            stem = Path(p).stem.lower()
            if stem and stem not in {"index", "main", "app", "__init__"}:
                candidates.append(stem.replace("_", "-"))

    if not candidates:
        return area if area in {"ui", "backend", "config"} else None

    counts: dict[str, int] = {}
    for c in candidates:
        counts[c] = counts.get(c, 0) + 1
    best, best_count = max(counts.items(), key=lambda kv: (kv[1], -len(kv[0])))
    if best_count >= max(1, len(paths) // 2) and len(best) <= 18:
        return best
    return area if area in {"ui", "backend", "config"} else None


def _extract_added_symbols(diff_text: str, previews: dict[str, str], target_paths: set[str]) -> list[str]:
    """Extract notable added routes, functions, classes, or components from the diff for target files."""
    symbols: list[str] = []
    seen: set[str] = set()

    def _record(sym: str) -> None:
        sym = sym.strip()
        if sym and sym not in seen and not sym.startswith("_"):
            seen.add(sym)
            symbols.append(sym)

    def _scan_lines(lines: list[str]) -> None:
        for line in lines:
            m_route = re.search(r'@app\.(get|post|put|patch|delete)\(\s*["\']([^"\']+)["\']', line)
            if m_route:
                _record(f"{m_route.group(1).upper()} {m_route.group(2)}")
                continue
            m_py = re.match(r"\s*(?:async\s+)?(?:def|class)\s+([A-Za-z_][A-Za-z0-9_]*)", line)
            if m_py:
                _record(m_py.group(1))
                continue
            m_ts = re.match(
                r"\s*export\s+(?:default\s+)?(?:async\s+)?(?:function|class|const|interface)\s+([A-Za-z_][A-Za-z0-9_]*)",
                line,
            )
            if m_ts:
                _record(m_ts.group(1))

    # Scan per-file hunks in diff_text
    current_file: str | None = None
    added_lines: list[str] = []
    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            if current_file in target_paths and added_lines:
                _scan_lines(added_lines)
            added_lines = []
            marker = line.rfind(" b/")
            current_file = line[marker + 3:].strip().strip('"') if marker >= 0 else None
        elif line.startswith("+") and not line.startswith("+++"):
            added_lines.append(line[1:])
    if current_file in target_paths and added_lines:
        _scan_lines(added_lines)

    for p, content in previews.items():
        if p in target_paths:
            _scan_lines(content.splitlines()[:80])

    return symbols[:4]


def _extract_summary_hint(session: Any) -> str | None:
    """Pull one concise factual change phrase from session.output_summary if available."""
    summary = (getattr(session, "output_summary", None) or "").strip()
    if not summary:
        return None
    for raw_line in summary.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        # Skip markdown headers, horizontal rules, commit notes, and boilerplate lead-ins
        if line.startswith(("#", "---", "```", "✓ Host commit")):
            continue
        line = re.sub(r"^[-*•\d.)]+\s+", "", line).strip()
        line = re.sub(r"\*\*([^*]+)\*\*", r"\1", line)
        line = re.sub(r"`([^`]+)`", r"\1", line)
        line = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", line)
        lower = line.lower()
        if len(line) < 12:
            continue
        if lower.startswith((
            "i have ", "i've ", "done", "completed", "verified", "all tests",
            "summary", "changes:", "what changed", "verification", "note:",
        )):
            continue
        # Take first clause up to ~60 chars
        clause = re.split(r"[.;—]\s+", line)[0].strip().rstrip(".")
        if 12 <= len(clause) <= 64:
            return clause[0].lower() + clause[1:] if clause[0].isupper() and not clause[:2].isupper() else clause
    return None


def _infer_commit_type(session: Any, area: str, files: list[ChangedFile]) -> str:
    if area == "docs":
        return "docs"
    if area == "tests":
        return "test"
    if area == "config":
        return "chore"

    meta = getattr(session, "metadata", None) or {}
    recipe_id = str(meta.get("recipe_id") or "").strip().lower()
    if recipe_id == "bugfix":
        return "fix"
    if recipe_id == "refactor":
        return "refactor"
    if recipe_id == "feature":
        return "feat"

    statuses = {f.status for f in files}
    if statuses == {"D"}:
        return "chore"
    summary = (getattr(session, "output_summary", None) or "").lower()
    if any(w in summary for w in ("root cause", "fixed ", "regression", "bug fix", "resolved crash")):
        return "fix"
    if any(w in summary for w in ("refactored", "extracted", "simplified", "deduplicated")):
        return "refactor"
    if "A" in statuses:
        return "feat"
    return "feat"


def _describe_file_group(
    files: list[ChangedFile],
    area: str,
    symbols: list[str],
    summary_hint: str | None,
    use_summary_hint: bool,
) -> str:
    if use_summary_hint and summary_hint:
        return summary_hint

    names = [Path(f.path).name for f in files]
    stems = [Path(f.path).stem.replace("_", " ").replace("-", " ") for f in files]
    all_added = all(f.status == "A" for f in files)
    all_deleted = all(f.status == "D" for f in files)

    if area == "docs":
        if len(names) == 1:
            return f"update {names[0]}"
        return f"update documentation ({', '.join(names[:2])}{' and more' if len(names) > 2 else ''})"
    if area == "tests":
        if len(stems) == 1:
            target = stems[0].removeprefix("test ").strip() or names[0]
            return f"{'add' if all_added else 'update'} tests for {target}"
        return f"{'add' if all_added else 'update'} test coverage across {len(files)} test files"
    if area == "config":
        return f"update {', '.join(names[:2])}" if len(names) <= 2 else f"update project configuration ({len(files)} files)"

    verb = "remove" if all_deleted else ("add" if all_added else "update")
    if symbols:
        top = ", ".join(symbols[:2])
        if area == "ui":
            return f"{verb} UI components ({top})"
        if area == "backend":
            return f"{verb} {top}"
        return f"{verb} {top}"

    if len(names) == 1:
        return f"{verb} {names[0]}"
    if len(names) == 2:
        return f"{verb} {names[0]} and {names[1]}"
    area_label = {"ui": "frontend", "backend": "server"}.get(area, "project")
    return f"{verb} {area_label} modules ({names[0]} and {len(names) - 1} more)"


def heuristic_commit_plan(session: Any, snapshot: WorkingTreeSnapshot) -> list[dict[str, Any]]:
    """Build a succinct, diff-aware 1-or-multi-commit plan without requiring an LLM."""
    if not snapshot.files:
        return []

    buckets: dict[str, list[ChangedFile]] = {}
    for cf in snapshot.files:
        area = _classify_area(cf.path)
        buckets.setdefault(area, []).append(cf)

    # Decide single commit vs logical split
    code_areas = [a for a in ("backend", "ui", "other") if a in buckets]
    non_code_areas = [a for a in ("config", "tests", "docs") if a in buckets]

    should_split = False
    if len(snapshot.files) >= 4:
        if len(code_areas) >= 2:
            should_split = True
        elif code_areas and "docs" in buckets and len(snapshot.files) >= 4:
            should_split = True
        elif len(buckets) >= 3:
            should_split = True

    summary_hint = _extract_summary_hint(session)

    if not should_split:
        primary_area = code_areas[0] if code_areas else next(iter(buckets))
        ctype = _infer_commit_type(session, primary_area, snapshot.files)
        scope = _infer_scope(snapshot.paths, primary_area)
        symbols = _extract_added_symbols(snapshot.diff_text, snapshot.untracked_previews, set(snapshot.paths))
        desc = _describe_file_group(snapshot.files, primary_area, symbols, summary_hint, use_summary_hint=True)
        prefix = f"{ctype}({scope}): " if scope else f"{ctype}: "
        # Avoid double prefix if summary_hint already starts with a conventional type
        if re.match(r"^(feat|fix|refactor|docs|test|chore|perf|style)(\([^)]+\))?:\s+", desc, re.IGNORECASE):
            msg = sanitize_commit_message(desc)
        else:
            msg = sanitize_commit_message(prefix + desc)
        return [{
            "message": msg,
            "files": snapshot.paths,
            "rationale": f"Single cohesive change across {len(snapshot.files)} file{'s' if len(snapshot.files) != 1 else ''}",
        }]

    # Multi-commit logical grouping in clean dependency order: config -> backend -> ui -> other -> tests -> docs
    order = ["config", "backend", "ui", "other", "tests", "docs"]
    # If there's only 1 code area and <= 2 test files, keep tests bundled with the code commit
    if len(code_areas) == 1 and "tests" in buckets and len(buckets["tests"]) <= 2:
        buckets[code_areas[0]].extend(buckets.pop("tests"))

    plan: list[dict[str, Any]] = []
    rationale_labels = {
        "config": "Configuration and dependency updates",
        "backend": "Backend/core logic changes",
        "ui": "Frontend/UI changes",
        "tests": "Automated test coverage",
        "docs": "Documentation updates",
        "other": "Supporting file updates",
    }
    used_hint = False
    for area in order:
        group_files = buckets.get(area)
        if not group_files:
            continue
        paths = [f.path for f in group_files]
        ctype = _infer_commit_type(session, area, group_files)
        scope = _infer_scope(paths, area)
        symbols = _extract_added_symbols(snapshot.diff_text, snapshot.untracked_previews, set(paths))
        use_hint = (not used_hint) and (area in code_areas) and len(code_areas) == 1
        if use_hint and summary_hint:
            used_hint = True
        desc = _describe_file_group(group_files, area, symbols, summary_hint, use_summary_hint=use_hint)
        prefix = f"{ctype}({scope}): " if scope else f"{ctype}: "
        msg = sanitize_commit_message(prefix + desc)
        plan.append({
            "message": msg,
            "files": paths,
            "rationale": rationale_labels.get(area, "Logical change group"),
        })

    return plan


def fast_suggested_commit_message(session: Any, repo_path: Path) -> str:
    """Fast synchronous commit message derived from actual diff (never blindly echoing the goal)."""
    try:
        snapshot = collect_working_tree_changes(repo_path)
    except Exception:
        snapshot = WorkingTreeSnapshot()
    if snapshot.files:
        plan = heuristic_commit_plan(session, snapshot)
        if plan:
            return plan[0]["message"]
    hint = _extract_summary_hint(session)
    if hint:
        return sanitize_commit_message(f"feat: {hint}")
    return "chore: apply session updates"


def _build_llm_prompt(session: Any, snapshot: WorkingTreeSnapshot) -> tuple[str, str]:
    system_prompt = (
        "You are a senior software engineer crafting crisp, high-signal git commit plans from an actual diff.\n"
        "Rules:\n"
        "1. Read the actual diff and file list. NEVER copy or repeat the user's Goal prompt as the commit message.\n"
        "2. Write succinct Conventional Commit messages: `type(scope): imperative summary` (<= 72 chars). "
        "Explain WHAT changed (and WHY if helpful). Optional short body (1-3 bullets) only for complex commits.\n"
        "3. Use 1 commit when changes form one cohesive unit. Split into 2–4 logical commits when changes cleanly "
        "separate (e.g. backend API vs frontend UI, refactor vs new feature, distinct fixes, docs/config vs code).\n"
        "4. Every file in CHANGED_FILES must appear in EXACTLY ONE commit's `files` array. Never invent paths.\n"
        "5. Never add Co-Authored-By trailers.\n"
        "6. Respond with ONLY valid JSON matching: "
        '{"commits": [{"message": "...", "files": ["path1"], "rationale": "short reason"}]}'
    )

    file_lines = "\n".join(f"- [{f.status}] {f.path}" for f in snapshot.files)
    goal_ctx = (getattr(session, "goal", None) or getattr(session, "description", None) or "").strip()[:300]
    summary_ctx = (getattr(session, "output_summary", None) or "").strip()[:1000]

    parts = [
        f"CHANGED_FILES ({len(snapshot.files)}):\n{file_lines}",
        f"DIFF_STAT:\n{snapshot.diff_stat or '(new files only)'}",
    ]
    if goal_ctx:
        parts.append(f"BACKGROUND_GOAL (context only — DO NOT copy into commit message):\n{goal_ctx}")
    if summary_ctx:
        parts.append(f"AGENT_SUMMARY:\n{summary_ctx}")
    if snapshot.diff_text:
        parts.append(f"GIT_DIFF:\n{snapshot.diff_text}")
    if snapshot.untracked_previews:
        previews_txt = "\n\n".join(
            f"--- NEW FILE: {p} ---\n{txt}" for p, txt in snapshot.untracked_previews.items()
        )
        parts.append(f"NEW_FILE_PREVIEWS:\n{previews_txt}")

    return system_prompt, "\n\n".join(parts)


def _parse_and_reconcile_plan(raw_text: str, snapshot: WorkingTreeSnapshot) -> list[dict[str, Any]] | None:
    """Parse JSON commit plan from LLM/CLI output and guarantee 100% file coverage with zero hallucinations."""
    if not raw_text:
        return None
    text = raw_text.strip()
    # Strip markdown code fences if present
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    else:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start : end + 1]

    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None

    raw_commits = payload.get("commits") if isinstance(payload, dict) else None
    if not isinstance(raw_commits, list) or not raw_commits:
        return None

    valid_paths = set(snapshot.paths)
    assigned: set[str] = set()
    cleaned: list[dict[str, Any]] = []

    for item in raw_commits:
        if not isinstance(item, dict):
            continue
        msg = sanitize_commit_message(str(item.get("message") or ""))
        if not msg:
            continue
        raw_files = item.get("files") if isinstance(item.get("files"), list) else []
        commit_files: list[str] = []
        for f in raw_files:
            fp = str(f).strip()
            if fp in valid_paths and fp not in assigned:
                assigned.add(fp)
                commit_files.append(fp)
        if not commit_files:
            continue
        rationale = str(item.get("rationale") or "").strip()[:140] or None
        cleaned.append({
            "message": msg,
            "files": commit_files,
            "rationale": rationale,
        })

    if not cleaned:
        return None

    # Reconcile any changed files the model omitted so nothing is silently left uncommitted
    missing = [p for p in snapshot.paths if p not in assigned]
    if missing:
        cleaned[-1]["files"].extend(missing)

    return cleaned


def _try_llm_api_plan(system_prompt: str, user_prompt: str, snapshot: WorkingTreeSnapshot) -> list[dict[str, Any]] | None:
    """Attempt fast JSON commit planning via RDC's configured OpenAI/OpenRouter/Ollama client."""
    try:
        from openai import OpenAI
    except ImportError:
        return None

    from .intent import load_nanobot_config
    from .vault import get_secret

    cfg = load_nanobot_config()
    provider = cfg.get("llm_provider", "openrouter")

    if provider == "ollama":
        client = OpenAI(api_key="ollama", base_url="http://localhost:11434/v1", timeout=15.0)
        model = cfg.get("ollama_model") or cfg.get("model") or "qwen2.5-coder:7b"
    else:
        openrouter_key = get_secret("OPENROUTER_API_KEY") or os.getenv("OPENROUTER_API_KEY")
        openai_key = get_secret("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")
        if openrouter_key:
            client = OpenAI(api_key=openrouter_key, base_url="https://openrouter.ai/api/v1", timeout=15.0)
            model = cfg.get("model") or "anthropic/claude-3.5-haiku"
        elif openai_key:
            client = OpenAI(api_key=openai_key, timeout=15.0)
            model = "gpt-4o-mini"
        else:
            return None

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
            max_tokens=900,
        )
        content = resp.choices[0].message.content or ""
        return _parse_and_reconcile_plan(content, snapshot)
    except Exception as exc:
        logger.debug("LLM API commit planning skipped: %s", type(exc).__name__)
        return None


def _try_cli_plan(system_prompt: str, user_prompt: str, snapshot: WorkingTreeSnapshot) -> list[dict[str, Any]] | None:
    """Optional fast read-only fallback via Claude CLI with tools disabled when no API key is configured."""
    claude_bin = shutil.which("claude")
    if not claude_bin:
        return None
    full_prompt = f"{system_prompt}\n\n{user_prompt}"
    try:
        res = subprocess.run(
            [claude_bin, "--print", "--output-format", "text", "--tools", "", "--model", "haiku"],
            input=full_prompt,
            text=True,
            capture_output=True,
            timeout=20,
            check=False,
        )
        if res.returncode == 0 and res.stdout.strip():
            return _parse_and_reconcile_plan(res.stdout, snapshot)
    except Exception as exc:
        logger.debug("CLI commit planning skipped: %s", type(exc).__name__)
    return None


def generate_commit_plan(
    session: Any,
    repo_path: Path,
    *,
    use_llm: bool = True,
) -> dict[str, Any]:
    """Generate a diff-aware 1-or-multi-commit plan for the session's uncommitted changes."""
    snapshot = collect_working_tree_changes(repo_path)
    if not snapshot.files:
        return {
            "source": "empty",
            "fingerprint": "",
            "changed_files": [],
            "commits": [],
        }

    if use_llm:
        system_prompt, user_prompt = _build_llm_prompt(session, snapshot)
        llm_commits = _try_llm_api_plan(system_prompt, user_prompt, snapshot)
        if llm_commits:
            return {
                "source": "llm",
                "fingerprint": snapshot.fingerprint,
                "changed_files": snapshot.paths,
                "commits": llm_commits,
            }
        cli_commits = _try_cli_plan(system_prompt, user_prompt, snapshot)
        if cli_commits:
            return {
                "source": "cli",
                "fingerprint": snapshot.fingerprint,
                "changed_files": snapshot.paths,
                "commits": cli_commits,
            }

    return {
        "source": "heuristic",
        "fingerprint": snapshot.fingerprint,
        "changed_files": snapshot.paths,
        "commits": heuristic_commit_plan(session, snapshot),
    }
