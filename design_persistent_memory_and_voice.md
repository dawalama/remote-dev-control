# Persistent Memory, Voice Loop & State Machine Orchestration — Product Direction

## Vision

The ADT server evolves from a dashboard into an **AI operating layer** — it has persistent memory (knows you across sessions), a central state machine (current state of all work), and multiple input channels (voice, text, mobile, desktop, MCP, agents). The user speaks or types intent, the server understands it, routes to the right agent or integration, and takes verified actions.

```
        Voice ──┐
       Mobile ──┤
      Desktop ──┤──→ Intent ──→ Routing ──→ Action ──→ State Machine
    MCP Tools ──┤    Engine      Layer      Engine      (verified)
  Agent Output──┤                                          │
                │                                          ▼
                │                                    Persistent Memory
                │                                    (what happened,
                │                                     what was decided,
                │                                     what user prefers)
```

## 1. Persistent Memory

### What it is

Server-side memory that persists across sessions, projects, and devices. Not just project knowledge (rules/learnings from the knowledge system design) — this is **operational memory** about the user, their work, and the system's own state.

### Memory layers

```
┌─────────────────────────────────────────────┐
│ User Memory (preferences, patterns)         │
│ "prefers concise responses"                 │
│ "works on invoiceapp Mon-Wed, dashboard     │
│  Thu-Fri"                                   │
│ "always reviews PRs before merging"         │
├─────────────────────────────────────────────┤
│ Session Memory (current work context)       │
│ "working on auth refactor since 2pm"        │
│ "3 agents running: invoiceapp, dashboard,   │
│  docs"                                      │
│ "last voice command: check test results"    │
├─────────────────────────────────────────────┤
│ Work Memory (decisions, outcomes)           │
│ "decided to use JWT over sessions (Feb 10)" │
│ "deploy to staging failed, root cause was   │
│  missing env var DATABASE_URL"              │
│ "PR #42 merged, PR #43 awaiting review"     │
├─────────────────────────────────────────────┤
│ Project Knowledge (rules, learnings)        │
│ (see design_knowledge_system.md)            │
└─────────────────────────────────────────────┘
```

### How it differs from project knowledge

| | Project Knowledge | Persistent Memory |
|---|---|---|
| **Scope** | Per-project | Cross-project, user-level |
| **Storage** | .ai/ files + DB | DB only (not git-committed) |
| **Content** | Rules, learnings, context | Preferences, work state, decisions |
| **Audience** | AI agents doing code work | The orchestration layer itself |
| **Lifespan** | Permanent (until deleted) | Mixed — some permanent, some decay |

### Memory operations

```
remember(key, value, scope, ttl?)
  → Store a memory. Scope: user, project, session.
  → TTL: None (permanent), duration (auto-expire).

recall(query, scope?, limit?)
  → Semantic search across memories.
  → "What did we decide about auth?" → returns relevant memories.

forget(key_or_query)
  → Explicit deletion. "Forget my preference about X."

decay()
  → Background process. Session memories expire.
  → Work memories lose relevance score over time.
  → User memories persist indefinitely.
```

### Memory as grounding

When the voice/intent system processes a command, it first recalls relevant memories:

```
User: "How's the auth refactor going?"

recall("auth refactor") →
  - [work] "Started auth refactor on invoiceapp, branch: feat/jwt-auth"
  - [work] "3 tests failing as of last run (15 min ago)"
  - [work] "Decided JWT over sessions, Feb 10"
  - [session] "Agent running on invoiceapp, status: in_progress"

→ Server can give informed response without user re-explaining context
```

## 2. State Machine as Central Hub

### Current state

The state machine (`state_machine.py`) tracks system state — projects, terminals, tasks, processes. It broadcasts via WebSocket to the dashboard and mobile.

### Evolution

The state machine becomes the **single source of truth** that all channels read from and write to. Every action is a state transition. Every input channel proposes transitions. Verification gates control which transitions execute.

```
                    ┌─────────────────────┐
                    │    State Machine    │
                    │                     │
                    │  Projects           │
     propose ──────→  Terminals          │──────→ broadcast
     transitions    │  Tasks              │        to all
                    │  Agents             │        channels
                    │  Memory             │
                    │  User Preferences   │
                    └─────────────────────┘
                              │
                        verification
                         gate (for
                        destructive
                         actions)
```

