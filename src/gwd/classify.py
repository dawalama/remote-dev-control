"""Task complexity classifier.

Determines whether a task should be handled by a single agent (simple)
or the multi-agent orchestrator (complex).
"""

import re

from .types import TaskComplexity

# Keywords that suggest complexity
COMPLEX_KEYWORDS = {
    "refactor", "implement", "integrate", "migrate", "redesign",
    "add endpoint", "add api", "add auth", "authentication",
    "add feature", "build", "create system", "set up", "setup",
    "convert", "rewrite", "architect",
}

# Keywords that suggest simplicity
SIMPLE_KEYWORDS = {
    "list", "show", "print", "rename", "typo", "fix typo",
    "change", "update", "add import", "remove", "delete",
    "move", "copy", "format", "lint", "log",
}


def classify_task(description: str) -> TaskComplexity:
    """Classify a task as simple or complex.

    Heuristic based on word count and keyword matching.
    Default bias: SIMPLE.
    """
    desc_lower = description.lower().strip()
    words = desc_lower.split()
    word_count = len(words)

    # Very short tasks are simple
    if word_count <= 8:
        # Unless they contain complex keywords
        for kw in COMPLEX_KEYWORDS:
            if kw in desc_lower:
                return TaskComplexity.COMPLEX
        return TaskComplexity.SIMPLE

    # Long tasks are complex
    if word_count > 20:
        # Unless they only contain simple keywords
        has_complex = any(kw in desc_lower for kw in COMPLEX_KEYWORDS)
        if has_complex:
            return TaskComplexity.COMPLEX
        # Even long tasks that are just verbose simple requests stay simple
        has_simple = any(kw in desc_lower for kw in SIMPLE_KEYWORDS)
        if has_simple and not has_complex:
            return TaskComplexity.SIMPLE
        return TaskComplexity.COMPLEX

    # Medium length — check keywords
    for kw in COMPLEX_KEYWORDS:
        if kw in desc_lower:
            return TaskComplexity.COMPLEX

    # Default bias: simple
    return TaskComplexity.SIMPLE
