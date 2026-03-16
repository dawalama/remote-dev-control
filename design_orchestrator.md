# Orchestrator — Product Direction

## The Workflow

This is the target experience. Everything else is in service of making this feel natural.

### Scenario: Testing mindshare-monitor

```
1. USER (voice/chat): "Hey, I want to focus on the mindshare project"

   ORCHESTRATOR:
     - Fuzzy match "mindshare project" → mindshare-monitor (in trenches collection)
     - recall() → last session was 3 days ago, was working on Twitter integration
     - Set active project in state machine

   RESPONSE: "Focusing on mindshare-monitor. Last time you were working on
   the Twitter feed integration. What do you want to do?"

2. USER: "Can you load up the project, and test it against one of the top
   coins in dexscreener?"

   ORCHESTRATOR decomposes into steps:
     a. Start API process for mindshare-monitor       → process manager
     b. Start web frontend for mindshare-monitor      → process manager
     c. Wait for both to be healthy                    → health checks
     d. Find a top coin CA from dexscreener            → agent task
     e. Enter the CA into the running webapp           → browser automation
     f. Capture screenshot / get preview link          → existing screenshot system
     g. Send to user via Slack/TG + share preview link → notification system
     h. Confirm with user                              → verification

   RESPONSE: "Starting the API and web server. I'll find a trending coin
   from dexscreener and test it. Give me a minute."

   [executes steps a-g, streaming status updates to mobile/desktop]

   RESPONSE: "Here's mindshare-monitor running with $PEPE
   (0x6982...3fad). Preview: https://rdc.local/preview/mindshare-monitor"
   [sends screenshot to Slack/TG]

3. USER (looking at preview): "Hey the twitter list can be improved.
   It does not load. Can you try to fix it?"

   ORCHESTRATOR:
     - Understands context: user is looking at mindshare-monitor preview
     - The complaint is about the Twitter list component
     - Captures screenshot of current state as context
     - Constructs agent prompt with:
       * What the user said
       * Screenshot of current state
       * Project context (from knowledge system)
       * Relevant learnings about Twitter API
     - Opens terminal with agent
     - Shares terminal link to mobile

   RESPONSE: "I see the Twitter list issue. Opening an agent to fix it.
   Here's the terminal if you want to watch or take over:
   https://rdc.local/mobile#terminal/mindshare-monitor"

4. USER can now:
   - Watch the agent work via mobile terminal overlay
   - Take control by typing in the terminal
   - Give more voice instructions: "focus on the API response, I think
     the Twitter endpoint is returning empty"
   - Walk away and get notified when done
```

## What This Implies

### The orchestrator is a conversation, not a command parser

The key difference from a CLI or chatbot: the orchestrator maintains **multi-turn context** and can decompose a vague request into concrete steps. "Load up the project and test it" is 7+ distinct operations. The user shouldn't have to specify each one.

```
Traditional:
  rdc start mindshare-monitor --api --web
  rdc agent run "find top coin on dexscreener"
  rdc browser open mindshare-monitor
  rdc browser input --selector="#ca-input" --value="0x..."
  rdc screenshot mindshare-monitor
  rdc notify slack --image=screenshot.png

Orchestrator:
  "Load it up and test it against a top coin on dexscreener"
```

### The orchestrator needs a plan-execute-monitor loop

```
┌─────────────────────────────────────────────────┐
│              Orchestrator Loop                   │
│                                                  │
│  Input ──→ Understand ──→ Plan ──→ Execute ──→  │
│    ↑        (+ memory     (decompose  (steps     │
│    │         recall)       into       run in     │
│    │                       steps)     sequence   │
│    │                                  or         │
│    │                                  parallel)  │
│    │                                     │       │
│    │         Monitor ←───────────────────┘       │
│    │           │                                 │
│    │           ├─→ Step succeeded → next step    │
│    │           ├─→ Step failed → recover or ask  │
│    │           ├─→ Need user input → ask + wait  │
│    │           └─→ All done → report + remember  │
│    │                                             │
│    └──── User follow-up ←── Report ──────────────│
└─────────────────────────────────────────────────┘
```

