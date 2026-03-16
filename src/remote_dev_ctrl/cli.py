"""CLI interface for remote-dev-ctrl."""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich import print as rprint
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.tree import Tree

from .indexer import build_full_index
from .models import KnowledgeNode, NodeType
from .store import (
    get_config_path,
    get_index_path,
    load_config,
    load_index,
    save_config,
    save_index,
)

app = typer.Typer(
    name="rdc",
    help="Remote Dev Ctrl - Command center for AI-assisted development",
    no_args_is_help=True,
)
console = Console()


@app.command()
def init(
    name: Annotated[Optional[str], typer.Argument(help="Project name (omit for global init)")] = None,
    description: Annotated[Optional[str], typer.Option("--desc", "-d", help="Project description for AI analysis")] = None,
    path: Annotated[Optional[Path], typer.Option("--path", "-p", help="Project path (defaults to ./<name>)")] = None,
    proj_type: Annotated[Optional[str], typer.Option("--type", "-t", help="Override: backend, frontend, fullstack")] = None,
    backend: Annotated[Optional[str], typer.Option("--backend", "-b", help="Override: fastapi, express, django")] = None,
    frontend: Annotated[Optional[str], typer.Option("--frontend", "-f", help="Override: react, vue, nextjs")] = None,
    database: Annotated[Optional[str], typer.Option("--database", help="Override: postgres, mongodb, sqlite")] = None,
    deployment: Annotated[Optional[str], typer.Option("--deploy", help="Override: docker, render, vercel")] = None,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip confirmation")] = False,
    no_register: Annotated[bool, typer.Option("--no-register", help="Don't register with rdc")] = False,
):
    """Initialize a new project or global .ai directory.
    
    Without arguments: Initialize global ~/.ai/ directory.
    With name: Create a new project with AI-inferred configuration.
    
    Examples:
        rdc init                                    # Global init
        rdc init myapi --desc "REST API for invoices"
        rdc init myapp --type=fullstack --backend=fastapi
    """
    # Global init if no name provided
    if not name:
        _init_global()
        return
    
    # Project init
    from .llm import analyze_project_description, is_ollama_available
    from .scaffold import create_project
    
    # Get project configuration
    if not description:
        rprint("[bold]Let's set up your new project![/bold]")
        description = typer.prompt("Describe what you are creating (e.g. 'A fullstack react/fastapi app for booking flights')")

    rprint(f"[bold]Analyzing project description...[/bold]")
    import os
    from .server.vault import get_secret
    if get_secret("OPENROUTER_API_KEY") or os.getenv("OPENROUTER_API_KEY") or get_secret("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY"):
        rprint("  Using Cloud LLM (OpenRouter/OpenAI)")
    elif is_ollama_available():
        rprint("  Using local LLM (Ollama)")
    else:
        rprint("  Using heuristics (No LLM available)")
    
    inferred = analyze_project_description(description)
    inferred["description"] = description
    
    # Check if LLM suggested a better name
    suggested_name = inferred.get("suggested_name")
    generic_names = {"myapi", "myapp", "myproject", "app", "api", "project", "test", "demo"}
    
    if suggested_name and name.lower() in generic_names:
        # LLM found a name in description and user gave generic name
        project_name = suggested_name
        rprint(f"  [cyan]Suggested name:[/cyan] {suggested_name} (from description)")
    elif suggested_name and name.lower() != suggested_name.lower():
        # LLM found a different name - will ask user
        project_name = name  # Use provided name for now, ask later
    else:
        project_name = name
    
    project_path = path or Path.cwd() / project_name
    
    # Apply overrides
    if proj_type:
        inferred["type"] = proj_type
    if backend:
        inferred["stack"]["backend"] = backend
    if frontend:
        inferred["stack"]["frontend"] = frontend
    if database:
        inferred["database"] = database
    if deployment:
        inferred["deployment"] = deployment
    
    # Show configuration
    rprint("")
    rprint(Panel(
        f"[bold]Name:[/bold]       {project_name}\n"
        f"[bold]Type:[/bold]       {inferred['type']}\n"
        f"[bold]Backend:[/bold]    {inferred['stack'].get('backend', 'none')}\n"
        f"[bold]Frontend:[/bold]   {inferred['stack'].get('frontend', 'none')}\n"
        f"[bold]Database:[/bold]   {inferred.get('database', 'none')}\n"
        f"[bold]Deployment:[/bold] {inferred.get('deployment', 'docker')}\n"
        f"[bold]Features:[/bold]   {', '.join(inferred.get('features', [])) or 'none'}\n"
        f"\n[dim]{inferred.get('reasoning', '')}[/dim]",
        title="Project Configuration",
        border_style="cyan"
    ))
    rprint(f"[bold]Path:[/bold] {project_path}")
    rprint("")
    
    # Confirm
    if not yes:
        proceed = typer.confirm("Create project with this configuration?", default=True)
        if not proceed:
            # Allow editing
            edit = typer.confirm("Edit configuration?", default=True)
            if edit:
                # Allow changing the name
                new_name = typer.prompt("Name", default=project_name)
                new_type = typer.prompt("Type", default=inferred["type"])
                new_backend = typer.prompt("Backend", default=inferred["stack"].get("backend", "none"))
                new_frontend = typer.prompt("Frontend", default=inferred["stack"].get("frontend", "none"))
                new_db = typer.prompt("Database", default=inferred.get("database", "none"))
                new_deploy = typer.prompt("Deployment", default=inferred.get("deployment", "docker"))
                
                project_name = new_name
                project_path = path or Path.cwd() / project_name
                inferred["type"] = new_type
                inferred["stack"]["backend"] = new_backend
                inferred["stack"]["frontend"] = new_frontend
                inferred["database"] = new_db
                inferred["deployment"] = new_deploy
            else:
                rprint("[yellow]Cancelled.[/yellow]")
                raise typer.Exit(0)
    
    # Create project
    rprint("")
    rprint(f"[bold]Creating project...[/bold]")
    
    result = create_project(
        path=project_path,
        name=project_name,
        config=inferred,
        register=not no_register,
    )
    
    for f in result["created_files"]:
        rprint(f"  [green]✓[/green] {f}")
    
    # Register in DB
    if not no_register:
        from .server.db import init_databases, ProjectRepository
        from .server.db.models import Project as DBProject
        init_databases()
        repo = ProjectRepository()
        tag_list = [inferred["type"]] + inferred.get("features", [])
        tag_list = [t for t in tag_list if t]
        repo.upsert(DBProject(
            name=project_name,
            path=str(project_path),
            description=description,
            tags=tag_list,
        ))
        rprint(f"  [green]✓[/green] Registered with rdc")
    
    # Initialize git
    import subprocess
    try:
        subprocess.run(["git", "init"], cwd=project_path, capture_output=True, check=True)
        rprint(f"  [green]✓[/green] Initialized git repository")
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    
    rprint("")
    rprint(f"[green]✓ Project created at {project_path}[/green]")
    rprint("")
    rprint("[bold]Next steps:[/bold]")
    rprint(f"  cd {project_path}")
    
    if inferred["stack"].get("backend") in ("fastapi", "django"):
        rprint("  uv sync")
        rprint("  make dev")
    elif inferred["stack"].get("backend") == "express":
        rprint("  pnpm install")
        rprint("  pnpm dev")
    elif inferred["stack"].get("frontend") != "none":
        if inferred["type"] == "fullstack":
            rprint("  uv sync && cd frontend && pnpm install")
        else:
            rprint("  pnpm install")
            rprint("  pnpm dev")


def _init_global():
    """Initialize global .ai directory."""
    config = load_config()
    
    global_ai_dir = config.global_ai_dir
    global_ai_dir.mkdir(parents=True, exist_ok=True)
    
    rules_file = global_ai_dir / "rules.md"
    if not rules_file.exists():
        rules_file.write_text("""# Global AI Rules

> Universal rules for all projects. AI assistants should follow these unless project-specific rules override them.

## Remote Dev Ctrl (rdc)

You have access to `rdc` - a CLI for knowledge, skills, and tools:

```bash
# Run skills (high-level workflows)
rdc run skill techdebt              # Find code issues
rdc run skill review                # Code review

# Run tools (single-purpose functions)
rdc run tool git_status_summary     # Git status as JSON
rdc run tool find_todos path=src    # Find TODOs

# Discovery
rdc skill list                      # List available skills
rdc tool list                       # List available tools
```

## Code Style

- Write concise, functional code
- Keep variable names clear and minimal
- No obvious comments unless explicitly requested
- Clean up unused imports after changes
- Add error handling only where critical

## Behavior

- Don't jump to the first solution—verify assumptions first
- High confidence: proceed. Low confidence: ask.
- Suggest solutions the user didn't think of
- Value good arguments over authority
- Speculation is fine, but flag it

## After Corrections

When corrected, ask: "Should I add this to learnings?"
Then run: `rdc learn "Title" -i "issue" -c "correction"`
""")
    
    learnings_file = global_ai_dir / "learnings.md"
    if not learnings_file.exists():
        learnings_file.write_text("""# Global Learnings

> Universal corrections that apply across all projects.

<!-- New entries are added below this line -->

---

*No entries yet.*
""")
    
    # Create skills and tools directories
    (global_ai_dir / "skills").mkdir(exist_ok=True)
    (global_ai_dir / "tools").mkdir(exist_ok=True)
    
    save_config(config)
    
    rprint(f"[green]✓[/green] Initialized global AI directory at {global_ai_dir}")
    rprint(f"[green]✓[/green] Configuration saved to {get_config_path()}")