### Transition types

```
# Informational (no verification needed)
{ type: "QUERY", intent: "status of invoiceapp" }
→ Read state, return response

# Safe actions (auto-verified)
{ type: "ACTION", intent: "open terminal for invoiceapp" }
{ type: "ACTION", intent: "show me the logs" }
→ Execute immediately

# Destructive actions (require verification)
{ type: "ACTION", intent: "deploy invoiceapp to staging", verify: true }
{ type: "ACTION", intent: "stop all agents", verify: true }
→ Ask for confirmation before executing

# Agent delegation (async, monitored)
{ type: "DELEGATE", intent: "fix the failing tests in invoiceapp" }
→ Spawn agent, monitor progress, report back
```

### Verification modes

```
Voice: "Deploy to staging"
Server: "I'll deploy invoiceapp to staging. Confirm?"
User: "Yes" / "No" / "Wait, which branch?"

Mobile: Push notification with approve/reject buttons
Desktop: Modal confirmation dialog
MCP: Return verification prompt to the calling agent
```

## 3. Voice Loop

### Current state

Mobile has Deepgram STT, routes to terminal or command bar. One-directional — voice goes in, text comes out on screen.

### Evolution: always-listening intent loop

```
┌─────────────────────────────────────────────────┐
│                Voice Loop                        │
│                                                  │
│  Mic Input ──→ STT ──→ Intent ──→ Router ──→    │
│     ↑          (Deepgram) Parser    │            │
│     │                     │         │            │
│     │              ┌──────┴───┐     │            │
│     │              │ Memory   │     ├─→ Query    │
│     │              │ Recall   │     ├─→ Action   │
│     │              └──────────┘     ├─→ Delegate │
│     │                               ├─→ Remember │
│     │                               └─→ Terminal │
│     │                                     │      │
│     └──── TTS ←── Response ←──────────────┘      │
│           (optional)                             │
└─────────────────────────────────────────────────┘
```

### Intent categories

```
Status queries:
  "How's invoiceapp doing?" → query agent/task status
  "Any failing tests?" → query last test results
  "What's blocking the deploy?" → query work memory

Actions:
  "Open a terminal for dashboard" → state machine transition
  "Run the tests" → spawn process
  "Deploy to staging" → verified action

Delegation:
  "Fix the CSS on the login page" → spawn agent with task
  "Review PR 43" → spawn review agent
  "Add dark mode to the settings page" → create task + optionally spawn

Memory:
  "Remember that we're using Stripe for payments" → store in project memory
  "What did we decide about caching?" → recall from work memory
  "From now on, always run tests before deploy" → store as user preference/rule

Terminal passthrough:
  "Tell the agent to focus on the API first" → route to active terminal as text input
```

### Intent parsing

Two-tier approach:
1. **Fast classification** — Small/local model or keyword matching. Determines category (query, action, delegate, remember, terminal). Needs to be <200ms.
2. **Full understanding** — If needed, send to a capable model with memory context for nuanced understanding. Used for ambiguous or complex intents.

```
"check the tests" → fast: keyword "check" + "tests" → action: run tests
"I think we should use Redis instead of Memcached for the session store"
  → fast: unclear → full model → memory: store decision about session store
```

### Response modes

The server doesn't always need to talk back. Response mode depends on context:

```
Voice-active (headphones, walking):
  → TTS response: "Invoiceapp has 3 tests failing. Want me to look into it?"

Screen-active (looking at dashboard):
  → Visual update: highlight failing tests, show notification

Background (not actively interacting):
  → Push notification on mobile
  → Queue for next interaction: "By the way, the deploy finished"
```

## 4. Multi-Channel Orchestration

### All channels feed the same pipeline

```
Voice:   "Deploy invoiceapp" ──┐
Mobile:  tap "Deploy" button ──┤──→ Intent: deploy(invoiceapp)
Desktop: click deploy button ──┤     → Verify → Execute
CLI:     rdc deploy invoiceapp─┤     → Update state machine
MCP:     deploy_project() ─────┘     → Broadcast to all channels
```

