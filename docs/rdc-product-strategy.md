# RDC Product Strategy

Date: July 10, 2026  
Status: Proposed strategy for validation  
Related review: [Acquisition and IP Due Diligence](acquisition-due-diligence.md)

Execution plan: [RDC Founder-User Implementation Plan](rdc-implementation-plan.md)

## Strategy in one sentence

RDC lets developers delegate work to agents running on their own machines, supervise that work remotely, and take direct terminal or browser control when human intervention is needed.

## Strategic thesis

RDC should not compete primarily as a remote terminal, remote desktop, cloud IDE, or another general-purpose coding agent. Its strongest position is as a local control plane for human-supervised development agents.

The user's workstation or development server already contains valuable context:

- Checked-out repositories and worktrees.
- Credentials and authenticated developer tools.
- Containers, databases, caches, and local services.
- Hardware and network access that may not be available in a cloud IDE.
- Existing agent CLIs and provider subscriptions.
- The exact environment in which the software is built and tested.

RDC makes that environment remotely operable without requiring the developer to reproduce it elsewhere. The product should optimize for delegation and supervision, while retaining direct control as an expert recovery mechanism.

The core interaction model is:

1. **Delegate** — Give an agent a goal, project, constraints, and execution policy.
2. **Supervise** — Follow plans, milestones, evidence, risks, costs, and approval requests.
3. **Intervene** — Open a terminal or browser when automation is stuck or human judgment is required.
4. **Return control** — Give the session back to the agent without losing its history or state.

## The customer problem

Development agents increasingly work for minutes or hours rather than returning one immediate answer. Developers need a way to leave the workstation without abandoning the work.

Current tools address only parts of the problem:

- SSH and remote desktop provide control but little agent-specific context.
- Cloud IDEs provide a remote environment but require moving or recreating local development state.
- Coding agents execute work but often lack durable cross-provider supervision, approval, and recovery.
- Chat and notification products can report status but cannot reliably connect the report to terminals, browsers, processes, diffs, and test evidence.

The problem RDC should solve is:

> How can a developer safely delegate work to an agent on an existing machine, leave the desk, understand what is happening, and intervene only when necessary?

## Target users

### Primary user: agent-heavy individual developer

This developer regularly runs one or more coding agents on a workstation or private server. They want to start a task, walk away, and supervise it from a phone, tablet, or another computer.

Important needs:

- Fast setup on an existing machine.
- Project and worktree isolation.
- Clear task state instead of raw terminal output.
- Approval requests with enough context to make a decision.
- Diffs, tests, browser screenshots, and logs as evidence.
- Direct terminal/browser takeover when necessary.
- Reliable resume and reconnect behavior.

### Secondary user: small engineering team or technical lead

This user supervises multiple projects or agent jobs on dedicated development machines. They need policy, auditability, resource limits, and visibility across work.

Important needs:

- Identity and access control.
- Ownership of tasks and sessions.
- Shared policies and approval boundaries.
- Durable audit trails.
- Cost, capacity, and failure visibility.
- Integration with source control and code review.

### Future user: enterprise platform team

This buyer may operate controlled development runners or workstations on behalf of engineers. The opportunity is significant, but this should not be the initial market because enterprise requirements for isolation, compliance, identity, and support are much higher.

## Jobs to be done

### Primary job

When I give a development agent a substantial task, I want to leave my desk and still know whether the work is progressing safely, so that I can use otherwise idle time without surrendering control of my machine or code.

### Supporting jobs

- When an agent reaches a risky decision, show me the relevant context and let me approve, reject, or redirect it.
- When an agent gets stuck, let me inspect and take over the exact terminal or browser involved.
- When I return to my workstation, preserve the complete history and current state of the work.
- When a task finishes, show evidence that the result is correct rather than only an agent-written summary.
- When several tasks run concurrently, show resource usage, conflicts, ownership, and priority.

## Product model

### The durable work session

The central product object should be a durable work session, not a terminal tab or a chat message.

Every work session should contain:

- Goal and acceptance criteria.
- Project, repository, branch, and worktree.
- Agent and provider.
- Plan, current step, and blockers.
- Execution policy and resource budget.
- Commands and tool actions.
- Approval requests and decisions.
- Terminal, browser, and process associations.
- Files changed and source-control state.
- Tests, screenshots, logs, and other evidence.
- Elapsed time and provider cost.
- Final result, unresolved risks, and next actions.