@app.command()
def add(
    path: Annotated[Path, typer.Argument(help="Path to the project directory")],
    name: Annotated[Optional[str], typer.Option("--name", "-n", help="Project name")] = None,
    description: Annotated[Optional[str], typer.Option("--desc", "-d", help="Project description")] = None,
    tags: Annotated[Optional[str], typer.Option("--tags", "-t", help="Comma-separated tags")] = None,
):
    """Register a project with the knowledge system."""
    path = path.expanduser().resolve()
    
    if not path.exists():
        rprint(f"[red]Error:[/red] Path does not exist: {path}")
        raise typer.Exit(1)
    
    project_name = name or path.name
    tag_list = [t.strip() for t in tags.split(",")] if tags else []
    
    # Auto-infer details and generate rdc.yaml if not provided
    from .llm import analyze_existing_project
    import yaml
    
    if not name or not description:
        rprint("[bold]Analyzing existing project to infer details...[/bold]")
        import os
        from .server.vault import get_secret
        from .llm import is_ollama_available
        if get_secret("OPENROUTER_API_KEY") or os.getenv("OPENROUTER_API_KEY") or get_secret("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY"):
            rprint("  Using Cloud LLM (OpenRouter/OpenAI)")
        elif is_ollama_available():
            rprint("  Using local LLM (Ollama)")
        else:
            rprint("  Using heuristics (No LLM available)")
            
        inferred = analyze_existing_project(path)
        project_name = name or inferred.get("name", path.name)
        description = description or inferred.get("description", "")
        
        if not tags:
            tag_list = [inferred.get("type")] + inferred.get("features", [])
            tag_list = [t for t in tag_list if t]
    
    # Always try to generate rdc.yaml if it doesn't exist
    rdc_yaml = path / "rdc.yaml"
    if not rdc_yaml.exists():
        try:
            # Re-infer if we didn't do it above
            if 'inferred' not in locals():
                inferred = analyze_existing_project(path)
            
            yaml_content = {
                "name": project_name,
                "description": description,
                "type": inferred.get("type", "backend"),
                "stack": inferred.get("stack", {}),
                "features": inferred.get("features", []),
            }
            rdc_yaml.write_text(yaml.dump(yaml_content, sort_keys=False))
            rprint(f"[green]✓[/green] Generated {rdc_yaml.name}")
        except Exception as e:
            rprint(f"[yellow]Could not generate rdc.yaml: {e}[/yellow]")
    
    ai_dir = path / ".ai"
    if not ai_dir.exists():
        ai_dir.mkdir(parents=True)

        (ai_dir / "rules.md").write_text(f"""# {project_name} Rules

> Project-specific rules and patterns for AI assistants.

## Stack

<!-- Add your technology stack here -->

## Conventions

<!-- Add project-specific conventions -->

## Patterns

- Check `plan/decisions.md` for architectural context
- Check `.ai/learnings.md` for past corrections
""")

        (ai_dir / "learnings.md").write_text(f"""# {project_name} Learnings

> Project-specific corrections and lessons learned.

---

*No entries yet.*
""")

        (ai_dir / "context.md").write_text(f"""# {project_name} Context

> Quick reference for AI assistants.

## Overview

<!-- What is this project? -->

## Key Directories

<!-- Important directories and their purpose -->

## Common Tasks

<!-- How to perform common operations -->
""")

        rprint(f"[green]✓[/green] Created .ai/ directory with templates")

    # Register in DB
    from .server.db import init_databases, ProjectRepository
    from .server.db.models import Project as DBProject
    init_databases()
    repo = ProjectRepository()
    if repo.get(project_name):
        rprint(f"[yellow]Project already registered:[/yellow] {project_name}")
        raise typer.Exit(1)
    repo.create(DBProject(
        name=project_name,
        path=str(path),
        description=description,
        tags=tag_list,
    ))

    rprint(f"[green]✓[/green] Registered project: {project_name}")
    rprint(f"   Path: {path}")
    if description:
        rprint(f"   Description: {description}")
    if tag_list:
        rprint(f"   Tags: {', '.join(tag_list)}")


@app.command()
def remove(
    name: Annotated[str, typer.Argument(help="Project name to remove")],
):
    """Remove a project from the knowledge system."""
    from .server.db import init_databases, ProjectRepository
    init_databases()
    repo = ProjectRepository()

    project = repo.get(name)
    if not project:
        rprint(f"[red]Error:[/red] Project not found: {name}")
        raise typer.Exit(1)

    repo.delete(project.id)

    rprint(f"[green]✓[/green] Removed project: {name}")
    rprint(f"   Note: .ai/ directory was not deleted from {project.path}")


@app.command(name="list")
def list_projects():
    """List all registered projects."""
    from .server.db import init_databases, ProjectRepository
    init_databases()
    repo = ProjectRepository()
    projects = repo.list()

    if not projects:
        rprint("[yellow]No projects registered.[/yellow]")
        rprint("Use 'rdc add <path>' to register a project.")
        return

    table = Table(title="Registered Projects")
    table.add_column("Name", style="cyan")
    table.add_column("Path")
    table.add_column("Tags", style="green")
    table.add_column(".ai/ exists", style="yellow")

    for p in projects:
        ai_exists = "✓" if Path(p.path, ".ai").exists() else "✗"
        table.add_row(p.name, p.path, ", ".join(p.tags) or "-", ai_exists)

    console.print(table)


@app.command()
def index(
    refresh: Annotated[bool, typer.Option("--refresh", "-r", help="Force rebuild index")] = False,
):
    """Build or refresh the knowledge index."""
    config = load_config()
    
    existing = load_index()
    if existing and not refresh:
        rprint("[yellow]Index exists.[/yellow] Use --refresh to rebuild.")
        rprint(f"   Location: {get_index_path()}")
        return
    
    rprint("Building knowledge index...")
    root = build_full_index(config)
    save_index(root)
    
    stats = {
        "projects": len(root.find_by_type(NodeType.PROJECT)),
        "documents": len(root.find_by_type(NodeType.DOCUMENT)),
        "sections": len(root.find_by_type(NodeType.SECTION)),
    }
    
    rprint(f"[green]✓[/green] Index built successfully")
    rprint(f"   Projects: {stats['projects']}")
    rprint(f"   Documents: {stats['documents']}")
    rprint(f"   Sections: {stats['sections']}")
    rprint(f"   Location: {get_index_path()}")


@app.command()
def tree():
    """Display the knowledge tree structure."""
    index = load_index()
    
    if not index:
        rprint("[yellow]No index found.[/yellow] Run 'rdc index' first.")
        return
    
    rprint(Panel(index.to_toc(), title="Knowledge Tree", border_style="blue"))


@app.command()
def toc(
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: text, json")] = "text",
):
    """Output the table of contents for LLM consumption."""
    index = load_index()
    
    if not index:
        rprint("[yellow]No index found.[/yellow] Run 'rdc index' first.")
        raise typer.Exit(1)
    
    if format == "json":
        print(json.dumps(index.to_compact_json(), indent=2))
    else:
        print(index.to_toc())


@app.command()
def get(
    node_id: Annotated[str, typer.Argument(help="Node ID to retrieve")],
    content: Annotated[bool, typer.Option("--content", "-c", help="Include file content")] = False,
):
    """Retrieve a specific node by ID."""
    index = load_index()
    
    if not index:
        rprint("[yellow]No index found.[/yellow] Run 'rdc index' first.")
        raise typer.Exit(1)
    
    node = index.find_by_id(node_id)
    
    if not node:
        rprint(f"[red]Error:[/red] Node not found: {node_id}")
        raise typer.Exit(1)
    
    rprint(Panel(
        f"[bold]Name:[/bold] {node.name}\n"
        f"[bold]Type:[/bold] {node.node_type.value}\n"
        f"[bold]Summary:[/bold] {node.summary or 'N/A'}\n"
        f"[bold]File:[/bold] {node.file_path or 'N/A'}\n"
        f"[bold]Lines:[/bold] {node.start_line}-{node.end_line}" if node.start_line else "",
        title=f"Node: {node_id}",
        border_style="cyan"
    ))
    
    if content and node.file_path and node.file_path.exists():
        file_content = node.file_path.read_text()
        if node.start_line is not None and node.end_line is not None:
            lines = file_content.split("\n")
            file_content = "\n".join(lines[node.start_line:node.end_line + 1])
        rprint(Panel(file_content, title="Content", border_style="green"))


@app.command()
def search(
    query: Annotated[str, typer.Argument(help="Search query")],
    tag: Annotated[Optional[str], typer.Option("--tag", "-t", help="Filter by tag")] = None,
):
    """Search the knowledge base."""
    index = load_index()
    
    if not index:
        rprint("[yellow]No index found.[/yellow] Run 'rdc index' first.")
        raise typer.Exit(1)
    
    results = []
    query_lower = query.lower()
    
    def search_node(node: "KnowledgeNode"):
        if tag and tag not in node.tags:
            return
        
        match_score = 0
        if query_lower in node.name.lower():
            match_score += 2
        if node.summary and query_lower in node.summary.lower():
            match_score += 1
        
        if match_score > 0:
            results.append((node, match_score))
        
        for child in node.children:
            search_node(child)
    
    search_node(index)
    
    if not results:
        rprint(f"[yellow]No results for:[/yellow] {query}")
        return
    
    results.sort(key=lambda x: x[1], reverse=True)
    
    table = Table(title=f"Search Results: '{query}'")
    table.add_column("ID", style="cyan")
    table.add_column("Name")
    table.add_column("Type", style="green")
    table.add_column("Summary")
    
    for node, _ in results[:10]:
        summary = (node.summary[:50] + "...") if node.summary and len(node.summary) > 50 else (node.summary or "-")
        table.add_row(node.id, node.name, node.node_type.value, summary)
    
    console.print(table)


