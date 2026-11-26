"""Workflow Recipe system for issue-typed coding, planning, and review playbooks.

Each recipe defines not just a prompt template, but a structured problem-solving
methodology tailored to the type of engineering work:
  - `bugfix`: Reproduce first -> isolate root cause -> regression test -> minimal fix
  - `feature`: Plan contract & boundaries -> implement -> verify -> cross-model review
  - `refactor`: Lock behavior with tests -> small incremental steps -> zero drift
  - `design-plan`: Read-only architecture/approach spike with tradeoffs & step plan
  - `code-review`: Paranoid cross-model diff review (security, races, silent failures)
  - `code-audit`: Full-repo 5-category scored security & reliability audit
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from .db.repositories import get_project_repo


@dataclass
class Recipe:
    id: str
    name: str
    description: str
    prompt_template: str  # supports {goal}, {project_name}, {stack}, {project_path}
    model: str | None = None
    inputs: dict[str, str] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    # Workflow & routing metadata
    category: str = "coding"  # "coding" | "planning" | "review"
    icon: str = "⚡"
    review_mode: str = "implementation"  # "implementation" | "advisory"
    default_tier: str = "coding"  # "fast" | "coding" | "reasoning"
    plan_tier: str = "reasoning"
    review_tier: str = "reasoning"
    phases: list[str] = field(default_factory=lambda: ["implement", "verify"])
    default_max_rounds: int = 3
    default_criteria: list[str] = field(default_factory=list)


BUGFIX_PROMPT = """\
You are a systematic Debugger solving a bug in **{project_name}** ({stack}).

**Goal / Bug Report:**
{goal}

**Required Problem-Solving Methodology (do not skip steps):**
1. **Reproduce first:** Find or write a minimal reproduction (failing test or command) BEFORE editing production code.
2. **Isolate root cause:** Trace the actual failure path and explain *why* it happens. Do not shotgun-edit or paper over the symptom.
3. **Minimal, targeted fix:** Fix the root cause cleanly without unrelated refactors.
4. **Regression test:** Keep or add a test that fails before your fix and passes after.
5. **Verify:** Run the relevant test suite/build and report the exact root cause, files changed, and verification output.
"""

FEATURE_PROMPT = """\
You are a senior engineer implementing a bounded feature in **{project_name}** ({stack}).

**Goal:**
{goal}

**Required Problem-Solving Methodology:**
1. **Approach & Contract First:** Before editing files, inspect existing conventions and write a brief plan (data flow, affected files, edge cases, failure modes).
2. **Keep boundaries clean:** Reuse existing patterns, types, and abstractions in the repo. Avoid speculative abstractions.
3. **Implement cleanly:** Make focused, cohesive changes that cover happy path AND error/empty states.
4. **Tests & Verification:** Add/update tests for the new behavior and run the project's test/build checks to confirm zero regressions.
5. **Summary:** Report what changed, key tradeoffs, and how you verified it.
"""

REFACTOR_PROMPT = """\
You are a senior engineer performing a behavior-preserving refactor in **{project_name}** ({stack}).

**Goal:**
{goal}

**Required Problem-Solving Methodology:**
1. **Lock existing behavior first:** Inspect and run existing tests around the target area before changing code; add characterization tests if coverage is missing.
2. **Incremental, verifiable steps:** Keep diffs clean and mechanical—avoid mixing unrelated behavior changes into a refactor.
3. **Verify zero drift:** Re-run tests, type checks, and lint after refactoring to prove external behavior is preserved.
4. **Summary:** List structural improvements made, files touched, and verification evidence.
"""

DESIGN_PLAN_PROMPT = """\
You are a Principal Systems Architect conducting a **read-only** planning and approach spike for **{project_name}** ({stack}).

**Problem / Goal to Plan:**
{goal}