This model connects high-level delegation to low-level intervention. A developer should be able to move from a goal to its plan, from a plan step to the command that failed, and from that command to the live terminal without changing mental models.

### Three operating modes

| Mode | User intent | Product behavior | Strategic role |
|---|---|---|---|
| Delegate | “Complete this outcome” | Create a governed agent work session | Primary |
| Supervise | “Tell me whether it is safe and progressing” | Present state, evidence, approvals, and exceptions | Primary |
| Intervene | “I need direct control” | Attach to terminal/browser and optionally return control | Essential support |

Direct access should be available but not dominate navigation, onboarding, or positioning. Raw terminal control is an escape hatch for expert users; it is not the ideal interface for routine mobile work.

## Primary workflow

1. The developer selects a project or worktree.
2. The developer states a goal and optional acceptance criteria.
3. RDC shows the proposed agent, policy, budget, and expected approval boundaries.
4. The agent creates a plan and begins work.
5. RDC presents milestones and exceptions rather than flooding the user with output.
6. The developer reviews evidence or responds to an approval request remotely.
7. If necessary, the developer opens the associated terminal or browser and takes control.
8. The developer either completes the intervention or returns control to the agent with guidance.
9. RDC presents the final diff, tests, screenshots, unresolved risks, and recommended next step.

## Example use case

A developer asks RDC to add subscription cancellation to a web application and then leaves home.

From a phone, the developer sees:

- Goal: Add subscription cancellation.
- Current step: Apply and validate the database migration.
- Status: Waiting for approval.
- Proposed action: Run a migration against the local development database.
- Evidence: Relevant schema diff, rollback command, and affected tests.
- Current result: 18 tests passed and one test failed.
- Browser evidence: Screenshot of the new account settings flow.

The developer approves the migration. A test process later hangs, so they open the associated terminal, stop the process, give the agent additional guidance, and return control. The work continues without requiring a full remote-desktop session.

## What RDC should and should not be

### RDC should be

- A control plane across heterogeneous development agents.
- A durable record of goals, actions, approvals, evidence, and results.
- A safe bridge to the developer's existing machine and environment.
- A mobile-friendly supervision interface.
- An expert intervention tool when automation fails.
- Local-first by default, with a deliberate and secure remote-access architecture.

### RDC should not initially be

- A replacement for SSH or a general remote desktop.
- A full cloud IDE.
- A new foundation model or tightly coupled proprietary coding agent.
- A broad team project-management system.
- A voice-first development environment.
- A collection of unrelated integrations without a common work-session model.

## Differentiation

RDC's potential differentiation comes from the combination of:

- Agent-independent goal execution.
- Operation on the developer's existing environment.
- Durable supervision across devices.
- Direct connection between goals, agent actions, terminals, browsers, diffs, and tests.
- Human approvals and explicit execution policies.
- The ability to take control and return work to the agent.

Any one of these features can be copied or provided by another category. The integrated and auditable loop is the product.

## Strategic principles

1. **Goals before terminals.** Start users with the desired outcome, not a shell prompt.
2. **Exceptions before streams.** Present important state changes and blockers before raw output.
3. **Evidence before summaries.** Prefer diffs, tests, screenshots, and recorded actions over unsupported agent claims.
4. **Approval before consequence.** Sensitive actions require explicit policy or human authorization.
5. **Intervention without context loss.** Direct control must preserve and rejoin the work session.
6. **Provider independence.** Agent providers are replaceable execution engines, not the core data model.
7. **Local-first and closed by default.** Remote capability must never imply public exposure by default.
8. **One primary workflow.** Additional channels and integrations must strengthen the delegate-supervise-intervene loop.

## Stress test of the strategy

### Perspective 1: Individual developer

**Argument for the strategy**

The developer can use idle time and continue work while away from a workstation. Their environment is already configured, and the ability to intervene provides confidence that an agent will not remain stuck for hours.

**Objections**

- “I can already use SSH, tmux, or remote desktop.”
- “I do not want another daemon with access to all my source and credentials.”
- “Agent summaries are unreliable; I need to see exactly what happened.”
- “Typing code in a phone terminal is miserable.”

