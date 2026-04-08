# RDC — Remote Dev Ctrl

## What This Is

A command center for AI-assisted development. Server + dashboard + CLI for managing projects, terminals, tasks, actions (services and commands), and AI agents.

## Architecture

- **Backend**: Python (FastAPI) at `src/remote_dev_ctrl/server/`
- **Frontend**: React + TypeScript + Tailwind + Zustand at `frontend/`
- **CLI**: Typer at `src/remote_dev_ctrl/cli.py`
- **Database**: SQLite at `~/.rdc/data/` (auto-migrated)
- **Config**: YAML at `~/.rdc/config.yml`

## Key Directories

```
src/remote_dev_ctrl/server/
  app.py            # FastAPI app, routes, lifespan
  config.py         # Config loading, RDC home
  chrome.py         # Local Chrome process lifecycle (find binary, start/stop, CDP readiness)
  browser.py        # Browser session management (Chrome or Docker), CDP _LiveConnection
  browser_use.py    # browser-use wrapper (observe/act/screenshot with CDP fallback)
  terminal.py       # PTY management, WebSocket relay
  worker.py         # Task execution engine
  intent.py         # AI orchestrator (natural language → actions)
  streaming.py      # SSE/streaming endpoints
  db/               # SQLite repos, migrations
  agents/           # Agent provider abstraction
    tools.py        # Agent tool definitions + executors (file, git, browser tools)

frontend/src/
  layouts/           # desktop.tsx, mobile.tsx, kiosk.tsx — ALL first-class
  features/          # Feature modules (terminal, tasks, chat, browser, etc.)
  stores/            # Zustand state (state-store, ui-store, terminal-store, etc.)
  hooks/             # Shared hooks (use-orchestrator, use-voice, use-browser-agent)
  components/        # Shared UI components
```

## Development Rules

1. **All three layouts are first-class**: desktop, mobile, kiosk. Never make a change that applies to all layouts in only one place. Check all three.

2. **Frontend build**: `cd frontend && pnpm run build` (runs `tsc -b && vite build`). Must pass with zero errors — unused variables/imports are build errors.

3. **Server start**: `rdc server start` or `uvicorn remote_dev_ctrl.server.app:app --reload`

4. **Frontend dev**: `cd frontend && pnpm dev` (Vite dev server, proxies API to :8420)

5. **Component patterns**:
   - Desktop uses `EmbeddedTerminal`, `RightTabs`, `ChatFAB`
   - Kiosk uses `EmbeddedTerminal`, `KioskSideTabs`, `KioskChatPanel`, `KioskActionBar`
   - Mobile uses card components from `features/mobile/` + `MobileCommandBar` + `ChatCard`

6. **State flow**: Server → WebSocket `/ws/state` → `state-store.ts` → components subscribe via Zustand selectors

7. **Terminology: "actions"**: The user-facing term is **"actions"** — split into services (long-running) and commands (one-shot). API routes use `/actions/*`, frontend type is `Action`, WebSocket state field is `actions`. Internally, Python classes still use `ProcessManager`/`ProcessConfig` and the DB table is `process_configs` — these are implementation details that don't leak to users. The tab ID remains `"processes"` in frontend routing for backward compat.

8. **Default collection**: The `"general"` collection ID is a system constant shared between server (migration SQL, `models.py` default, `repositories.py` delete guard) and frontend (`DEFAULT_COLLECTION_ID` in `collection-picker.tsx`). It is seeded by migration, cannot be deleted, and orphaned projects fall back to it. Do not introduce a second source of truth — the string `"general"` is the contract.

9. **Terminal architecture**: PTY relay processes (`socat`-based) survive server restarts. Session metadata persisted to `~/.rdc/terminal_sessions.json`. Mobile/kiosk `TerminalOverlay` supports long-press Back or tap title to open a terminal switcher dropdown.

10. **Database migrations**: SQL files in `src/remote_dev_ctrl/server/db/migrations/`. Auto-run on server start.

11. **Browser architecture**: Local Chrome subprocess (no Docker) managed by `ChromeProcess` in `chrome.py`. `BrowserManager` in `browser.py` discovers the CDP WebSocket URL from `/json/version`, creates page targets, and manages `_LiveConnection` instances. The `browser_use.py` wrapper provides `observe()`/`act()`/`screenshot()` for the agent loop. Config `browser.backend` supports `"chrome"` (default) or `"docker"` (legacy). Chrome profiles stored at `~/.rdc/chrome-profiles/`.

12. **Browser agent tools**: 5 tools in `agents/tools.py` (`browser_observe`, `browser_click`, `browser_type`, `browser_navigate`, `browser_screenshot`). Observe/screenshot auto-approve; click/type/navigate require approval. The `/browser/sessions/{id}/agent/loop` endpoint runs a multi-step observe→act loop (max 20 steps). The one-shot `/browser/sessions/{id}/agent` endpoint is kept for backward compat.

13. **FloatingAgentPanel**: Present in ALL three layouts (desktop, mobile, kiosk). The browser agent input panel that lets users send instructions to the browser automation agent.

## Common Tasks

```bash
# Build frontend
cd frontend && pnpm run build

# Type-check only
cd frontend && npx tsc --noEmit

# Start server with hot-reload
rdc server start --reload

# Run frontend dev server
cd frontend && pnpm dev
```

<!-- dotai:start -->
# AI Context

# AI Knowledge Base (~/.ai/)

This project uses a structured knowledge system at `~/.ai/` with project-level
overrides in `.ai/`. Before starting work, read the relevant context files.

## Structure

```
~/.ai/
  rules.md          # Coding rules, conventions, and lessons learned
  roles/            # Cognitive modes (personas for different tasks)
  skills/           # Reusable workflows (slash commands)
  tools/            # Python tool implementations
```