**STRICT RULE: Do NOT modify repository source files.** Inspect the codebase deeply and produce a concrete, decision-ready plan:
1. **Current State & Constraints:** Cite exact file paths and existing abstractions we should build on (or avoid breaking).
2. **Approach Options (2–3):** Compare concrete options with clear tradeoffs (complexity, cost, failure modes, reversibility).
3. **Recommended Approach:** State clearly which option to take and *why*.
4. **Step-by-Step Execution Plan:** Ordered, file-level implementation steps with explicit acceptance criteria and verification commands so a coding agent can execute cleanly without guessing.
5. **Open Risks / Questions:** Flag anything ambiguous that needs human confirmation before coding starts.
"""

CODE_REVIEW_PROMPT = """\
You are a Paranoid Staff Engineer & Security Reviewer auditing changes in **{project_name}** ({stack}).

**Review Scope / Focus:**
{goal}

**STRICT RULE: Do NOT modify files unless explicitly asked.** Ignore formatting, naming, and style nitpicks. Focus strictly on bugs that tests miss:
1. **Correctness & Edge Cases:** Off-by-one, null/empty inputs, silent failures, swallowed exceptions, state desync.
2. **Concurrency & Races:** Shared mutable state, non-atomic updates, stale closures, retry/idempotency hazards.
3. **Security & Trust Boundaries:** Auth/authz gaps, input validation, injection, secret/credential leakage, unsafe deserialization.
4. **Missing Verification:** Untested failure paths or brittle assumptions.

**Required Output Format:**
1. **Verdict:** `APPROVE` | `APPROVE WITH NOTES` | `REQUEST CHANGES`
2. **Findings Table** (ordered Critical → Low, every item with exact `file:line` and concrete fix):
   | # | Severity | Location | Issue | Concrete Fix |
   |---|----------|----------|-------|--------------|