**Strategic response**

RDC must save the developer from watching a terminal, not merely reproduce the terminal remotely. It earns adoption through structured state, evidence, approvals, and fast intervention. Security and transparency are adoption features, not infrastructure details.

**Result**: Passes conditionally. The value is credible if RDC materially reduces monitoring time and provides trustworthy evidence. A terminal-centric implementation fails this test.

### Perspective 2: Staff engineer responsible for reliability

**Argument for the strategy**

A unified work-session model can reduce fragmented scripts, agent processes, terminal tabs, and undocumented actions. Policies and audit trails can make agent execution safer and easier to diagnose.

**Objections**

- The control plane becomes a highly privileged single point of compromise.
- Provider behavior differs, making a common lifecycle difficult.
- Durable sessions, resumability, cancellation, and idempotency are distributed-systems problems.
- Direct human takeover can leave agent state inconsistent.

**Strategic response**

The architecture must treat agents as unreliable workers and the session record as authoritative. Commands and approvals need durable identities; takeover must suspend the agent; return-to-agent must provide an explicit state transition and new context snapshot.

**Result**: Passes only with architectural discipline. The existing global-state and WebSocket design is insufficient for the strategy.

### Perspective 3: Security engineer

**Argument for the strategy**

A governed control plane could be safer than ad hoc tunnels, shared terminals, and unrestricted agent CLIs if it provides least privilege, approvals, isolation, and auditing.

**Objections**

- RDC has access to shells, browsers, credentials, repositories, and external messaging.
- A remotely reachable vulnerability becomes workstation compromise.
- Screenshots, logs, and context are sensitive exfiltration channels.
- Mobile devices and persistent tokens enlarge the attack surface.

**Strategic response**

Remote access cannot be a growth feature until authentication, route-complete authorization, WebSocket security, origin enforcement, short-lived credentials, audit logging, and safe network defaults are verified. Enterprise use additionally requires identity integration and workload isolation.

**Result**: Fails in the current implementation; the strategy remains viable only after the P0 security program in the diligence review.

### Perspective 4: VP of Product

**Argument for the strategy**

The workflow addresses a growing behavioral change: developers are becoming supervisors of asynchronous agents. Mobile supervision is better aligned with that behavior than shrinking an IDE onto a phone.

**Objections**

- The category is young, and users may not yet trust agents with long-running work.
- The product can sprawl across remote desktop, orchestration, messaging, knowledge, and DevOps.
- It may be difficult to explain in one sentence.
- Heavy users may be too technical and prefer custom scripts.

**Strategic response**

Focus on one outcome: safely continue agent work while away from the desk. Remove features from the primary experience unless they improve delegation, supervision, evidence, approval, or intervention.

**Result**: Passes. The positioning is understandable if the product remains disciplined.

### Perspective 5: Engineering manager or small-team buyer

**Argument for the strategy**

The buyer gains visibility into agent work, common policies, reusable workflows, and evidence of completion. RDC could reduce duplicated agent setup and provide safer operational controls.

**Objections**

- Managers do not want surveillance of developers' personal workstations.
- Ownership and accountability become ambiguous when humans and agents share sessions.
- Teams require SSO, access reviews, retention controls, and support.
- Local machines are inconsistent and frequently offline.

**Strategic response**

Team deployment should use dedicated developer-controlled runners or explicit opt-in workstations, not silent employee monitoring. Sessions must record human and agent authorship separately. Team features should follow individual product-market validation.

**Result**: Promising later market, but premature as the initial target.

### Perspective 6: Enterprise security and procurement

**Argument for the strategy**

Running inside an enterprise-controlled network may preserve data locality and provide a governed alternative to unmanaged agent tools.

**Objections**

- No current SSO, device posture, centralized policy, data retention, compliance evidence, or supported isolation model.
- Third-party agent and model providers create data-processing and licensing concerns.
- A workstation daemon with remote command execution is difficult to approve.

**Strategic response**

Do not lead with enterprise claims. A future enterprise edition would need identity federation, policy administration, dedicated runners, secrets isolation, exportable audit events, deployment controls, and explicit provider/data routing.