### What already exists vs what's needed

| Capability | Exists? | Where |
|---|---|---|
| Start/stop processes per project | Yes | process manager, state machine |
| Terminal with agent | Yes | terminal.py, PTY management |
| Browser automation / screenshots | Yes | browser sessions, CDP |
| Preview page with live view | Yes | screencast viewer |
| Mobile with terminal overlay | Yes | mobile_page.py |
| Notifications | Mostly | mobile/dashboard via WS; add SMS deep link |
| Project context / knowledge | Planned | design_knowledge_system.md |
| Persistent memory | Planned | design_persistent_memory_and_voice.md |
| **Conversation context** | **No** | **Need: multi-turn dialogue state** |
| **Intent decomposition** | **No** | **Need: LLM-powered planner** |
| **Step execution engine** | **No** | **Need: sequential/parallel step runner** |
| **Web research agent** | **No** | **Need: NanoClaw — lightweight LLM + tools agent** |

### The three missing pieces

**1. Conversation Manager**
Maintains multi-turn dialogue state. Knows what was said, what's in progress, what's being waited on. Not just chat history — structured state:

```python
class Conversation:
    id: str
    project: str | None          # active project context
    turns: list[Turn]            # conversation history
    active_plan: Plan | None     # currently executing plan
    pending_question: str | None # waiting for user response
    channel: str                 # voice, mobile, desktop, slack
```

**2. Planner (LLM-powered)**
Takes a user request + conversation context + memory + project state → produces a plan (list of steps). This is where the LLM does the heavy lifting. The planner sees:

- User's request
- Active project and its state (processes running? which ones?)
- Conversation history (what led to this request)
- Available capabilities (what can the system actually do?)
- Relevant memories (last time user tested this project, what happened)

And outputs:

```python
class Plan:
    steps: list[Step]
    explanation: str  # "I'll start the servers, find a coin, and test it"

class Step:
    id: str
    action: str       # "start_process", "agent_task", "browser_action",
                      # "screenshot", "notify", "ask_user"
    params: dict      # action-specific parameters
    depends_on: list  # step IDs that must complete first
    on_failure: str   # "abort", "skip", "ask_user"
```

**3. Executor**
Runs plan steps using existing systems. Maps step actions to actual operations:

```python
STEP_HANDLERS = {
    "start_process":   → process manager (existing)
    "stop_process":    → process manager (existing)
    "wait_healthy":    → health check loop (new, simple)
    "agent_task":      → spawn agent with prompt (existing AgentManager)
    "browser_open":    → browser session (existing)
    "browser_action":  → CDP commands (existing)
    "screenshot":      → screenshot system (existing)
    "notify":          → Slack/TG/push (needs connectors)
    "ask_user":        → pause, send question, wait for response
    "terminal_open":   → terminal.py (existing)
    "share_link":      → generate + send link (new, simple)
    "remember":        → persistent memory (planned)
}
```

Most of the actual capabilities exist. The executor is mostly **glue** — calling existing systems in the right order.

**4. Web Research Agent (NanoClaw)**

For steps like "find a top coin on dexscreener" — the system needs a lightweight, independent agent that can do web research, extract data, and return results. Different from code-editing agents (cursor-agent).