3. **Top Risk Summary:** 2–3 bullet summary of what would wake someone up at 3am if shipped as-is.
"""

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

BUGFIX_RECIPE = Recipe(
    id="bugfix",
    name="Bugfix",
    description="Reproduce first, isolate root cause, add regression test, apply minimal fix",
    prompt_template=BUGFIX_PROMPT,
    tags=["coding", "debug", "bugfix"],
    category="coding",
    icon="🐞",
    review_mode="implementation",
    default_tier="coding",
    plan_tier="reasoning",
    review_tier="reasoning",
    phases=["diagnose", "implement", "verify"],
    default_max_rounds=3,
    default_criteria=["Root cause reproduced and isolated", "Regression test added and passing"],
)

FEATURE_RECIPE = Recipe(
    id="feature",
    name="Feature",
    description="Plan boundaries & data flow first, implement cleanly, verify with tests",
    prompt_template=FEATURE_PROMPT,
    tags=["coding", "feature", "build"],
    category="coding",
    icon="✨",
    review_mode="implementation",
    default_tier="coding",
    plan_tier="reasoning",
    review_tier="reasoning",
    phases=["plan", "implement", "verify", "review"],
    default_max_rounds=3,
    default_criteria=["Implementation matches goal and conventions", "Tests and build pass"],
)

REFACTOR_RECIPE = Recipe(
    id="refactor",
    name="Refactor",
    description="Lock existing behavior with tests, refactor incrementally, verify zero drift",
    prompt_template=REFACTOR_PROMPT,
    tags=["coding", "refactor", "cleanup"],
    category="coding",
    icon="🔧",
    review_mode="implementation",
    default_tier="coding",
    plan_tier="reasoning",
    review_tier="reasoning",
    phases=["plan", "implement", "verify"],
    default_max_rounds=3,
    default_criteria=["Existing tests pass with zero behavior regression"],
)

DESIGN_PLAN_RECIPE = Recipe(
    id="design-plan",
    name="Plan / Spike",
    description="Read-only architecture & approach planning with tradeoffs and execution steps",
    prompt_template=DESIGN_PLAN_PROMPT,
    tags=["planning", "architecture", "design"],
    category="planning",
    icon="🗺",
    review_mode="advisory",
    default_tier="reasoning",
    plan_tier="reasoning",
    review_tier="reasoning",
    phases=["plan"],
    default_max_rounds=1,
    default_criteria=["Concrete options and tradeoffs compared", "File-level execution plan ready"],
)

CODE_REVIEW_RECIPE = Recipe(
    id="code-review",
    name="Code Review",
    description="Paranoid cross-model review of diffs for bugs, races, edge cases, and security",
    prompt_template=CODE_REVIEW_PROMPT,
    tags=["review", "quality", "security"],
    category="review",
    icon="🔍",
    review_mode="advisory",
    default_tier="reasoning",
    plan_tier="reasoning",
    review_tier="reasoning",
    phases=["review"],
    default_max_rounds=1,
    default_criteria=["Findings ranked by severity with file:line and concrete fixes"],
)

CODE_AUDIT_RECIPE = Recipe(
    id="code-audit",
    name="Code Audit",
    description="Security-focused code audit with structured scoring across 5 categories",
    prompt_template=CODE_AUDIT_PROMPT,
    model="opus-4.6",
    inputs={"project_name": "", "stack": "", "project_path": ""},
    tags=["security", "audit", "quality"],
    category="review",
    icon="🛡",
    review_mode="advisory",
    default_tier="reasoning",
    plan_tier="reasoning",
    review_tier="reasoning",
    phases=["review"],
    default_max_rounds=1,
    default_criteria=["All 5 audit categories scanned with severity table"],
)

BUILTIN_RECIPES: dict[str, Recipe] = {
    "bugfix": BUGFIX_RECIPE,
    "feature": FEATURE_RECIPE,
    "refactor": REFACTOR_RECIPE,
    "design-plan": DESIGN_PLAN_RECIPE,
    "code-review": CODE_REVIEW_RECIPE,
    "code-audit": CODE_AUDIT_RECIPE,
}


_CLASSIFY_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    (
        "code-audit",
        re.compile(r"\b(security audit|code audit|pentest|vulnerability scan|threat model)\b", re.I),
        "Security/audit intent detected",
    ),
    (
        "code-review",
        re.compile(r"\b(review|audit diff|check pr|inspect diff|critique changes|look over.*diff)\b", re.I),
        "Review intent detected",
    ),
    (
        "design-plan",
        re.compile(
            r"\b(plan|design|architect|architecture|spike|rfc|tradeoff|trade-off|how should we|approach|evaluate options|brainstorm|assumptions)\b",
            re.I,
        ),
        "Planning/architecture intent detected",
    ),
    (
        "bugfix",
        re.compile(
            r"\b(fix|bug|broken|crash|error|exception|failing|failure|regression|flaky|hang|stuck|timeout|500|404|409|traceback|debug|doesn'?t work|not working)\b",
            re.I,
        ),
        "Bugfix/debugging intent detected",
    ),
    (
        "refactor",
        re.compile(
            r"\b(refactor|clean ?up|simplify|extract|deduplicate|rename|reorganize|decouple|migrate|split module|consolidate)\b",
            re.I,
        ),
        "Refactor intent detected",
    ),
]


def classify_goal_recipe(goal: str) -> dict[str, Any]:
    """Classify a raw user goal into the best-fit issue-type recipe.

    Fast, deterministic zero-latency classifier (with clear reason and confidence)
    so the UI can suggest the right problem-solving approach as the user types.
    """
    text = (goal or "").strip()
    if not text:
        return {
            "recipe_id": "feature",
            "confidence": 0.5,
            "reason": "Default implementation recipe",
            "recipe": _serialize_recipe(FEATURE_RECIPE),
        }

    for recipe_id, pattern, reason in _CLASSIFY_PATTERNS:
        if pattern.search(text):
            recipe = BUILTIN_RECIPES[recipe_id]
            return {
                "recipe_id": recipe_id,
                "confidence": 0.85,
                "reason": reason,
                "recipe": _serialize_recipe(recipe),
            }

    return {
        "recipe_id": "feature",
        "confidence": 0.65,
        "reason": "Feature/implementation goal",
        "recipe": _serialize_recipe(FEATURE_RECIPE),
    }


def _resolve_project_context(project_name: str) -> tuple[str, str]:
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
    return project_path, stack


def get_recipe_spec(recipe_id: str) -> Optional[Recipe]:
    """Return a Recipe spec by ID (built-in or DB-defined)."""
    try:
        from .db.repositories import get_recipe_repo

        db_recipe = get_recipe_repo().get(recipe_id)
        if db_recipe:
            base = BUILTIN_RECIPES.get(recipe_id)
            return Recipe(
                id=db_recipe.id,
                name=db_recipe.name,
                description=db_recipe.description or (base.description if base else ""),
                prompt_template=db_recipe.prompt_template,
                model=db_recipe.model,
                tags=db_recipe.tags or (base.tags if base else []),
                category=base.category if base else "coding",
                icon=base.icon if base else "⚡",
                review_mode=base.review_mode if base else "implementation",
                default_tier=base.default_tier if base else "coding",
                plan_tier=base.plan_tier if base else "reasoning",
                review_tier=base.review_tier if base else "reasoning",
                phases=list(base.phases) if base else ["implement", "verify"],
                default_max_rounds=base.default_max_rounds if base else 3,
                default_criteria=list(base.default_criteria) if base else [],
            )
    except Exception:
        pass
    return BUILTIN_RECIPES.get(recipe_id)


def render_recipe(recipe_id: str, project_name: str, *, goal: str = "") -> Optional[str]:
    """Look up a recipe, resolve project context, and fill placeholders.

    Returns the rendered prompt string, or None if recipe not found.
    DB recipes take priority over built-ins (allows overriding built-in templates).
    """
    spec = get_recipe_spec(recipe_id)
    if not spec:
        return None

    project_path, stack = _resolve_project_context(project_name)

    class _SafeDict(dict):
        def __missing__(self, key: str) -> str:
            return "{" + key + "}"

    rendered = spec.prompt_template.format_map(
        _SafeDict(
            goal=goal or spec.description,
            project_name=project_name,
            stack=stack,
            project_path=project_path,
        )
    )
    if goal and "{goal}" not in spec.prompt_template:
        rendered = f"{rendered.rstrip()}\n\n**Specific Focus:**\n{goal}"
    return rendered


def apply_recipe_to_goal(recipe_id: str | None, project_name: str, goal: str) -> str:
    """Wrap a user goal in the selected recipe's structured problem-solving methodology."""
    if not recipe_id:
        return goal
    rendered = render_recipe(recipe_id, project_name, goal=goal)
    return rendered if rendered else goal


