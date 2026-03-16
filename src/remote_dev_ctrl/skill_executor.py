"""Skill executor - runs skills by executing referenced tools."""

import json
import re
from pathlib import Path

from .models import Skill
from .skills import load_all_skills
from .store import load_config
from .tools import load_all_tools


def extract_tool_references(skill: Skill) -> list[str]:
    """Extract tool names referenced in a skill."""
    tools = set()
    
    # Check description and steps for tool: references
    content = skill.description + "\n" + "\n".join(skill.steps)
    
    # Match patterns like `tool:name` or tool:name
    pattern = r'`?tool:(\w+)`?'
    for match in re.finditer(pattern, content):
        tools.add(match.group(1))
    
    # Also match rdc tool run <name>
    pattern = r'rdc tool run (\w+)'
    for match in re.finditer(pattern, content):
        tools.add(match.group(1))
    
    return sorted(tools)


def execute_skill(
    skill_name: str,
    path: str = ".",
    args: dict | None = None,
    verbose: bool = False,
) -> dict:
    """
    Execute a skill by running all its referenced tools.
    
    Args:
        skill_name: Skill name or trigger (e.g., "/techdebt" or "Find Tech Debt")
        path: Working directory for tools
        args: Additional arguments to pass to tools
        verbose: Print progress
    
    Returns:
        Dictionary with tool results and summary
    """
    config = load_config()
    skills = load_all_skills(config)
    
    # Find the skill
    skill = next(
        (s for s in skills if s.name.lower() == skill_name.lower() or s.trigger == skill_name),
        None
    )
    
    if not skill:
        return {"error": f"Skill not found: {skill_name}"}
    
    # Get referenced tools
    tool_names = extract_tool_references(skill)
    
    if not tool_names:
        return {
            "error": "No tools referenced in skill",
            "skill": skill.name,
            "hint": "Skills should reference tools using `tool:name` or `rdc tool run name`",
        }
    
    # Load tool registry
    registry = load_all_tools(config)
    
    results = {
        "skill": skill.name,
        "trigger": skill.trigger,
        "description": skill.description.split("\n")[0],
        "tools_executed": [],
        "tool_results": {},
        "errors": [],
    }
    
    # Execute each tool
    for tool_name in tool_names:
        tool = registry.get(tool_name)
        
        if not tool:
            results["errors"].append(f"Tool not found: {tool_name}")
            continue
        
        if verbose:
            print(f"Running: {tool_name}...")
        
        try:
            # Build kwargs based on tool params and provided args
            kwargs = {}
            
            for param in tool.params:
                # Check if this param should get the path
                if param.name == "path":
                    kwargs["path"] = path
                elif param.name == "filepath" and args and "filepath" in args:
                    kwargs["filepath"] = args["filepath"]
                elif args and param.name in args:
                    kwargs[param.name] = args[param.name]
            
            result = tool(**kwargs)
            results["tools_executed"].append(tool_name)
            results["tool_results"][tool_name] = result
            
        except Exception as e:
            results["errors"].append(f"{tool_name}: {str(e)}")
    
    return results


def format_techdebt_report(results: dict) -> str:
    """Format techdebt skill results as a readable report."""
    lines = [
        f"# Tech Debt Report",
        f"",
        f"**Skill:** {results.get('skill', 'Unknown')}",
        f"",
    ]
    
    # TODOs
    if "find_todos" in results.get("tool_results", {}):
        todos = results["tool_results"]["find_todos"]
        if todos:
            lines.append("## TODOs/FIXMEs Found")
            lines.append("")
            for item in todos[:20]:  # Limit to 20
                lines.append(f"- `{item.get('file', '?')}:{item.get('line', '?')}` - {item.get('content', '')[:80]}")
            if len(todos) > 20:
                lines.append(f"- ... and {len(todos) - 20} more")
            lines.append("")
        else:
            lines.append("## TODOs: None found ✓")
            lines.append("")
    
    # Duplicates
    if "find_duplicates" in results.get("tool_results", {}):
        dupes = results["tool_results"]["find_duplicates"]
        if dupes:
            lines.append("## Potential Duplicates")
            lines.append("")
            for item in dupes[:10]:
                locations = item.get("locations", [])
                lines.append(f"- Found in {item.get('count', '?')} places:")
                for loc in locations[:3]:
                    lines.append(f"  - `{loc.get('file', '?')}:{loc.get('line', '?')}`")
            lines.append("")
        else:
            lines.append("## Duplicates: None found ✓")
            lines.append("")
    
    # Git status
    if "git_status_summary" in results.get("tool_results", {}):
        status = results["tool_results"]["git_status_summary"]
        lines.append("## Git Status")
        lines.append("")
        if status.get("clean"):
            lines.append("- Repository is clean ✓")
        else:
            if status.get("staged"):
                lines.append(f"- Staged: {len(status['staged'])} files")
            if status.get("modified"):
                lines.append(f"- Modified: {len(status['modified'])} files")
            if status.get("untracked"):
                lines.append(f"- Untracked: {len(status['untracked'])} files")
        lines.append("")
    
    # Errors
    if results.get("errors"):
        lines.append("## Errors")
        lines.append("")
        for err in results["errors"]:
            lines.append(f"- {err}")
        lines.append("")
    
    return "\n".join(lines)


def format_generic_report(results: dict) -> str:
    """Format generic skill results as a readable report."""
    lines = [
        f"# Skill Execution Report",
        f"",
        f"**Skill:** {results.get('skill', 'Unknown')}",
        f"**Description:** {results.get('description', '')}",
        f"",
        f"## Tools Executed",
        f"",
    ]
    
    for tool_name in results.get("tools_executed", []):
        lines.append(f"### {tool_name}")
        lines.append("")
        result = results.get("tool_results", {}).get(tool_name, {})
        if isinstance(result, (dict, list)):
            lines.append("```json")
            lines.append(json.dumps(result, indent=2, default=str)[:2000])
            lines.append("```")
        else:
            lines.append(f"```\n{str(result)[:2000]}\n```")
        lines.append("")
    
    if results.get("errors"):
        lines.append("## Errors")
        lines.append("")
        for err in results["errors"]:
            lines.append(f"- {err}")
    
    return "\n".join(lines)
