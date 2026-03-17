# MCP Integration Guide

Set up Remote Dev Ctrl with Model Context Protocol for direct AI integration.

## What is MCP?

[Model Context Protocol](https://modelcontextprotocol.io/) allows AI assistants to directly call tools and read resources without going through the command line.

**Benefits:**
- AI can access browser context directly (no shell commands needed)
- Structured data exchange

**When to use MCP vs CLI:**
- **MCP**: Cursor IDE, Claude Desktop, other MCP-compatible clients
- **CLI**: cursor-agent, terminal-based workflows, any AI that can run shell commands

---

## Cursor IDE Setup

### 1. Find Your MCP Config

Cursor stores MCP config at:
- macOS/Linux: `~/.cursor/mcp.json`
- Windows: `%APPDATA%\Cursor\mcp.json`

Create the file if it doesn't exist.

### 2. Add Configuration

```json
{
  "mcpServers": {
    "remote-dev-ctrl": {
      "command": "python",
      "args": ["-m", "remote_dev_ctrl.mcp.server"],
      "env": {
        "PYTHONPATH": "/Users/YOUR_USERNAME/remote-dev-ctrl/src"
      }
    }
  }
}
```

**Important:** Replace `/Users/YOUR_USERNAME/remote-dev-ctrl` with your actual path.

### 3. Restart Cursor

Completely quit and reopen Cursor for changes to take effect.

### 4. Verify

In Cursor, the AI should now have access to:
- Browser context tools (list, get, capture browser contexts)

---

## Alternative: Using rdc-mcp Command

If `rdc` is installed globally:

```json
{
  "mcpServers": {
    "remote-dev-ctrl": {
      "command": "rdc-mcp",
      "args": []
    }
  }
}
```

This requires `rdc-mcp` to be in your PATH.

---

## What MCP Exposes

### Tools

| Tool | Description |
|------|-------------|
| `list_browser_contexts` | List all active browser contexts |
| `get_browser_context` | Get details of a specific browser context |
| `capture_browser_context` | Capture a screenshot/state from a browser context |

---

## Troubleshooting

### MCP Server Not Starting

Check if the module can be imported:

```bash
PYTHONPATH=/path/to/remote-dev-ctrl/src python -c "from remote_dev_ctrl.mcp.server import main; print('OK')"
```

### Cursor Not Detecting MCP

1. Ensure `mcp.json` is valid JSON
2. Restart Cursor completely (not just reload)
3. Check Cursor's developer console for errors

### Permission Errors

Ensure the Python environment has access to your project directories.

---

## For Other MCP Clients

The MCP server follows the standard protocol. Configuration varies by client, but the server command is always:

```bash
python -m remote_dev_ctrl.mcp.server
```

With `PYTHONPATH` set to include the `src` directory.

---

## Testing MCP Locally

You can test the MCP server manually:

```bash
cd /path/to/remote-dev-ctrl
PYTHONPATH=src python -m remote_dev_ctrl.mcp.server
```

The server communicates over stdio, so you'll see it waiting for input. Press Ctrl+C to exit.

---

## When NOT to Use MCP

If you're using:
- `cursor-agent` (CLI-based) - Use `rdc` commands directly
- Terminal AI workflows - Use `rdc` commands
- Scripts or automation - Use `rdc` commands

MCP is primarily for GUI-based AI assistants that support the protocol.
