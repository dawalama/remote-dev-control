#!/usr/bin/env bash
#
# Remote Dev Ctrl (RDC) — Install Script
# Usage: curl -sSL https://raw.githubusercontent.com/dawalama/remote-dev-ctrl/main/install.sh | bash
#    or: ./install.sh
#
set -euo pipefail

# ── Colors ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m'

info()  { echo -e "${BLUE}ℹ${NC}  $*"; }
ok()    { echo -e "${GREEN}✓${NC}  $*"; }
warn()  { echo -e "${YELLOW}⚠${NC}  $*"; }
fail()  { echo -e "${RED}✗${NC}  $*"; exit 1; }
step()  { echo -e "\n${BOLD}── $* ──${NC}"; }

# ── Configuration ───────────────────────────────────────────────────────────
RDC_REPO="https://github.com/dawalama/remote-dev-ctrl.git"
RDC_DIR="${RDC_DIR:-$HOME/remote-dev-ctrl}"
RDC_HOME="${RDC_HOME:-$HOME/.rdc}"
MIN_PYTHON="3.11"
MIN_NODE="18"

# ── Helpers ─────────────────────────────────────────────────────────────────
version_ge() {
  # Returns 0 if $1 >= $2 (semantic version comparison)
  printf '%s\n%s' "$2" "$1" | sort -V -C
}

command_exists() {
  command -v "$1" &>/dev/null
}

# ── Pre-flight checks ──────────────────────────────────────────────────────
step "Checking prerequisites"

# Python
if command_exists python3; then
  PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
  if version_ge "$PY_VERSION" "$MIN_PYTHON"; then
    ok "Python $PY_VERSION"
  else
    fail "Python $MIN_PYTHON+ required (found $PY_VERSION). Install from https://python.org"
  fi
else
  fail "Python 3 not found. Install from https://python.org"
fi

# Node.js
if command_exists node; then
  NODE_VERSION=$(node -v | sed 's/v//' | cut -d. -f1)
  if [ "$NODE_VERSION" -ge "$MIN_NODE" ]; then
    ok "Node.js v$(node -v | sed 's/v//')"
  else
    fail "Node.js $MIN_NODE+ required (found v$(node -v)). Install from https://nodejs.org"
  fi
else
  fail "Node.js not found. Install from https://nodejs.org"
fi

# Git
command_exists git || fail "Git not found. Install git first."
ok "Git $(git --version | awk '{print $3}')"

# ── Install uv (Python package manager) ────────────────────────────────────
step "Setting up Python package manager"

if command_exists uv; then
  ok "uv already installed ($(uv --version 2>/dev/null || echo 'unknown'))"
else
  info "Installing uv (fast Python package manager)..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  # Source the env so uv is available in this session
  export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
  if command_exists uv; then
    ok "uv installed"
  else
    warn "uv install completed but not in PATH. You may need to restart your shell."
    warn "Falling back to pip."
  fi
fi

# ── Install pnpm (frontend package manager) ────────────────────────────────
step "Setting up frontend package manager"

if command_exists pnpm; then
  ok "pnpm already installed ($(pnpm --version))"
else
  info "Installing pnpm..."
  if command_exists npm; then
    npm install -g pnpm
    ok "pnpm installed via npm"
  elif command_exists corepack; then
    corepack enable
    corepack prepare pnpm@latest --activate
    ok "pnpm installed via corepack"
  else
    curl -fsSL https://get.pnpm.io/install.sh | sh -
    export PATH="$HOME/.local/share/pnpm:$PATH"
    ok "pnpm installed"
  fi
fi

# ── Clone or update repository ──────────────────────────────────────────────
step "Getting RDC source code"

if [ -d "$RDC_DIR/.git" ]; then
  info "Repository exists at $RDC_DIR, pulling latest..."
  cd "$RDC_DIR"
  git pull --rebase || warn "Pull failed — continuing with existing code"
  ok "Updated $RDC_DIR"
else
  if [ -d "$RDC_DIR" ]; then
    warn "$RDC_DIR exists but is not a git repo. Using as-is."
  else
    info "Cloning repository..."
    git clone "$RDC_REPO" "$RDC_DIR"
    ok "Cloned to $RDC_DIR"
  fi
  cd "$RDC_DIR"
fi

# ── Install Python dependencies ─────────────────────────────────────────────
step "Installing Python dependencies"

cd "$RDC_DIR"
if command_exists uv; then
  uv sync
  ok "Python dependencies installed (uv sync)"
