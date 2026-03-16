"""Index generation from .ai/ directories and markdown files."""

import hashlib
import re
from datetime import datetime
from pathlib import Path

from .models import GlobalConfig, KnowledgeNode, LearningEntry, NodeType, ProjectConfig


def generate_id(content: str) -> str:
    """Generate a short stable ID from content."""
    return hashlib.sha256(content.encode()).hexdigest()[:8]


def parse_learning_entries(content: str, project: str | None = None) -> list[LearningEntry]:
    """Parse learning entries from a learnings.md file."""
    entries = []
    
    # Match entries with format: ### YYYY-MM-DD: Title
    entry_pattern = re.compile(
        r"###\s+(\d{4}-\d{2}-\d{2}):\s*(.+?)\n"
        r".*?\*\*Issue:\*\*\s*(.+?)\n"
        r".*?\*\*Correction:\*\*\s*(.+?)(?:\n.*?\*\*Context:\*\*\s*(.+?))?(?=\n###|\n---|\Z)",
        re.DOTALL
    )
    
    for match in entry_pattern.finditer(content):
        date_str, title, issue, correction, context = match.groups()
        entries.append(LearningEntry(
            id=generate_id(f"{date_str}{title}"),
            date=datetime.strptime(date_str, "%Y-%m-%d"),
            title=title.strip(),
            issue=issue.strip(),
            correction=correction.strip(),
            context=context.strip() if context else None,
            project=project,
        ))
    
    return entries


def parse_markdown_sections(file_path: Path) -> list[tuple[str, int, int, str]]:
    """Parse markdown file into sections. Returns [(title, start_line, end_line, content)]."""
    content = file_path.read_text()
    lines = content.split("\n")
    sections = []
    
    current_title = None
    current_start = 0
    current_lines = []
    
    for i, line in enumerate(lines):
        if line.startswith("## "):
            if current_title:
                sections.append((
                    current_title,
                    current_start,
                    i - 1,
                    "\n".join(current_lines)
                ))
            current_title = line[3:].strip()
            current_start = i
            current_lines = []
        else:
            current_lines.append(line)
    
    if current_title:
        sections.append((
            current_title,
            current_start,
            len(lines) - 1,
            "\n".join(current_lines)
        ))
    
    return sections


def summarize_section(content: str, max_length: int = 150) -> str:
    """Generate a brief summary of section content."""
    # Remove markdown formatting
    clean = re.sub(r"[#*`\[\]]", "", content)
    clean = re.sub(r"\n+", " ", clean).strip()
    
    if len(clean) <= max_length:
        return clean
    return clean[:max_length].rsplit(" ", 1)[0] + "..."


def build_document_node(file_path: Path, parent_id: str) -> KnowledgeNode:
    """Build a document node with section children from a markdown file."""
    file_id = generate_id(str(file_path))
    doc_name = file_path.stem.replace("_", " ").title()
    
    sections = parse_markdown_sections(file_path)
    children = []
    
    for title, start, end, content in sections:
        section_id = f"{file_id}_{generate_id(title)}"
        children.append(KnowledgeNode(
            id=section_id,
            name=title,
            node_type=NodeType.SECTION,
            summary=summarize_section(content),
            file_path=file_path,
            start_line=start,
            end_line=end,
        ))
    
    return KnowledgeNode(
        id=file_id,
        name=doc_name,
        node_type=NodeType.DOCUMENT,
        file_path=file_path,
        summary=f"Contains {len(sections)} sections" if sections else None,
        children=children,
    )


def build_project_node(project: ProjectConfig) -> KnowledgeNode:
    """Build a project node from a project configuration."""
    from .skills import load_skills_from_dir, build_skills_node
    
    ai_path = project.full_ai_path
    children = []
    
    if ai_path.exists():
        for md_file in sorted(ai_path.glob("*.md")):
            children.append(build_document_node(md_file, project.name))
    
    # Add project skills
    skills = load_skills_from_dir(project.skills_path, project.name)
    if skills:
        children.append(build_skills_node(skills, "Skills", f"skills_{generate_id(project.name)}"))
    
    return KnowledgeNode(
        id=f"project_{generate_id(project.name)}",
        name=project.name,
        node_type=NodeType.PROJECT,
        summary=project.description,
        file_path=project.path,
        tags=project.tags,
        children=children,
    )


def build_tools_node(tools: list, category_name: str, category_id: str) -> KnowledgeNode:
    """Build a knowledge node for a collection of tools."""
    children = []
    
    for t in tools:
        children.append(KnowledgeNode(
            id=f"tool_{t.name}",
            name=f"{t.name}()",
            node_type=NodeType.TOOL,
            summary=t.description,
            file_path=t.file_path,
            tags=t.tags,
            metadata={"signature": t.to_signature(), "scope": t.scope},
        ))
    
    return KnowledgeNode(
        id=category_id,
        name=category_name,
        node_type=NodeType.CATEGORY,
        summary=f"{len(tools)} tools available",
        children=children,
    )


def build_global_node(global_ai_dir: Path, config: GlobalConfig) -> KnowledgeNode:
    """Build the global knowledge node."""
    from .skills import load_skills_from_dir, build_skills_node
    from .tools import load_all_tools
    
    children = []
    
    if global_ai_dir.exists():
        for md_file in sorted(global_ai_dir.glob("*.md")):
            children.append(build_document_node(md_file, "global"))
    
    # Add global skills
    skills = load_skills_from_dir(config.global_skills_path, "global")
    if skills:
        children.append(build_skills_node(skills, "Skills", "global_skills"))
    
    # Add global tools
    try:
        registry = load_all_tools(config)
        global_tools = registry.list(scope="global")
        if global_tools:
            children.append(build_tools_node(global_tools, "Tools", "global_tools"))
    except Exception:
        pass  # Tools may fail to load if dependencies missing
    
    return KnowledgeNode(
        id="global",
        name="Global Knowledge",
        node_type=NodeType.CATEGORY,
        summary="Universal rules and learnings across all projects",
        file_path=global_ai_dir,
        children=children,
    )


def build_full_index(config: GlobalConfig) -> KnowledgeNode:
    """Build the complete knowledge index tree."""
    global_node = build_global_node(config.global_ai_dir, config)
    
    project_nodes = [build_project_node(p) for p in config.projects]
    
    projects_category = KnowledgeNode(
        id="projects",
        name="Projects",
        node_type=NodeType.CATEGORY,
        summary=f"{len(project_nodes)} registered projects",
        children=project_nodes,
    )
    
    return KnowledgeNode(
        id="root",
        name="AI Knowledge Base",
        node_type=NodeType.ROOT,
        summary="Hierarchical knowledge index for AI-assisted development",
        children=[global_node, projects_category],
    )
