# Configuration Reference

RDC configuration lives at `~/.rdc/config.yml` (or `$RDC_HOME/config.yml`).

## Full Example

```yaml
# Server settings
server:
  host: 127.0.0.1        # Bind address (use 0.0.0.0 for LAN access)
  port: 8420              # Server port
  secret_key: null        # Auth secret (null = no auth)
  tls:
    enabled: false
    cert_file: null
    key_file: null

# LLM Providers
# API keys should be stored in vault: rdc config set-secret KEY VALUE
providers:
  anthropic:
    type: anthropic
    default: true
    # Key from vault: ANTHROPIC_API_KEY
  openai:
    type: openai
    # Key from vault: OPENAI_API_KEY
  ollama:
    type: ollama
    model: llama3.2:3b    # Default Ollama model

# Agent settings
agents:
  default_provider: anthropic   # Which provider to use by default
  max_concurrent: 3             # Max simultaneous agent runs
  auto_spawn: false             # Auto-spawn terminals for new projects

# Communication channels
channels:
  telegram:
    enabled: false
    token: ${TELEGRAM_BOT_TOKEN}  # Env var substitution supported
    allowed_users: []             # Telegram user IDs (empty = all)
  voice:
    enabled: false
    provider: twilio
    # Twilio credentials from vault:
    #   rdc config set-secret TWILIO_ACCOUNT_SID ...
    #   rdc config set-secret TWILIO_AUTH_TOKEN ...
    #   rdc config set-secret TWILIO_PHONE_NUMBER ...
  web:
    enabled: true

# Visual streaming (optional)
visual:
  neko:
    enabled: false
    image: "ghcr.io/m1k1o/neko:firefox"
  terminal:
    enabled: false
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `RDC_HOME` | `~/.rdc` | RDC home directory |

Config values support `${VAR_NAME}` syntax for environment variable substitution.

## Secrets Vault

API keys and sensitive values are stored encrypted in `~/.rdc/secrets.json`:

```bash
# Store a secret
rdc config set-secret ANTHROPIC_API_KEY sk-ant-...
rdc config set-secret OPENAI_API_KEY sk-...
rdc config set-secret X_BEARER_TOKEN ...

# Secrets are automatically available to the server
```

## Per-Project Configuration

Each registered project can have its own settings stored in the database:

- **Terminal command** — Default command when spawning terminals for this project
- **Process configs** — Auto-discovered processes (dev servers, watchers, etc.)
- **Agent settings** — Provider overrides per project
- **Stack profile** — Auto-detected language, framework, package manager

Configure per-project settings via the dashboard (Project Settings page) or CLI.

## Authentication

By default, no authentication is required (localhost-only access). To enable:

1. Set a secret key in config:
   ```yaml
   server:
     secret_key: "your-secret-key-here"
   ```

2. Or set via vault:
   ```bash
   rdc config set-secret RDC_SECRET_KEY your-secret-key-here
   ```

When authentication is enabled, the dashboard will show a login page.

## Network Access

To access RDC from other devices on your network:

```yaml
server:
  host: 0.0.0.0    # Listen on all interfaces
  port: 8420
```

Then access from other devices via `http://<your-ip>:8420`.

For TLS (HTTPS):
```yaml
server:
  tls:
    enabled: true
    cert_file: /path/to/cert.pem
    key_file: /path/to/key.pem
```

## Caddy Reverse Proxy

RDC includes a built-in Caddy integration for subdomain-based routing. This is useful when combined with a tunnel (like Cloudflare Tunnel) to expose your dashboard and dev server previews to the internet.

### How it works

When enabled, RDC starts a Caddy process alongside the server. Each dev server process gets a subdomain like `frontend-myapp.yourdomain.com` that proxies to the local port. The RDC dashboard itself gets a dedicated subdomain.

Caddy is managed entirely via its admin API — RDC maintains the JSON config in memory and pushes atomic reloads on every route change (zero downtime).

### Configuration

```yaml
caddy:
  enabled: true                         # Enable Caddy reverse proxy
  base_domain: yourdomain.com           # Root domain for subdomains
  rdc_domain: rdc.yourdomain.com        # Dashboard subdomain
  listen_port: 8888                     # Caddy HTTP listener (tunnel points here)
  admin_port: 2019                      # Caddy admin API (localhost only)
```

| Field | Default | Description |
|-------|---------|-------------|
| `enabled` | `false` | Start Caddy on server boot |
| `base_domain` | `truesteps.studio` | Root domain for preview subdomains |
| `rdc_domain` | `rdc.truesteps.studio` | Dashboard subdomain |
| `listen_port` | `8888` | HTTP port Caddy listens on |
| `admin_port` | `2019` | Caddy admin API port |

### Caddy binary

RDC auto-downloads Caddy to `~/.rdc/bin/caddy` if it's not found in `$PATH`. You can also install it manually:

```bash
# macOS
brew install caddy

# Or let RDC download it automatically on first start
```

### Route assignment

When you start a process, RDC generates a subdomain from the project and process name:

| Project | Process | Subdomain |
|---------|---------|-----------|
| myapp | frontend | `myapp-frontend.yourdomain.com` |
| myapp | server | `myapp-server.yourdomain.com` |
| api | dev | `api-dev.yourdomain.com` |

Routes are added and removed dynamically as processes start and stop.

### With Cloudflare Tunnel

The recommended setup for remote access. See the [README](../README.md#remote-access-cloudflare-tunnel--caddy) for step-by-step setup.

```
Internet → Cloudflare Tunnel → Caddy (:8888) → RDC (:8420) / Dev servers
```

Key points:
- Cloudflare handles TLS termination — Caddy runs in HTTP mode (`automatic_https` is disabled)
- Point `*.yourdomain.com` at the tunnel in your `cloudflared` config
- Caddy's `listen_port` must match the `cloudflared` ingress service port
- No port forwarding or static IP needed
