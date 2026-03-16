"""
Tools module - Reusable executable functions for AI-assisted development.

Tools are single-purpose Python functions that can be:
1. Called directly from Python
2. Executed via CLI: `rdc tool run <tool_name> [args]`
3. Referenced in skills for the AI to use
"""

from .base import Tool, ToolRegistry, tool
from .registry import discover_tools, get_builtin_tools, get_registry, load_all_tools

__all__ = [
    "Tool",
    "ToolRegistry",
    "tool",
    "discover_tools",
    "get_builtin_tools",
    "get_registry",
    "load_all_tools",
]
