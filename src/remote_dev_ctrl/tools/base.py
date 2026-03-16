"""Base classes and decorators for tools."""

from __future__ import annotations

import inspect
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, get_type_hints

if TYPE_CHECKING:
    pass


@dataclass
class ToolParam:
    """A parameter for a tool."""
    name: str
    type: str
    required: bool
    default: Any = None
    description: str = ""


@dataclass
class Tool:
    """A registered tool that can be executed."""
    
    name: str
    description: str
    func: Callable
    params: list[ToolParam] = field(default_factory=list)
    returns: str = "Any"
    tags: list[str] = field(default_factory=list)
    scope: str = "global"  # "global" or project name
    file_path: Path | None = None
    
    def __call__(self, *args, **kwargs) -> Any:
        return self.func(*args, **kwargs)
    
    def to_signature(self) -> str:
        """Generate a function signature string."""
        params = []
        for p in self.params:
            if p.required:
                params.append(f"{p.name}: {p.type}")
            else:
                default = repr(p.default)
                params.append(f"{p.name}: {p.type} = {default}")
        return f"{self.name}({', '.join(params)}) -> {self.returns}"
    
    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "name": self.name,
            "description": self.description,
            "signature": self.to_signature(),
            "params": [
                {
                    "name": p.name,
                    "type": p.type,
                    "required": p.required,
                    "description": p.description,
                }
                for p in self.params
            ],
            "returns": self.returns,
            "tags": self.tags,
            "scope": self.scope,
        }
    
    def to_prompt(self) -> str:
        """Generate documentation for LLM consumption."""
        lines = [
            f"## tool:{self.name}",
            "",
            f"**Signature:** `{self.to_signature()}`",
            "",
            f"**Description:** {self.description}",
            "",
        ]
        
        if self.params:
            lines.append("**Parameters:**")
            for p in self.params:
                req = "(required)" if p.required else "(optional)"
                lines.append(f"- `{p.name}` ({p.type}) {req}: {p.description}")
            lines.append("")
        
        lines.append(f"**Returns:** {self.returns}")
        
        if self.tags:
            lines.append(f"\n**Tags:** {', '.join(self.tags)}")
        
        return "\n".join(lines)


class ToolRegistry:
    """Registry for all available tools."""
    
    def __init__(self):
        self._tools: dict[str, Tool] = {}
    
    def register(self, t: Tool) -> None:
        self._tools[t.name] = t
    
    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)
    
    def list(self, scope: str | None = None, tag: str | None = None) -> list[Tool]:
        tools = list(self._tools.values())
        if scope:
            tools = [t for t in tools if t.scope == scope]
        if tag:
            tools = [t for t in tools if tag in t.tags]
        return sorted(tools, key=lambda t: t.name)
    
    def search(self, query: str) -> list[Tool]:
        query = query.lower()
        results = []
        for t in self._tools.values():
            if query in t.name.lower() or query in t.description.lower():
                results.append(t)
        return results
    
    def to_prompt(self) -> str:
        """Generate full tools documentation for LLM."""
        lines = ["# Available Tools", ""]
        for t in sorted(self._tools.values(), key=lambda x: x.name):
            lines.append(t.to_prompt())
            lines.append("")
        return "\n".join(lines)


# Global registry instance
_registry = ToolRegistry()


def tool(
    name: str | None = None,
    description: str | None = None,
    tags: list[str] | None = None,
):
    """Decorator to register a function as a tool.
    
    Usage:
        @tool(name="git_staged", description="Get staged files", tags=["git"])
        def git_staged_files() -> list[str]:
            ...
    """
    def decorator(func: Callable) -> Callable:
        tool_name = name or func.__name__
        tool_desc = description or func.__doc__ or "No description"
        tool_tags = tags or []
        
        # Extract parameters from function signature
        sig = inspect.signature(func)
        hints = get_type_hints(func) if hasattr(func, "__annotations__") else {}
        
        params = []
        for param_name, param in sig.parameters.items():
            param_type = hints.get(param_name, Any).__name__ if param_name in hints else "Any"
            has_default = param.default is not inspect.Parameter.empty
            params.append(ToolParam(
                name=param_name,
                type=param_type,
                required=not has_default,
                default=param.default if has_default else None,
                description="",  # Could be extracted from docstring
            ))
        
        return_type = hints.get("return", Any)
        returns = getattr(return_type, "__name__", str(return_type))
        
        t = Tool(
            name=tool_name,
            description=tool_desc.strip().split("\n")[0],  # First line only
            func=func,
            params=params,
            returns=returns,
            tags=tool_tags,
        )
        
        _registry.register(t)
        
        # Attach tool metadata to function
        func._tool = t
        return func
    
    return decorator


def get_global_registry() -> ToolRegistry:
    """Get the global tool registry."""
    return _registry
