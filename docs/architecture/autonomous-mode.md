# RDC v2 — Architecture

**Status:** Approved Design
**Date:** 2026-04-03
**Version:** 1.0 (post deep interview)

---

## Vision

RDC v2 is a **personal development operating system**. It manages your projects, agents, terminals, and tasks across contexts — whether you're at your desk with three monitors or on a walk with just your phone.

The core interaction model is **channels** — persistent workspaces where you organize work, talk to your orchestrator, run terminals, execute missions, and review results.

---

## Three Contexts

RDC adapts to how and where you're working. Context is an **explicit toggle**, not auto-detected.

### At-Home

Laptop, monitors, keyboard. Full setup.

- IDE-like workspace with split panes
- Multiple terminals visible simultaneously
- Browser preview side-by-side
- Quick channel switching
- Phone as secondary controller (approve things, monitor agents from the couch)

### On-The-Move

Phone only. Cafe, commute, walk.

- Channel list with status indicators
- Chat-first interaction with orchestrator
- Approve/reject plans
- Terminal available as overlay for emergencies
- Voice input as primary input method

### Autonomous

System runs without you. You're asleep or away.

- Missions execute in auto-mode
- Notifications push when: done, stuck, needs approval
- When you return: summary of what happened, deferred decisions, diffs

---

## Channels

### What Is a Channel?

A channel is a **workspace** — not a message thread. It contains:

- **Message queue**: The conversational history and state log. Chat with the orchestrator, system events, approval requests, mission updates. This is the source of truth for everything that happened in this context.
- **Workspace layout**: Spatial arrangement of panels — terminals, chat, mission progress, browser preview. Customizable per channel.
- **Terminals**: Zero or more, belonging to this channel's context.
- **Missions**: Autonomous tasks being planned/executed in this context.

```
#chilly-snacks/payments (workspace)
┌─────────────────────────┬──────────────┐
│  Message Queue          │  Terminal 1  │
│  (chat + state log)     │  (Claude)    │
│                         ├──────────────┤
│  [Approval banner]      │  Terminal 2  │
│                         │  (Shell)     │
├─────────────────────────┤              │
│  Mission Progress       │              │
│  Step 3/5 ████░░ 60%   │              │
└─────────────────────────┴──────────────┘
```

On mobile, the same workspace collapses into tabs:

```
#chilly-snacks/payments
┌─────────────────────────┐
│ [Chat] [Terms] [Mission]│  ← tabs or swipe
├─────────────────────────┤
│  (active tab content)   │
└─────────────────────────┘
```

### Channel Types

| Type | Created By | Lifetime | Example |
|---|---|---|---|
| **Project** | Auto-created per project | Permanent | `#chilly-snacks` |
| **Mission** | Auto-created when mission starts | Archives on completion | `#chilly-snacks/payments` |
| **Ephemeral** | User (`/channel` or button) | Until user archives | `#quick-question` |
| **System** | Auto-created | Permanent | `#system`, `#system/observer` |
| **Event** | Auto-created for PRs, deploys | Archives on merge/complete | `#chilly-snacks/pr-42` |

### Channel Sidebar

Flat list by default. Toggle to project-grouped view when channels accumulate:

```
[Flat]                    [By Project]

#chilly-snacks            chilly-snacks
#chilly-snacks/payments     #default
#quickdraw-wallet           #payments
#system                   quickdraw-wallet
                            #default
                          system
                            #system
                            #observer
```

### Channel Properties

- **Multi-project**: A channel usually has one project, but can span multiple (for cross-cutting work like "update shared library and all consumers").
- **Auto-mode toggle**: Per-channel. When on, orchestrator skips tactical approvals (file edits, commands). Strategic decisions still ask.
- **Token budget**: Per-channel spending limit. Execution pauses when exceeded.
- **Archivable**: Archived channels remain searchable but leave the sidebar.

### Input Routing

**Focus-based.** Click/tap on a pane to direct input there.

- Terminal focused → keystrokes go to terminal stdin
- Chat pane focused → input goes to orchestrator
- Active pane shows a visible indicator (colored border)
- Voice: routed to whatever has focus. "Switch to terminal" / "switch to chat" voice commands available.

---

## Projects and Channels

Projects remain the organizational unit. Channels are the interaction surface on top.

```
Projects (permanent, organizational)
  └── Channels (workspaces, per-project or cross-project)
        ├── Message Queue (history, state)
        ├── Terminals (execution)
        └── Missions (autonomous work)
```