@app.command()
def context(
    project: Annotated[Optional[str], typer.Option("--project", "-p", help="Specific project")] = None,
    output: Annotated[str, typer.Option("--output", "-o", help="Output format: text, json, markdown")] = "markdown",
):
    """Generate context for an AI assistant session."""
    index = load_index()
    config = load_config()
    
    if not index:
        rprint("[yellow]No index found.[/yellow] Run 'rdc index' first.")
        raise typer.Exit(1)
    
    if output == "json":
        if project:
            proj_node = next(
                (n for n in index.find_by_type(NodeType.PROJECT) if n.name == project),
                None
            )
            if proj_node:
                print(json.dumps(proj_node.to_compact_json(), indent=2))
            else:
                rprint(f"[red]Error:[/red] Project not found: {project}")
        else:
            print(json.dumps(index.to_compact_json(), indent=2))
    else:
        lines = ["# AI Knowledge Context", ""]
        lines.append("## How to Use This Index")
        lines.append("")
        lines.append("1. Read the Table of Contents below to understand available knowledge")
        lines.append("2. Use node IDs to request specific content: `rdc get <node_id> --content`")
        lines.append("3. Search for topics: `rdc search <query>`")
        lines.append("")
        lines.append("## Table of Contents")
        lines.append("")
        lines.append("```")
        lines.append(index.to_toc())
        lines.append("```")
        
        print("\n".join(lines))


@app.command()
def learn(
    title: Annotated[str, typer.Argument(help="Brief title for the learning")],
    issue: Annotated[str, typer.Option("--issue", "-i", help="What was done incorrectly")] = "",
    correction: Annotated[str, typer.Option("--correction", "-c", help="What should be done instead")] = "",
    context_text: Annotated[Optional[str], typer.Option("--context", help="Additional context")] = None,
    project: Annotated[Optional[str], typer.Option("--project", "-p", help="Project name (omit for global)")] = None,
    interactive: Annotated[bool, typer.Option("--interactive", "-I", help="Interactive mode")] = False,
):
    """Add a new learning entry."""
    from datetime import datetime
    
    config = load_config()
    
    if interactive or not issue or not correction:
        rprint("[bold]Add a new learning[/bold]\n")
        if not issue:
            issue = typer.prompt("Issue (what went wrong)")
        if not correction:
            correction = typer.prompt("Correction (what to do instead)")
        if context_text is None:
            context_text = typer.prompt("Context (optional, press Enter to skip)", default="")
    
    if project:
        proj = config.get_project(project)
        if not proj:
            rprint(f"[red]Error:[/red] Project not found: {project}")
            raise typer.Exit(1)
        learnings_path = proj.full_ai_path / "learnings.md"
    else:
        learnings_path = config.global_ai_dir / "learnings.md"
    
    if not learnings_path.exists():
        rprint(f"[red]Error:[/red] Learnings file not found: {learnings_path}")
        raise typer.Exit(1)
    
    date_str = datetime.now().strftime("%Y-%m-%d")
    
    entry = f"""
### {date_str}: {title}

**Issue:** {issue}

**Correction:** {correction}
"""
    if context_text:
        entry += f"\n**Context:** {context_text}\n"
    
    content = learnings_path.read_text()
    
    insert_marker = "<!-- New entries are added below this line -->"
    if insert_marker in content:
        new_content = content.replace(insert_marker, insert_marker + "\n" + entry)
    elif "---\n" in content:
        parts = content.split("---\n", 1)
        new_content = parts[0] + "---\n" + entry + "\n" + parts[1].lstrip()
    else:
        new_content = content.rstrip() + "\n" + entry
    
    if "*No entries yet.*" in new_content:
        new_content = new_content.replace("*No entries yet.*", "")
    
    learnings_path.write_text(new_content)
    
    scope = f"project '{project}'" if project else "global"
    rprint(f"[green]✓[/green] Added learning to {scope}: {title}")
    
    rdc_index = load_index()
    if rdc_index:
        rprint("   Rebuilding index...")
        new_index = build_full_index(config)
        save_index(new_index)
        rprint("   [green]✓[/green] Index updated")


@app.command()
def watch():
    """Watch .ai/ directories and auto-rebuild index on changes."""
    from .watcher import watch_knowledge_dirs
    watch_knowledge_dirs()


# Run subcommand group (unified execution)
run_app = typer.Typer(help="Run skills or tools")
app.add_typer(run_app, name="run")


@run_app.command("skill")
def run_skill(
    name: Annotated[str, typer.Argument(help="Skill name (e.g., techdebt, review, commit)")],
    path: Annotated[str, typer.Option("--path", "-p", help="Working directory")] = ".",
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Show progress")] = False,
    json_output: Annotated[bool, typer.Option("--json", "-j", help="Output as JSON")] = False,
):
    """Execute a skill by running all its referenced tools."""
    from .skill_executor import (
        execute_skill,
        format_techdebt_report,
        format_generic_report,
    )
    
    # Normalize name - remove leading / if present
    skill_name = name.lstrip("/")
    
    if verbose:
        rprint(f"[bold]Executing skill:[/bold] {skill_name}")
        rprint(f"[bold]Path:[/bold] {path}")
        rprint("")
    
    # Try to find by name or with / prefix
    results = execute_skill(skill_name, path=path, verbose=verbose)
    if "error" in results and "not found" in results.get("error", "").lower():
        results = execute_skill(f"/{skill_name}", path=path, verbose=verbose)
    
    if "error" in results and not results.get("tools_executed"):
        rprint(f"[red]Error:[/red] {results['error']}")
        if "hint" in results:
            rprint(f"[yellow]Hint:[/yellow] {results['hint']}")
        raise typer.Exit(1)
    
    if json_output:
        print(json.dumps(results, indent=2, default=str))
    else:
        # Use specialized formatter for known skills
        if skill_name in ("techdebt", "Find Tech Debt"):
            report = format_techdebt_report(results)
        else:
            report = format_generic_report(results)
        
        rprint(Panel(report, title=f"Skill: {results.get('skill', skill_name)}", border_style="green"))


@run_app.command("tool")
def run_tool(
    name: Annotated[str, typer.Argument(help="Tool name (e.g., find_todos, git_status_summary)")],
    args: Annotated[Optional[list[str]], typer.Argument(help="Arguments as key=value")] = None,
    json_output: Annotated[bool, typer.Option("--json", "-j", help="Output as JSON")] = False,
):
    """Execute a tool and display results."""
    from .tools import load_all_tools
    
    config = load_config()
    registry = load_all_tools(config)
    t = registry.get(name)
    
    if not t:
        rprint(f"[red]Error:[/red] Tool not found: {name}")
        raise typer.Exit(1)
    
    # Parse arguments
    kwargs = {}
    if args:
        for arg in args:
            if "=" in arg:
                key, value = arg.split("=", 1)
                try:
                    kwargs[key] = json.loads(value)
                except json.JSONDecodeError:
                    kwargs[key] = value
            else:
                rprint(f"[red]Error:[/red] Invalid argument format: {arg}")
                rprint("Use key=value format")
                raise typer.Exit(1)
    
    try:
        result = t(**kwargs)
        
        if json_output:
            print(json.dumps({"result": result}, indent=2, default=str))
        else:
            if isinstance(result, (list, dict)):
                rprint(Panel(json.dumps(result, indent=2, default=str), title="Result"))
            else:
                rprint(f"[green]Result:[/green] {result}")
    except Exception as e:
        rprint(f"[red]Error executing tool:[/red] {e}")
        raise typer.Exit(1)


# Skills subcommand group (for management: list, show, new)
skills_app = typer.Typer(help="Manage reusable AI skills/workflows")
app.add_typer(skills_app, name="skill")


@skills_app.command("new")
def skill_new(
    name: Annotated[str, typer.Argument(help="Name for the new skill")],
    trigger: Annotated[Optional[str], typer.Option("--trigger", "-t", help="Slash command trigger")] = None,
    project: Annotated[Optional[str], typer.Option("--project", "-p", help="Project (omit for global)")] = None,
):
    """Create a new skill from template."""
    from .skills import create_skill_template, generate_skill_id
    
    config = load_config()
    
    if project:
        proj = config.get_project(project)
        if not proj:
            rprint(f"[red]Error:[/red] Project not found: {project}")
            raise typer.Exit(1)
        skills_dir = proj.skills_path
    else:
        skills_dir = config.global_skills_path
    
    skills_dir.mkdir(parents=True, exist_ok=True)
    
    skill_id = generate_skill_id(name)
    skill_path = skills_dir / f"{skill_id}.md"
    
    if skill_path.exists():
        rprint(f"[red]Error:[/red] Skill already exists: {skill_path}")
        raise typer.Exit(1)
    
    template = create_skill_template(name, trigger)
    skill_path.write_text(template)
    
    scope = f"project '{project}'" if project else "global"
    rprint(f"[green]✓[/green] Created skill in {scope}: {skill_path}")
    rprint(f"   Edit the file to define your skill's steps and inputs")


