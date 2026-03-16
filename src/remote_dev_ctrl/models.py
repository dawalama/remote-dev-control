"""Core data models for the hierarchical knowledge index."""

from datetime import datetime
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field


class NodeType(str, Enum):
    ROOT = "root"
    CATEGORY = "category"
    PROJECT = "project"
    DOCUMENT = "document"
    SECTION = "section"
    ENTRY = "entry"
    SKILL = "skill"
    TOOL = "tool"


class Skill(BaseModel):
    """A reusable AI skill/workflow."""
    
    id: str
    name: str
    description: str
    trigger: str | None = Field(None, description="Slash command or trigger phrase")
    inputs: list[dict] = Field(default_factory=list, description="Input parameters")
    steps: list[str] = Field(default_factory=list, description="Execution steps")
    examples: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    file_path: Path | None = None
    scope: str = "global"  # "global" or project name
    
    def to_prompt(self) -> str:
        """Generate the full prompt for LLM execution."""
        lines = [
            f"# Skill: {self.name}",
            "",
            f"**Description:** {self.description}",
            "",
        ]
        
        if self.inputs:
            lines.append("## Inputs")
            for inp in self.inputs:
                required = "(required)" if inp.get("required") else "(optional)"
                lines.append(f"- `{inp['name']}` {required}: {inp.get('description', '')}")
            lines.append("")
        
        if self.steps:
            lines.append("## Steps")
            for i, step in enumerate(self.steps, 1):
                lines.append(f"{i}. {step}")
            lines.append("")
        
        if self.examples:
            lines.append("## Examples")
            for ex in self.examples:
                lines.append(f"- {ex}")
        
        return "\n".join(lines)


class KnowledgeNode(BaseModel):
    """A node in the hierarchical knowledge tree."""
    
    id: str = Field(..., description="Unique identifier for this node")
    name: str = Field(..., description="Human-readable name")
    node_type: NodeType = Field(..., description="Type of this node")
    summary: str | None = Field(None, description="Brief description for LLM reasoning")
    
    file_path: Path | None = Field(None, description="Path to the source file")
    start_line: int | None = Field(None, description="Start line in file (for sections)")
    end_line: int | None = Field(None, description="End line in file (for sections)")
    
    tags: list[str] = Field(default_factory=list, description="Semantic tags for filtering")
    metadata: dict = Field(default_factory=dict, description="Arbitrary metadata")
    
    children: list["KnowledgeNode"] = Field(default_factory=list)
    
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)

    def find_by_id(self, node_id: str) -> "KnowledgeNode | None":
        if self.id == node_id:
            return self
        for child in self.children:
            result = child.find_by_id(node_id)
            if result:
                return result
        return None

    def find_by_tag(self, tag: str) -> list["KnowledgeNode"]:
        results = []
        if tag in self.tags:
            results.append(self)
        for child in self.children:
            results.extend(child.find_by_tag(tag))
        return results

    def find_by_type(self, node_type: NodeType) -> list["KnowledgeNode"]:
        results = []
        if self.node_type == node_type:
            results.append(self)
        for child in self.children:
            results.extend(child.find_by_type(node_type))
        return results

    def to_toc(self, indent: int = 0) -> str:
        """Generate a table-of-contents style representation for LLM consumption."""
        prefix = "  " * indent
        type_icon = {
            NodeType.ROOT: "📚",
            NodeType.CATEGORY: "📁",
            NodeType.PROJECT: "🗂️",
            NodeType.DOCUMENT: "📄",
            NodeType.SECTION: "📑",
            NodeType.ENTRY: "•",
            NodeType.TOOL: "🔧",
            NodeType.SKILL: "⚡",
        }.get(self.node_type, "•")
        
        lines = [f"{prefix}{type_icon} [{self.id}] {self.name}"]
        if self.summary:
            lines.append(f"{prefix}   └─ {self.summary}")
        
        for child in self.children:
            lines.append(child.to_toc(indent + 1))
        
        return "\n".join(lines)

    def to_compact_json(self) -> dict:
        """Compact JSON for LLM context - omits empty fields."""
        data = {"id": self.id, "name": self.name, "type": self.node_type.value}
        if self.summary:
            data["summary"] = self.summary
        if self.file_path:
            data["file"] = str(self.file_path)
        if self.tags:
            data["tags"] = self.tags
        if self.children:
            data["children"] = [c.to_compact_json() for c in self.children]
        return data


class LearningEntry(BaseModel):
    """A single learning/correction entry."""
    
    id: str
    date: datetime
    title: str
    issue: str
    correction: str
    context: str | None = None
    tags: list[str] = Field(default_factory=list)
    project: str | None = None


class ProjectConfig(BaseModel):
    """Configuration for a registered project."""
    
    name: str
    path: Path
    ai_dir: Path = Field(default=Path(".ai"))
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    
    @property
    def full_ai_path(self) -> Path:
        return self.path / self.ai_dir
    
    @property
    def skills_path(self) -> Path:
        return self.full_ai_path / "skills"


class GlobalConfig(BaseModel):
    """Global configuration for the knowledge system."""
    
    version: str = "1.0.0"
    global_ai_dir: Path = Field(default=Path.home() / ".ai")
    projects: list[ProjectConfig] = Field(default_factory=list)
    
    @property
    def global_skills_path(self) -> Path:
        return self.global_ai_dir / "skills"
    
    def get_project(self, name: str) -> ProjectConfig | None:
        return next((p for p in self.projects if p.name == name), None)
    
    def add_project(self, project: ProjectConfig) -> None:
        existing = self.get_project(project.name)
        if existing:
            self.projects.remove(existing)
        self.projects.append(project)