def _serialize_recipe(r: Recipe, *, builtin: bool = True) -> dict[str, Any]:
    return {
        "id": r.id,
        "name": r.name,
        "description": r.description,
        "tags": r.tags,
        "prompt_template": r.prompt_template,
        "model": r.model,
        "builtin": builtin,
        "category": r.category,
        "icon": r.icon,
        "review_mode": r.review_mode,
        "default_tier": r.default_tier,
        "plan_tier": r.plan_tier,
        "review_tier": r.review_tier,
        "phases": r.phases,
        "default_max_rounds": r.default_max_rounds,
        "default_criteria": r.default_criteria,
    }


def list_recipes() -> list[dict]:
    """Return recipe metadata for API responses (built-in + user DB recipes).

    DB recipes with the same ID as a built-in override the built-in version.
    """
    by_id: dict[str, dict] = {
        r.id: _serialize_recipe(r, builtin=True) for r in BUILTIN_RECIPES.values()
    }

    try:
        from .db.repositories import get_recipe_repo

        for r in get_recipe_repo().list():
            base = BUILTIN_RECIPES.get(r.id)
            by_id[r.id] = {
                "id": r.id,
                "name": r.name,
                "description": r.description or (base.description if base else ""),
                "tags": r.tags or (base.tags if base else []),
                "prompt_template": r.prompt_template,
                "model": r.model,
                "builtin": r.id in BUILTIN_RECIPES,
                "category": base.category if base else "coding",
                "icon": base.icon if base else "⚡",
                "review_mode": base.review_mode if base else "implementation",
                "default_tier": base.default_tier if base else "coding",
                "plan_tier": base.plan_tier if base else "reasoning",
                "review_tier": base.review_tier if base else "reasoning",
                "phases": list(base.phases) if base else ["implement", "verify"],
                "default_max_rounds": base.default_max_rounds if base else 3,
                "default_criteria": list(base.default_criteria) if base else [],
            }
    except Exception:
        pass

    return list(by_id.values())
