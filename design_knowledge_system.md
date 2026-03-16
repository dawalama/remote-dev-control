# Knowledge System — Feature Design

## Vision

A knowledge capture and assembly pipeline that makes AI agents better over time. Rules, learnings, and context travel with the project (git-committed `.ai/` files), are indexed in SQLite for fast querying, and served to any AI agent via MCP tools or HTTP endpoints.

## Problem

The `.ai/` file structure exists (rules.md, learnings.md, context.md) but there's no pipeline to:
- Parse and index these files into queryable structures
- Assemble the right context for an LLM call (budget-aware, model-aware)
- Let AI agents capture learnings mid-session (closing the feedback loop)
- Search across projects for relevant knowledge

## Architecture

```
.ai/rules.md  ─────┐
.ai/learnings.md ───┤  parse     ┌──────────┐  assemble   ┌─────────────┐
.ai/context.md ─────┤──────────→ │ SQLite   │────────────→│  Context    │
                    │           │ Index    │             │  Output     │
                    │           └──────────┘             └─────────────┘
                    │               ↑ write                  │     │
                    │               │                        │     │
                    │         ┌─────┴──────┐           HTTP API   MCP
                    │         │ add_learning│           endpoint   tools
                    │         │ add_rule    │
                    │         └────────────┘
```

### Dual storage: files as source of truth, DB as index

**Files (git-committed):**
- `.ai/rules.md` — project conventions, always included in context
- `.ai/learnings.md` — structured corrections with category/confidence
- `.ai/context.md` — project overview, architecture, key decisions
- Portable: clone repo → get knowledge. Editable in any text editor.

**DB (SQLite, not committed):**
- Parsed entries from files with additional metadata
- Usage stats (times_matched, last_used) that don't belong in files
- Enables fast search, filtering, ranking for the assembly pipeline
- Cross-project queries

**Sync:**
- File → DB: On project load / server start. Re-sync on file change.
- DB → File: When UI or MCP tool edits an entry, write back to markdown file AND update DB.
- Conflict rule: file wins (someone may have edited via git pull, text editor, or AI agent).

## File Formats

### rules.md
```markdown
# Rules

- Always use pnpm, never npm or yarn
- All API responses must use Pydantic models
- Tests use pytest with async fixtures
- Error responses follow RFC 7807 Problem Details format
```

Simple bullet list. Each line is one rule. Always included in context (small, high-signal).

### learnings.md
```markdown
# Learnings

## Use Decimal for monetary amounts
**Category:** code-style | **Confidence:** confirmed
- **Wrong:** Using float for price calculations
- **Right:** Use `decimal.Decimal` for all money fields
- **Why:** Float rounding errors cause cent discrepancies in invoices

## wkhtmltopdf must be in PATH for PDF generation
**Category:** tooling | **Confidence:** confirmed
- The Docker image needs `apt-get install wkhtmltopdf`
- Local dev: `brew install wkhtmltopdf`

## Rate limiting should be per-API-key
**Category:** architecture | **Confidence:** provisional
- **Wrong:** Rate limiting by IP address
- **Right:** Rate limit by API key, fall back to IP for unauthenticated
- **Why:** Multiple users behind same NAT get unfairly limited
```

Structured enough to parse, readable enough to open in vim. Category and confidence are parseable from the bold markers. The DB entry adds metadata not in the file (times_matched, last_used, created_at).

### context.md
```markdown
# Project Context

## Overview
REST API for invoice management with PDF generation.

## Stack
FastAPI, PostgreSQL, SQLAlchemy, wkhtmltopdf

## Architecture
- src/api/ — route handlers
- src/models/ — Pydantic + SQLAlchemy models
- src/services/ — business logic
- src/templates/ — Jinja2 templates for PDF rendering

## Key Decisions
- Chose wkhtmltopdf over weasyprint for better CSS support
- Using cursor-based pagination for invoice list endpoints
- PDF generation is async (task queue) for invoices > 10 pages
```

Freeform markdown. Included in full (or summarized for small context budgets).

## DB Models

```python
class Rule(BaseModel):
    id: str
    project: str          # project name or "__global__"
    text: str             # the rule text
    source_file: str      # path to .ai/rules.md
    source_line: int      # line number for editing
    created_at: str
    times_served: int     # how often included in context assembly

class Learning(BaseModel):
    id: str
    project: str
    title: str
    category: str         # code-style, architecture, tooling, debugging, model-specific
    confidence: str       # provisional, confirmed
    incorrect: str | None
    correct: str | None
    why: str | None
    body: str             # full markdown body
    source_file: str
    source_line: int
    model_affinity: str | None  # "claude", "gpt", None (universal)
    created_at: str
    times_served: int
    last_used: str | None
```