Project-specific overrides live in `<project>/.ai/` with the same structure.
Project rules take precedence over global rules.

## Active Rules

- **no-coauthor-trailer**: Do not add Co-Authored-By trailers to git commits unless explicitly asked. (applies to: `*`) — `/Users/dawa/.ai/rules/no-coauthor-trailer.md`
- **no-credential-logging**: Never print, log, or expose credentials to stdout/stderr where LLM context can capture them. (applies to: `*`) — `/Users/dawa/.ai/rules/no-credential-logging.md`
- **no-data-exfiltration**: Never send sensitive data to external endpoints without explicit user confirmation. (applies to: `*`) — `/Users/dawa/.ai/rules/no-data-exfiltration.md`
- **no-insecure-credential-storage**: Never pass secrets via CLI arguments, URL query strings, or temporary files. (applies to: `*`) — `/Users/dawa/.ai/rules/no-insecure-credential-storage.md`
- **no-sensitive-file-access**: Never read credential files (~/.ssh, ~/.aws, .env) unless explicitly authorized by the user. (applies to: `*`) — `/Users/dawa/.ai/rules/no-sensitive-file-access.md`
- **no-useEffect**: Never call useEffect directly. (applies to: `*.tsx, *.ts`) — `/Users/dawa/.ai/rules/no-useeffect.md`


## Available Roles

- **Systems Architect**: Senior architect focused on system design, tradeoffs, and scalability
- **Debugger**: Root cause analyst who isolates problems systematically
- **Founder Mode**: Product thinker focused on vision, impact, and 10x outcomes
- **Mentor**: Patient teacher who explains decisions, teaches patterns, and builds understanding
- **Product Manager**: Scopes features, writes acceptance criteria, and prioritizes ruthlessly
- **QA Engineer**: Methodical tester who breaks things systematically
- **Paranoid Reviewer**: Staff engineer focused on production safety and correctness
- **Security Engineer**: Application security specialist focused on threat modeling and vulnerability detection
- **Release Engineer**: Gets code shipped — no bikeshedding, no blockers, just ship
- **Technical Writer**: Documentation specialist focused on clarity and accuracy

To adopt a role, read its file from `~/.ai/roles/` and follow its persona.

## Available Skills

### Code Quality

- **minimalist-review** `/run_minimalist-review`: Review any business decision, plan, or strategy through the minimalist entrepren
- **Code Review** `/run_review` (role: reviewer): Analyze the current branch's diff against the base branch for structural issues 

### Debugging

- **Investigate** `/run_investigate` (role: debugger): Systematic root-cause investigation for bugs, errors, or unexpected behavior.

### Deployment

- **Ship** `/run_ship` (role: ship): Non-interactive ship workflow: sync, test, push, create PR.

### Maintenance

- **Find Tech Debt** `/run_techdebt`: Analyze the codebase to identify technical debt, code duplication, and areas nee

### Scaffolding

- **Scaffold** `/run_scaffold`: Generate boilerplate for a new module, component, or service by following existi

### Verification

- **validate-idea** `/run_validate-idea`: Validate a business idea using the minimalist entrepreneur framework. Use when s
- **Verify** `/run_verify`: Run comprehensive verification on recent changes: tests, types, lint, and build.

### Workflow

- **Careful Mode** `/run_careful` [context: production, sensitive]: Activate production-safety mode. When this skill is active, apply extra caution 
- **Commit Helper** `/run_commit`: Analyze staged changes and generate a well-structured commit message following c
- **company-values** `/run_company-values`: Help define company values and culture for a minimalist business. Use when someo
- **Context Dump** `/run_context`: Generate a comprehensive context dump for starting a new AI session or onboardin
- **find-community** `/run_find-community`: Help identify and evaluate communities to build a minimalist business around. Us
- **first-customers** `/run_first-customers`: Create a strategy for selling to your first 100 customers using the minimalist e
- **grow-sustainably** `/run_grow-sustainably`: Evaluate business decisions through the lens of sustainable, profitable growth. 
- **Learn** `/run_learn`: Capture a lesson from the current session and turn it into a permanent rule so t
- **marketing-plan** `/run_marketing-plan`: Create a minimalist marketing plan focused on building an audience through conte
- **Parallel Work Mode** `/run_parallel`: Set up and manage parallel development using git worktrees. This enables multipl
- **Plan** `/run_plan`: Structured planning workflow: clarify the problem, explore the solution space, p
- **pricing** `/run_pricing`: Help figure out pricing for a product or service using minimalist entrepreneur p

Skills are available as slash commands (e.g. `/run_review`).
Folder-based skills may include helper scripts in `scripts/` — prefer these over writing from scratch.

## How to Use

1. **Start of session**: Read `~/.ai/rules.md` and the project's `.ai/rules.md`
2. **Before a task**: Check if a relevant skill exists and adopt its role
3. **When unsure**: The rules contain conventions, corrections, and project-specific guidance

## Composing Skills with Roles

When the user writes `/<skill> as <role>`, adopt the role's full persona
before executing the skill's steps. The role shapes *how* you think;
the skill defines *what* you do.

Examples:
- `/run_review as qa` — run the Code Review skill while thinking like a QA Engineer
- `/run_review as paranoid-reviewer` — review code as a paranoid staff engineer focused on security
- `/run_techdebt as debugger` — hunt tech debt with a debugger's systematic mindset
- `/run_review` (no role) — run the skill with its default role, or no persona if none is set

To compose: find the matching role from **Available Roles** above,
adopt its persona and principles, then execute the skill's steps.
The role's anti-patterns become things to watch for during execution.


Read `~/.ai/rules.md` at the start of every conversation.
<!-- dotai:end -->
