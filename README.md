# Remote Dev Ctrl (RDC)

Some of the best ideas hit when you're away from your desk — on a coffee run, taking a walk, or just stepping outside for a break. RDC was built so you can act on that inspiration immediately, from any device. Pull up the dashboard on your phone, kick off a build, check on an agent, or start a task — without needing to rush back to your workstation.

A command center for AI-assisted development. Manage multiple projects, orchestrate AI agents, run tasks, and control everything from a responsive web dashboard or CLI.

## What It Does

**Command Center Dashboard** — Real-time web UI with three layouts (desktop, mobile, kiosk) for managing projects, terminals, tasks, actions, and AI agents from any device.

**Terminal Management** — Spawn and control PTY sessions (shell, Claude, Cursor, etc.) with WebSocket streaming, auto-reconnect, and input detection alerts.

**Task System** — Create tasks with recipes, assign LLM models, track progress, review outputs. Built-in recipes for common workflows like code audits.

**Actions** — Auto-discover and manage project actions: services (long-running dev servers, APIs) and commands (builds, tests, lints). Start, stop, attach, view logs.

**AI Orchestrator** — Natural language control via chat or voice. "Start a terminal for my-project", "show tasks", "switch to kiosk mode".

**AI Agents** — Spawn and manage AI coding agents (Claude, Cursor, etc.) per project. Assign tasks, view logs, stop or retry.

**Voice & Phone Control** — Call your RDC instance via Twilio and control it by voice. Pair with a dashboard client for full remote control, or get verbal status updates hands-free.

**Browser Preview** — Share a browser session with VNC, capture screenshots, and record sessions with rrweb for replay.

**MCP Server** — Model Context Protocol integration exposing browser context to AI assistants (Cursor, Claude, etc.).

## Quick Start

```bash
# One-line install (clones repo, installs deps, builds frontend)
curl -sSL https://raw.githubusercontent.com/dawalama/remote-dev-ctrl/main/install.sh | bash

# Or manual install
git clone https://github.com/dawalama/remote-dev-ctrl.git
cd remote-dev-ctrl
uv sync                          # Python deps
cd frontend && pnpm install && pnpm run build && cd ..

# Start the server
rdc server start

# Open the dashboard
open http://localhost:8420

# Register a project
rdc add ~/my-project --name my-project
```

## Installation

### Prerequisites

| Requirement | Version | Notes |
|-------------|---------|-------|
| Python | 3.11+ | 3.12+ recommended |
| Node.js | 18+ | For building the dashboard |
| Git | any | |
| uv | any | Auto-installed by `install.sh` |
| pnpm | any | Auto-installed by `install.sh` |

### Install Script

The install script checks prerequisites, installs package managers, clones the repo, installs dependencies, builds the frontend, and creates default config:

```bash
./install.sh
```

### Manual Install

```bash
# 1. Clone
git clone https://github.com/dawalama/remote-dev-ctrl.git
cd remote-dev-ctrl

# 2. Python dependencies
uv sync              # recommended
# or: pip install -e .

# 3. Frontend
cd frontend
pnpm install
pnpm run build
cd ..

# 4. Initialize (creates ~/.rdc/ with default config)
rdc server start     # auto-initializes on first run
```

### Configuration

Config lives at `~/.rdc/config.yml`. Created automatically on first run. Key settings:

```yaml
server:
  host: 127.0.0.1
  port: 8420

providers:
  anthropic:
    type: anthropic
  openai:
    type: openai
  ollama:
    type: ollama
    model: llama3.2:3b

agents:
  default_provider: anthropic
  max_concurrent: 3
```

API keys are stored securely in the vault:

```bash
rdc config set-secret ANTHROPIC_API_KEY sk-ant-...
rdc config set-secret OPENAI_API_KEY sk-...
```

### Directory Structure

```
~/.rdc/                      # RDC home (configurable via $RDC_HOME)
  config.yml                 # Server + provider configuration
  data/                      # SQLite databases (auto-created)
    rdc.db                   #   Projects, agents, processes, settings
    tasks.db                 #   Task management
    logs.db                  #   Activity logs
  logs/                      # Runtime logs
    agents/                  #   Per-agent output logs
    processes/               #   Per-process output logs
  recordings/                # Browser session recordings
  contexts/                  # Uploaded context files
```

## Server Commands

```bash
rdc server start             # Start (foreground)
rdc server start -d          # Start (background daemon)
rdc server status            # Check if running
rdc server stop              # Stop
rdc server restart           # Restart
```

The server serves both the API and the dashboard frontend on a single port (default: 8420).

