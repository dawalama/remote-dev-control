# Quick Setup

## Automated Install

```bash
curl -sSL https://raw.githubusercontent.com/dawalama/remote-dev-control/main/install.sh | bash
```

## Manual Install

```bash
# 1. Clone & install
git clone https://github.com/dawalama/remote-dev-control.git
cd remote-dev-control
uv sync                    # or: pip install -e .

# 2. Build frontend
cd frontend && pnpm install && pnpm run build && cd ..

# 3. Start
rdc server start
open http://localhost:8420
```

## Add Your Projects

```bash
rdc add ~/my-project --name my-project
```

## Set Up API Keys (optional)

```bash
rdc config set-secret ANTHROPIC_API_KEY sk-ant-...
rdc config set-secret OPENAI_API_KEY sk-...
```

## Configure

Edit `~/.rdc/config.yml` — see [docs/configuration.md](docs/configuration.md) for reference.

## Full Documentation

- [Human Guide](docs/human-guide.md) — Complete user guide
- [AI Agent Guide](docs/ai-agent-guide.md) — Instructions for AI assistants
- [Configuration](docs/configuration.md) — Full config reference
- [MCP Setup](docs/mcp-setup.md) — IDE integration