- Adding a project creates a default channel for it
- Multiple channels per project is natural: `#chilly-snacks`, `#chilly-snacks/payments`, `#chilly-snacks/deploy`
- Terminals are tied to projects (for cwd) but can appear in one or more channels
- A terminal in `#chilly-snacks/payments` is also visible in the parent `#chilly-snacks`

---

## The Orchestrator

The orchestrator lives in every channel's chat pane. It's always available — not a separate mode.

### Three-Tier Architecture

```
Meta-Orchestrator (singleton, always running)
  ├── Watches all channels for incoming messages
  ├── Spawns per-channel orchestrators on demand
  ├── Coordinates cross-channel work
  ├── Manages global token budget
  └── Lives in #system channel

Per-Channel Orchestrator (spawned per active channel)
  ├── Owns the interview → plan → execute → verify lifecycle
  ├── Manages terminals and API calls within this channel
  ├── Reports token usage upward
  ├── Has read access to other channels (if user permits)
  └── Dies when channel is archived or idle

Observer (batch process, not live)
  ├── Scans event store periodically (daily/weekly/on-demand)
  ├── Produces: rules, patterns, reports, suggestions
  ├── Posts to #system/observer
  └── Never auto-applies — proposes, user approves
```

### Cross-Channel Access

Per-channel orchestrators are isolated by default. Cross-channel read access is granted by the user:

- You say "use the same auth pattern from quickdraw-wallet" in `#chilly-snacks`
- The orchestrator checks: do I have read access to `#quickdraw-wallet`?
- If yes → searches that channel's message queue
- If no → asks the meta-orchestrator → asks you for permission

### Orchestrator's LLM Usage

Tiered model selection per task type:

| Task | Model Tier |
|---|---|
| Routing decisions | Local (Ollama) or Haiku |
| Ambiguity scoring | Sonnet |
| Plan generation | Sonnet |
| Confidence assessment | Opus (rare) |
| Mission summaries | Sonnet or Haiku |

**Fallback chain:** Preferred → cheaper → local. If no API credits, everything falls back to local models. Quality degrades but work never blocks.

**Cached routing:** Routing decisions are cached as rules. First time: LLM decides. Second time: rule fires (zero cost). Over time, most routing is free.

### Interaction Model

```
You: "Add Stripe checkout to this project"

Orchestrator: I have a few questions:
  1. Which Stripe product? (Checkout Sessions, Payment Intents, or Subscriptions?)
  2. Do you have test keys ready?
  3. Should I add webhook handlers?

You: "Checkout sessions, yes, yes"

Orchestrator: Here's my plan:
  1. Install stripe package
  2. Create /api/checkout route
  3. Add webhook handler
  4. Update product page
  5. Run tests
  Estimated: ~15 min, ~$0.40 tokens. Approve?

You: "Go"

[Terminal opens in channel, Claude Code starts]
[Progress visible in mission panel]
[15 min later:]

Orchestrator: Done. 4 files changed, tests pass.
  Want me to commit?
```

### Mission Lifecycle

```
SUBMITTED → INTERVIEWING → PLANNING → APPROVED → EXECUTING → VERIFYING → COMPLETE
                ↑               ↑                      ↓           ↓
                └── (clarify) ←────────────────────────┘     FIXING ←┘
                                                                ↓
                                                         (max iterations)
                                                                ↓
                                                             STUCK → notify user
```

When stuck:
- Check if remaining steps depend on the stuck step
- If independent AND orchestrator is confident → continue, mark stuck step for human
- If dependent or low confidence → stop, notify, wait

### Approval Model

**Default: always ask.** Auto-mode is a per-channel toggle, flippable mid-execution.

Two levels:
- **Tactical** (file edits, commands): Auto-mode skips these
- **Strategic** (architecture decisions, approach choices): Always asks, even in auto-mode

The orchestrator learns permission patterns over time: "user always approves file edits in src/" → stops asking for those.

### Execution Backends

The orchestrator picks the right backend per step. Terminal is a tool, not a requirement.

| Backend | When | Example |
|---|---|---|
| **CLI Agent** (Claude/Cursor in terminal) | Complex multi-file changes, debugging | "Implement the payment integration" |
| **LLM API** (direct call, no terminal) | Focused tasks, review, analysis | "Review this diff for security issues" |
| **Shell** (command in terminal) | Build, test, deploy | "Run the test suite" |
| **Browser** (Chrome + CDP) | Web testing, scraping | "Verify the checkout page works" |

A mission might use zero terminals (all API calls) or five terminals (complex multi-agent work).

---

## Token Budgeting

Token usage is tracked and budgeted at two levels:

| Level | Tracks | Budget |
|---|---|---|
| **Channel** | All LLM calls in this channel | Configurable, NULL = unlimited |
| **Mission** | This mission's LLM calls | Configurable, falls back to channel budget |

