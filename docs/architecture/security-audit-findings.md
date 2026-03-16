# Security & Code Audit — remote-dev-ctrl

**Project:** remote-dev-ctrl  
**Stack:** Python 3.11 (FastAPI, Typer, DuckDB/SQLite, MCP) + React/TypeScript/Vite  
**Path:** /Users/dawa/remote-dev-ctrl  
**Audit date:** 2025-03-08  

---

## Findings

| # | Category | Location | Severity | Confidence | Description | Fix |
|---|----------|----------|----------|------------|-------------|-----|
| 1 | SECURITY | `src/remote_dev_ctrl/server/terminal.py:536-538` | CRITICAL | CONFIRMED | Terminal creation uses `subprocess.Popen(command, shell=True, cwd=cwd)`. `command` and `cwd` come from API (`POST /terminals`). Any authenticated user can send arbitrary `command` (e.g. `; rm -rf /`) and get full shell execution on the server. | (1) Do not pass client-supplied `command` to a shell. Use a fixed shell with `[os.environ.get("SHELL","/bin/bash"), "-c", command]` only if you must support one-shot commands, or (2) Restrict to an allowlist (e.g. configured `terminal_command` / SHELL only). Validate `cwd` is under an allowed project root and reject otherwise. |
| 2 | SECURITY | `src/gwd/tools.py:333-334`, `src/remote_dev_ctrl/server/agents/tools.py:423-424` | HIGH | CONFIRMED | `_run_command(project_path, command)` uses `asyncio.create_subprocess_shell(command, ...)`. `command` is LLM/agent-controlled (run_command tool args). Attacker who can influence task/agent input can achieve RCE. | Run commands via `create_subprocess_exec` with a fixed executable (e.g. `["/bin/bash", "-c", command]`) and pass `command` as a single argument, or use an allowlist of commands. Prefer exec with argv list for non-shell tools. |
| 3 | SECURITY | `src/gwd/tools.py:361-362` | HIGH | CONFIRMED | `_git_diff` builds shell command with `cmd += f" -- {file}"`. `file` is validated by `_resolve_path` for traversal but the raw string is then passed into `_run_command` (shell). A value like `x; rm -rf /` can break out and execute arbitrary commands. | Do not interpolate `file` into the shell string. Use `create_subprocess_exec("git", "diff", "--", *sanitized_paths)` with resolved paths only, or pass a single safe string and avoid shell. |
| 4 | SECURITY | `src/remote_dev_ctrl/server/app.py:4971-4990`, middleware | HIGH | CONFIRMED | `POST /terminals` accepts `command` and `cwd` from the request body and is not in `ENDPOINT_PERMISSIONS`. Any authenticated user (including viewer role) can create a terminal with arbitrary command → same as #1. | Add `("POST", "/terminals")` (and related terminal endpoints) to `ENDPOINT_PERMISSIONS` with e.g. `AGENTS_SPAWN` or a dedicated permission, and fix command handling as in #1. |
| 5 | SECURITY | `src/remote_dev_ctrl/server/app.py:6912-6932`, `6957-6988`, `4394-4416` | HIGH | CONFIRMED | `/admin/logs`, `/admin/status`, `/admin/restart`, `/admin/settings` are not in `ENDPOINT_PERMISSIONS`. Any authenticated user can read server logs (possible token leak), trigger server restart, and change admin settings. | Map admin routes to an admin-only permission (e.g. `TOKENS_MANAGE` or a new `ADMIN`) in `ENDPOINT_PERMISSIONS` and enforce in middleware. |
| 6 | SECURITY | `src/remote_dev_ctrl/server/app.py:5609`, terminal WS | MEDIUM | CONFIRMED | WebSocket auth uses token in query string (`?token=...`). Tokens in URLs can be logged (proxies, server logs) or leaked via Referer. | Prefer auth via first WebSocket message or a short-lived session cookie. If keeping query param, ensure no logging of full URL and document the risk. |
| 7 | SECURITY | `frontend/src/features/browser/recording-player.tsx:108` | MEDIUM | SUSPECTED | `containerRef.current!.innerHTML = ""` then rrweb `Replayer` with `events` from API. If recording data can contain script or dangerous HTML and rrweb injects it into the DOM, stored XSS is possible. | Verify rrweb sanitizes replayed content. If not, sanitize or render in an isolated context (e.g. iframe with restricted origin/sandbox). |
| 8 | RELIABILITY | `src/remote_dev_ctrl/server/middleware.py:56` | MEDIUM | CONFIRMED | `RateLimiter.cleanup()` is never called. `_minute_requests` / `_second_requests` grow unbounded with distinct client IDs → memory growth over time. | Call `rate_limiter.cleanup()` periodically (e.g. background task every 60s) or evict in `check()` when trimming old entries. |
| 9 | RELIABILITY | `src/remote_dev_ctrl/server/app.py:2113-2120` | LOW | CONFIRMED | Multiple `.fetchone()[0]` on COUNT queries. If any such query were changed to a conditional that can return no row, `fetchone()` would be `None` and `[0]` would raise. | Keep COUNT(*) as-is (always returns a row). For any new code that might return no row, use `(row := cur.fetchone()) and row[0]` or handle None explicitly. |
| 10 | DATA | `src/remote_dev_ctrl/server/db/repositories.py:364, 592, 801, 924` | LOW | CONFIRMED | SQL uses f-strings to build `WHERE` clause from a list of conditions. Currently conditions are fixed (status, project_id, created_at). Pattern is error-prone and could invite injection if someone adds user input to the condition list. | Prefer a fixed set of condition strings in a list and join them, or use a small allowlist map (e.g. `{"status": "status = ?"}`) so no user input ever becomes part of the SQL fragment. |
| 11 | PERFORMANCE | `src/remote_dev_ctrl/server/app.py:6913` | MEDIUM | CONFIRMED | `get_server_logs(lines: int = 200)` passes `lines` to `tail -n str(lines)` with no bound. A large value (e.g. 10**9) can cause high memory/CPU and slow response. | Validate and cap `lines` (e.g. 1–5000) and return 400 for out-of-range. |
| 12 | EDGE_CASES | `src/remote_dev_ctrl/server/worker.py:373-389` | LOW | CONFIRMED | If `subprocess.Popen` fails after `log_file = open(log_path, "a")`, the file handle is closed in `except`; if the process is created but a later line (e.g. `db.execute`) fails, the log file is not closed in that path. | Use a try/finally or context manager so the log file is always closed on any exception after open. |
| 13 | EDGE_CASES | `src/gwd/tools.py:160-165` | LOW | SUSPECTED | `_resolve_path` uses `str(target).startswith(str(base))`. On Windows, drive letter or case differences could make the check unreliable; symlinks could also affect resolution. | Use `Path.resolve()` and compare with `target.is_relative_to(base)` (Python 3.12+) or `os.path.commonpath([base, target]) == base` for cross-platform safety. |

