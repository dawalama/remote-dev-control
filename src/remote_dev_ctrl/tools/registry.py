"""Tool discovery and loading from .ai/tools directories."""

import importlib.util
import sys
from pathlib import Path

from ..models import GlobalConfig
from .base import Tool, ToolRegistry, get_global_registry
from .builtin import BUILTIN_TOOLS


def load_tools_from_file(file_path: Path, scope: str = "global") -> list[Tool]:
    """Load tools from a Python file.
    
    The file should use the @tool decorator to register functions.
    """
    if not file_path.exists() or not file_path.suffix == ".py":
        return []
    
    if file_path.name.startswith("_"):
        return []
    
    tools = []
    
    # Create a unique module name
    module_name = f"rdc_tools_{scope}_{file_path.stem}"
    
    try:
        spec = importlib.util.spec_from_file_location(module_name, file_path)
        if spec is None or spec.loader is None:
            return []
        
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        
        # Inject the tool decorator into the module's namespace
        from .base import tool as tool_decorator
        module.__dict__["tool"] = tool_decorator
        
        spec.loader.exec_module(module)
        
        # Find all functions with _tool attribute
        for name in dir(module):
            obj = getattr(module, name)
            if callable(obj) and hasattr(obj, "_tool"):
                t = obj._tool
                t.scope = scope
                t.file_path = file_path
                tools.append(t)
    except Exception as e:
        print(f"Warning: Failed to load tools from {file_path}: {e}")
    
    return tools


def load_tools_from_dir(tools_dir: Path, scope: str = "global") -> list[Tool]:
    """Load all tools from a directory."""
    tools = []
    
    if not tools_dir.exists():
        return tools
    
    for py_file in sorted(tools_dir.glob("*.py")):
        tools.extend(load_tools_from_file(py_file, scope))
    
    return tools


def discover_tools(config: GlobalConfig) -> list[Tool]:
    """Discover all tools from global and project directories."""
    all_tools = []
    
    # Global tools
    global_tools_dir = config.global_ai_dir / "tools"
    all_tools.extend(load_tools_from_dir(global_tools_dir, "global"))
    
    # Project tools
    for project in config.projects:
        project_tools_dir = project.full_ai_path / "tools"
        all_tools.extend(load_tools_from_dir(project_tools_dir, project.name))
    
    return all_tools


def get_builtin_tools() -> list[Tool]:
    """Return tools shipped with the package (e.g. twitter_read)."""
    return list(BUILTIN_TOOLS)


def load_all_tools(config: GlobalConfig) -> ToolRegistry:
    """Load all tools into a new registry (builtins first, then discovered)."""
    registry = ToolRegistry()
    for t in get_builtin_tools():
        registry.register(t)
    for tool in discover_tools(config):
        registry.register(tool)
    return registry


def get_registry() -> ToolRegistry:
    """Get the global registry (for decorator-registered tools)."""
    return get_global_registry()