## Context Assembly Pipeline

```
assemble_context(project, model_id?, budget_tokens?) → str
```

1. Load global rules (~/.ai/rules.md) — always included
2. Load project rules (project/.ai/rules.md) — always included
3. Load project context (project/.ai/context.md) — always included
4. Query learnings from DB, filtered by:
   - Project scope (global + this project)
   - Model affinity (universal + matching model)
   - Sorted by: confidence (confirmed first), then times_served (most useful first), then recency
5. Fill remaining budget with learnings, truncating if over budget
6. Format output as structured markdown

### Model Profiles

```python
MODEL_PROFILES = {
    "claude-opus":   {"budget": 8000, "format": "structured"},
    "claude-sonnet": {"budget": 4000, "format": "structured"},
    "gpt-4o":        {"budget": 6000, "format": "structured"},
    "llama-3":       {"budget": 2000, "format": "compact"},
    "default":       {"budget": 4000, "format": "structured"},
}
```

Budget is in tokens. Rules + context typically use ~500-1500 tokens, rest goes to learnings.

## MCP Interface

### Tools

```
get_project_context(project: str, model?: str, budget?: int) → str
  Returns assembled context. The primary tool agents call at session start.

add_learning(project: str, title: str, category?: str, incorrect?: str, correct?: str, why?: str) → {id, file_path}
  Captures a learning. Writes to .ai/learnings.md AND indexes in DB.
  Called by agents mid-session when corrected or when discovering something.

add_rule(project: str, rule: str) → {id, file_path}
  Appends a rule to .ai/rules.md AND indexes in DB.

search_learnings(query: str, project?: str) → [{title, category, confidence, body}]
  Search across learnings. Useful for "has anyone solved X before?"

list_rules(project: str) → [str]
  Returns all rules for a project (global + project-specific).
```

### Resources

```
project://{name}/rules      → raw rules markdown
project://{name}/learnings  → raw learnings markdown
project://{name}/context    → assembled context (full pipeline output)
```

## HTTP Endpoints

```
GET /projects/{name}/context?model=default&format=markdown
  → Assembled context string (same pipeline as MCP tool)

GET /projects/{name}/rules
  → List of parsed rules

GET /projects/{name}/learnings?category=&confidence=
  → List of parsed learnings with filtering

POST /projects/{name}/learnings
  → Create learning (writes to file + DB)

PATCH /projects/{name}/learnings/{id}
  → Update learning (writes to file + DB)

POST /projects/{name}/rules
  → Add rule (writes to file + DB)

POST /projects/{name}/sync
  → Force re-sync files → DB
```

## Settings Page Evolution

The `/settings/projects` page gains new sections:

```
┌─────────────────────────────────────────────────────┐
│ Collections (existing)                              │
├─────────────────────────────────────────────────────┤
│ Project: [ switcher ]                               │
│                                                     │
│ General: name, description, path, collection        │
│ Terminal: terminal_command                           │
│ Rules: inline editor for .ai/rules.md               │
│ Learnings: table with category/confidence filters   │
│ Context Preview: "this is what the LLM sees"        │
│ Processes: table                                    │
└─────────────────────────────────────────────────────┘
```

**Context Preview** is the killer feature for the settings page — shows exactly what gets assembled and sent to the LLM, with token counts per section.

## The Feedback Loop

```
Agent starts session
  → calls get_project_context() via MCP
  → gets rules + learnings + context
  → works on task

User corrects agent
  → agent calls add_learning() via MCP
  → learning written to .ai/learnings.md
  → indexed in DB

Next session (any agent, any model)
  → get_project_context() includes the new learning
  → agent doesn't repeat the mistake
```

## Build Phases

**Phase 1: Parser + DB models**
- Parse rules.md → list of rules
- Parse learnings.md → list of structured learnings
- DB tables for rules, learnings
- Sync logic (file → DB, DB → file)

**Phase 2: Context assembly**
- assemble_context() function
- Model profiles
- HTTP endpoint: GET /projects/{name}/context

**Phase 3: MCP tools**
- get_project_context
- add_learning
- add_rule
- search_learnings

**Phase 4: UI**
- Rules editor in settings page
- Learnings table with filters
- Context preview panel

**Phase 5: Intelligence**
- Auto-suggest learnings from agent error patterns
- Cross-project learning search ("this project had the same issue")
- Learning deduplication and merging