@skills_app.command("list")
def skill_list(
    project: Annotated[Optional[str], typer.Option("--project", "-p", help="Filter by project")] = None,
):
    """List all available skills."""
    from .skills import load_all_skills, load_skills_from_dir
    
    config = load_config()
    
    if project:
        proj = config.get_project(project)
        if not proj:
            rprint(f"[red]Error:[/red] Project not found: {project}")
            raise typer.Exit(1)
        skills = load_skills_from_dir(proj.skills_path, project)
    else:
        skills = load_all_skills(config)
    
    if not skills:
        rprint("[yellow]No skills found.[/yellow]")
        rprint("Use 'rdc skill new <name>' to create one.")
        return
    
    table = Table(title="Available Skills")
    table.add_column("Name", style="cyan")
    table.add_column("Trigger", style="green")
    table.add_column("Scope")
    table.add_column("Description")
    
    for skill in skills:
        desc = (skill.description[:40] + "...") if len(skill.description) > 40 else skill.description
        table.add_row(
            skill.name,
            skill.trigger or "-",
            skill.scope,
            desc.replace("\n", " "),
        )
    
    console.print(table)


@skills_app.command("show")
def skill_show(
    name: Annotated[str, typer.Argument(help="Skill name or trigger")],
    prompt: Annotated[bool, typer.Option("--prompt", help="Output as LLM prompt")] = False,
):
    """Show details of a specific skill."""
    from .skills import load_all_skills
    
    config = load_config()
    skills = load_all_skills(config)
    
    # Find by name or trigger
    skill = next(
        (s for s in skills if s.name.lower() == name.lower() or s.trigger == name),
        None
    )
    
    if not skill:
        rprint(f"[red]Error:[/red] Skill not found: {name}")
        raise typer.Exit(1)
    
    if prompt:
        print(skill.to_prompt())
    else:
        rprint(Panel(
            f"[bold]Name:[/bold] {skill.name}\n"
            f"[bold]Trigger:[/bold] {skill.trigger or 'N/A'}\n"
            f"[bold]Scope:[/bold] {skill.scope}\n"
            f"[bold]File:[/bold] {skill.file_path}\n"
            f"[bold]Tags:[/bold] {', '.join(skill.tags) or 'N/A'}\n\n"
            f"[bold]Description:[/bold]\n{skill.description}\n\n"
            f"[bold]Steps:[/bold]\n" + "\n".join(f"  {i}. {s}" for i, s in enumerate(skill.steps, 1)),
            title=f"Skill: {skill.name}",
            border_style="cyan"
        ))


@skills_app.command("prompt")
def skill_prompt(
    name: Annotated[str, typer.Argument(help="Skill name or trigger")],
):
    """Output skill as LLM prompt (for piping to AI tools)."""
    from .skills import load_all_skills
    
    config = load_config()
    skills = load_all_skills(config)
    
    # Normalize name
    skill_name = name.lstrip("/")
    
    skill = next(
        (s for s in skills if s.name.lower() == skill_name.lower() 
         or s.trigger == name or s.trigger == f"/{skill_name}"),
        None
    )
    
    if not skill:
        rprint(f"[red]Error:[/red] Skill not found: {name}", file=__import__("sys").stderr)
        raise typer.Exit(1)
    
    print(skill.to_prompt())


# Tools subcommand group
tools_app = typer.Typer(help="Manage reusable code tools/functions")
app.add_typer(tools_app, name="tool")


@tools_app.command("new")
def tool_new(
    name: Annotated[str, typer.Argument(help="Name for the new tool")],
    description: Annotated[str, typer.Option("--desc", "-d", help="Tool description")] = "",
    project: Annotated[Optional[str], typer.Option("--project", "-p", help="Project (omit for global)")] = None,
):
    """Create a new tool from template."""
    config = load_config()
    
    if project:
        proj = config.get_project(project)
        if not proj:
            rprint(f"[red]Error:[/red] Project not found: {project}")
            raise typer.Exit(1)
        tools_dir = proj.full_ai_path / "tools"
    else:
        tools_dir = config.global_ai_dir / "tools"
    
    tools_dir.mkdir(parents=True, exist_ok=True)
    
    # Convert name to valid Python identifier
    tool_filename = name.lower().replace("-", "_").replace(" ", "_")
    tool_path = tools_dir / f"{tool_filename}.py"
    
    if tool_path.exists():
        rprint(f"[red]Error:[/red] Tool file already exists: {tool_path}")
        raise typer.Exit(1)
    
    func_name = tool_filename
    desc = description or f"Description for {name}"
    
    template = f'''"""Tools for {name}."""

from remote_dev_ctrl.tools import tool


@tool(name="{func_name}", description="{desc}", tags=[])
def {func_name}() -> str:
    """
    {desc}
    
    Returns:
        Result of the operation
    """
    # TODO: Implement this tool
    return "Not implemented"
'''
    
    tool_path.write_text(template)
    
    scope = f"project '{project}'" if project else "global"
    rprint(f"[green]✓[/green] Created tool in {scope}: {tool_path}")
    rprint(f"   Edit the file to implement your tool function")


@tools_app.command("list")
def tool_list(
    project: Annotated[Optional[str], typer.Option("--project", "-p", help="Filter by project")] = None,
    tag: Annotated[Optional[str], typer.Option("--tag", "-t", help="Filter by tag")] = None,
):
    """List all available tools."""
    from .tools import load_all_tools
    
    config = load_config()
    registry = load_all_tools(config)
    tools = registry.list(scope=project, tag=tag)
    
    if not tools:
        rprint("[yellow]No tools found.[/yellow]")
        rprint("Use 'rdc tool new <name>' to create one.")
        return
    
    table = Table(title="Available Tools")
    table.add_column("Name", style="cyan")
    table.add_column("Signature", style="green")
    table.add_column("Scope")
    table.add_column("Tags")
    
    for t in tools:
        table.add_row(
            t.name,
            t.to_signature(),
            t.scope,
            ", ".join(t.tags) or "-",
        )
    
    console.print(table)


@tools_app.command("show")
def tool_show(
    name: Annotated[str, typer.Argument(help="Tool name")],
    prompt: Annotated[bool, typer.Option("--prompt", help="Output as LLM prompt")] = False,
):
    """Show details of a specific tool."""
    from .tools import load_all_tools
    
    config = load_config()
    registry = load_all_tools(config)
    t = registry.get(name)
    
    if not t:
        rprint(f"[red]Error:[/red] Tool not found: {name}")
        raise typer.Exit(1)
    
    if prompt:
        print(t.to_prompt())
    else:
        params_str = "\n".join(
            f"  - {p.name} ({p.type}): {'required' if p.required else 'optional'}"
            for p in t.params
        ) or "  (none)"
        
        rprint(Panel(
            f"[bold]Name:[/bold] {t.name}\n"
            f"[bold]Signature:[/bold] {t.to_signature()}\n"
            f"[bold]Scope:[/bold] {t.scope}\n"
            f"[bold]File:[/bold] {t.file_path}\n"
            f"[bold]Tags:[/bold] {', '.join(t.tags) or 'N/A'}\n\n"
            f"[bold]Description:[/bold]\n{t.description}\n\n"
            f"[bold]Parameters:[/bold]\n{params_str}\n\n"
            f"[bold]Returns:[/bold] {t.returns}",
            title=f"Tool: {t.name}",
            border_style="cyan"
        ))


@tools_app.command("prompt")
def tool_prompt(
    name: Annotated[str, typer.Argument(help="Tool name")],
):
    """Output tool documentation as LLM prompt."""
    from .tools import load_all_tools
    
    config = load_config()
    registry = load_all_tools(config)
    t = registry.get(name)
    
    if not t:
        rprint(f"[red]Error:[/red] Tool not found: {name}")
        raise typer.Exit(1)
    
    print(t.to_prompt())


# Keep old tool run as hidden alias for backwards compatibility
@tools_app.command("run", hidden=True)
def tool_run_legacy(
    name: Annotated[str, typer.Argument(help="Tool name")],
    args: Annotated[Optional[list[str]], typer.Argument(help="Arguments as key=value")] = None,
    json_output: Annotated[bool, typer.Option("--json", "-j", help="Output as JSON")] = False,
):
    """[Deprecated] Use 'rdc run tool <name>' instead."""
    from .tools import load_all_tools
    
    config = load_config()
    registry = load_all_tools(config)
    t = registry.get(name)
    
    if not t:
        rprint(f"[red]Error:[/red] Tool not found: {name}")
        raise typer.Exit(1)
    
    # Parse arguments
    kwargs = {}
    if args:
        for arg in args:
            if "=" in arg:
                key, value = arg.split("=", 1)
                # Try to parse as JSON for complex types
                try:
                    kwargs[key] = json.loads(value)
                except json.JSONDecodeError:
                    kwargs[key] = value
            else:
                rprint(f"[red]Error:[/red] Invalid argument format: {arg}")
                rprint("Use key=value format")
                raise typer.Exit(1)
    
    try:
        result = t(**kwargs)
        
        if json_output:
            print(json.dumps({"result": result}, indent=2, default=str))
        else:
            if isinstance(result, (list, dict)):
                rprint(Panel(json.dumps(result, indent=2, default=str), title="Result"))
            else:
                rprint(f"[green]Result:[/green] {result}")
    except Exception as e:
        rprint(f"[red]Error executing tool:[/red] {e}")
        raise typer.Exit(1)