## Remote Access (Cloudflare Tunnel + Caddy)

RDC is designed to be accessed from anywhere — your phone, tablet, or another machine. The recommended setup uses **Cloudflare Tunnel** for secure ingress and **Caddy** as a local reverse proxy for subdomain routing.

### Why this stack

- **Cloudflare Tunnel** — Exposes your local machine to the internet without port forwarding, static IPs, or firewall rules. Free tier works fine.
- **Caddy** — Routes subdomains locally. The RDC dashboard gets `rdc.yourdomain.com`, and each dev server process gets its own subdomain like `frontend-myapp.preview.yourdomain.com`. Caddy is auto-downloaded by RDC if not installed.

### Architecture

```
Internet → Cloudflare Tunnel → Caddy (:8888) → RDC Server (:8420)
                                             → Dev servers (:3000, :5173, etc.)

rdc.yourdomain.com           → localhost:8420  (dashboard + API)
frontend-myapp.yourdomain.com → localhost:5173  (preview URL)
```

### Setup

#### 1. Get a domain on Cloudflare

Point your domain's DNS to Cloudflare (free plan works). You'll need a domain like `yourdomain.com`.

#### 2. Install and configure `cloudflared`

```bash
# macOS
brew install cloudflared

# Login to Cloudflare
cloudflared tunnel login

# Create a tunnel
cloudflared tunnel create rdc

# Configure the tunnel — route your wildcard domain to Caddy's listen port
cat > ~/.cloudflared/config.yml << 'EOF'
tunnel: rdc
credentials-file: ~/.cloudflared/<TUNNEL_ID>.json

ingress:
  - hostname: "*.yourdomain.com"
    service: http://localhost:8888
  - hostname: "yourdomain.com"
    service: http://localhost:8888
  - service: http_status:404
EOF

# Add DNS records (wildcard + apex)
cloudflared tunnel route dns rdc "*.yourdomain.com"
cloudflared tunnel route dns rdc "yourdomain.com"
```

#### 3. Configure RDC's Caddy integration

Add to `~/.rdc/config.yml`:

```yaml
caddy:
  enabled: true
  base_domain: yourdomain.com       # Your Cloudflare domain
  rdc_domain: rdc.yourdomain.com    # Where the dashboard lives
  listen_port: 8888                 # Must match cloudflared ingress
  admin_port: 2019                  # Caddy admin API (local only)
```

#### 4. Start everything

```bash
# Start the tunnel (in background or separate terminal)
cloudflared tunnel run rdc

# Start RDC (Caddy starts automatically when enabled)
rdc server start
```

The dashboard is now at `https://rdc.yourdomain.com`. Preview URLs are assigned automatically when you start dev server actions.

#### 5. (Recommended) Run the tunnel as a service

```bash
# macOS — install as a launch agent
sudo cloudflared service install

# Linux — install as a systemd service
sudo cloudflared service install
sudo systemctl enable --now cloudflared
```

### Security

When exposing RDC to the internet, **enable authentication**:

```bash
# Set a secret key
rdc config set-secret RDC_SECRET_KEY $(openssl rand -hex 32)
```

Then add to `~/.rdc/config.yml`:

```yaml
server:
  secret_key: ${RDC_SECRET_KEY}
```

The dashboard will require a token to access. You can also use Cloudflare Access (Zero Trust) for additional protection.

## Dashboard

Three responsive layouts, all first-class:

| Layout | Best For | Access |
|--------|----------|--------|
| Desktop | Large screens, IDE-like workflow | `?layout=desktop` |
| Kiosk | Tablets, dedicated terminals with sidebar | `?layout=kiosk` |
| Mobile | Phones, on-the-go monitoring | `?layout=mobile` |

### Features Across All Layouts

- **Project switcher** with collection support and activity indicators
- **Terminal management** — spawn, kill, restart with preset agents
- **Task management** — create, run, review, approve/reject, view output
- **Action management** — services (start, stop, restart, attach) and commands (run, view logs)
- **Chat/orchestrator** — natural language commands and AI responses
- **Browser sessions** — shared browser preview with rrweb recording
- **Attention alerts** — terminals waiting for input highlighted prominently
- **Global text input** — floating input bar for terminal dictation
- **Voice control** — speech-to-text for commands and terminal input (kiosk/mobile)

## CLI Commands

### Project Management

```bash
rdc add <path> [-n name]     # Register existing project
rdc remove <name>            # Unregister project
rdc list                     # List registered projects
```

### Secrets & Config

```bash
rdc config set-secret KEY VALUE    # Store API key securely
rdc config get KEY                 # Read config value
```