Reference projects:
- [nanobot](https://github.com/HKUDS/nanobot) — ultra-lightweight assistant (~3.5k lines), multi-provider, persistent memory
- [picoclaw](https://github.com/sipeed/picoclaw) — Go, single binary <10MB, <1s startup, runs on $10 hardware. Multi-provider (OpenRouter, Groq, Anthropic, etc.), workspace-based memory/skills. Proves the "spawn a lightweight subprocess" model works at extreme scale.
- [SmolAgents](https://huggingface.co/docs/smolagents) — ~1k lines, code-first (LLM writes Python, not JSON), minimal abstraction
- [OpenAI Swarm](https://github.com/openai/swarm) — lightweight multi-agent with handoffs, minimal middleware
- [MCP-Agent](https://github.com/lastmile-ai/mcp-agent) — composable patterns (router, orchestrator, map-reduce) over MCP

Key takeaway across all: the best lightweight agents share three traits — (1) single-file or single-binary deployment, (2) multi-provider behind one interface so model routing is trivial, (3) minimal context per step instead of dragging full history. PicoClaw and nanobot validate that you don't need a framework — a small, focused agent with tools is enough.

Design principles:
- **Simple**: one Python class, no heavy framework. Spawned by the orchestrator, returns structured results.
- **Independent**: runs its own event loop, doesn't block the orchestrator. Communicates via callback or queue.
- **Tool-equipped**: small set of tools — fetch URL, extract data, call API, parse HTML. Not a full browser automation suite.
- **Managed**: orchestrator spawns it, monitors it, kills it if it takes too long. Same lifecycle as any other step in a plan.

### Token Cost Optimization

The biggest cost sink in agents is calling expensive models for every step. Most agent steps don't need frontier-level reasoning. The strategy:

**OpenRouter as the single provider** — one API key, one endpoint, model routing is just a string swap:

```python
OPENROUTER_BASE = "https://openrouter.ai/api/v1"
OPENROUTER_KEY = os.environ["OPENROUTER_API_KEY"]  # one key for everything

async def llm_call(model: str, messages: list, tools: list = None) -> dict:
    """Single function for all LLM calls. Model routing = changing the string."""
    resp = await httpx.post(
        f"{OPENROUTER_BASE}/chat/completions",
        headers={"Authorization": f"Bearer {OPENROUTER_KEY}"},
        json={
            "model": model,
            "messages": messages,
            "tools": tools,
        },
    )
    return resp.json()
```

**Tiered model routing** — match model capability to step complexity:

```
┌──────────────────────────────────────────────────────────────────┐
│  Step Complexity          Model (via OpenRouter)       $/M in   │
│                                                                  │
│  Tool selection,          google/gemini-2.0-flash      $0.10    │
│  URL construction,        deepseek/deepseek-chat-v3    $0.27    │
│  JSON extraction          anthropic/claude-3.5-haiku   $0.80    │
│                                                                  │
│  Multi-step reasoning,    anthropic/claude-sonnet-4    $3.00    │
│  ambiguous queries,       openai/gpt-4o               $2.50    │
│  error recovery                                                  │
│                                                                  │
│  Complex planning,        anthropic/claude-opus-4      $15.00   │
│  novel situations,        openai/o1                    $15.00   │
│  multi-source synthesis   (only when escalating)                │
└──────────────────────────────────────────────────────────────────┘
```

Default tiers (configurable in settings):

```python
MODEL_TIERS = {
    "cheap":  "google/gemini-2.0-flash",       # extraction, formatting, simple tool use
    "mid":    "anthropic/claude-sonnet-4",      # reasoning, planning, error recovery
    "high":   "anthropic/claude-opus-4",        # escalation only
}
```

The router decides which tier to use BEFORE calling the LLM. Simple heuristic:
- Has tools returned data? → cheap model to extract/format
- Is this the first planning step? → mid-tier model
- Did a previous step fail? → escalate to higher tier
- Is the user's request ambiguous? → mid or high tier

**Compacted context sharing** — don't pass full conversation history to every step:

```python
class StepContext:
    """Minimal context passed to each agent step. NOT the full history."""
    task: str              # "Find top trending coin CA on dexscreener"
    relevant_results: dict # only outputs from prior steps this step depends on
    constraints: list[str] # "return just the contract address", "use API if available"
    budget: int            # remaining token budget for this step

# BAD: pass entire conversation + all prior steps (10k+ tokens)
# GOOD: pass only what this step needs (~500 tokens)
```

Each step gets a **compacted context** — just the task, the outputs it depends on, and constraints. Not the full agent memory. The orchestrator manages the full state; individual steps see only what they need.

**Tool-first, LLM-second** — avoid LLM calls when a tool can answer directly:

```python
# Before calling LLM: check if a tool can handle it deterministically
async def smart_step(task, context):
    # Pattern match known task types
    if "dexscreener" in task and "trending" in task:
        # Direct API call, no LLM needed
        data = await web_fetch("https://api.dexscreener.com/token-boosts/top/v1")
        return {"ca": data[0]["tokenAddress"], "name": data[0]["symbol"]}

    # Fall back to LLM only when needed
    return await llm_step(task, context, model="haiku")
```

For well-known data sources (APIs with predictable schemas), skip the LLM entirely. Maintain a registry of "known tools" that map task patterns to direct API calls. The LLM is only needed for novel tasks or when direct tools fail.

**Token budget enforcement:**

```python
class AgentBudget:
    max_tokens: int = 5000       # total budget for this agent run
    used_tokens: int = 0
    steps: int = 0
    max_steps: int = 10          # hard limit on steps
    escalation_threshold: int = 2 # escalate after N consecutive failures

    def can_continue(self) -> bool:
        return self.used_tokens < self.max_tokens and self.steps < self.max_steps

    def select_tier(self, failures: int) -> str:
        if failures >= self.escalation_threshold:
            return "high"   # → opus/o1 via OpenRouter
        if self.steps == 0:
            return "mid"    # → sonnet for initial planning
        return "cheap"      # → flash/haiku for tool use and extraction

    def record(self, tokens: int):
        self.used_tokens += tokens
        self.steps += 1
```

### Estimated cost per research task

```
"Find top trending coin on dexscreener":
  - Direct API call (no LLM): $0.00
  - With haiku for parsing:    $0.001
  - With sonnet fallback:      $0.01

"Research competitor pricing for SaaS product":
  - 3-4 web fetches + haiku extraction:  $0.005
  - 1 sonnet synthesis step:             $0.01
  - Total:                               ~$0.015

vs naive approach (opus for every step):  $0.30-0.50
```

### Implementation

```python
class NanoClaw:
    """Lightweight research agent. Token-efficient, routed via OpenRouter."""

    tools = [web_fetch, api_call, parse_html, extract_json]
    known_sources = {
        "dexscreener": dexscreener_direct,
        "coingecko": coingecko_direct,
        # ... registry of direct API handlers
    }

    def __init__(self, openrouter_key: str, tiers: dict = None):
        self.openrouter_key = openrouter_key
        self.tiers = tiers or MODEL_TIERS  # cheap/mid/high model strings

    async def run(self, task: str, context: StepContext) -> dict:
        budget = AgentBudget(max_tokens=context.budget or 5000)

        # 1. Check if a known tool can handle it directly (no LLM, $0.00)
        for pattern, handler in self.known_sources.items():
            if pattern in task.lower():
                try:
                    return await handler(task, context)
                except Exception:
                    pass  # fall through to LLM

        # 2. LLM loop — all calls go through OpenRouter, model varies by tier
        failures = 0
        while budget.can_continue():
            tier = budget.select_tier(failures)
            model = self.tiers[tier]

            result = await llm_call(
                model=model,
                messages=[
                    {"role": "system", "content": "You are a research agent. Use tools to find information. Return structured results. Be concise."},
                    {"role": "user", "content": f"Task: {task}\n\nContext: {context.compact()}"},
                ],
                tools=self.tools,
            )

            tokens_used = result.get("usage", {}).get("total_tokens", 0)
            budget.record(tokens_used)

            if result_is_complete(result):
                return extract_structured_result(result)

            # Intermediate result — compact and continue
            context = context.with_result(extract_intermediate(result))
            failures += 1 if is_failure(result) else 0

        return {"error": "budget exhausted", "partial": context.results}
```

For tasks that need actual browser interaction (filling forms, clicking), use the existing CDP/browser automation. NanoClaw is for data retrieval — faster, cheaper, no browser overhead.

Progression:
- v1: Direct API handlers for known sources + haiku for unknown. ~$0.001-0.01 per task.
- v2: Add headless browser fallback for JS-rendered sites.
- v3: Tool registry grows based on actual usage. Most common tasks become zero-LLM.

## Channel Routing

The orchestrator is channel-agnostic in planning, channel-aware in communication:

```
Voice call:
  Input: speech → STT → text
  Output: text → TTS → speech
  Status updates: spoken summaries ("servers are starting")
  Handoff: "I'll send the link to your phone"

Mobile:
  Input: text or voice
  Output: cards, notifications, terminal overlay
  Status updates: progress indicators on screen
  Handoff: terminal overlay opens automatically

Desktop dashboard:
  Input: command bar, chat panel
  Output: UI updates, toast notifications
  Status updates: state machine broadcasts → UI re-renders

Slack/TG:
  Input: messages in a channel/DM
  Output: messages, screenshots, links
  Status updates: threaded replies
  Handoff: "opening terminal, here's the link"
```

### Cross-channel handoff

The scenario demonstrates natural channel handoffs:
1. Start on voice → "focusing on mindshare-monitor"
2. Results delivered to Slack/TG → screenshot + preview link
3. User opens preview on mobile/desktop → sees the running app
4. Follow-up on voice → "twitter list doesn't load"
5. Terminal link sent to mobile → user watches/takes over

The orchestrator tracks which channels are active and routes responses to the right place. The state machine broadcasts ensure all channels stay in sync.

## Notifications — Use What We Have

Don't build Slack/TG connectors. The mobile page IS the notification channel.

The simplest path: **text/SMS the user a link to `/mobile`** with the right context. The mobile page already renders projects, terminals, previews, screenshots — everything the user needs to see.

```
Orchestrator completes a task:
  → Text user: "mindshare-monitor tested with $PEPE — https://rdc.host/mobile#preview/mindshare-monitor"
  → User taps link → mobile page opens with preview
  → OR: mobile page already open → push state update via existing WS

Desktop:
  → Dashboard already receives state machine broadcasts
  → Toast notification + attention card appears automatically
```

The notification system is just:
1. **In-app** (already exists): state machine broadcasts → mobile/dashboard update
2. **SMS/text link** (simple): send a deep link to `/mobile#context`. One API call to Twilio or similar.

No need for Slack/TG bot infrastructure. The mobile page is the universal client.

## Verification & Control

### When to verify

```
Auto-execute (no verification):
  - Start/stop dev processes
  - Open terminals
  - Take screenshots
  - Query status
  - Search/recall memory

Verify first:
  - Deploy to staging/production
  - Send external notifications (first time)
  - Delete data
  - Git operations (push, force operations)
  - Spending money (API calls with cost)

Always verify:
  - Deploy to production
  - Destructive database operations
```

### User control levels

```
"Let the agent handle it"     → fully autonomous, notify on completion
"Let me watch"                → share terminal, agent drives
"Let me drive"                → user takes terminal control
"Stop"                        → halt current plan, preserve state
"What's it doing?"            → status report of current plan/steps
```

## Example: Full Orchestrator Flow (Internal)

```
USER: "Can you load up the project, and test it against one of the top
       coins in dexscreener?"

ORCHESTRATOR receives intent:
  raw: "load up the project, test against top coin dexscreener"
  conversation_context: { active_project: "mindshare-monitor" }
  memory_recall: [
    "mindshare-monitor has processes: api (port 8000), web (port 3000)",
    "last test used $WIF contract address",
    "web app has input field for contract address on main page"
  ]

PLANNER (LLM call with capabilities list + context):
  Plan:
    1. start_process(project="mindshare-monitor", process="api")
    2. start_process(project="mindshare-monitor", process="web")
    3. wait_healthy(urls=["localhost:8000/health", "localhost:3000"])
       depends_on: [1, 2]
    4. agent_task(prompt="Find the contract address of a top trending
       coin on dexscreener.com. Return just the CA.", type="web_research")
       depends_on: []  # can run in parallel with 1-3
    5. browser_open(url="localhost:3000")
       depends_on: [3]
    6. browser_action(selector="#ca-input", action="fill", value="{step4.result}")
       depends_on: [4, 5]
    7. browser_action(selector="#submit-btn", action="click")
       depends_on: [6]
    8. wait(seconds=3)  # let page load results
       depends_on: [7]
    9. screenshot(project="mindshare-monitor", full_page=true)
       depends_on: [8]
    10. share_link(type="preview", project="mindshare-monitor")
        depends_on: [5]
    11. notify(message="mindshare-monitor tested with {step4.coin_name}",
              link="{step10.url}")
        depends_on: [9, 10]
        # → pushes state update to mobile/dashboard via WS
        # → if user not connected, texts SMS deep link to /mobile

EXECUTOR runs steps, streaming status:
  "Starting API server..."
  "Starting web server..."
  "Looking up trending coins on dexscreener..."
  "Found $PEPE (0x6982...3fad) — entering into the app..."
  "Screenshot captured. Sending to Slack."
  "Done. Preview: https://rdc.local/preview/mindshare-monitor"
```

## Architecture Summary

```
┌──────────────────────────────────────────────────────────┐
│                    ADT Server                            │
│                                                          │
│  ┌─────────┐  ┌───────────┐  ┌──────────┐  ┌─────────┐ │
│  │ Voice   │  │ Mobile    │  │ Desktop  │  │ Slack/  │ │
│  │ (STT)   │  │ (WS+HTTP) │  │ (WS+HTTP)│  │ TG Bot  │ │
│  └────┬────┘  └─────┬─────┘  └────┬─────┘  └────┬────┘ │
│       │             │              │              │      │
│       └──────┬──────┴──────┬───────┴──────┬───────┘      │
│              │             │              │              │
│         ┌────▼─────────────▼──────────────▼────┐        │
│         │         Conversation Manager          │        │
│         │    (multi-turn, channel-aware)         │        │
│         └────────────────┬──────────────────────┘        │
│                          │                               │
│                    ┌─────▼─────┐                         │
│                    │  Planner  │ (LLM: decompose intent  │
│                    │           │  into executable steps)  │
│                    └─────┬─────┘                         │
│                          │                               │
│                    ┌─────▼─────┐                         │
│                    │ Executor  │ (run steps, monitor,     │
│                    │           │  handle failures)        │
│                    └─────┬─────┘                         │
│                          │                               │
│    ┌──────────┬──────────┼──────────┬──────────┐        │
│    ▼          ▼          ▼          ▼          ▼        │
│  Process   Terminal   Browser   Agents   Notify        │
│  Manager   (PTY)      (CDP)     (spawn)  (Slack/TG)    │
│                                                          │
│  ┌──────────────────────────────────────────────┐       │
│  │            State Machine                      │       │
│  │  (single source of truth, broadcasts to all)  │       │
│  └──────────────────────────────────────────────┘       │
│                                                          │
│  ┌──────────────────────────────────────────────┐       │
│  │         Persistent Memory + Knowledge         │       │
│  │   (user prefs, work history, project context) │       │
│  └──────────────────────────────────────────────┘       │
└──────────────────────────────────────────────────────────┘
```

## Current Chat/Voice Problem & Fix

### What happens now

Voice and text input go through two broken layers:

**Layer 1: Client-side regex matching** (`mobile_page.py` `sendCommand()`, line 1882):
```
"select <project>" → selectProject()
"open terminal"    → spawnTerminal()
"task: <text>"     → create task
everything else    → fallback to voice_command WS event
```

This is brittle — "manage collections" doesn't match any regex, falls through to the WS fallback.

**Layer 2: Chat LLM with `[ACTION:...]` patterns** (`app.py`, line 2624):
The LLM sees a hardcoded list of 4 actions + 3 UI actions:
```
[ACTION:start_process:id]
[ACTION:stop_process:id]
[ACTION:create_task:title|description]
[ACTION:start_preview:id]
[UI_ACTION:show_tab:tab]
[UI_ACTION:select_project:name]
[UI_ACTION:open_task_modal]
```

When the user says "manage collections", the LLM has no "navigate to page" or "open settings" action. So it picks the closest thing — `create_task` — which is wrong.

**The root cause:** Two dumb layers (regex + limited action list) instead of one smart layer (the orchestrator).

### What should happen

ALL user input (voice, chat, mobile commands) goes to the orchestrator. No client-side regex. No hardcoded action patterns.

```
BEFORE:
  voice/text → client regex → (miss) → chat LLM → [ACTION:guess_wrong]

AFTER:
  voice/text → orchestrator → understand → plan → execute
```

The orchestrator has full context:
- All available capabilities (not just 4 hardcoded actions)
- Active project, conversation history, memory
- Knowledge of all pages/routes ("/settings/projects", "/mobile", etc.)
- Process list, terminal state, browser sessions

### Migration path

Phase 1: **Expand the action vocabulary** (quick fix, before full orchestrator):
- Add missing actions: `navigate`, `open_settings`, `open_terminal`, `manage_collections`, `search_projects`, `open_preview`
- Update the chat system prompt with all available actions
- This is a band-aid but immediately fixes "manage collections" type failures

Phase 2: **Route everything through one endpoint** (`POST /orchestrator`):
- Replace client-side regex in `sendCommand()` with a single call to the orchestrator
- Replace the chat endpoint's `[ACTION:...]` parsing with the orchestrator's plan-execute model
- All channels (mobile command bar, desktop chat, voice) call the same endpoint

Phase 3: **Full orchestrator** with conversation context, memory recall, multi-step plans

The key insight: the orchestrator endpoint replaces BOTH the client-side regex matching AND the chat's `[ACTION:...]` system. One smart layer instead of two dumb ones.

### What the orchestrator knows (that the current chat doesn't)

```python
# Current chat sees 4 actions. Orchestrator sees everything:
ORCHESTRATOR_CAPABILITIES = {
    # Navigation
    "navigate": ["dashboard (/)", "settings (/settings/projects)", "mobile (/mobile)", "debug (/debug)"],

    # Project management
    "select_project": "switch active project",
    "search_projects": "open Cmd+K project search",
    "add_project": "open add project dialog",
    "manage_collections": "open settings page → collections section",

    # Processes
    "start_process": "start a project process",
    "stop_process": "stop a running process",
    "list_processes": "show process status",

    # Terminals
    "open_terminal": "open terminal for project",
    "send_to_terminal": "send text to active terminal",

    # Tasks
    "create_task": "create a new task",
    "list_tasks": "show tasks for project",
    "run_task": "execute a pending task",

    # Agents
    "spawn_agent": "start an agent on a project",
    "stop_agent": "stop a running agent",

    # Browser/Preview
    "open_preview": "open browser preview for project",
    "take_screenshot": "capture screenshot",

    # UI
    "show_tab": "switch dashboard tab",
    "show_logs": "open live logs",
    "show_screenshots": "open screenshots panel",

    # Knowledge (planned)
    "remember": "store in persistent memory",
    "recall": "search memory",
    "add_learning": "capture a project learning",
    "add_rule": "add a project rule",
}
```

With this vocabulary, "manage collections" → `navigate:settings` or `manage_collections` — obvious, not ambiguous.

## Build Order

Given the existing codebase, the fastest path to the scenario working:

1. **Conversation manager** — multi-turn state, active project tracking
2. **Planner** — LLM call that produces step lists from natural language
3. **Executor** — step runner that calls existing systems (most capabilities already exist)
4. **NanoClaw v1** — lightweight research agent (LLM + web_fetch + JSON parse)
5. **SMS link notification** — text a `/mobile#context` deep link (Twilio, one endpoint)
6. **Voice loop integration** — wire STT → conversation manager
7. **Memory integration** — recall context to improve planning