@tools_app.command("docs")
def tool_docs(
    output: Annotated[str, typer.Option("--output", "-o", help="Output format: text, json")] = "text",
):
    """Generate documentation for all tools."""
    from .tools import load_all_tools
    
    config = load_config()
    registry = load_all_tools(config)
    
    if output == "json":
        tools_data = [t.to_dict() for t in registry.list()]
        print(json.dumps(tools_data, indent=2))
    else:
        print(registry.to_prompt())


# =============================================================================
# Server Commands (Command Center)
# =============================================================================

from .server.config import get_rdc_home

server_app = typer.Typer(help="RDC Command Center server")
app.add_typer(server_app, name="server")


@server_app.command("start")
def server_start(
    host: Annotated[str, typer.Option("--host", "-h", help="Host to bind")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", "-p", help="Port to bind")] = 8420,
    daemon: Annotated[bool, typer.Option("--daemon", "-d", help="Run in background")] = False,
    reload: Annotated[bool, typer.Option("--reload", "-r", help="Auto-reload on changes")] = False,
    tls: Annotated[bool, typer.Option("--tls", help="Enable TLS (uses config or auto-gen certs)")] = False,
    cert_file: Annotated[Optional[str], typer.Option("--cert", help="TLS certificate file")] = None,
    key_file: Annotated[Optional[str], typer.Option("--key", help="TLS private key file")] = None,
):
    """Start the RDC Command Center server."""
    from .server.config import ensure_rdc_home, Config
    
    ensure_rdc_home()
    config = Config.load()
    
    # Determine TLS settings
    use_tls = tls or config.server.tls.enabled
    ssl_cert = cert_file or (config.server.tls.cert_file if config.server.tls.enabled else None)
    ssl_key = key_file or (config.server.tls.key_file if config.server.tls.enabled else None)
    
    protocol = "https" if use_tls else "http"
    ws_protocol = "wss" if use_tls else "ws"
    
    # Build uvicorn command
    uvicorn_args = [
        "remote_dev_ctrl.server.app:app",
        "--host", host,
        "--port", str(port),
    ]
    
    if use_tls:
        if ssl_cert and ssl_key:
            uvicorn_args.extend(["--ssl-certfile", ssl_cert, "--ssl-keyfile", ssl_key])
        else:
            rprint("[yellow]Warning:[/yellow] TLS enabled but no cert/key provided.")
            rprint("         Generate self-signed certs or configure in ~/.rdc/config.yml")
            rprint("")
            rprint("Generate self-signed cert:")
            rprint("  openssl req -x509 -newkey rsa:4096 -keyout key.pem -out cert.pem -days 365 -nodes")
            rprint("")
            raise typer.Exit(1)
    
    if daemon:
        # Run in background
        import subprocess
        import sys
        
        log_path = get_rdc_home() / "logs" / "server.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        
        cmd = [sys.executable, "-m", "uvicorn"] + uvicorn_args
        
        with open(log_path, "a") as log_file:
            process = subprocess.Popen(
                cmd,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        
        # Save PID
        pid_path = get_rdc_home() / "server.pid"
        pid_path.write_text(str(process.pid))
        
        rprint(f"[green]✓[/green] Server started in background")
        rprint(f"   PID: {process.pid}")
        rprint(f"   URL: {protocol}://{host}:{port}")
        rprint(f"   Logs: {log_path}")
        if use_tls:
            rprint(f"   TLS: enabled")
        rprint("")
        rprint(f"Stop with: rdc server stop")
        return
    
    # Foreground mode: tee logs to server.log so dashboard can read them.
    # We use uvicorn's log_config so the file handler survives reload
    # (reload=True spawns a child process that re-applies log_config).
    log_path = get_rdc_home() / "logs" / "server.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    import copy
    import uvicorn.config
    log_config = copy.deepcopy(uvicorn.config.LOGGING_CONFIG)
    log_config["formatters"]["file"] = {
        "format": "%(asctime)s %(levelname)s %(name)s: %(message)s",
    }
    log_config["handlers"]["file"] = {
        "class": "logging.FileHandler",
        "filename": str(log_path),
        "formatter": "file",
        "level": "INFO",
    }
    # Add file handler to uvicorn loggers (uvicorn and uvicorn.access have
    # explicit handler lists; uvicorn.error inherits from uvicorn)
    for logger_name in ("uvicorn", "uvicorn.access"):
        handlers = log_config["loggers"][logger_name].get("handlers", [])
        log_config["loggers"][logger_name]["handlers"] = handlers + ["file"]
    # Root logger catches app-level logging (rdc.server, etc.)
    log_config["root"] = {"handlers": ["default", "file"], "level": "INFO"}

    rprint(f"[bold]Starting RDC Command Center...[/bold]")
    rprint(f"  URL: {protocol}://{host}:{port}")
    rprint(f"  API docs: {protocol}://{host}:{port}/docs")
    rprint(f"  WebSocket: {ws_protocol}://{host}:{port}/ws")
    rprint(f"  Logs: {log_path}")
    if use_tls:
        rprint(f"  TLS: enabled")
    rprint("")
    rprint("[dim]Press Ctrl+C to stop[/dim]")
    rprint("")

    import uvicorn

    # Always enable reload, but by default only watch a trigger directory
    # so code changes don't auto-restart. The /admin/restart endpoint touches
    # a file in the trigger dir to initiate a graceful reload (no port gap).
    # Use --reload to also watch source code for auto-reload on every save.
    reload_trigger_dir = get_rdc_home() / "reload-trigger"
    reload_trigger_dir.mkdir(parents=True, exist_ok=True)

    reload_dirs = [str(reload_trigger_dir)]
    if reload:
        # Also watch source code for auto-reload on save
        import remote_dev_ctrl
        reload_dirs.append(str(Path(remote_dev_ctrl.__file__).parent))

    run_kwargs = {
        "host": host,
        "port": port,
        "reload": True,
        "reload_dirs": reload_dirs,
        "log_config": log_config,
    }

    if use_tls and ssl_cert and ssl_key:
        run_kwargs["ssl_certfile"] = ssl_cert
        run_kwargs["ssl_keyfile"] = ssl_key

    uvicorn.run("remote_dev_ctrl.server.app:app", **run_kwargs)


@server_app.command("status")
def server_status():
    """Check server status."""
    import urllib.request
    import urllib.error
    from .server.config import Config
    
    config = Config.load()
    url = f"http://{config.server.host}:{config.server.port}/status"
    
    # Check PID file
    pid_path = get_rdc_home() / "server.pid"
    pid = None
    if pid_path.exists():
        try:
            pid = int(pid_path.read_text().strip())
            # Check if process is running
            os.kill(pid, 0)
        except (ValueError, OSError):
            pid = None
            pid_path.unlink(missing_ok=True)
    
    # Try to connect
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            data = json.loads(response.read().decode())
            
            rprint("[green]● Server is running[/green]")
            rprint(f"  URL: http://{config.server.host}:{config.server.port}")
            if pid:
                rprint(f"  PID: {pid}")
            rprint(f"  Agents: {data.get('agents', {}).get('running', 0)} running")
            rprint(f"  Tasks: {data.get('queue', {}).get('pending', 0)} pending")
            rprint(f"  Clients: {data.get('connected_clients', 0)} connected")
    except urllib.error.URLError:
        if pid:
            rprint(f"[yellow]● Server process exists (PID {pid}) but not responding[/yellow]")
        else:
            rprint("[dim]○ Server is not running[/dim]")
            rprint(f"  Start with: rdc server start")


@server_app.command("stop")
def server_stop(
    force: Annotated[bool, typer.Option("--force", "-f", help="Force kill")] = False,
):
    """Stop the running server."""
    import signal
    
    pid_path = get_rdc_home() / "server.pid"
    
    if not pid_path.exists():
        rprint("[yellow]No server PID file found.[/yellow]")
        return
    
    try:
        pid = int(pid_path.read_text().strip())
        sig = signal.SIGKILL if force else signal.SIGTERM
        os.kill(pid, sig)
        pid_path.unlink()
        rprint(f"[green]✓[/green] Server stopped (PID {pid})")
    except ValueError:
        rprint("[red]Invalid PID file[/red]")
        pid_path.unlink()
    except OSError as e:
        if e.errno == 3:  # No such process
            rprint("[yellow]Server process not found (already stopped?)[/yellow]")
            pid_path.unlink()
        else:
            rprint(f"[red]Error stopping server:[/red] {e}")


@server_app.command("restart")
def server_restart(
    graceful: Annotated[bool, typer.Option("--graceful", "-g", help="Use SIGTERM instead of SIGKILL")] = False,
    daemon: Annotated[bool, typer.Option("--daemon", "-d", help="Run in background")] = True,
    port: Annotated[int, typer.Option("--port", "-p", help="Port to run on")] = 8420,
):
    """Restart the server (stop then start). Uses force kill by default."""
    import signal
    import time
    import subprocess as sp
    import sys
    
    # Warn if not running from a venv
    if not hasattr(sys, 'real_prefix') and not (hasattr(sys, 'base_prefix') and sys.base_prefix != sys.prefix):
        rprint("[yellow]Warning: Not running from a virtual environment.[/yellow]")
        rprint("[yellow]Consider using: .venv/bin/rdc server restart[/yellow]")
    
    pid_path = get_rdc_home() / "server.pid"
    
    # Force kill any existing uvicorn/remote_dev_ctrl processes
    rprint("[yellow]Stopping server...[/yellow]")
    sp.run(["pkill", "-9", "-f", "uvicorn.*remote_dev_ctrl"], capture_output=True)
    sp.run(["pkill", "-9", "-f", "python.*remote_dev_ctrl.server"], capture_output=True)
    
    # Also try PID file if exists
    if pid_path.exists():
        try:
            pid = int(pid_path.read_text().strip())
            sig = signal.SIGTERM if graceful else signal.SIGKILL
            os.kill(pid, sig)
        except (ValueError, OSError):
            pass
        pid_path.unlink(missing_ok=True)
    
    time.sleep(2)  # Give processes time to die and release locks
    
    # Start
    rprint("[blue]Starting server...[/blue]")
    
    if daemon:
        log_path = get_rdc_home() / "logs" / "server.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            sys.executable, "-m", "uvicorn",
            "remote_dev_ctrl.server.app:app",
            "--host", "0.0.0.0",
            "--port", str(port),
        ]
        
        with open(log_path, "a") as log_file:
            proc = sp.Popen(
                cmd,
                stdout=log_file,
                stderr=sp.STDOUT,
                start_new_session=True,
            )
        
        pid_path.write_text(str(proc.pid))
        time.sleep(2)
        
        if proc.poll() is None:
            rprint(f"[green]✓[/green] Server restarted (PID {proc.pid})")
            rprint(f"  Dashboard: http://localhost:{port}/")
            rprint(f"  Logs: {log_path}")
        else:
            rprint("[red]Server failed to start[/red]")
            rprint(f"Check logs: {log_path}")
    else:
        import uvicorn
        rprint(f"[green]✓[/green] Server restarting on http://0.0.0.0:{port}")
        uvicorn.run("remote_dev_ctrl.server.app:app", host="0.0.0.0", port=port)


# =============================================================================
# Worker Commands
# =============================================================================

worker_app = typer.Typer(help="Task worker for executing long-running tasks")
app.add_typer(worker_app, name="worker")


@worker_app.command("start")
def worker_start(
    daemon: Annotated[bool, typer.Option("--daemon", "-d", help="Run in background")] = False,
    max_concurrent: Annotated[int, typer.Option("--max", "-m", help="Max concurrent tasks")] = 3,
    worker_id: Annotated[Optional[str], typer.Option("--id", help="Worker ID (auto-generated if omitted)")] = None,
):
    """Start the task worker."""
    import subprocess
    import sys
    import time
    
    pid_path = get_rdc_home() / "worker.pid"
    log_path = get_rdc_home() / "worker.log"
    
    # Check if already running
    if pid_path.exists():
        try:
            pid = int(pid_path.read_text().strip())
            os.kill(pid, 0)
            rprint(f"[yellow]Worker already running (PID {pid})[/yellow]")
            rprint("Use 'rdc worker stop' first, or 'rdc worker status' to check")
            raise typer.Exit(1)
        except OSError:
            pid_path.unlink(missing_ok=True)
    
    if daemon:
        cmd = [
            sys.executable, "-m", "remote_dev_ctrl.server.worker",
        ]
        if worker_id:
            cmd.extend(["--id", worker_id])
        
        env = os.environ.copy()
        env["RDC_WORKER_MAX_CONCURRENT"] = str(max_concurrent)
        
        with open(log_path, "a") as log_file:
            log_file.write(f"\n=== Worker starting at {datetime.now().isoformat()} ===\n")
            proc = subprocess.Popen(
                cmd,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                env=env,
            )
        
        pid_path.write_text(str(proc.pid))
        time.sleep(1)
        
        if proc.poll() is None:
            rprint(f"[green]✓[/green] Worker started (PID {proc.pid})")
            rprint(f"  Max concurrent: {max_concurrent}")
            rprint(f"  Logs: {log_path}")
        else:
            rprint("[red]Worker failed to start[/red]")
            rprint(f"Check logs: {log_path}")
            raise typer.Exit(1)
    else:
        rprint(f"[blue]Starting worker (max_concurrent={max_concurrent})...[/blue]")
        from .server.worker import run_worker
        run_worker(max_concurrent=max_concurrent, worker_id=worker_id)


@worker_app.command("stop")
def worker_stop(
    force: Annotated[bool, typer.Option("--force", "-f", help="Force kill (SIGKILL)")] = False,
):
    """Stop the running worker."""
    import signal
    
    pid_path = get_rdc_home() / "worker.pid"
    
    if not pid_path.exists():
        rprint("[yellow]No worker PID file found.[/yellow]")
        raise typer.Exit(0)
    
    try:
        pid = int(pid_path.read_text().strip())
        sig = signal.SIGKILL if force else signal.SIGTERM
        os.kill(pid, sig)
        rprint(f"[green]✓[/green] Sent {sig.name} to worker (PID {pid})")
        pid_path.unlink(missing_ok=True)
    except ValueError:
        rprint("[red]Invalid PID file[/red]")
        pid_path.unlink(missing_ok=True)
    except OSError as e:
        rprint(f"[yellow]Worker not running:[/yellow] {e}")
        pid_path.unlink(missing_ok=True)


@worker_app.command("status")
def worker_status():
    """Check worker status."""
    from .server.db.connection import get_db, init_databases
    
    init_databases()
    
    pid_path = get_rdc_home() / "worker.pid"
    
    # Check PID file
    if pid_path.exists():
        try:
            pid = int(pid_path.read_text().strip())
            os.kill(pid, 0)
            rprint(f"[green]✓[/green] Worker process running (PID {pid})")
        except (ValueError, OSError):
            rprint("[yellow]Worker PID file exists but process not running[/yellow]")
    else:
        rprint("[yellow]No worker PID file found[/yellow]")
    
    # Check DB for registered workers
    try:
        db = get_db("logs")
        workers = db.execute("""
            SELECT id, hostname, pid, status, last_heartbeat, max_concurrent, current_load
            FROM workers
            ORDER BY last_heartbeat DESC
            LIMIT 10
        """).fetchall()
        
        if workers:
            rprint("\n[bold]Registered Workers:[/bold]")
            table = Table()
            table.add_column("ID")
            table.add_column("Host")
            table.add_column("PID")
            table.add_column("Status")
            table.add_column("Load")
            table.add_column("Last Heartbeat")
            
            for w in workers:
                worker_id, hostname, pid, status, heartbeat, max_conc, load = w
                status_color = "green" if status == "running" else "yellow" if status == "stopping" else "red"
                table.add_row(
                    worker_id,
                    hostname,
                    str(pid),
                    f"[{status_color}]{status}[/{status_color}]",
                    f"{load}/{max_conc}",
                    heartbeat,
                )
            
            console.print(table)
        else:
            rprint("[dim]No workers registered in database[/dim]")
        
        # Show pending tasks
        tasks_db = get_db("tasks")
        pending = tasks_db.execute("""
            SELECT COUNT(*) FROM tasks WHERE status = 'pending'
        """).fetchone()[0]
        in_progress = tasks_db.execute("""
            SELECT COUNT(*) FROM tasks WHERE status = 'in_progress'
        """).fetchone()[0]
        
        rprint(f"\n[bold]Task Queue:[/bold] {pending} pending, {in_progress} in progress")
        
    except Exception as e:
        rprint(f"[red]Error checking database:[/red] {e}")


@worker_app.command("list")
def worker_list():
    """List all registered workers."""
    worker_status()  # Same output


# =============================================================================
# Config Commands
# =============================================================================

config_app = typer.Typer(help="Manage RDC configuration")
app.add_typer(config_app, name="config")


@config_app.command("init")
def config_init(
    force: Annotated[bool, typer.Option("--force", "-f", help="Overwrite existing")] = False,
):
    """Initialize RDC configuration."""
    from .server.config import get_rdc_home, get_default_config_template, ensure_rdc_home
    
    ensure_rdc_home()
    config_path = get_rdc_home() / "config.yml"
    
    if config_path.exists() and not force:
        rprint(f"[yellow]Config already exists:[/yellow] {config_path}")
        rprint("Use --force to overwrite")
        return
    
    config_path.write_text(get_default_config_template())
    rprint(f"[green]✓[/green] Created config at {config_path}")
    rprint(f"   Edit to customize providers, channels, and agents")


@config_app.command("edit")
def config_edit():
    """Open config in editor."""
    import subprocess
    from .server.config import get_rdc_home, ensure_rdc_home
    
    ensure_rdc_home()
    config_path = get_rdc_home() / "config.yml"
    
    if not config_path.exists():
        rprint("[yellow]Config not found. Run 'rdc config init' first.[/yellow]")
        return
    
    editor = os.environ.get("EDITOR", "vim")
    subprocess.run([editor, str(config_path)])


@config_app.command("show")
def config_show():
    """Show current configuration."""
    from .server.config import Config, get_rdc_home
    
    config_path = get_rdc_home() / "config.yml"
    
    if not config_path.exists():
        rprint("[yellow]No config found. Using defaults.[/yellow]")
        rprint("Run 'rdc config init' to create config file.")
        return
    
    rprint(Panel(config_path.read_text(), title=str(config_path), border_style="cyan"))


@config_app.command("path")
def config_path():
    """Show config file path."""
    from .server.config import get_rdc_home
    print(get_rdc_home() / "config.yml")


@config_app.command("set-secret")
def config_set_secret(
    key: Annotated[str, typer.Argument(help="Secret key name")],
    value: Annotated[str, typer.Option("--value", "-v", help="Secret value", prompt=True, hide_input=True)] = "",
):
    """Store a secret securely."""
    from .server.vault import set_secret
    
    set_secret(key, value)
    rprint(f"[green]✓[/green] Stored secret: {key}")


@config_app.command("get-secret")
def config_get_secret(
    key: Annotated[str, typer.Argument(help="Secret key name")],
):
    """Get a secret value."""
    from .server.vault import get_secret
    
    value = get_secret(key)
    if value:
        print(value)
    else:
        rprint(f"[yellow]Secret not found:[/yellow] {key}")
        raise typer.Exit(1)


@config_app.command("list-secrets")
def config_list_secrets():
    """List stored secrets and show which ones are needed."""
    from .server.vault import get_vault
    from .server.config import Config, get_rdc_home
    import re
    
    vault = get_vault()
    stored_keys = set(vault.list_keys())
    
    # Find secrets actively used in config (not commented out)
    active_secrets: dict[str, str] = {}
    commented_secrets: dict[str, str] = {}
    
    config_path = get_rdc_home() / "config.yml"
    if config_path.exists():
        lines = config_path.read_text().split('\n')
        for i, line in enumerate(lines):
            # Skip lines that start with #
            stripped = line.strip()
            if stripped.startswith('#'):
                # Check commented lines for potential secrets
                for match in re.finditer(r'\$\{([A-Z][A-Z0-9_]+)\}', line):
                    key = match.group(1)
                    if key != "VAR_NAME":  # Skip example placeholder
                        commented_secrets[key] = _get_secret_description(key)
                continue
            
            # Active (uncommented) secrets
            for match in re.finditer(r'\$\{([A-Z][A-Z0-9_]+)\}', line):
                key = match.group(1)
                if key != "VAR_NAME":
                    active_secrets[key] = _get_secret_description(key)
    
    # Display stored secrets
    if stored_keys:
        table = Table(title="Stored Secrets")
        table.add_column("Key", style="cyan")
        table.add_column("Status")
        table.add_column("Used For")
        
        for key in sorted(stored_keys):
            table.add_row(key, "[green]✓ set[/green]", _get_secret_description(key))
        
        console.print(table)
    else:
        rprint("[dim]No secrets stored yet.[/dim]")
    
    # Show secrets needed (uncommented in config but not set)
    needed = set(active_secrets.keys()) - stored_keys
    if needed:
        rprint("")
        rprint("[yellow]Secrets needed (referenced in config):[/yellow]")
        for key in sorted(needed):
            rprint(f"  [yellow]○[/yellow] {key} - {active_secrets[key]}")
        rprint("")
        rprint("[dim]Set with: rdc config set-secret <KEY>[/dim]")
    
    # Show available providers and their requirements
    rprint("")
    rprint("[bold]Provider Requirements:[/bold]")
    rprint("  [green]cursor[/green] - No API key needed (uses Cursor login)")
    rprint("  [green]ollama[/green] - No API key needed (runs locally)")
    rprint("  [dim]claude[/dim] - Needs ANTHROPIC_API_KEY")
    rprint("  [dim]openai[/dim] - Needs OPENAI_API_KEY")
    rprint("  [dim]gemini[/dim] - Needs GEMINI_API_KEY")


def _get_secret_description(key: str) -> str:
    """Get description for a secret key."""
    descriptions = {
        "ANTHROPIC_API_KEY": "Claude/Anthropic API",
        "OPENAI_API_KEY": "OpenAI API",
        "GEMINI_API_KEY": "Google Gemini API",
        "TELEGRAM_BOT_TOKEN": "Telegram bot",
        "TWILIO_ACCOUNT_SID": "Twilio account SID",
        "TWILIO_AUTH_TOKEN": "Twilio auth token",
        "TWILIO_PHONE_NUMBER": "Twilio phone number",
        "X_BEARER_TOKEN": "X (Twitter) API bearer token (twitter_read tool)",
        "TWITTER_BEARER_TOKEN": "X (Twitter) API bearer token (alias)",
        "RDC_SECRET_KEY": "Server security",
    }
    return descriptions.get(key, "")


@config_app.command("delete-secret")
def config_delete_secret(
    key: Annotated[str, typer.Argument(help="Secret key name")],
):
    """Delete a stored secret."""
    from .server.vault import get_vault
    
    vault = get_vault()
    if vault.delete(key):
        rprint(f"[green]✓[/green] Deleted secret: {key}")
    else:
        rprint(f"[yellow]Secret not found:[/yellow] {key}")


# =============================================================================
# Token Commands
# =============================================================================

token_app = typer.Typer(help="Manage API tokens")
app.add_typer(token_app, name="token")


@token_app.command("create")
def token_create(
    name: Annotated[str, typer.Argument(help="Token name/description")],
    role: Annotated[str, typer.Option("--role", "-r", help="Role: admin, operator, viewer, agent")] = "operator",
    expires: Annotated[Optional[int], typer.Option("--expires", "-e", help="Expires in N days")] = None,
):
    """Create a new API token."""
    from .server.auth import get_auth_manager, Role
    
    try:
        role_enum = Role(role)
    except ValueError:
        rprint(f"[red]Invalid role:[/red] {role}")
        rprint("Valid roles: admin, operator, viewer, agent")
        raise typer.Exit(1)
    
    auth = get_auth_manager()
    plain_token, info = auth.create_token(
        name=name,
        role=role_enum,
        expires_in_days=expires,
    )
    
    rprint()
    rprint(Panel(
        f"[bold green]{plain_token}[/bold green]",
        title="New API Token",
        subtitle="Save this - it won't be shown again!",
    ))
    rprint()
    rprint(f"[dim]ID:[/dim] {info.id}")
    rprint(f"[dim]Name:[/dim] {info.name}")
    rprint(f"[dim]Role:[/dim] {info.role.value}")
    if info.expires_at:
        rprint(f"[dim]Expires:[/dim] {info.expires_at.isoformat()}")
    rprint()
    rprint("[dim]Use with:[/dim]")
    rprint(f"  curl -H 'Authorization: Bearer {plain_token}' http://127.0.0.1:8420/status")


@token_app.command("list")
def token_list():
    """List all API tokens."""
    from .server.auth import get_auth_manager
    
    auth = get_auth_manager()
    tokens = auth.list_tokens()
    
    if not tokens:
        rprint("[dim]No tokens found. Create one with:[/dim] rdc token create <name>")
        return
    
    table = Table(title="API Tokens")
    table.add_column("ID", style="dim")
    table.add_column("Name")
    table.add_column("Role")
    table.add_column("Created")
    table.add_column("Last Used")
    table.add_column("Status")
    
    for t in tokens:
        status = "[red]revoked[/red]" if t.revoked else "[green]active[/green]"
        if t.expires_at and not t.revoked:
            from datetime import datetime
            if t.expires_at < datetime.now():
                status = "[yellow]expired[/yellow]"
        
        table.add_row(
            t.id,
            t.name,
            t.role.value,
            t.created_at.strftime("%Y-%m-%d"),
            t.last_used_at.strftime("%Y-%m-%d %H:%M") if t.last_used_at else "[dim]never[/dim]",
            status,
        )
    
    rprint(table)


@token_app.command("revoke")
def token_revoke(
    token_id: Annotated[str, typer.Argument(help="Token ID to revoke")],
):
    """Revoke an API token."""
    from .server.auth import get_auth_manager
    
    auth = get_auth_manager()
    if auth.revoke_token(token_id):
        rprint(f"[green]✓[/green] Token revoked: {token_id}")
    else:
        rprint(f"[red]Token not found:[/red] {token_id}")


@token_app.command("delete")
def token_delete(
    token_id: Annotated[str, typer.Argument(help="Token ID to delete")],
    force: Annotated[bool, typer.Option("--force", "-f", help="Skip confirmation")] = False,
):
    """Permanently delete an API token."""
    from .server.auth import get_auth_manager
    
    if not force:
        confirm = typer.confirm(f"Permanently delete token {token_id}?")
        if not confirm:
            raise typer.Abort()
    
    auth = get_auth_manager()
    if auth.delete_token(token_id):
        rprint(f"[green]✓[/green] Token deleted: {token_id}")
    else:
        rprint(f"[red]Token not found:[/red] {token_id}")


# =============================================================================
# Agent Commands
# =============================================================================

agent_app = typer.Typer(help="Manage AI agents")
app.add_typer(agent_app, name="agent")


@agent_app.command("list")
def agent_list():
    """List all agents."""
    from .server.config import Config, ensure_rdc_home
    from .server.agents import AgentManager
    
    ensure_rdc_home()
    config = Config.load()
    manager = AgentManager(config)
    
    agents = manager.list()
    
    if not agents:
        rprint("[yellow]No agents found.[/yellow]")
        rprint("Use 'rdc agent spawn <project>' to start one.")
        return
    
    table = Table(title="Agents")
    table.add_column("Project", style="cyan")
    table.add_column("Status")
    table.add_column("Provider")
    table.add_column("Task")
    table.add_column("PID")
    
    status_colors = {
        "idle": "dim",
        "working": "green",
        "testing": "blue",
        "waiting": "yellow",
        "error": "red",
        "stopped": "dim",
    }
    
    for agent in agents:
        color = status_colors.get(agent.status.value, "white")
        table.add_row(
            agent.project,
            f"[{color}]{agent.status.value}[/{color}]",
            agent.provider,
            agent.current_task[:40] + "..." if agent.current_task and len(agent.current_task) > 40 else (agent.current_task or "-"),
            str(agent.pid) if agent.pid else "-",
        )
    
    console.print(table)


@agent_app.command("spawn")
def agent_spawn(
    project: Annotated[str, typer.Argument(help="Project name")],
    provider: Annotated[Optional[str], typer.Option("--provider", "-p", help="LLM provider")] = None,
    task: Annotated[Optional[str], typer.Option("--task", "-t", help="Initial task")] = None,
    worktree: Annotated[Optional[str], typer.Option("--worktree", "-w", help="Use specific worktree")] = None,
):
    """Spawn an agent for a project."""
    from .server.config import Config, ensure_rdc_home
    from .server.agents import AgentManager
    
    ensure_rdc_home()
    config = Config.load()
    manager = AgentManager(config)
    
    try:
        state = manager.spawn(project, provider=provider, worktree=worktree, task=task)
        rprint(f"[green]✓[/green] Spawned agent for {project}")
        rprint(f"   Provider: {state.provider}")
        rprint(f"   PID: {state.pid}")
        if task:
            rprint(f"   Task: {task}")
    except ValueError as e:
        rprint(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


@agent_app.command("stop")
def agent_stop(
    project: Annotated[str, typer.Argument(help="Project name")],
    force: Annotated[bool, typer.Option("--force", "-f", help="Force kill")] = False,
):
    """Stop an agent."""
    from .server.config import Config, ensure_rdc_home
    from .server.agents import AgentManager
    
    ensure_rdc_home()
    config = Config.load()
    manager = AgentManager(config)
    
    if manager.stop(project, force=force):
        rprint(f"[green]✓[/green] Stopped agent for {project}")
    else:
        rprint(f"[yellow]No agent found for {project}[/yellow]")


@agent_app.command("logs")
def agent_logs(
    project: Annotated[str, typer.Argument(help="Project name")],
    lines: Annotated[int, typer.Option("--lines", "-n", help="Number of lines")] = 50,
    follow: Annotated[bool, typer.Option("--follow", "-f", help="Follow log output")] = False,
):
    """View agent logs."""
    from .server.config import Config, ensure_rdc_home, get_rdc_home
    from .server.agents import AgentManager
    
    ensure_rdc_home()
    
    log_path = get_rdc_home() / "logs" / "agents" / f"{project}.log"
    
    if not log_path.exists():
        rprint(f"[yellow]No logs found for {project}[/yellow]")
        return
    
    if follow:
        import subprocess
        subprocess.run(["tail", "-f", str(log_path)])
    else:
        config = Config.load()
        manager = AgentManager(config)
        logs = manager.get_logs(project, lines=lines)
        print(logs)


@agent_app.command("assign")
def agent_assign(
    project: Annotated[str, typer.Argument(help="Project name")],
    task: Annotated[str, typer.Argument(help="Task description")],
):
    """Assign a task to an agent."""
    from .server.config import Config, ensure_rdc_home
    from .server.agents import AgentManager
    
    ensure_rdc_home()
    config = Config.load()
    manager = AgentManager(config)
    
    try:
        state = manager.assign_task(project, task)
        rprint(f"[green]✓[/green] Assigned task to {project}")
        rprint(f"   Status: {state.status.value}")
    except ValueError as e:
        rprint(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


@agent_app.command("status")
def agent_status(
    project: Annotated[str, typer.Argument(help="Project name")],
):
    """Get detailed status for an agent."""
    from .server.config import Config, ensure_rdc_home
    from .server.agents import AgentManager
    
    ensure_rdc_home()
    config = Config.load()
    manager = AgentManager(config)
    
    agent = manager.get(project)
    
    if not agent:
        rprint(f"[yellow]No agent found for {project}[/yellow]")
        return
    
    rprint(Panel(
        f"[bold]Project:[/bold] {agent.project}\n"
        f"[bold]Status:[/bold] {agent.status.value}\n"
        f"[bold]Provider:[/bold] {agent.provider}\n"
        f"[bold]PID:[/bold] {agent.pid or 'N/A'}\n"
        f"[bold]Worktree:[/bold] {agent.worktree or 'N/A'}\n"
        f"[bold]Current Task:[/bold] {agent.current_task or 'None'}\n"
        f"[bold]Started:[/bold] {agent.started_at or 'N/A'}\n"
        f"[bold]Last Activity:[/bold] {agent.last_activity or 'N/A'}\n"
        f"[bold]Error:[/bold] {agent.error or 'None'}",
        title=f"Agent: {project}",
        border_style="cyan"
    ))


# =============================================================================
# Queue Commands
# =============================================================================

queue_app = typer.Typer(help="Manage task queue")
app.add_typer(queue_app, name="queue")


@queue_app.command("list")
def queue_list(
    project: Annotated[Optional[str], typer.Option("--project", "-p", help="Filter by project")] = None,
    all_tasks: Annotated[bool, typer.Option("--all", "-a", help="Include completed tasks")] = False,
):
    """List tasks in the queue."""
    from .server.db.connection import init_databases
    from .server.db.repositories import TaskRepository, resolve_project_id
    from .server.db.models import TaskStatus

    init_databases()
    repo = TaskRepository()

    pid = resolve_project_id(project) if project else None
    if project and not pid:
        rprint(f"[red]Error:[/red] Project not found: {project}")
        raise typer.Exit(1)

    if all_tasks:
        tasks = repo.list(project_id=pid, limit=100)
    else:
        all_results = repo.list(project_id=pid, limit=200)
        tasks = [t for t in all_results if t.status not in (TaskStatus.COMPLETED, TaskStatus.CANCELLED)]
    
    if not tasks:
        rprint("[yellow]No tasks in queue.[/yellow]")
        return
    
    table = Table(title="Task Queue")
    table.add_column("ID", style="cyan")
    table.add_column("Project")
    table.add_column("Description")
    table.add_column("Priority")
    table.add_column("Status")
    table.add_column("Assigned")
    
    status_colors = {
        "pending": "white",
        "assigned": "blue",
        "in_progress": "green",
        "blocked": "yellow",
        "completed": "dim",
        "failed": "red",
        "cancelled": "dim",
    }
    
    for task in tasks:
        color = status_colors.get(task.status.value, "white")
        desc = task.description[:35] + "..." if len(task.description) > 35 else task.description
        table.add_row(
            task.id,
            task.project,
            desc,
            task.priority.value,
            f"[{color}]{task.status.value}[/{color}]",
            task.assigned_to or "-",
        )
    
    console.print(table)


@queue_app.command("add")
def queue_add(
    project: Annotated[str, typer.Argument(help="Project name")],
    description: Annotated[str, typer.Argument(help="Task description")],
    priority: Annotated[str, typer.Option("--priority", "-p", help="Priority: low, normal, high, urgent")] = "normal",
):
    """Add a task to the queue."""
    from .server.db.connection import init_databases
    from .server.db.repositories import TaskRepository, resolve_project_id
    from .server.db.models import TaskPriority

    init_databases()
    repo = TaskRepository()

    try:
        prio = TaskPriority(priority)
    except ValueError:
        rprint(f"[red]Invalid priority:[/red] {priority}")
        rprint("Use: low, normal, high, urgent")
        raise typer.Exit(1)

    pid = resolve_project_id(project)
    if not pid:
        rprint(f"[red]Error:[/red] Project not found: {project}")
        rprint("Register it first with: rdc add <path> --name <name>")
        raise typer.Exit(1)

    task = repo.create(project_id=pid, description=description, priority=prio)
    rprint(f"[green]✓[/green] Created task {task.id}")
    rprint(f"   Project: {project}")
    rprint(f"   Priority: {priority}")


@queue_app.command("cancel")
def queue_cancel(
    task_id: Annotated[str, typer.Argument(help="Task ID")],
):
    """Cancel a task."""
    from .server.db.connection import init_databases
    from .server.db.repositories import TaskRepository
    
    init_databases()
    repo = TaskRepository()
    
    task = repo.cancel(task_id)
    if task:
        rprint(f"[green]✓[/green] Cancelled task {task_id}")
    else:
        rprint(f"[yellow]Task not found or not cancellable:[/yellow] {task_id}")


@queue_app.command("stats")
def queue_stats():
    """Show queue statistics."""
    from .server.db.connection import init_databases
    from .server.db.repositories import TaskRepository
    
    init_databases()
    repo = TaskRepository()
    stats = repo.stats()
    
    by_project = stats.get("by_project", {})
    project_lines = "\n".join(f"  {p}: {c}" for p, c in by_project.items()) if by_project else "  (none)"
    
    rprint(Panel(
        f"[bold]Total:[/bold] {stats['total']}\n"
        f"[bold]Pending:[/bold] {stats.get('pending', 0)}\n"
        f"[bold]In Progress:[/bold] {stats.get('in_progress', 0)}\n"
        f"[bold]Blocked:[/bold] {stats.get('blocked', 0)}\n"
        f"[bold]Completed:[/bold] {stats.get('completed', 0)}\n"
        f"[bold]Failed:[/bold] {stats.get('failed', 0)}\n\n"
        f"[bold]By Project:[/bold]\n" + project_lines,
        title="Queue Stats",
        border_style="cyan"
    ))


if __name__ == "__main__":
    app()