**Result**: Fails as a current enterprise product; remains a longer-term option.

### Perspective 7: Competitive strategist

**Argument for the strategy**

RDC sits across categories rather than directly inside one: remote access, coding agents, agent orchestration, and developer control planes. Its cross-provider and bring-your-own-machine approach can avoid dependence on a single agent vendor.

**Objections**

- Agent vendors can add mobile apps, remote sessions, and approval notifications.
- Remote-development vendors can add agents.
- Open-source users can combine SSH, tmux, notifications, and agent CLIs.
- Cross-provider support may produce a lowest-common-denominator experience.

**Strategic response**

RDC needs a durable, provider-neutral execution and evidence model that is costly to recreate as a collection of integrations. It should integrate deeply with leading agents while owning session history, policy, intervention, and verification.

**Result**: The moat is not secure. Speed, workflow quality, provider independence, and accumulated execution evidence must create defensibility.

### Perspective 8: Financial buyer or acquirer

**Argument for the strategy**

The product can become a strategic layer between models, coding agents, and development environments. An acquirer with an existing developer distribution channel could accelerate adoption.

**Objections**

- Security remediation and architecture work may exceed the cost of rebuilding the core.
- Willingness to pay among individual developers is uncertain.
- External agent providers may capture most of the value.
- Support burden across local environments may be high.

**Strategic response**

Validate behavioral demand before expanding the platform: do developers repeatedly leave meaningful agent tasks running and return remotely to approve or intervene? If yes, the integrated workflow is an asset. If not, broad platform investment is unjustified.

**Result**: Invest only against measured usage and retention, not feature breadth.

### Perspective 9: Privacy-conscious open-source user

**Argument for the strategy**

Local-first operation and bring-your-own-provider support are attractive alternatives to moving entire development environments into a hosted service.

**Objections**

- “Local-first” loses meaning if the remote gateway, telemetry, or model path leaks data.
- A proprietary hosted control service could become a dependency.
- The project currently lacks clear licensing and data-flow documentation.

**Strategic response**

Document every data flow, make telemetry opt-in, permit fully self-hosted operation, and expose provider routing clearly. Resolve licensing before using open-source positioning.

**Result**: Potentially strong audience after trust and licensing foundations are established.

## Synthesis of the stress test

The strategy survives product and user scrutiny but fails current security and enterprise-readiness tests. This distinction is important:

- The **market thesis** is plausible.
- The **current implementation** is not yet safe enough to deliver the thesis remotely.
- The **differentiation** is promising but not yet defensible.
- The **initial customer** should be an agent-heavy individual developer, not an enterprise buyer.
- The **terminal and browser** should remain essential intervention tools, not the main product surface.

## Key strategic risks

| Risk | Consequence | Mitigation |
|---|---|---|
| Agent vendors add equivalent supervision | RDC becomes redundant | Own cross-provider session history, policy, evidence, and intervention |
| Users prefer SSH/tmux | Product becomes an unnecessary layer | Prove reduced monitoring time and better recovery |
| Remote compromise | Catastrophic user harm and loss of trust | Security gate before remote positioning; closed-by-default architecture |
| Provider fragmentation | Inconsistent and unreliable experience | Capability contracts and provider-specific adapters |
| Feature sprawl | Slow stabilization and unclear message | Enforce the primary workflow and explicit non-goals |
| Local environment variability | High support cost | Diagnostics, supported profiles, dedicated-runner option |
| Low willingness to pay | Weak business | Validate repeated use and time saved before pricing expansion |
| Weak evidence quality | Users cannot trust completion | First-class tests, diffs, screenshots, logs, and provenance |

## Assumptions that must be validated

1. Developers routinely leave agent tasks running long enough to benefit from remote supervision.
2. A meaningful percentage of those tasks require approval, redirection, or recovery.
3. Structured status and evidence are more valuable than raw terminal streaming.
4. Developers prefer using an existing machine over recreating the environment in a cloud IDE.
5. Cross-provider support matters enough to offset the complexity it introduces.
6. Users will install a privileged local control plane if its security model is credible.
7. Mobile supervision produces repeat usage rather than novelty usage.

## Validation plan

### Phase 1: problem validation

Interview 12 to 20 developers who use coding agents daily. Ask for concrete recent examples rather than reactions to the concept:

- How often did an agent run after they left the desk?
- How did they monitor it?
- What caused them to return to the machine?
- What information was required to approve or redirect it?
- What failures wasted the most time?
- Which machines and environments could not easily move to a hosted IDE?

Success signal: at least half can identify repeated recent instances of unattended work and meaningful intervention cost.

### Phase 2: concierge workflow

Test the delegate-supervise-intervene loop with a narrow prototype:

- One supported operating system.
- One or two agent providers.
- One terminal implementation.
- No public internet exposure by default.
- Goal, plan, state, approval, evidence, and terminal takeover.

Success signals:

- At least 60% of activated users complete three remotely supervised sessions within two weeks.
- At least 30% of sessions are checked remotely.
- Users can resolve most approval or recovery events without returning to the workstation.
- No user reports uncertainty about whether the human or agent controls the session.

### Phase 3: willingness to pay

Test pricing only after repeated behavior exists. Candidate value metrics include:

- Number of concurrently active work sessions.
- Number of registered machines or runners.
- Retention and audit-history duration.
- Team policy and shared supervision features.

Avoid charging primarily for terminal minutes; that frames RDC as commodity remote access.

## North-star and supporting metrics

### North-star metric

**Successfully supervised remote work sessions per weekly active developer.**

A successful session completes its stated outcome or reaches a deliberate human decision with sufficient evidence, without requiring the developer to return physically to the machine.

### Supporting metrics

- Installation-to-first-session time.
- Percentage of goals that reach an executable plan.
- Remote check-in rate.
- Approval response time.
- Intervention rate and successful return-to-agent rate.
- Task completion without workstation return.
- Cancellation and recovery success.
- Evidence-view rate before acceptance.
- Weekly retained developers and projects.
- Security-sensitive denials and policy violations.
- Support incidents per supported environment.

## Product sequencing

### Now: make the primary loop trustworthy

- Complete the P0 security work in the diligence review.
- Establish the durable work-session model.
- Support goal, plan, progress, approval, evidence, and terminal takeover.
- Choose one primary browser path and one primary terminal path.
- Make reconnect, cancellation, and ownership explicit.
- Test the workflow with agent-heavy individual developers.

### Next: make it repeatable

- Add provider capability adapters.
- Add worktree isolation and conflict detection.
- Improve evidence capture and final review.
- Add reliable notifications and deep links into the relevant session event.
- Add backup, restore, diagnostics, and supported release packaging.

### Later: make it governable for teams

- Dedicated runners and organization ownership.
- SSO and role-based policy.
- Shared approval rules and budgets.
- Exportable audit events and retention controls.
- Source-control and review-system integration.
- Capacity, cost, and reliability reporting.

### Defer

- Voice-first control.
- Phone-call workflows.
- Broad messaging-channel support.
- General-purpose project management.
- Multiple overlapping browser implementations.
- Enterprise compliance claims before the required controls exist.

## Decision rules

Continue investing in the strategy if:

- Users repeatedly supervise meaningful tasks away from the workstation.
- Structured evidence and approvals reduce time spent watching agents.
- Terminal/browser intervention resolves failures without destroying session continuity.
- Users demonstrate retention beyond initial novelty.

Reconsider or narrow the strategy if:

- Most usage remains raw terminal access.
- Users rarely leave agent tasks running.
- Agent-provider native remote features satisfy the need.
- Maintaining heterogeneous local environments dominates engineering capacity.
- Security requirements make individual self-hosting impractical.

If raw terminal access becomes the dominant retained behavior, RDC should not continue building a broad orchestration platform by assumption. It should either narrow into a best-in-class secure remote developer console or stop competing with established remote-access tools.

## Final strategic recommendation

Pursue the hybrid model with a deliberate priority order:

1. Goals and durable work sessions are the product core.
2. Supervision, evidence, and approvals are the primary remote experience.
3. Terminal and browser access are essential intervention and recovery mechanisms.
4. Direct remote development is supported but is not the lead positioning.
5. Remote exposure is not marketed until the security foundation passes explicit acceptance tests.

The strategy should be judged by whether developers can safely keep meaningful work moving while away from the desk—not by the number of integrations, terminal sessions, or agent providers RDC can display.
