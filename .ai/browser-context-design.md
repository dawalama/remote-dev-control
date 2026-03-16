# Browser Context & Preview System

## Overview

RDC provides shared browser sessions between the developer and AI agents, with context capture (screenshot + accessibility tree) for feeding visual information to agents.

## Architecture

```
Dashboard (iframe viewer)
    ↓ session_id
RDC Server (FastAPI)
    ├── BrowserManager (browser.py)
    │     ├── create_session(target_url)
    │     ├── capture_context(session_id)
    │     └── stop_session(session_id)
    ↓ CDP (WebSocket)
Browserless Docker Container
    (ghcr.io/browserless/chromium)
```

## Components

### Browser Sessions
- Browserless Docker containers with headless Chromium
- CDP (Chrome DevTools Protocol) for programmatic access
- Live viewer embeddable in dashboard iframe
- Session state persisted in SQLite (`browser_sessions` table)

### Context Capture
- Screenshot + accessibility tree + page metadata
- Stored in `~/.rdc/contexts/`
- Attachable to tasks for agent consumption
- API: `POST /context/capture`, `GET /context`, `GET /context/{id}/screenshot`

### rrweb Session Recording
- Records DOM mutations for session replay
- CDP binding for low-latency event push
- Periodic buffer drain as fallback
- Chunked JSON storage in `~/.rdc/recordings/{rec_id}/chunk_{n}.json`

### PinchTab Integration
- Connect to existing browser tabs (no Docker needed)
- Uses PinchTab browser extension for CDP access
- Same context capture capabilities

## Dashboard Integration

- **Browser tab**: Start/stop sessions, view in iframe, capture context
- **Attachments tab**: Browse captured contexts, share with agents
- **PinchTab tab**: Connect to existing browser tabs

## MCP Tools

Agents can capture and read browser context via MCP:
- `capture_browser_context` — screenshot + a11y tree
- `get_browser_context` — read previously captured context
- `browser_eval` — evaluate JavaScript in the browser
