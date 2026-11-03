"""MCP server implementation for remote-dev-ctrl.

Exposes browser context tools (list, get, capture) via MCP protocol.
Knowledge, skills, and tools are managed separately by dotai.
"""

import base64
import json
import os
from pathlib import Path

import httpx
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    ImageContent,
    Resource,
    ResourceTemplate,
    TextContent,
    Tool,
    CallToolResult,
    ListResourcesResult,
    ListToolsResult,
    ReadResourceResult,
)

RDC_SERVER_URL = os.environ.get("RDC_SERVER_URL", "http://127.0.0.1:8420")
from ..server.config import get_rdc_home
CONTEXTS_DIR = get_rdc_home() / "contexts"


def create_server() -> Server:
    """Create and configure the MCP server."""
    server = Server("remote-dev-ctrl")

    # =========================================================================
    # TOOLS - Browser context tools
    # =========================================================================

    @server.list_tools()
    async def list_tools() -> ListToolsResult:
        """List all available tools."""
        tools = []

        tools.append(Tool(
            name="list_browser_contexts",
            description="List captured browser contexts (screenshots + accessibility trees). Each context is a snapshot of a web page's visual state and DOM structure.",
            inputSchema={
                "type": "object",
                "properties": {
                    "project": {
                        "type": "string",
                        "description": "Filter by project name (optional)",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max number of contexts to return (default 10)",
                    },
                },
                "required": [],
            },
        ))

        tools.append(Tool(
            name="get_browser_context",
            description="Get a browser context capture including the accessibility tree, metadata, and screenshot. The accessibility tree describes all interactive elements, text content, and page structure — use it to understand what the user sees on the page.",
            inputSchema={
                "type": "object",
                "properties": {
                    "context_id": {
                        "type": "string",
                        "description": "Context ID to retrieve. Use 'latest' for the most recent capture.",
                    },
                    "include_screenshot": {
                        "type": "boolean",
                        "description": "Include the screenshot image (default true)",
                    },
                },
                "required": ["context_id"],
            },
        ))

        tools.append(Tool(
            name="capture_browser_context",
            description="Capture a new browser context snapshot (screenshot + accessibility tree) of the current page state in the shared browser session. Use this when you need to see what the user is currently looking at.",
            inputSchema={
                "type": "object",
                "properties": {
                    "description": {
                        "type": "string",
                        "description": "Optional description of why this capture is being taken",
                    },
                },
                "required": [],
            },
        ))

        tools.append(Tool(
            name="list_agent_wrappers",
            description="List all registered AI agent wrappers, model providers, cognitive tiers, and local/self-hosted model endpoints in Remote Dev Control (RDC).",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": [],
            },
        ))

        tools.append(Tool(
            name="register_agent_wrapper",
            description="Self-register or update an AI coding CLI wrapper or model provider in Remote Dev Control (RDC). Use this when a user asks you to add yourself or a custom model wrapper (e.g. aider, opencode, goose, grok, codex-oss, claude-custom) as an available provider.",
            inputSchema={
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Unique wrapper ID (e.g. 'aider-qwen-local', 'grok-fast')"},
                    "label": {"type": "string", "description": "Human-readable label (e.g. 'Aider (Local Qwen3-Coder)')"},
                    "driver": {
                        "type": "string",
                        "enum": ["codex", "claude", "cursor", "grok", "aider", "opencode", "goose", "generic"],
                        "description": "Execution driver family",
                    },
                    "model_provider": {
                        "type": "string",
                        "description": "Model provider (openai, anthropic, openrouter, ollama, vllm, mlx, xai, vendor)",
                    },
                    "default_model": {"type": "string", "description": "Default model slug (e.g. 'qwen3-coder:32b', 'claude-sonnet-4-5')"},
                    "model_tier": {
                        "type": "string",
                        "enum": ["decision", "fast", "coding", "reasoning"],
                        "description": "Cognitive tier this wrapper is best suited for",
                    },
                    "hosting": {
                        "type": "string",
                        "enum": ["cloud", "local", "self_hosted", "hybrid"],
                        "description": "Where inference runs",
                    },
                    "api_base_url": {"type": "string", "description": "Optional custom OpenAI/Anthropic-compatible base URL"},
                    "executable": {"type": "string", "description": "CLI binary name (e.g. 'aider', 'claude', 'codex')"},
                    "terminal_command": {"type": "string", "description": "Command to launch interactive TUI in terminal"},
                    "session_command_template": {
                        "type": "string",
                        "description": "Optional non-interactive supervised session command template containing {prompt}",
                    },
                    "setup_command": {"type": "string", "description": "Optional setup/login command"},
                    "description": {"type": "string", "description": "Short description of this wrapper"},
                },
                "required": ["id", "label"],
            },
        ))

        tools.append(Tool(
            name="evaluate_decision",
            description="Run RDC's System 1 Decision Engine (Jev / Choice / Score / Noul) to evaluate structured probabilistic decisions in <50ms.",
            inputSchema={
                "type": "object",
                "properties": {
                    "state": {
                        "description": "Application state (string or JSON object) to evaluate",
                    },
                    "questions": {
                        "type": "object",
                        "description": "Map of question_id -> {type: 'choice'|'score'|'noul', instructions: str, criteria?: dict[str, str]}",
                    },
                },
                "required": ["state", "questions"],
            },
        ))

        return tools

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> CallToolResult:
        """Execute a tool and return results."""
        if name == "list_browser_contexts":
            return await _tool_list_contexts(arguments)
        if name == "get_browser_context":
            return await _tool_get_context(arguments)
        if name == "capture_browser_context":
            return await _tool_capture_context(arguments)
        if name == "list_agent_wrappers":
            return _tool_list_agent_wrappers()
        if name == "register_agent_wrapper":
            return _tool_register_agent_wrapper(arguments)
        if name == "evaluate_decision":
            return _tool_evaluate_decision(arguments)

        return [TextContent(type="text", text=f"Error: Tool not found: {name}")]

    # =========================================================================
    # RESOURCES - Browser contexts & Self-registration instructions
    # =========================================================================

    @server.list_resources()
    async def list_resources() -> ListResourcesResult:
        """List all available resources."""
        resources = [
            Resource(
                uri="rdc://instructions/wrapper-self-registration",
                name="RDC Wrapper Self-Registration Instructions",
                description="How any CLI agent or model wrapper can register itself as an available provider in RDC",
                mimeType="application/json",
            )
        ]

        # Browser contexts
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{RDC_SERVER_URL}/context")
                if resp.status_code == 200:
                    contexts = resp.json()
                    for ctx in (contexts[:10] if isinstance(contexts, list) else []):
                        resources.append(Resource(
                            uri=f"rdc://context/{ctx['id']}",
                            name=f"Context: {ctx.get('title') or ctx.get('url') or ctx['id']}",
                            description=f"Browser capture — {ctx.get('url', '')} ({ctx.get('timestamp', '')[:19]})",
                            mimeType="text/plain",
                        ))
                    if contexts:
                        resources.append(Resource(
                            uri="rdc://context/latest",
                            name="Context: Latest Capture",
                            description="Most recent browser context capture (screenshot + accessibility tree)",
                            mimeType="text/plain",
                        ))
        except Exception:
            pass

        return resources

    @server.read_resource()
    async def read_resource(uri: str) -> ReadResourceResult:
        """Read a specific resource."""
        parts = uri.replace("rdc://", "").split("/")

        if not parts:
            return [TextContent(type="text", text="Invalid URI")]

        category = parts[0]

        if category == "instructions" and len(parts) >= 2 and parts[1] == "wrapper-self-registration":
            from ..server.agent_registry import get_wrapper_self_registration_instructions
            info = get_wrapper_self_registration_instructions(RDC_SERVER_URL)
            return [TextContent(type="text", text=json.dumps(info, indent=2))]

        # Browser context
        if category == "context" and len(parts) >= 2:
            return await _read_context_resource(parts[1])

        return [TextContent(type="text", text=f"Unknown resource: {uri}")]

    def _tool_list_agent_wrappers() -> CallToolResult:
        from ..server.agent_registry import (
            discover_model_providers,
            get_model_tier_matrix,
            list_provider_readiness,
            load_agent_provider_policy,
        )
        payload = {
            "policy": load_agent_provider_policy(),
            "wrappers": list_provider_readiness(),
            "model_providers": discover_model_providers(),
            "tier_matrix": get_model_tier_matrix(),
        }
        return [TextContent(type="text", text=json.dumps(payload, indent=2))]

    def _tool_register_agent_wrapper(arguments: dict) -> CallToolResult:
        from ..server.agent_registry import (
            AgentWrapperSpec,
            register_dropin_wrapper,
            upsert_wrapper_spec,
        )
        wid = str(arguments.get("id") or "").strip()
        if not wid:
            return [TextContent(type="text", text="Error: 'id' is required")]
        driver = str(arguments.get("driver") or "generic").strip()
        executable = arguments.get("executable") or wid
        spec = AgentWrapperSpec(
            id=wid,
            label=str(arguments.get("label") or wid),
            driver=driver,
            description=str(arguments.get("description") or f"Registered via MCP ({driver})"),
            model_provider=str(arguments.get("model_provider") or "vendor"),
            default_model=arguments.get("default_model"),
            model_tier=str(arguments.get("model_tier") or "coding"),
            hosting=str(arguments.get("hosting") or "cloud"),
            api_base_url=arguments.get("api_base_url"),
            executable=executable,
            terminal_command=str(arguments.get("terminal_command") or executable),
            session_command_template=arguments.get("session_command_template"),
            setup_command=arguments.get("setup_command"),
            source="dropin",
            supports_sessions=True,
            show_in_terminal_menu=True,
        )
        saved = upsert_wrapper_spec(spec)
        dropin_path = register_dropin_wrapper(saved)
        return [
            TextContent(
                type="text",
                text=json.dumps(
                    {
                        "registered": True,
                        "wrapper": saved.model_dump(),
                        "dropin_manifest": str(dropin_path),
                    },
                    indent=2,
                ),
            )
        ]

    def _tool_evaluate_decision(arguments: dict) -> CallToolResult:
        from ..server.decision import get_decision_engine
        state = arguments.get("state")
        questions = arguments.get("questions") or {}
        res = get_decision_engine().evaluate(state=state, questions=questions)
        return [TextContent(type="text", text=json.dumps(res.model_dump(), indent=2))]

    # =========================================================================
    # Browser Context Helpers
    # =========================================================================

    async def _tool_list_contexts(arguments: dict) -> CallToolResult:
        try:
            params = {}
            if arguments.get("project"):
                params["project"] = arguments["project"]
            if arguments.get("limit"):
                params["limit"] = arguments["limit"]
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{RDC_SERVER_URL}/context", params=params)
                if resp.status_code != 200:
                    return [TextContent(type="text", text=f"Error: {resp.status_code} {resp.text}")]
                contexts = resp.json()

            if not contexts:
                return [TextContent(type="text", text="No browser contexts captured yet. Use the Preview panel to capture context.")]

            lines = [f"Found {len(contexts)} context capture(s):\n"]
            for ctx in contexts:
                lines.append(f"- **{ctx['id']}** | {ctx.get('title', 'Untitled')} | {ctx.get('url', '')} | {ctx.get('timestamp', '')[:19]}")
            lines.append(f"\nUse get_browser_context with a context_id to see the full accessibility tree and screenshot.")
            return [TextContent(type="text", text="\n".join(lines))]
        except httpx.ConnectError:
            return [TextContent(type="text", text="Error: Cannot connect to RDC server. Is it running?")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {e}")]

    async def _tool_get_context(arguments: dict) -> CallToolResult:
        context_id = arguments.get("context_id", "")
        include_screenshot = arguments.get("include_screenshot", True)

        try:
            if context_id == "latest":
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.get(f"{RDC_SERVER_URL}/context", params={"limit": 1})
                    if resp.status_code != 200:
                        return [TextContent(type="text", text=f"Error: {resp.status_code}")]
                    contexts = resp.json()
                    if not contexts:
                        return [TextContent(type="text", text="No contexts captured yet.")]
                    context_id = contexts[0]["id"]

            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{RDC_SERVER_URL}/context/{context_id}")
                if resp.status_code == 404:
                    return [TextContent(type="text", text=f"Context '{context_id}' not found.")]
                if resp.status_code != 200:
                    return [TextContent(type="text", text=f"Error: {resp.status_code}")]
                ctx = resp.json()

            parts = []

            # Structured text: metadata + a11y tree
            text_lines = [
                f"# Browser Context: {ctx.get('title', 'Untitled')}",
                f"",
                f"**URL:** {ctx.get('url', 'unknown')}",
                f"**Captured:** {ctx.get('timestamp', '')[:19]}",
                f"**Context ID:** {context_id}",
            ]
            if ctx.get("meta", {}).get("a11y_node_count"):
                text_lines.append(f"**A11y Nodes:** {ctx['meta']['a11y_node_count']}")
            if ctx.get("description"):
                text_lines.append(f"**Description:** {ctx['description']}")

            a11y = ctx.get("a11y", [])
            if a11y:
                text_lines.append(f"\n## Accessibility Tree ({len(a11y)} elements)\n")
                for node in a11y:
                    role = node.get("role", "")
                    name = node.get("name", "")
                    value = node.get("value", "")
                    props = node.get("properties", {})
                    desc = node.get("description", "")

                    line = f"- [{role}]"
                    if name:
                        line += f' "{name}"'
                    if value:
                        line += f" value={value}"
                    if desc:
                        line += f" ({desc})"
                    if props:
                        prop_str = ", ".join(f"{k}" for k, v in props.items() if v)
                        if prop_str:
                            line += f" [{prop_str}]"
                    text_lines.append(line)

            parts.append(TextContent(type="text", text="\n".join(text_lines)))

            # Screenshot
            if include_screenshot:
                ss_path = ctx.get("screenshot_path", "")
                if ss_path and Path(ss_path).exists():
                    img_data = base64.b64encode(Path(ss_path).read_bytes()).decode()
                    parts.append(ImageContent(
                        type="image",
                        data=img_data,
                        mimeType="image/png",
                    ))

            return parts
        except httpx.ConnectError:
            return [TextContent(type="text", text="Error: Cannot connect to RDC server. Is it running?")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {e}")]

    async def _tool_capture_context(arguments: dict) -> CallToolResult:
        try:
            params = {}
            if arguments.get("description"):
                params["description"] = arguments["description"]
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(f"{RDC_SERVER_URL}/context/capture", params=params)
                if resp.status_code != 200:
                    return [TextContent(type="text", text=f"Error: {resp.status_code} — {resp.json().get('detail', resp.text)}")]
                result = resp.json()

            ctx_id = result.get("id", "")
            return [TextContent(type="text", text=(
                f"Context captured: {ctx_id}\n"
                f"Title: {result.get('title', '')}\n"
                f"URL: {result.get('url', '')}\n\n"
                f"Use get_browser_context with context_id='{ctx_id}' to see the full accessibility tree and screenshot."
            ))]
        except httpx.ConnectError:
            return [TextContent(type="text", text="Error: Cannot connect to RDC server. Is it running?")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {e}")]

    async def _read_context_resource(context_id: str) -> ReadResourceResult:
        """Read a context as a structured text resource."""
        try:
            if context_id == "latest":
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.get(f"{RDC_SERVER_URL}/context", params={"limit": 1})
                    if resp.status_code == 200:
                        contexts = resp.json()
                        if contexts:
                            context_id = contexts[0]["id"]
                        else:
                            return [TextContent(type="text", text="No contexts captured yet.")]

            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{RDC_SERVER_URL}/context/{context_id}")
                if resp.status_code != 200:
                    return [TextContent(type="text", text=f"Context not found: {context_id}")]
                ctx = resp.json()

            lines = [
                f"# Browser Context: {ctx.get('title', 'Untitled')}",
                f"URL: {ctx.get('url', '')}",
                f"Captured: {ctx.get('timestamp', '')[:19]}",
                f"ID: {context_id}",
                f"Screenshot: {ctx.get('screenshot_path', '')}",
                "",
            ]

            a11y = ctx.get("a11y", [])
            if a11y:
                lines.append(f"## Accessibility Tree ({len(a11y)} elements)\n")
                for node in a11y:
                    role = node.get("role", "")
                    name = node.get("name", "")
                    value = node.get("value", "")
                    line = f"- [{role}]"
                    if name:
                        line += f' "{name}"'
                    if value:
                        line += f" value={value}"
                    props = node.get("properties", {})
                    if props:
                        line += f" [{', '.join(k for k, v in props.items() if v)}]"
                    lines.append(line)

            return [TextContent(type="text", text="\n".join(lines))]
        except Exception as e:
            return [TextContent(type="text", text=f"Error reading context: {e}")]

    return server


async def run_server():
    """Run the MCP server."""
    server = create_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main():
    """Entry point for the MCP server."""
    import asyncio
    asyncio.run(run_server())


if __name__ == "__main__":
    main()
