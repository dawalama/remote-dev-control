# Remote Desktop Agent Plan

Purpose: planning document for enabling full remote desktop streaming/control for long-lived autonomous agent loops, with evidence-rich PR outputs.

## Scope

- Browser remains the operator console.
- Execution surface is full computer control (not browser-only): desktop apps, terminal, IDE, emulator/simulator, multi-window flows.
- Human takeover is optional and must preserve traceability.

## Ordered Operations

Implement in this order:

1. **Architecture Lock**
   - Finalize control plane and streaming/control stack.
   - Define one isolated desktop session per agent run.
2. **Security Gate**
   - Gateway-only exposure; no public raw desktop protocol ports.
   - AuthN/AuthZ, short-lived session tokens, revoke/kill switch.
   - Scoped secrets/filesystem/network permissions per run.
3. **Desktop Runtime**
   - Session lifecycle: start/stop/attach/detach/force-terminate.
   - Health checks and automatic recovery for stalled sessions.
4. **Artifact Pipeline**
   - Deterministic per-run artifact directory.
   - Mandatory: recording, screenshots on key events/failures, terminal/action logs, runtime metadata.
5. **Agent Context Bridge**
   - Inbound context to agent: latest screenshots, recent logs, focused app/window state, objective/constraints.
   - Outbound context from agent: run summary, blockers, evidence links, next actions.
6. **PR Evidence Contract**
   - Auto-attach summary + evidence links + validation output on PR create/update.
   - Autonomous desktop tasks require evidence before merge.
7. **Autonomy Controls**
   - Run states: `running`, `waiting`, `blocked`, `needs-input`, `completed`.
   - Retry policy + stuck detection + escalation behavior.
8. **Isolation and Scale Hardening**
   - Evolve toward ephemeral per-run environments.
   - Scheduler, concurrency limits, quotas, stale-run cleanup.

## Branch Strategy

- `feat/remote-desktop-platform`: core platform implementation.
- `run/<agent-id>/<task-id>`: autonomous task execution branches.
- Optional hardening stream: `feat/remote-desktop-security-hardening`.

## Required Run Artifacts

- `run-summary.md`: objective, outcome, blockers, next actions.
- `steps.json`: timestamped action trace.
- `terminal.log`: command/output trace.
- `screenshots/` and/or `recording.mp4`: visual evidence.
- `metadata.json`: branch, commit SHA, prompt, environment/session config.

## Readiness Criteria

A run is valid only if it can:

- launch isolated desktop session;
- be remotely viewed/controlled from browser;
- provide live context into the agent loop;
- generate replayable evidence artifacts;
- publish evidence-linked PR updates automatically.