else
  pip install -e .
  ok "Python dependencies installed (pip)"
fi

# ── Build frontend ──────────────────────────────────────────────────────────
step "Building frontend dashboard"

cd "$RDC_DIR/frontend"
pnpm install --frozen-lockfile 2>/dev/null || pnpm install
ok "Frontend dependencies installed"

pnpm run build
ok "Frontend built"

# ── Initialize RDC home ────────────────────────────────────────────────────
step "Initializing RDC ($RDC_HOME)"

mkdir -p "$RDC_HOME"/{data,logs/agents,logs/processes,bin,contexts,recordings}

# Create default config if it doesn't exist
if [ ! -f "$RDC_HOME/config.yml" ]; then
  cat > "$RDC_HOME/config.yml" << 'YAML'
# RDC Configuration
# Docs: https://github.com/dawalama/remote-dev-ctrl/blob/main/docs/configuration.md

server:
  host: 127.0.0.1
  port: 8420

# LLM Providers — configure API keys via: rdc config set-secret <KEY> <VALUE>
providers:
  anthropic:
    type: anthropic
    # API key stored in vault: rdc config set-secret ANTHROPIC_API_KEY sk-...
  openai:
    type: openai
    # API key stored in vault: rdc config set-secret OPENAI_API_KEY sk-...
  ollama:
    type: ollama
    model: llama3.2:3b

# Agent settings
agents:
  default_provider: anthropic
  max_concurrent: 3
  auto_spawn: false

# Communication channels (optional)
channels:
  telegram:
    enabled: false
    # token: ${TELEGRAM_BOT_TOKEN}
  voice:
    enabled: false
    provider: twilio
  web:
    enabled: true
YAML
  ok "Created default config at $RDC_HOME/config.yml"
else
  ok "Config already exists at $RDC_HOME/config.yml"
fi

# ── Verify installation ─────────────────────────────────────────────────────
step "Verifying installation"

cd "$RDC_DIR"

# Check rdc CLI
if command_exists rdc; then
  ok "rdc CLI available: $(which rdc)"
else
  # Try to find it in the venv
  if [ -f "$RDC_DIR/.venv/bin/rdc" ]; then
    info "rdc found in virtualenv. Add to your PATH:"
    echo "  export PATH=\"$RDC_DIR/.venv/bin:\$PATH\""
    warn "Or activate the venv: source $RDC_DIR/.venv/bin/activate"
  else
    warn "rdc command not found in PATH. You may need to restart your shell."
  fi
fi

# Check frontend build
if [ -f "$RDC_DIR/frontend/dist/index.html" ]; then
  ok "Frontend build present"
else
  warn "Frontend build not found — dashboard may not work"
fi

# ── Print summary ───────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}${BOLD}  RDC installed successfully!${NC}"
echo -e "${BOLD}════════════════════════════════════════════════════════${NC}"
echo ""
echo -e "  ${BOLD}Source:${NC}    $RDC_DIR"
echo -e "  ${BOLD}Config:${NC}    $RDC_HOME/config.yml"
echo -e "  ${BOLD}Data:${NC}      $RDC_HOME/data/"
echo ""
echo -e "  ${BOLD}Quick start:${NC}"
echo ""

# Show PATH instructions if needed
if ! command_exists rdc 2>/dev/null; then
  echo -e "  ${YELLOW}# Add rdc to your PATH (add to ~/.zshrc or ~/.bashrc):${NC}"
  echo -e "  export PATH=\"$RDC_DIR/.venv/bin:\$PATH\""
  echo ""
fi

echo -e "  # Start the server"
echo -e "  rdc server start"
echo ""
echo -e "  # Open the dashboard"
echo -e "  open http://localhost:8420"
echo ""
echo -e "  # Register a project"
echo -e "  rdc add ~/my-project --name my-project"
echo ""
echo -e "  # (Optional) Set up API keys"
echo -e "  rdc config set-secret ANTHROPIC_API_KEY sk-ant-..."
echo -e "  rdc config set-secret OPENAI_API_KEY sk-..."
echo ""
echo -e "  # (Optional) Remote access via Cloudflare Tunnel + Caddy"
echo -e "  # See: $RDC_DIR/README.md#remote-access-cloudflare-tunnel--caddy"
echo ""
echo -e "  ${BOLD}Documentation:${NC}"
echo -e "  $RDC_DIR/docs/human-guide.md"
echo -e "  $RDC_DIR/docs/ai-agent-guide.md"
echo ""
