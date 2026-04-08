# RDC v2 — Next Session Notes

## What's Done

### Agent Session System
- Session model: multi-terminal, DB-persisted, event timeline (events table)
- Terminal output monitor: per-terminal polling, idle/prompt completion detection
- Session viewer: full-width in desktop, overlay on mobile/kiosk
- Session viewer tabs: Summary (git changes), Timeline (events), Log (terminal output)
- Mark Done: kills terminals, saves log, generates summary
- Session retry, delete endpoints
- Sessions tab replaces Tasks in all layouts
- Session recovery on server restart
- Agent terminal labels: "Agent: task description..."

### Orchestrator Intelligence
- Multi-turn tool loop (max 5): LLM → tools → feed back → synthesize
- Tools: run_command, read_file, write_file, edit_file
- Tools: list/switch/create/archive/delete workstreams
- present_ui: LLM returns A2UI interactive components
- Forced synthesis when tool loop exhausts + dedup for repeated reads
- Async mode: returns immediately, posts result to channel
- Fallback chain: Qwen free → Gemini Flash → GPT-4o Mini on 429
- System prompt: intelligence focus, spawn_agent for complex tasks

### A2UI Component System
- 9 components: text, code, actions, confirm, input, progress, diff, file_list, task_card
- Interactive feedback loop: button clicks → orchestrator
- Thinking indicator while waiting
- Friendly labels for action responses

### Desktop Floating Chat Panel
- Floating panel (bottom-right), half/expanded
- Chat toggle in command bar (Cmd+/)
- Persists open/closed state, scrolls to latest

### Code Quality
- Shared utils.py: strip_ansi, lazy accessors, JSON helpers
- useChannelSend hook: shared across 3 channel panels
- SessionStatus enum (Python + TypeScript)
- Batch session queries (N+1 eliminated)

### Infrastructure
- Workstream persistence across refresh
- Active filter across all collections
- Model routing: Qwen 3.6 Plus / MiniMax M2.7 / gemma4
- Theme fixes for accent containers

## Known Issues (Fix First)

### 1. Terminal Visibility After Session Spawn
- New terminals created via spawn_agent don't appear until next state broadcast
- Root cause: tm.create() outside /terminals API doesn't trigger immediate broadcast
- Fix: ensure _broadcast_state_sync runs AFTER terminal is fully registered

### 2. Session Completion Detection
- Shell prompt detection works but has edge cases (custom prompts, slow startup)
- output_seen flag prevents false positives but delays detection
- Future: use exit signal wrapping (trap EXIT) for instant detection

### 3. Log Cleanup
- Claude Code output has box-drawing chars, spinners that survive ANSI stripping
- Need more aggressive terminal output normalization

### 4. Polling vs WebSocket Push
- Message polling (2s) and session refresh are HTTP-based
- Should use WebSocket events: new_message, session_updated
- Would eliminate polling entirely

## What's Next (Priority Order)

### 1. File Drop in Chat
- Drag-and-drop files into message panel
- Upload to project context, orchestrator can reference

### 2. WebSocket Push for Messages + Sessions
- Eliminate polling: server pushes new messages via existing WS
- Session status changes broadcast to clients
- Terminal creation events reach frontend instantly

### 3. Session Exit Signal
- `trap 'echo __RDC_EXIT:$?' EXIT; claude ...`
- Instant completion detection

### 4. Orchestrator → Session Bridge
- Tool calls logged to active session timeline
- API-direct work visible alongside terminal work

### 5. Observer System
- Periodic batch analysis of event store
- Rules, patterns, reports → #system/observer