## Task Recipes

Recipes are reusable task templates. Built-in recipes:

| Recipe | Model | Description |
|--------|-------|-------------|
| Code Audit | Claude Opus | Security-focused audit with structured scoring |

Create custom recipes via the API or dashboard settings.

## MCP Integration

For AI assistants that support [Model Context Protocol](https://modelcontextprotocol.io/):

```json
{
  "mcpServers": {
    "remote-dev-ctrl": {
      "command": "python",
      "args": ["-m", "remote_dev_ctrl.mcp.server"],
      "env": {
        "PYTHONPATH": "/path/to/remote-dev-ctrl/src"
      }
    }
  }
}
```

## API

The server exposes a REST + WebSocket API on port 8420. Interactive docs are available at `/docs` (Swagger UI) when the server is running.

Key endpoints:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/ws/state` | WS | Real-time state updates |
| `/projects` | GET/POST | List/create projects |
| `/terminals` | POST | Spawn terminal session |
| `/terminals/{id}/ws` | WS | Terminal I/O stream |
| `/tasks` | GET/POST | List/create tasks |
| `/tasks/{id}/run` | POST | Execute a task |
| `/tasks/{id}/review` | POST | Approve/reject task |
| `/processes` | GET | List actions (services + commands) |
| `/processes/register` | POST | Register a new action |
| `/processes/suggest` | POST | AI-suggest an action from description |
| `/processes/{id}/start` | POST | Start/run an action |
| `/processes/{id}/stop` | POST | Stop an action |
| `/processes/{id}/attach` | POST | Attach to orphaned process |
| `/processes/{id}/logs` | GET | Get action output logs |
| `/agents/spawn` | POST | Spawn an AI coding agent |
| `/agents/{project}/stop` | POST | Stop a running agent |
| `/agents/{project}/logs` | GET | Get agent output logs |
| `/browser/start/{id}` | POST | Start browser preview for a service |
| `/browser/sessions` | GET | List active browser sessions |
| `/voice/call` | POST | Initiate a phone call via Twilio |
| `/voice/pair` | POST | Pair phone with a dashboard client |
| `/orchestrator` | POST | Send message to AI orchestrator |
| `/models` | GET | List available LLM models |
| `/recipes` | GET | List task recipes |

## Documentation

- [Human Guide](docs/human-guide.md) - Complete user guide
- [AI Agent Guide](docs/ai-agent-guide.md) - Instructions for AI assistants
- [Configuration](docs/configuration.md) - Full configuration reference (includes Caddy proxy setup)
- [MCP Setup](docs/mcp-setup.md) - Model Context Protocol integration
- [Architecture](docs/architecture/) - System design docs

## Architecture

```
                    Phone (Twilio)
                         │
┌────────────────────────┼────────────────────┐
│              Web Dashboard                   │
│     (React + Zustand + Tailwind + xterm)     │
│     Desktop │ Mobile │ Kiosk layouts         │
└──────────────────┬──────────────────────────┘
                   │ HTTP/WS
┌──────────────────┴──────────────────────────┐
│              FastAPI Server (:8420)           │
│  ┌──────────┬──────────┬──────────────────┐  │
│  │ Terminal  │  Task    │  Action          │  │
│  │ Manager   │  Worker  │  Discovery       │  │
│  ├──────────┼──────────┼──────────────────┤  │
│  │ AI       │  Intent  │  Agent           │  │
│  │ Orchestr.│  Engine  │  Manager         │  │
│  ├──────────┼──────────┼──────────────────┤  │
│  │ Voice/   │  Browser │  MCP             │  │
│  │ Phone    │  Preview │  Server          │  │
│  ├──────────┴──────────┴──────────────────┤  │
│  │         SQLite (rdc, tasks, logs)       │  │
│  └────────────────────────────────────────┘  │
└──────────────┬───────────────────────────────┘
               │ (optional)
┌──────────────┴───────────────────────────────┐
│  Caddy (:8888) → Cloudflare Tunnel → Internet │
└──────────────────────────────────────────────┘
```

## Philosophy

- **Control from anywhere** — Phone, tablet, or desktop. Voice, chat, or CLI. No need to be at your workstation.
- **Terminals that survive** — PTY sessions persist across server restarts and network drops. Pick up where you left off.
- **Agent-agnostic** — Works with Claude, Cursor, OpenAI, Ollama, or any LLM
- **Self-contained** — SQLite databases, no external services required. One port, one process.
- **AI-first** — Natural language orchestrator, task system with LLM execution, voice control built in
