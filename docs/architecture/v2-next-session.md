# RDC v2 — Next Session Notes

## What's Done (rdc-v2 branch, 12 commits)

### Backend
- Channel data model: channels, channel_projects, channel_messages, terminal_channels, events (FTS5)
- Channel manager with CRUD, messages, terminal linking, event store
- Channel API: 12 endpoints (CRUD, messages, terminals, events search, delete)
- Auto-create default channel per project (on startup + on project add)
- Project name resolution server-side (accepts names, resolves to UUIDs)
- API returns project_names + collection_ids per channel for frontend filtering

### Frontend
- Channel store (Zustand): CRUD, messages, archive, delete, auto-mode toggle
- Channel sidebar: flat/grouped toggle, collection dropdown, active filter, activity dots
- Channel settings: hover "..." → rename, archive, delete, project info
- Channel panel: bottom-docked, toggleable, routes through /orchestrator
- Focus retained on input after send
- ChatFAB removed from desktop layout
- Chat icon removed from command bar
- Project bar removed (channels replace it)

### Layout
```
[Channel Sidebar] [Terminal (full width)]     [Right Tabs]
                  [Channel Panel (bottom)]
```

## What's Next (Priority Order)

### 1. Rich Channel Messages + UI Action Dispatch
The channel panel currently only shows text. It needs:

**UI Actions** (what ChatFAB did):
- Orchestrator returns `actions` array (e.g. `{type: "show_tab", tab: "tasks"}`)
- These need to fire the same dispatchers (useUIStore.setTab, etc.)
- Wire the orchestrator response handler to check for actions and execute them

**Rich Rendering**:
- Messages can contain structured content in `metadata`
- Task list, diff viewer, approval buttons, terminal output snippets
- Use the existing `json-render` Spec system or a simpler component switch

### 2. Make Everything Channel-First
Currently the entire frontend reads `currentProject` from project store. Should read `activeChannelId` from channel store and derive project(s) from channel.

Key refactors:
- EmbeddedTerminal: show terminals linked to active channel (not just by project name)
- RightTabs: filter actions/tasks/activity by channel's projects
- Process list: filter by channel context
- Terminal spawn: auto-link new terminal to active channel

### 3. Mobile + Kiosk Layouts
- Mobile: channel list → tap → workspace with tabs (chat, terminal, mission)
- Kiosk: channel sidebar (collapsible) + workspace
- Both need ChannelSidebar + ChannelPanel components (already built)

### 4. Terminal ↔ Channel Wiring
- Spawning a terminal auto-links it to the active channel
- Terminal tab shows channel membership
- Switching channels switches visible terminals

### 5. Event Store Emission
- Emit structured events from terminal output, orchestrator, system
- Wire into existing code paths
- Basic search API already built

### 6. Offline Message Queue
- Queue messages in localStorage/IndexedDB when offline
- Sync on WebSocket reconnect
- `synced` flag already in DB schema

## Design Decisions Captured
- Channels are workspaces (layout + messages), not just message threads
- Channel panel docks at bottom (like VS Code terminal panel)
- Focus-based input routing (click terminal → terminal, click panel → orchestrator)
- Collection filter in sidebar replaces project bar
- Active filter shows channels with running terminals/processes
- System channels show in all collections
- Everything should become channel-first (derive project from channel, not vice versa)
