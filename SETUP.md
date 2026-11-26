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

When you open `http://localhost:8420` for the first time with no projects, the **Welcome** screen detects your installed CLI agents and lets you browse or quick-connect existing folders (or create a new project) in 1 click.

You can also register a repo from the CLI:

```bash
rdc add ~/my-project --name my-project
```

Project configs are stored centrally under `~/.rdc/projects/<name>.yaml` so your repos stay clean.

## Set Up API Keys (optional)

If you already have a CLI agent signed in (`claude`, `codex`, `cursor-agent`, or `agy`), you can run Goals right away without raw API keys. Optional API keys for background briefs and summaries are stored in the encrypted vault:

```bash
rdc config set-secret OPENROUTER_API_KEY sk-or-...
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
