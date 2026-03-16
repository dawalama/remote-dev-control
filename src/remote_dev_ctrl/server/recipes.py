"""Recipe system for reusable task templates."""

from dataclasses import dataclass, field
from typing import Optional

from .db.repositories import get_project_repo


@dataclass
class Recipe:
    id: str
    name: str
    description: str
    prompt_template: str  # with {project_name}, {stack}, {project_path} placeholders
    model: str | None = None  # cursor-agent model id (e.g. "opus-4.6", "sonnet-4.6")
    inputs: dict[str, str] = field(default_factory=dict)  # placeholder -> default value
    tags: list[str] = field(default_factory=list)


CODE_AUDIT_PROMPT = """\
You are a security-focused senior engineer conducting a thorough code audit.

**Project Context:**
- Project: {project_name}
- Language/Stack: {stack}
- Path: {project_path}

**Scoring (maximize your score):**
- +1: Low — minor issues, cosmetic, unlikely edge cases
- +5: Medium — functional bugs, data inconsistencies, performance issues
- +10: Critical — security vulnerabilities, data loss, crashes

**Audit systematically through each category:**
1. SECURITY: Injection (SQL, command, path traversal), auth/authz flaws, hardcoded secrets, unsafe deserialization, XSS
2. RELIABILITY: Null/None dereference, unhandled exceptions, race conditions, missing error propagation, resource leaks
3. PERFORMANCE: N+1 queries, blocking I/O in async context, memory leaks, redundant computation
4. DATA: Inconsistent state, missing validation at system boundaries, silent data corruption, stale caches
5. EDGE_CASES: Empty collections, timeout scenarios, concurrent access, boundary values, error recovery paths

**Output — for each finding:**

| # | Category | Location | Severity | Confidence | Description | Fix |
|---|----------|----------|----------|------------|-------------|-----|

Severity: CRITICAL / HIGH / MEDIUM / LOW
Confidence: CONFIRMED (can explain exact failure) / SUSPECTED (needs verification — explain what to check)

**Instructions:**
1. Scan each category above systematically — do not skip any
2. Every finding MUST have a specific file:line and an actionable fix
3. After your initial pass, ask: "What critical issues might I have missed?" — then do a second pass
4. End with summary table: Critical: X, High: Y, Medium: Z, Low: W
5. End with total score
"""

CODE_AUDIT_RECIPE = Recipe(
    id="code-audit",
    name="Code Audit",
    description="Security-focused code audit with structured scoring across 5 categories",
    prompt_template=CODE_AUDIT_PROMPT,
    model="opus-4.6",
    inputs={"project_name": "", "stack": "", "project_path": ""},
    tags=["security", "audit", "quality"],
)

BUILTIN_RECIPES: dict[str, Recipe] = {
    "code-audit": CODE_AUDIT_RECIPE,
}


def render_recipe(recipe_id: str, project_name: str) -> Optional[str]:
    """Look up a recipe, resolve project context, and fill placeholders.

    Returns the rendered prompt string, or None if recipe not found.
    DB recipes take priority over built-ins (allows overriding built-in templates).
    """
    prompt_template = None

    # Check DB first (user overrides take priority)
    try:
        from .db.repositories import get_recipe_repo
        db_recipe = get_recipe_repo().get(recipe_id)
        if db_recipe:
            prompt_template = db_recipe.prompt_template
    except Exception:
        pass

    # Fall back to built-in
    if not prompt_template:
        recipe = BUILTIN_RECIPES.get(recipe_id)
        if recipe:
            prompt_template = recipe.prompt_template

    if not prompt_template:
        return None

    # Resolve project path and stack from DB
    project_path = ""
    stack = "unknown"
    try:
        repo = get_project_repo()
        project = repo.get(project_name)
        if project:
            project_path = project.path
            profile = (project.config or {}).get("profile", {})
            stack_list = profile.get("stack", [])
            if stack_list:
                stack = ", ".join(stack_list)
    except Exception:
        pass

    return prompt_template.format(
        project_name=project_name,
        stack=stack,
        project_path=project_path,
    )


def list_recipes() -> list[dict]:
    """Return recipe metadata for API responses (built-in + user DB recipes).

    DB recipes with the same ID as a built-in override the built-in version.
    """
    # Start with built-ins keyed by ID
    by_id: dict[str, dict] = {}
    for r in BUILTIN_RECIPES.values():
        by_id[r.id] = {
            "id": r.id,
            "name": r.name,
            "description": r.description,
            "tags": r.tags,
            "prompt_template": r.prompt_template,
            "model": r.model,
            "builtin": True,
        }

    # DB recipes override built-ins if same ID
    try:
        from .db.repositories import get_recipe_repo
        for r in get_recipe_repo().list():
            by_id[r.id] = {
                "id": r.id,
                "name": r.name,
                "description": r.description or "",
                "tags": r.tags or [],
                "prompt_template": r.prompt_template,
                "model": r.model,
                "builtin": r.id in BUILTIN_RECIPES,
            }
    except Exception:
        pass

    return list(by_id.values())
