# One-Loop Dev Cycle: Planning

Integration plan for a single, risk-aware loop: coding agent → repo checks → review agent → machine-verifiable evidence → repeatable harnesses. Review agent is pluggable (Greptile, CodeRabbit, CodeQL, custom LLM); the control-plane pattern stays the same.

Inspired by [Ryan Carson’s thread](https://x.com/ryancarson/status/2023452909883609111) and [Harness engineering: leveraging Codex in an agent-first world](https://openai.com/index/harness-engineering/) (Ryan Lopopolo, OpenAI).

---

## The Loop (Target)

| Step | Description |
|------|-------------|
| 1 | **Coding agent writes code** — e.g. Cursor, RDC-spawned agent, or other. |
| 2 | **Repo enforces risk-aware checks before merge** — lint, tests, security/quality gates; failure blocks merge. |
| 3 | **Code review agent validates the PR** — runs on PR (or equivalent) and produces structured findings. |
| 4 | **Evidence is machine-verifiable** — tests + browser (e.g. RDC preview) + review output in a single attestation. |
| 5 | **Findings → repeatable harness cases** — review/CI failures become regression tests or policy rules so they don’t recur. |

The **review agent** can be Greptile, CodeRabbit, CodeQL + policy, custom LLM, or another service; the **control plane** (when to run, what to pass, how to interpret pass/fail) is shared.

---

## Current RDC Mapping

| Loop piece | What RDC has today | Gap |
|------------|---------------------|-----|
| **1. Coding agent** | Tasks, agent spawn, MCP tools, Cursor integration. Agent produces code and can create/update PRs via tools. | No standard “PR creation” flow; depends on agent/tooling. |
| **2. Risk-aware checks** | No CI in repo (no `.github/`). `rdc run skill review` does **staged** review only. Tasks can `requires_review` → human approval. | No **pre-merge** gate: tests/lint/security not enforced at merge. |
| **3. Review agent** | `/review` skill (staged diff); `task_block` / `task_review` for human review. No PR-level, pluggable review **service**. | No abstraction for “run review agent on this PR” or “review agent X vs Y”. |
| **4. Machine-verifiable evidence** | Browser preview (VNC), task result/artifacts, events in DB. | No **single evidence bundle** (tests + browser run + review result) that can be stored/compared. |
| **5. Findings → harness** | Learnings (`.ai/learnings`), rules. | No automatic promotion of review/CI findings into regression tests or policy. |

---

## Control-Plane Pattern (Proposed)

Keep the **when / what / how** independent of the specific review provider:

```
┌─────────────────────────────────────────────────────────────────────────┐
│ Control plane (RDC or repo)                                               │
│  • On: PR opened / updated / “ready for merge”                             │
│  • Input: diff, context, policy (e.g. from .ai/ or repo config)            │
│  • Run: risk checks (lint, test) + optional browser run + review agent    │
│  • Output: Evidence bundle (tests OK, preview OK, review result + list)   │
│  • Decision: merge allowed iff evidence passes policy                     │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                    ┌───────────────┼───────────────┐
                    ▼               ▼               ▼
              Greptile        CodeRabbit      CodeQL / custom LLM
              (review agent)  (review agent)  (review agent)
```

- **Same interface**: “Run review for this PR/diff; return structured result (pass/fail + findings).”
- **Pluggable backend**: config or env selects provider; each adapter normalizes to that interface.
- **Evidence bundle**: one artifact (e.g. JSON) with test summary, preview/screenshot refs, review findings; can be stored in DB or CI artifact.

---

## Can This Be Part of Our Dev Cycle?

**Yes, in phases.** Below is a minimal path that reuses RDC and avoids big-bang redesign.

### Phase 1: Evidence shape + local loop (no CI yet)

- **Define “evidence bundle”** schema: test results (e.g. pytest/jest output), preview/session ref (e.g. RDC task/session id or screenshot), review result (list of findings with severity/source).
- **Extend task or new “PR check” flow**: after agent work, run (1) tests, (2) optional RDC preview, (3) one review provider (e.g. `rdc run skill review` or a new “review PR” skill that calls one backend). Write one evidence bundle to DB or file.
- **Use existing `requires_review`**: keep human approval where needed; evidence bundle is the input to that decision.

Deliverable: “One loop” works **locally** (agent → run checks + review → evidence bundle → human approve/reject).

### Phase 2: Repo-level risk-aware checks (pre-merge gate)

- Add **CI** (e.g. GitHub Actions) that:
  - Runs tests and lint.
  - Optionally runs RDC preview in CI (if feasible) or records “preview not run” in evidence.
  - Calls the **same control-plane API** (e.g. “run review for this PR”) so the review agent runs in CI.
- **Merge rule**: branch protected; merge only if CI passes (tests + lint + review pass or allowed failure policy).

Deliverable: Merge is blocked until risk-aware checks (+ optional review agent) pass.

### Phase 3: Pluggable review agent + findings → harness

- **Review adapter layer**: one interface (e.g. `run_review(diff, context) -> ReviewResult`); implement for Greptile, CodeRabbit, CodeQL, custom LLM. Config (e.g. in `.ai/` or env) selects provider.
- **Findings → harness**: when a review finding or CI failure is “accepted” (e.g. human confirms or policy says “treat as regression”):
  - Option A: add a test or lint rule that would have caught it; run it in CI from then on.
  - Option B: add to `.ai/` rules or learnings so future agent runs avoid the same issue.
  - Option C: store in DB as “harness case” (e.g. “PR pattern X must trigger finding Y”) for future review/agent tuning.

Deliverable: Swap review provider without changing the loop; selected findings become repeatable checks or knowledge.

---

## Conventions to Preserve

- **Skills vs tools**: keep “run pre-merge checks” and “run review agent” as skills that call tools; review adapter can be a tool or internal to a skill.
- **State machine**: `task_block` / `task_review` stay the human gate; evidence bundle can be attached to the task or a new “check_run” entity.
- **No vector DB**: harness/rules stay in files or DB tables, not embeddings-only.
- **Git-friendly**: evidence bundle format (e.g. JSON) can be committed or stored server-side; avoid blocking normal git flow.

---

## Open Decisions

1. **Where does the control plane live?** RDC server (API that CI calls) vs repo-only (CI workflow that calls review APIs directly). RDC gives one place for evidence and task/review state; repo-only keeps CI self-contained.
2. **Evidence storage:** DB only, artifact only, or both? DB supports “pending review” and recall; artifact supports audit and third-party tools.
3. **Harness format:** New `.ai/harness/` (e.g. “PR checks”), or extend learnings/rules, or separate DB table. Depends on whether harness = “run this test” vs “apply this rule next time.”
4. **Blog reference:** Done — [Harness engineering](https://openai.com/index/harness-engineering/) is linked and summarized below.

---

## Alignment with “Harness engineering” (OpenAI)

The [OpenAI post](https://openai.com/index/harness-engineering/) describes building a product with Codex-only code and what made it work. Overlap with this loop:

| Post concept | Our loop |
|--------------|----------|
| **Ralph Wiggum loop** — agent reviews its own changes, requests agent reviews (local + cloud), iterates until reviewers satisfied | Same idea: review agent validates PR; control plane runs the loop and decides pass/fail. |
| **Repository knowledge as system of record** — AGENTS.md as map, structured docs, exec plans, quality/design docs | We use `.ai/` (rules, learnings, context); same idea of “map + pointers” and versioned, repo-local truth. |
| **Agent legibility** — app per worktree, CDP, DOM/screenshots/navigation so the agent can reason about UI | Our evidence bundle: tests + browser (e.g. RDC preview/screenshots) + review; machine-verifiable and legible to agents. |
| **Enforcing architecture** — custom linters, structural tests, “taste invariants”; promote rules into code when docs fall short | Our risk-aware checks + “findings → harness”: failures become lints/tests or `.ai/` rules so they don’t recur. |
| **Evaluation harnesses** (agent-produced) | Our “findings → repeatable harness cases”: review/CI findings become regression tests or policy. |
| **Doc-gardening / golden principles** — recurring agent that fixes stale docs; principles encoded and enforced mechanically | Same spirit: harness cases and rules enforce continuously; we can add a “harness gardener” later. |

Their choice of *minimal* blocking merge gates (throughput over strict gates) is a policy option; our control plane can support either strict gates or “merge with follow-up” depending on repo config.

**Further reading:** [Unlocking the Codex harness: how we built the App Server](https://openai.com/index/unlocking-the-codex-harness/) (same series).

---

## Next Steps

1. **Confirm scope:** Adopt Phase 1 as “one loop in our dev cycle” (local first), then Phase 2 when we want merge protection.
2. **Define evidence bundle schema** (fields, example JSON) and where it’s written (task metadata, new table, file).
3. **Implement one review adapter** (e.g. existing `rdc run skill review` behind the interface, or one external provider) and a small control-plane entry point (skill or API).
4. **Document** the loop in [Human Guide](./human-guide.md) and [AI Agent Guide](./ai-agent-guide.md) once the first phase is usable.

---

## References

- [Ryan Carson’s thread on the one-loop pattern](https://x.com/ryancarson/status/2023452909883609111?s=46&t=Y4b_XpgsHasfPjtS9iwdYA) — coding agent → risk-aware checks → review agent → machine-verifiable evidence → repeatable harnesses; pluggable review (Greptile, CodeRabbit, CodeQL, etc.).
- [Harness engineering: leveraging Codex in an agent-first world](https://openai.com/index/harness-engineering/) — Ryan Lopopolo, OpenAI (Feb 2026).
- [Unlocking the Codex harness: how we built the App Server](https://openai.com/index/unlocking-the-codex-harness/) — same series.
- [Ralph Wiggum Loop](https://ghuntley.com/loop/) — cited in the harness-engineering post (review/iterate until satisfied).