---

## Second pass: "What critical issues might I have missed?"

- **Token in URL:** Covered in #6.
- **Admin routes:** Covered in #5.
- **Path traversal:** Context paths (a11y_path, meta_path, screenshot_path) are server-generated under CONTEXTS_DIR; no API-controlled path for those. Worker `log_path` is set by the worker when spawning, not from request. No path traversal found for those.
- **SQL injection:** Repositories build WHERE from fixed condition strings and params; `project_ids` in `WHERE id IN (?,?,?)` is parameterized. No direct user input in SQL fragments.
- **Deserialization:** No pickle/yaml.unsafe load found. JSON used on server-controlled or validated paths.
- **CSRF:** API uses Bearer token in header; no cookie-based session for API. Same-origin + token reduces CSRF for API. Noted for completeness.

---

## Summary

| Severity | Count |
|----------|-------|
| Critical | 1 |
| High | 4 |
| Medium | 4 |
| Low | 4 |

**Total score (scoring: +1 LOW, +5 MEDIUM, +10 CRITICAL):**  
1×10 + 4×5 + 4×5 + 4×1 = 10 + 20 + 20 + 4 = **54**

---

## Recommended order of remediation

1. **#1 and #4** — Remove shell=True and restrict/validate terminal `command` and `cwd`; add permission for terminal creation.
2. **#2 and #3** — Replace `create_subprocess_shell` with exec-based invocation and stop interpolating `file` into shell in `_git_diff`.
3. **#5** — Restrict `/admin/*` to admin-only permission.
4. **#11** — Cap and validate `lines` in `/admin/logs`.
5. **#8** — Add periodic `RateLimiter.cleanup()`.
6. **#6, #7, #9, #10, #12, #13** — As capacity allows.
