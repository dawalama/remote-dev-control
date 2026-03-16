# TODO / Future Improvements

## Native Task Executor (replace cursor-agent)

Currently all tasks are executed via `cursor-agent` (Cursor CLI). This works but has limitations:

- Tied to Cursor subscription and rate limits
- Black box — no control over system prompt, tools, or behavior
- No structured output (just a log dump)
- Can't inject MCP tools or project-specific context
- `--resume` bug with MCP approval (exits with code 0)
- Can't chain steps programmatically

**Proposed approach:** Build a Python-native agent executor using Claude API directly.

- Take task description + project context (stack, files, conventions from project profile)
- Call Claude API with custom tool definitions (read file, write file, run shell, browser context)
- Run the agentic loop in Python — own the system prompt, tool set, and stopping conditions
- Return structured output (findings table, files changed, etc.)
- Use any model via API keys — no Cursor dependency

**Existing pieces that can be reused:**
- `src/remote_dev_ctrl/server/intent.py` — orchestrator with model selection
- Project profiles — stack detection, conventions, paths
- `src/remote_dev_ctrl/server/worker.py` — task queue and lifecycle management (keep as-is, just swap the spawn logic)

**Keep cursor-agent as a fallback provider** — it's good for interactive/general tasks. The native executor becomes the primary path.

## Telegram Orchestrator

Replace brittle regex parsing in `handle_telegram_command("message", ...)` with LLM orchestrator call.

- Current: ~120 lines of regex in `app.py`, falls back to "I didn't understand that"
- Fix: `engine.process(text, ctx)` with `channel="telegram"`
- Dispatch client-side actions to dashboards via WS broadcast
- Bot already exists: `src/remote_dev_ctrl/server/channels/telegram.py`
- Currently using phone calls instead; deferred.