### Channel-aware responses

The server knows which channels are active and responds appropriately:

```
User deploys via voice:
  → Voice: "Deploying invoiceapp to staging"
  → Mobile: notification card appears
  → Desktop: deploy status indicator updates
  → All: state machine broadcasts new state

Deploy completes:
  → If voice session active: TTS "Deploy complete, took 45 seconds"
  → If mobile only: push notification
  → If desktop only: toast notification
  → Always: state machine updated, work memory recorded
```

## 5. Agent Integration

### Agents as first-class participants

Agents (cursor-agent, claude, custom) are both consumers and producers in this system:

**Consumers:**
- Read project context via MCP (get_project_context)
- Read persistent memory via MCP (what was decided, what's the current state)
- Receive tasks from the orchestration layer

**Producers:**
- Report progress (state machine updates)
- Capture learnings (add_learning via MCP)
- Request human input (attention/review system already exists)
- Produce artifacts (code, PRs, docs)

### Agent spawning from voice

```
User: "Fix the login bug on dashboard"

Server:
  1. recall("login bug dashboard") → finds recent error logs, related work memory
  2. Determines: need to spawn agent for dashboard project
  3. Constructs task with context from memory
  4. Spawns agent with: task description + project context + relevant learnings
  5. Updates state machine: new task in_progress
  6. Responds: "I've started an agent on the login bug. I found some related
     error logs from yesterday — passing those along."
```

## Data Model Additions

```python
# Persistent memory entry
class Memory:
    id: str
    scope: str           # "user", "project:{name}", "session:{id}"
    category: str        # "preference", "decision", "work", "context"
    key: str             # searchable identifier
    value: str           # the memory content
    metadata: dict       # structured data (project, branch, agent, etc.)
    confidence: float    # 0-1, decays for session/work memories
    created_at: str
    accessed_at: str     # last time recalled
    expires_at: str | None  # for session-scoped memories
    source: str          # "voice", "agent", "ui", "system"

# Intent (logged for learning)
class Intent:
    id: str
    raw_input: str       # "deploy invoiceapp to staging"
    channel: str         # "voice", "mobile", "desktop", "mcp", "cli"
    category: str        # "query", "action", "delegate", "remember"
    parsed: dict         # { action: "deploy", project: "invoiceapp", target: "staging" }
    verification: str    # "none", "pending", "approved", "rejected"
    result: str | None   # outcome after execution
    created_at: str
```

## Open Questions

- **Wake word vs push-to-talk?** Always listening requires a wake word ("Hey ADT") or VAD (voice activity detection). Push-to-talk is simpler but less natural.
- **Which model for intent parsing?** Local (fast, private) vs API (accurate, latent). Could do hybrid — local for fast classification, API for complex intents.
- **Memory capacity limits?** How much memory before it becomes noise? Need relevance scoring and decay.
- **Multi-user?** Currently single-user. Memory and preferences are user-scoped but the system assumes one user. Multi-user adds auth + memory isolation.
- **Offline voice?** Deepgram requires internet. Whisper.cpp could work locally for basic commands.
- **Verification UX for voice?** "Confirm?" / "Yes" works but can be annoying. Maybe confidence-based — high confidence actions auto-execute, low confidence actions ask.

## Build Phases

**Phase 1: Persistent memory (DB + API)**
- Memory model + SQLite table
- CRUD endpoints (remember, recall, forget)
- Memory decay background task
- Basic recall by keyword/scope

**Phase 2: Memory integration with existing features**
- State machine records decisions to work memory automatically
- Agent completions stored as work memory
- Task outcomes stored as work memory
- Mobile/dashboard can display relevant memories

**Phase 3: Intent engine**
- Intent parser (fast classification tier)
- Router (query/action/delegate/remember)
- Verification gate for destructive actions
- Wire up voice input → intent → action pipeline

**Phase 4: Voice loop**
- Continuous listening mode (mobile + desktop)
- TTS responses
- Channel-aware response routing
- Conversation context (multi-turn voice)

**Phase 5: Cross-channel orchestration**
- All channels producing intents through same pipeline
- Channel-aware response broadcasting
- Agent spawning from intents
- Memory-grounded agent context