Orchestrator behavior:
- Before each LLM call: check remaining budget
- Approaching limit: slow down, queue non-urgent calls
- Exceeded: pause mission, notify user "budget exhausted, resume?"
- Cooldown-aware: wait for API quota reset if applicable

Dashboard shows: burn rate, remaining budget, cost per mission.

---

## Observability + Observer

### Event Store (always on, cheap)

Everything emits structured events to an append-only store. This is a log append — zero overhead.

| Source | Events |
|---|---|
| Terminal | command_executed, error_occurred, test_passed/failed |
| Orchestrator | mission_started, step_completed, step_failed, plan_generated |
| Agent | llm_call (model, tokens, latency), tool_used, file_edited |
| User | message_sent, approval_given, mode_toggled |
| System | server_started, connection_lost, budget_exceeded |

Storage: SQLite `events` table with FTS5 index. 30-day retention for raw events, summaries kept indefinitely.

### Observer (periodic batch)

NOT a live watcher. A batch process that scans the event store and produces insights.

**Runs:** On demand (`/observe`), daily digest, weekly report, on mission completion.

**Produces:**
- Error patterns → proposed rules for `.ai/rules.md`
- Workflow patterns → proposed prompt templates
- Agent performance → routing optimization suggestions
- Mission post-mortems → execution memory
- Cost reports → token usage breakdown

**Posts to:** `#system/observer` channel. User approves before any rule takes effect.

---

## Self-Improving Execution

Inspired by Hermes Agent. The system gets better over time through three mechanisms:

### Execution Memory

Per-project learnings stored in `.ai/rules.md`:
- "Tests fail when dev server isn't running" → start server before testing
- "This project uses pnpm not npm" → remember for all missions
- "CI requires Python 3.12+" → verify before submitting

The orchestrator reads project rules before planning and writes new learnings after each mission.

### Reflective Correction

When a step fails: analyze WHY (not just retry), identify root cause, adjust approach, apply fix, record the learning.

### Cached Routing

Routing decisions become rules over time. First occurrence: LLM decides. Subsequent: cached rule fires. Most routing becomes free.

---

## Voice

First-class input in all contexts. Focus-based routing:

- Voice goes to whatever pane has focus (terminal or chat)
- "Switch to terminal" / "switch to chat" to redirect
- "Switch to chilly-snacks" to change active channel
- Transcription → input (for terminal) or command parsing (for orchestrator)

---

## First Experience

1. `rdc setup` — API keys, server config (CLI, existing)
2. Dashboard opens → `#system` channel with welcome message and "what to do next"
3. User adds a project → default channel created
4. They're in a workspace: chat pane + terminal
5. No wizard, no tutorial. The product teaches through use.

---

## Data Model

```sql
channels (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  type TEXT NOT NULL,           -- project, mission, ephemeral, system, event
  parent_channel_id TEXT,       -- for sub-channels
  auto_mode BOOLEAN DEFAULT FALSE,
  token_spent INTEGER DEFAULT 0,
  token_budget INTEGER,         -- NULL = unlimited
  created_at TIMESTAMP,
  archived_at TIMESTAMP
)

channel_projects (
  channel_id TEXT REFERENCES channels(id),
  project_id TEXT REFERENCES projects(id),
  PRIMARY KEY (channel_id, project_id)
)

channel_messages (
  id TEXT PRIMARY KEY,
  channel_id TEXT NOT NULL REFERENCES channels(id),
  role TEXT NOT NULL,            -- user, orchestrator, system, agent
  content TEXT,
  metadata JSON,
  synced BOOLEAN DEFAULT TRUE,  -- for offline/sync
  created_at TIMESTAMP
)

terminal_channels (
  terminal_id TEXT REFERENCES terminal_sessions(id),
  channel_id TEXT REFERENCES channels(id),
  PRIMARY KEY (terminal_id, channel_id)
)

missions (
  id TEXT PRIMARY KEY,
  channel_id TEXT NOT NULL REFERENCES channels(id),
  title TEXT,
  description TEXT,
  status TEXT,
  plan JSON,
  current_step INTEGER,
  execution_log JSON,
  learnings JSON,
  token_input INTEGER DEFAULT 0,
  token_output INTEGER DEFAULT 0,
  token_budget INTEGER,
  submitted_via TEXT,
  created_at TIMESTAMP,
  updated_at TIMESTAMP
)

events (
  id TEXT PRIMARY KEY,
  timestamp TIMESTAMP NOT NULL,
  type TEXT NOT NULL,
  channel_id TEXT,
  project_id TEXT,
  mission_id TEXT,
  data JSON
)
```

