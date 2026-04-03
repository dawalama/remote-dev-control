# RDC v2 — Next Session Notes

## What's Done (rdc-v2 branch)

- Channel data model: channels, channel_projects, channel_messages, terminal_channels, events (FTS5)
- Channel manager with CRUD, messages, terminal linking, event store
- Channel API: 10 endpoints (CRUD, messages, terminals, events search)
- Auto-create default channel per project (on startup + on project add)
- Channel sidebar: flat/grouped toggle, collection filter, active filter, activity dots
- Channel messages: routed through /orchestrator with project context
- Desktop layout: channel sidebar + workspace (terminal + messages) + right tabs
- Right-click context menu for archive

## What's Next

### Priority 1: Rich Channel Messages + Deprecate ChatFAB

The channel message pane should replace ChatFAB as the single chat interface.

**Two capabilities in one pane:**

1. **UI Actions** (what ChatFAB does today):
   - Orchestrator returns structured actions like `{"type": "show_tab", "tab": "tasks"}`
   - These fire the same dispatchers (useUIStore.setTab, etc.)
   - The UI responds: tabs switch, terminals open, modals show
   
2. **Rich Message Rendering** (new):
   - Messages can contain structured content, not just text
   - Task list embedded in a message bubble
   - Terminal preview / output snippet
   - Approval buttons (approve/reject inline)
   - Diff viewer
   - File attachment / screenshot
   - Mission progress indicator

**Implementation approach:**
- Extend `ChannelMessage.metadata` to carry structured content types
- Create a `MessageRenderer` component that switches on content type
- Existing `json-render` Spec system could work here (already used in browser agent)
- Wire the orchestrator's action dispatch into the channel message handler
- Remove ChatFAB from all layouts once channel messages handle everything

### Priority 2: Mobile + Kiosk Layouts

- Mobile: channel list → tap → workspace with tabs (chat, terminal, mission)
- Kiosk: channel sidebar (collapsible) + workspace
- Both need the channel store + sidebar components (already built, just need wiring)

### Priority 3: Terminal ↔ Channel Wiring

- Spawning a terminal in a channel auto-links it
- Terminal tab shows which channel(s) it belongs to
- Switching channels switches visible terminals

### Priority 4: Event Store

- Emit structured events from terminal, orchestrator, system
- Basic search API (already built)
- Wire event emission into existing code paths

## Design Decisions to Carry Forward

- ChatFAB is deprecated, channel messages replace it
- Channel messages support BOTH rich rendering AND ui action dispatch
- Focus-based input routing (click terminal = terminal input, click chat = orchestrator)
- Collection filter in channel sidebar replaces project bar
- Active filter shows only channels with running terminals/processes
- Right-click context menu for channel management (archive, etc.)
