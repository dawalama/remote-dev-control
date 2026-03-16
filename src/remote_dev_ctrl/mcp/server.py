"""MCP server implementation for remote-dev-ctrl."""

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

from ..store import load_config, load_index
from ..tools import load_all_tools
from ..skills import load_all_skills

RDC_SERVER_URL = os.environ.get("RDC_SERVER_URL", os.environ.get("RDC_SERVER_URL", "http://127.0.0.1:8420"))
from ..server.config import get_rdc_home
CONTEXTS_DIR = get_rdc_home() / "contexts"


def create_server() -> Server:
    """Create and configure the MCP server."""
    server = Server("remote-dev-ctrl")
    config = load_config()
    
    # =========================================================================
    # TOOLS - Expose all registered tools as MCP tools
    # =========================================================================
    
    @server.list_tools()
    async def list_tools() -> ListToolsResult:
        """List all available tools."""
        registry = load_all_tools(config)
        tools = []
        
        for t in registry.list():
            # Build input schema from tool params
            properties = {}
            required = []
            
            for p in t.params:
                properties[p.name] = {
                    "type": _python_type_to_json(p.type),
                    "description": p.description or f"Parameter {p.name}",
                }
                if p.required:
                    required.append(p.name)
            
            tools.append(Tool(
                name=t.name,
                description=t.description,
                inputSchema={
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            ))
        
        # Browser context tools
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

        return tools
    
    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> CallToolResult:
        """Execute a tool and return results."""

        # Browser context tools
        if name == "list_browser_contexts":
            return await _tool_list_contexts(arguments)
        if name == "get_browser_context":
            return await _tool_get_context(arguments)
        if name == "capture_browser_context":
            return await _tool_capture_context(arguments)

        registry = load_all_tools(config)
        t = registry.get(name)
        
        if not t:
            return [TextContent(type="text", text=f"Error: Tool not found: {name}")]
        
        try:
            result = t(**arguments)
            
            if isinstance(result, (dict, list)):
                text = json.dumps(result, indent=2, default=str)
            else:
                text = str(result)
            
            return [TextContent(type="text", text=text)]
        except Exception as e:
            return [TextContent(type="text", text=f"Error executing tool: {e}")]
    
    # =========================================================================
    # RESOURCES - Expose knowledge, skills, and context
    # =========================================================================
    
    @server.list_resources()
    async def list_resources() -> ListResourcesResult:
        """List all available resources."""
        resources = []
        
        # Global knowledge files
        global_ai = config.global_ai_dir
        if global_ai.exists():
            for md_file in global_ai.glob("*.md"):
                resources.append(Resource(
                    uri=f"rdc://global/{md_file.stem}",
                    name=f"Global: {md_file.stem}",
                    description=f"Global {md_file.stem} knowledge",
                    mimeType="text/markdown",
                ))
        
        # Global skills
        skills = load_all_skills(config)
        for skill in skills:
            trigger = f" ({skill.trigger})" if skill.trigger else ""
            resources.append(Resource(
                uri=f"rdc://skills/{skill.id}",
                name=f"Skill: {skill.name}{trigger}",
                description=skill.description[:100] if skill.description else "",
                mimeType="text/markdown",
            ))
        
        # Project knowledge
        for project in config.projects:
            ai_path = project.full_ai_path
            if ai_path.exists():
                for md_file in ai_path.glob("*.md"):
                    resources.append(Resource(
                        uri=f"rdc://projects/{project.name}/{md_file.stem}",
                        name=f"{project.name}: {md_file.stem}",
                        description=f"Project {md_file.stem} for {project.name}",
                        mimeType="text/markdown",
                    ))
        
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

        # Knowledge index (ToC)
        resources.append(Resource(
            uri="rdc://index",
            name="Knowledge Index",
            description="Hierarchical table of contents for all knowledge",
            mimeType="text/plain",
        ))
        
        # Tool documentation
        resources.append(Resource(
            uri="rdc://tools/docs",
            name="Tool Documentation",
            description="Documentation for all available tools",
            mimeType="text/markdown",
        ))
        
        return resources
    
    @server.read_resource()
    async def read_resource(uri: str) -> ReadResourceResult:
        """Read a specific resource."""
        parts = uri.replace("rdc://", "").split("/")
        
        if not parts:
            return [TextContent(type="text", text="Invalid URI")]
        
        category = parts[0]
        
        # Global knowledge
        if category == "global" and len(parts) >= 2:
            file_path = config.global_ai_dir / f"{parts[1]}.md"
            if file_path.exists():
                return [TextContent(type="text", text=file_path.read_text())]
            return [TextContent(type="text", text=f"File not found: {parts[1]}")]
        
        # Skills
        if category == "skills" and len(parts) >= 2:
            skill_id = parts[1]
            skills = load_all_skills(config)
            skill = next((s for s in skills if s.id == skill_id), None)
            if skill:
                return [TextContent(type="text", text=skill.to_prompt())]
            return [TextContent(type="text", text=f"Skill not found: {skill_id}")]
        
        # Project knowledge
        if category == "projects" and len(parts) >= 3:
            project_name = parts[1]
            file_name = parts[2]
            project = config.get_project(project_name)
            if project:
                file_path = project.full_ai_path / f"{file_name}.md"
                if file_path.exists():
                    return [TextContent(type="text", text=file_path.read_text())]
            return [TextContent(type="text", text=f"Not found: {project_name}/{file_name}")]
        
        # Browser context
        if category == "context" and len(parts) >= 2:
            return await _read_context_resource(parts[1])

        # Knowledge index
        if category == "index":
            index = load_index()
            if index:
                return [TextContent(type="text", text=index.to_toc())]
            return [TextContent(type="text", text="Index not built. Run 'rdc index' first.")]
        
        # Tool documentation
        if category == "tools" and len(parts) >= 2 and parts[1] == "docs":
            registry = load_all_tools(config)
            return [TextContent(type="text", text=registry.to_prompt())]
        
        return [TextContent(type="text", text=f"Unknown resource: {uri}")]
    
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


def _python_type_to_json(python_type: str) -> str:
    """Convert Python type name to JSON schema type."""
    mapping = {
        "str": "string",
        "int": "integer",
        "float": "number",
        "bool": "boolean",
        "list": "array",
        "dict": "object",
        "List": "array",
        "Dict": "object",
        "None": "null",
        "NoneType": "null",
    }
    return mapping.get(python_type, "string")


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