---

## System Map

```
┌─────────────────────────────────────────────────────────────┐
│                        RDC v2                                │
│                                                              │
│  ┌──────────────────────────────────────────────────┐       │
│  │              Event Store (append-only)             │       │
│  │  All sources emit structured events               │       │
│  └───────────────────────┬──────────────────────────┘       │
│                          │ (periodic scan)                    │
│  ┌─────────────────────────────────────────────────────────┐│
│  │                   #system channel                        ││
│  │  Meta-Orchestrator + Token Budget + Observer (batch)     ││
│  └─────────────────────────────────────────────────────────┘│
│                                                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│  │ #project-a   │  │ #project-b   │  │ #ephemeral   │      │
│  │              │  │              │  │              │      │
│  │ Orchestrator │  │ Orchestrator │  │ Orchestrator │      │
│  │ Terminals[]  │  │ Terminals[]  │  │ (no project) │      │
│  │ Missions[]   │  │ Missions[]   │  │              │      │
│  │ Messages[]   │  │ Messages[]   │  │ Messages[]   │      │
│  └──────────────┘  └──────────────┘  └──────────────┘      │
│                                                              │
│  Execution Backends:                                         │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐       │
│  │CLI Agent│  │ LLM API │  │  Shell  │  │ Browser │       │
│  │Terminal │  │ Direct  │  │ Command │  │  CDP    │       │
│  └─────────┘  └─────────┘  └─────────┘  └─────────┘       │
│                                                              │
│  Inputs:                                                     │
│  ┌─────┐ ┌────────┐ ┌─────────┐ ┌───────┐ ┌─────┐         │
│  │ Web │ │Telegram│ │ Discord │ │ Voice │ │ CLI │         │
│  └─────┘ └────────┘ └─────────┘ └───────┘ └─────┘         │
│                                                              │
│  Contexts:                                                   │
│  ┌─────────┐  ┌──────────────┐  ┌────────────┐             │
│  │ At-Home │  │ On-The-Move  │  │ Autonomous │             │
│  │(desktop)│  │  (mobile)    │  │  (away)    │             │
│  └─────────┘  └──────────────┘  └────────────┘             │
└─────────────────────────────────────────────────────────────┘
```

---

## Implementation Plan

### v2.0: Channels (first ship)

The minimum that's useful daily. No orchestrator, no missions, no autonomous mode.

**Backend:**
- Channel CRUD API (create, archive, rename, list)
- Channel message API (post, list, search)
- `channel_projects` and `terminal_channels` junction tables
- Default channel auto-created when project is added
- Event store table + basic event emission

**Frontend:**
- Channel sidebar (flat list, grouped toggle)
- Channel workspace layout (chat pane + terminal pane, resizable)
- Per-channel message history (scrollable)
- Terminal-in-channel (spawn terminal tied to channel context)
- Focus-based input routing (visual indicator on active pane)
- Mobile: channel list → tap → workspace with tabs
- Offline message queue (localStorage, sync on reconnect)

**What carries over from v1:**
- Terminal management (PTY relay, snapshots, multi-client)
- Project management
- Process/action management
- Browser automation
- Auth, Telegram bot
- All existing API endpoints (backward compat)

### v2.1: Orchestrator MVP

- Orchestrator appears in channel chat
- Interview → plan → approve → execute (single agent)
- Verify-fix loop with confidence-based continuation
- Auto-mode toggle per channel
- Token tracking per channel and mission
- Tiered model selection for orchestrator's own calls

### v2.2: Event Store + Observer

- Structured event emission from all sources
- FTS5 search across events
- Batch Observer process (daily digest, cost reports)
- Rule proposals in `#system/observer`

### v2.3: Multi-Agent + Full Autonomous

- Multi-agent coordination (N terminals per channel)
- Git worktree isolation per agent
- LLM API as execution backend (terminal-free missions)
- Cross-channel orchestration via meta-orchestrator
- Execution memory and skill accumulation
- Discord/Slack webhook notification dispatch

---

## References

- [oh-my-openagent](https://ohmyopenagent.com/) — Named agents, deep interview, multi-model routing
- [Hermes Agent](https://hermes-agent.nousresearch.com/) — Self-improving agent, persistent memory, GEPA
- [CLIDeck](https://clideck.dev/) — Terminal multiplexer with Autopilot, status detection, prompt library
- [Sigrid Jin](https://x.com/realsigridjin/status/2039472968624185713) — Agent coordination > generated code
- Slack — Channel UX model people already understand
- VS Code — Workspace layout model for the channel interior
- tmux — Multi-client terminal sharing, aggressive-resize pattern
