#!/usr/bin/env bash
# Smart agent launcher for RDC terminals.
#
# Wraps any agentic CLI (Claude, Cursor, etc.) with smart defaults:
# - Detects prior conversation history and resumes if available
# - Falls back to a fresh start for new projects
# - Agent-agnostic: knows the resume flags for each supported CLI
#
# Usage: rdc-launch <agent> [extra-args...]
#   rdc-launch claude
#   rdc-launch cursor-agent
#   rdc-launch some-other-cli --custom-flag

set -euo pipefail

if [ $# -eq 0 ]; then
  echo "Usage: rdc-launch <agent> [extra-args...]"
  echo "  Supported agents: claude, cursor-agent, or any CLI"
  exit 1
fi

AGENT="$1"
shift
EXTRA_ARGS=("${@+"$@"}")

# ── Detect conversation history per agent ──

has_claude_history() {
  [ -d ".claude" ] && find .claude -name "*.jsonl" -maxdepth 3 2>/dev/null | grep -q .
}

has_cursor_history() {
  [ -d ".cursor" ] || [ -f ".cursorcontext" ]
}

# ── Build command with smart flags ──

case "$AGENT" in
  claude)
    if has_claude_history; then
      exec claude --continue ${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"}
    else
      exec claude ${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"}
    fi
    ;;

  cursor-agent)
    if has_cursor_history; then
      exec cursor-agent --resume ${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"}
    else
      exec cursor-agent ${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"}
    fi
    ;;

  *)
    # Unknown agent — just pass through
    exec "$AGENT" ${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"}
    ;;
esac
