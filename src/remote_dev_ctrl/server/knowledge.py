"""Bridge between DB projects and the knowledge indexer.

Converts ProjectRepository entries into the indexer's GlobalConfig/ProjectConfig
format and provides cached access to the knowledge tree.
"""

import logging
from pathlib import Path
from typing import Optional

from ..indexer import build_full_index, build_project_node
from ..models import GlobalConfig, KnowledgeNode, ProjectConfig

logger = logging.getLogger(__name__)

# Module-level cache
_cached_index: KnowledgeNode | None = None


def _build_config_from_db() -> GlobalConfig:
    """Convert DB projects into a GlobalConfig for the indexer."""
    from .db.repositories import ProjectRepository

    repo = ProjectRepository()
    db_projects = repo.list()

    projects = []
    for p in db_projects:
        projects.append(ProjectConfig(
            name=p.name,
            path=Path(p.path),
            description=p.description or None,
            tags=p.tags or [],
        ))

    return GlobalConfig(projects=projects)


def get_knowledge_index(force: bool = False) -> KnowledgeNode:
    """Return the full knowledge index tree, building lazily and caching."""
    global _cached_index
    if _cached_index is None or force:
        config = _build_config_from_db()
        _cached_index = build_full_index(config)
    return _cached_index


def get_project_knowledge(name: str) -> Optional[KnowledgeNode]:
    """Return the knowledge subtree for a single project."""
    from .db.repositories import ProjectRepository

    repo = ProjectRepository()
    db_proj = repo.get(name)
    if not db_proj:
        return None

    pc = ProjectConfig(
        name=db_proj.name,
        path=Path(db_proj.path),
        description=db_proj.description or None,
        tags=db_proj.tags or [],
    )
    return build_project_node(pc)


def invalidate_cache() -> None:
    """Clear the cached index so the next access rebuilds it."""
    global _cached_index
    _cached_index = None


def create_doc(project_name: str, filename: str, content: str) -> dict:
    """Create a new .ai/{filename} document in the project directory."""
    from .db.repositories import ProjectRepository

    repo = ProjectRepository()
    db_proj = repo.get(project_name)
    if not db_proj:
        raise ValueError(f"Project not found: {project_name}")

    ai_dir = Path(db_proj.path) / ".ai"
    ai_dir.mkdir(parents=True, exist_ok=True)

    # Sanitize filename
    safe_name = filename.strip().replace("..", "").lstrip("/")
    if not safe_name:
        raise ValueError("Invalid filename")
    if not safe_name.endswith(".md"):
        safe_name += ".md"

    file_path = ai_dir / safe_name
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")

    invalidate_cache()

    # Rebuild and return the new node
    node = get_project_knowledge(project_name)
    if node:
        # Find the newly created doc node
        result = _find_node_by_file(node, str(file_path))
        if result:
            return result.to_compact_json()
    return {"id": safe_name, "name": safe_name, "type": "document"}


def update_doc(node_id: str, content: str) -> dict:
    """Update a knowledge node's file content."""
    index = get_knowledge_index()
    node = index.find_by_id(node_id)
    if not node:
        raise ValueError(f"Node not found: {node_id}")
    if not node.file_path:
        raise ValueError(f"Node has no file path: {node_id}")

    file_path = Path(node.file_path)
    if not file_path.exists():
        raise ValueError(f"File not found: {file_path}")

    if node.start_line is not None and node.end_line is not None:
        # Section: replace specific line range
        lines = file_path.read_text(encoding="utf-8").splitlines(keepends=True)
        start = max(0, node.start_line - 1)
        end = min(len(lines), node.end_line)
        new_lines = content.splitlines(keepends=True)
        if new_lines and not new_lines[-1].endswith("\n"):
            new_lines[-1] += "\n"
        lines[start:end] = new_lines
        file_path.write_text("".join(lines), encoding="utf-8")
    else:
        # Full document
        file_path.write_text(content, encoding="utf-8")

    invalidate_cache()
    return {"id": node_id, "name": node.name, "type": node.node_type.value, "updated": True}


def _find_node_by_file(node: KnowledgeNode, file_path: str) -> Optional[KnowledgeNode]:
    """Walk the tree to find a node matching a file path."""
    if node.file_path and str(node.file_path) == file_path:
        return node
    for child in node.children:
        result = _find_node_by_file(child, file_path)
        if result:
            return result
    return None


def search_index(query: str, project: Optional[str] = None) -> list[dict]:
    """Search nodes by name/summary text. Returns compact JSON matches."""
    index = get_knowledge_index()
    q = query.lower()
    results: list[dict] = []

    def _walk(node: KnowledgeNode) -> None:
        # If project filter, skip non-matching project subtrees
        if project and node.node_type.value == "project" and node.name != project:
            return

        match = False
        if q in node.name.lower():
            match = True
        elif node.summary and q in node.summary.lower():
            match = True

        if match:
            results.append(node.to_compact_json())

        for child in node.children:
            _walk(child)

    _walk(index)
    return results
