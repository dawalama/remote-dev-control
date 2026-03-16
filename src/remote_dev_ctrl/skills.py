"""Skill parsing and management."""

import re
from pathlib import Path

from .models import GlobalConfig, KnowledgeNode, NodeType, ProjectConfig, Skill


def generate_skill_id(name: str) -> str:
    """Generate a URL-friendly skill ID from name."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def parse_skill_file(file_path: Path, scope: str = "global") -> Skill | None:
    """Parse a skill from a markdown file.
    
    Expected format:
    ---
    name: Skill Name
    trigger: /command
    tags: tag1, tag2
    ---
    
    Description of what this skill does.
    
    ## Inputs
    
    - `param1` (required): Description
    - `param2` (optional): Description
    
    ## Steps
    
    1. First step
    2. Second step
    
    ## Examples
    
    - Example usage 1
    - Example usage 2
    """
    if not file_path.exists():
        return None
    
    content = file_path.read_text()
    
    # Parse frontmatter
    frontmatter = {}
    body = content
    
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            fm_content = parts[1].strip()
            body = parts[2].strip()
            
            for line in fm_content.split("\n"):
                if ":" in line:
                    key, value = line.split(":", 1)
                    frontmatter[key.strip().lower()] = value.strip()
    
    name = frontmatter.get("name", file_path.stem.replace("-", " ").replace("_", " ").title())
    trigger = frontmatter.get("trigger")
    tags = [t.strip() for t in frontmatter.get("tags", "").split(",") if t.strip()]
    
    # Parse body sections
    description = ""
    inputs = []
    steps = []
    examples = []
    
    current_section = "description"
    
    for line in body.split("\n"):
        line_lower = line.lower().strip()
        
        if line_lower.startswith("## inputs"):
            current_section = "inputs"
            continue
        elif line_lower.startswith("## steps"):
            current_section = "steps"
            continue
        elif line_lower.startswith("## examples"):
            current_section = "examples"
            continue
        elif line.startswith("## "):
            current_section = "other"
            continue
        
        if current_section == "description":
            description += line + "\n"
        elif current_section == "inputs":
            match = re.match(r"-\s*`(\w+)`\s*\((required|optional)\):\s*(.+)", line)
            if match:
                inputs.append({
                    "name": match.group(1),
                    "required": match.group(2) == "required",
                    "description": match.group(3),
                })
        elif current_section == "steps":
            match = re.match(r"\d+\.\s*(.+)", line)
            if match:
                steps.append(match.group(1))
        elif current_section == "examples":
            match = re.match(r"-\s*(.+)", line)
            if match:
                examples.append(match.group(1))
    
    return Skill(
        id=generate_skill_id(name),
        name=name,
        description=description.strip(),
        trigger=trigger,
        inputs=inputs,
        steps=steps,
        examples=examples,
        tags=tags,
        file_path=file_path,
        scope=scope,
    )


def load_skills_from_dir(skills_dir: Path, scope: str = "global") -> list[Skill]:
    """Load all skills from a directory."""
    skills = []
    
    if not skills_dir.exists():
        return skills
    
    for md_file in sorted(skills_dir.glob("*.md")):
        skill = parse_skill_file(md_file, scope)
        if skill:
            skills.append(skill)
    
    return skills


def load_all_skills(config: GlobalConfig) -> list[Skill]:
    """Load all skills from global and project directories."""
    skills = []
    
    # Global skills
    skills.extend(load_skills_from_dir(config.global_skills_path, "global"))
    
    # Project skills
    for project in config.projects:
        skills.extend(load_skills_from_dir(project.skills_path, project.name))
    
    return skills


def build_skills_node(skills: list[Skill], category_name: str, category_id: str) -> KnowledgeNode:
    """Build a knowledge node for a collection of skills."""
    children = []
    
    for skill in skills:
        trigger_info = f" ({skill.trigger})" if skill.trigger else ""
        children.append(KnowledgeNode(
            id=f"skill_{skill.id}",
            name=skill.name + trigger_info,
            node_type=NodeType.SKILL,
            summary=skill.description[:100] + "..." if len(skill.description) > 100 else skill.description,
            file_path=skill.file_path,
            tags=skill.tags,
            metadata={"trigger": skill.trigger, "scope": skill.scope},
        ))
    
    return KnowledgeNode(
        id=category_id,
        name=category_name,
        node_type=NodeType.CATEGORY,
        summary=f"{len(skills)} skills available",
        children=children,
    )


def create_skill_template(name: str, trigger: str | None = None) -> str:
    """Generate a template for a new skill."""
    trigger_line = f"trigger: {trigger}" if trigger else "# trigger: /command"
    
    return f"""---
name: {name}
{trigger_line}
tags: 
---

Brief description of what this skill does and when to use it.

## Inputs

- `param1` (required): Description of required parameter
- `param2` (optional): Description of optional parameter

## Steps

1. First, do this
2. Then, do that
3. Finally, complete with this

## Examples

- Example: "Run this skill with param1=value"
- Example: "Use this when you need to..."
"""
