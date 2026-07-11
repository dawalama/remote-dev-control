# Acquisition and IP Due Diligence Review

Date: July 10, 2026  
Project: Remote Dev Ctrl (RDC)  
Repository reviewed: `agent-dev-tool`

Execution plan: [RDC Founder-User Implementation Plan](rdc-implementation-plan.md)

## Executive summary

Remote Dev Ctrl demonstrates strong product insight and considerable functional breadth. It combines project management, agent orchestration, terminals, browser control, task execution, process supervision, knowledge management, mobile access, and MCP integration into a local development command center.

The project is not currently ready to be acquired or represented as a production-ready commercial product without conditions. Its most important risks are:

- Critical authentication and authorization gaps around WebSockets and publicly exempted route prefixes.
- A frontend release that does not pass its existing build and lint commands.
- Effectively no reliable automated regression or security test suite relative to approximately 59,000 lines of source and roughly 196 API/WebSocket routes.
- Missing license, attribution, security, and contributor-governance artifacts needed for IP diligence.
- Large monolithic modules and broad feature scope that increase regression and maintenance risk.
- High key-person risk: the visible Git history is almost entirely attributable to one contributor.

The recommended acquisition posture is a conditional technology/talent acquisition. Valuation should reflect a significant security and stabilization program, and closing should depend on verified IP ownership and remediation or containment of the remote-control attack surface.

## Assessment scorecard

| Area | Assessment | Risk |
|---|---|---|
| Product vision | Strong and differentiated | Medium |
| Functional breadth | Impressive for the project age | Medium |
| Architecture | Valuable components with weak boundaries | High |
| Security | Multiple probable host-compromise paths | Critical |
| Testing and release quality | Effectively absent | Critical |
| Maintainability | Large monoliths and duplicated surfaces | High |
| IP provenance | Incomplete evidence | High |
| Bus factor | Nearly all history from one contributor | High |
| Production readiness | Not ready | Critical |

## Review scope and evidence

The review considered:

- Repository structure and Git history.
- Python and TypeScript source organization.
- Server authentication, authorization, browser, VNC, terminal, process, task, and WebSocket paths.
- Frontend package configuration and build/lint gates.
- Existing tests and documentation.
- Packaging, installation, licensing, contributor, and dependency-provenance signals.
- The product from staff-engineering, product-leadership, and daily-user perspectives.

Observed validation results:

- `npm run build` failed because of a TypeScript unused-variable error in `terminal-view.tsx`.
- `npm run lint` reported 44 errors and 15 warnings.
- Python tests could not be reconstructed in the review environment because the existing virtual environment referenced a missing interpreter and dependency reconstruction required unavailable network access.
- The only test file imports the obsolete `ai_knowledge` package, relies on Docker, and behaves primarily as a manual smoke-test script.

The repository contained existing uncommitted work in frontend browser/terminal files and backend app/browser files. This review did not alter those files.

## Critical security findings

### 1. Public route prefixes expose sensitive and mutating operations

The authentication middleware treats broad prefixes as public, including:

- `/context`
- `/screenshots/`
- `/vnc/sessions/`
- `/vnc/proxy/`
- `/browser/viewer`
- `/browser/cdp-proxy`

The prefix exemption applies regardless of HTTP method. Consequently, the exemption is not limited to loading an image or iframe: routes underneath those prefixes include context creation, upload, deletion, VNC session operations, screenshot operations, and browser control.

This is dangerous because context and screenshots may contain source code, credentials, customer data, internal URLs, or terminal output. VNC and CDP provide interactive access to browser sessions.

Required changes:

- Remove API mutation routes from public prefixes.
- Make authentication and authorization fail closed.
- Serve protected binary assets through authenticated handlers or short-lived signed URLs.
- Distinguish static UI assets from application data routes.
- Add tests enumerating every registered route and failing if an authorization policy is missing.

### 2. Authorization is fail-open for unmapped endpoints

The middleware applies role permission checks only when an endpoint appears in `ENDPOINT_PERMISSIONS`. A protected but unmapped endpoint merely requires a valid token; it receives no role-specific permission check.

Large parts of the application—including terminal, process, browser, VNC, settings, and administrative functionality—are not comprehensively represented in that permission map. This means a low-privilege authenticated token may reach powerful operations intended for an operator or administrator.

Required changes:

- Require an explicit permission declaration for every non-public route.
- Fail application startup when a route has no policy.
- Add domain permissions for terminal read/write, process start/stop, browser control, context access, screenshots, VNC, settings, pairing, and agent approvals.
- Test viewer, operator, agent, administrator, expired, revoked, and paired-device tokens.

### 3. Most WebSocket handlers do not authenticate clients

Most WebSocket endpoints accept connections without validating a token in the handler. Sensitive examples include:

- Terminal input/output.
- Server, process, and task log streaming.
- VNC traffic.
- Browser CDP control.
- Speech-to-text using the operator's configured API key.
- Agent step streaming and approval responses.
- The general `/ws` endpoint, which processes an agent `spawn` command.

HTTP middleware does not provide effective protection for WebSocket handlers in the same way it protects ordinary requests. Comments suggesting that authentication happens via query parameter or message are not matched by checks in most handlers.

An attacker who can reach the server may be able to read sensitive logs, interact with a terminal, control a browser, approve agent actions, consume paid API services, or spawn an agent. Because agents and terminals execute local commands, the potential impact is host compromise.

Required changes:

- Authenticate and authorize before calling `accept()`.
- Authorize individual messages such as spawn, input, approval, and cancellation.
- Use short-lived, audience-bound WebSocket tickets rather than long-lived bearer tokens in URLs.
- Validate `Origin` against an explicit allowlist.
- Apply connection, message, and bandwidth limits.
- Add integration tests for every WebSocket endpoint.

### 4. Remote-access guidance is unsafe with the current implementation

The README recommends exposing RDC through Cloudflare Tunnel and Caddy. That guidance conflicts with the current public routes, unauthenticated WebSockets, permissive cross-origin configuration, and host-level control capabilities.

The application also currently has:

- `allow_origins=["*"]` with credentials enabled.
- Public OpenAPI documentation.
- A globally permissive clipboard policy.
- Client-facing exception responses containing exception details and traceback fragments.

Required changes:

- Remove or prominently quarantine remote-access instructions until security acceptance criteria pass.
- Bind to loopback by default.
- Require an explicit unsafe/development flag to bind to other interfaces.
- Restrict origins, hosts, headers, and clipboard permissions.
- Return opaque production errors and retain detailed traces only in protected server logs.
- Document a threat model for local-only and remotely exposed deployments.

### 5. Command execution needs a formal trust boundary

The product intentionally executes shell commands through terminals, task agents, process definitions, and tool functions. Several paths use shell execution semantics. Shell use is not inherently incorrect for this product, but it substantially raises the importance of authorization, command provenance, approval policies, and isolation.

Required changes:

- Define which actors may execute commands and in which project roots.
- Use argument-vector subprocess execution where shell syntax is unnecessary.
- Validate and canonicalize working directories.
- Prevent project-root escape through paths and symlinks.
- Add explicit approvals for destructive, privileged, network, credential, and out-of-root operations.
- Record the actor, source, command, working directory, approval, result, and relevant task ID in a tamper-evident audit trail.
- Consider container, VM, or OS-level sandbox profiles for untrusted agents.

## Engineering assessment

### Architecture

The codebase has useful domain components, including event handling, repositories, migrations, agent providers, terminal management, process discovery, browser management, and multiple user interfaces. However, the top-level composition has grown into several very large modules:

- `server/app.py`: approximately 8,000 lines.
- `server/intent.py`: approximately 2,640 lines.
- `cli.py`: approximately 2,315 lines.
- `server/db/repositories.py`: approximately 1,600 lines.
- `system-settings.tsx`: approximately 1,530 lines.

The server relies heavily on module-global mutable managers. This makes initialization order, testing, cleanup, multi-instance operation, and error isolation difficult. A change to a central file has a large regression radius.

Recommended direction:

- Split `app.py` into domain routers: auth, projects, tasks, agents, terminals, processes, browser, context, screenshots, VNC, settings, and administration.
- Put domain behavior in injected services rather than route functions.
- Use a typed application container or FastAPI dependencies instead of module-global runtime managers.
- Define lifecycle ownership for processes, sockets, database connections, and background tasks.
- Extract protocol-independent domain state transitions from transport handlers.
- Generate a typed frontend API client from the OpenAPI schema.

### Testing

The current test posture is the largest engineering-readiness gap after security. One manual-style VNC test cannot validate a product with nearly 200 routes, local command execution, databases, background workers, WebSockets, multiple UI layouts, browser automation, and remote access.

Minimum test portfolio:

- Unit tests for token lifecycle, permissions, path policy, configuration, vault behavior, repositories, migrations, task transitions, and process discovery.
- Integration tests for every HTTP authorization class.
- WebSocket authentication and message-authorization tests.
- Terminal and process tests using controlled subprocess fixtures.
- Database migration-forward and backup/restore tests.
- Frontend component tests for the primary workflow.
- Browser end-to-end tests covering project selection, terminal attach, task launch, approval, browser observation, and recovery.
- Security tests for path traversal, SSRF, origin enforcement, CSRF assumptions, token leakage, log redaction, and upload limits.

### Build and release quality

The frontend does not currently pass its configured build and lint gates. There is no visible CI configuration at the repository root, and no release tags were found. Both `package-lock.json` and `pnpm-lock.yaml` exist even though the documentation directs users to pnpm.

Required changes:

- Fix the current TypeScript and lint failures.
- Select and enforce one Node package manager.
- Add CI covering Python versions 3.11 through 3.13, TypeScript build, lint, tests, dependency review, secret scanning, license scanning, and SBOM generation.
- Add reproducible release artifacts and signed version tags.
- Define semantic versioning, compatibility, database migration, upgrade, rollback, and support policies.
- Test the one-line installer in clean macOS and Linux environments.

### Installation and supply-chain safety

The documented installation method pipes a network script directly into a shell. The installer may then install package managers, clone a moving branch, globally install pnpm, resolve dependencies, and build the frontend. This is convenient but not appropriate as the only commercial installation path.

Required changes:

- Publish versioned, checksummed releases.
- Pin installer downloads and tool versions.
- Avoid silently continuing after a failed source update.
- Provide signed packages or platform-native installation options.
- Generate and publish CycloneDX or SPDX SBOMs.
- Add dependency vulnerability and license gates.
- Document exactly what is installed and changed on the host.

### Documentation drift

The repository contains stale references to the former `ai_knowledge` and ADT names. The sole test imports the former package. The TODO file also references paths that no longer exist. The Git remote and published clone URLs appear to reflect different repository names.

This is more than a cosmetic issue: it undermines reproducibility, creates broken guidance, and complicates IP transfer.

Required changes:

- Complete the rename with an automated repository-wide validation check.
- Verify every documented command in CI.
- Establish one authoritative architecture document and one supported installation path.
- Mark speculative design documents clearly so they are not mistaken for implemented behavior.

## IP and legal diligence

### Missing project license and attribution

No root `LICENSE`, `NOTICE`, contributor agreement, provenance policy, `SECURITY.md`, or third-party attribution file was found.

Until these are supplied, a buyer cannot confidently determine:

- What rights are granted to users or potential acquirers.
- Whether all bundled static vendor JavaScript can be redistributed.
- Whether required third-party notices are being provided.
- Whether every contributor assigned relevant rights to the seller.
- Whether AI-generated code was reviewed for provenance and incompatible reproduction.
- Whether branding, domains, package names, and repository identities can be transferred.

### Contributor and key-person risk

The visible Git history is almost entirely associated with one contributor. This simplifies the contributor list but increases both key-person risk and the importance of confirming that the individual or selling entity owns all work.

Required closing evidence:

- Founder, employee, and contractor invention-assignment agreements.
- Confirmation of whether development occurred while employed elsewhere and whether any prior-employer resources or confidential information were used.
- A list of all contributors outside the visible Git identities.
- AI coding-tool usage and provenance representations.
- Identification of copied, adapted, generated, or externally sourced code and assets.
- Repository, package namespace, domain, Cloudflare, and trademark ownership evidence.
- Security incident and vulnerability disclosure history.

### Third-party dependencies and bundled assets

The product uses a substantial Python and JavaScript dependency set and commits third-party browser assets under `server/static/vendor`. A lock file alone does not establish redistribution compliance.

Required diligence:

- Produce complete source and binary SBOMs.
- Scan direct and transitive licenses.
- Review copyleft, source-availability, model, font, icon, and bundled-browser obligations.
- Verify notices for committed vendor files.
- Review dependency security and maintenance status.
- Confirm commercial terms and usage policies for external AI, voice, messaging, tunnel, browser, and agent-CLI providers.

## Product leadership assessment

### Strongest product opportunity

The defensible thesis is not simply another coding agent. The stronger position is a local control plane for human-supervised development agents:

- Multiple project and agent sessions.
- Terminal and browser observation.
- Durable task and process state.
- Mobile and kiosk supervision.
- Knowledge/context capture.
- Explicit review and approval.
- MCP and CLI interoperability.

The combination of browser plus terminal visibility, mobile supervision, and project-aware agent management is valuable. If reliability and safety are made central, RDC could become an operator console for development work performed by heterogeneous agents.

### Product weakness: excessive simultaneous scope

The project currently includes or plans multiple browser paths, VNC, PinchTab, Browserless/CDP, terminals, tasks, processes, knowledge management, scaffolding, MCP, voice, Twilio, Telegram, mobile, kiosk, multiple providers, and orchestration.

This breadth creates an impressive demonstration but diffuses engineering effort and obscures the primary customer outcome. The product lacks evidence of a single hardened workflow that users can trust daily.

Recommended primary workflow:

1. Register a project.
2. Launch or attach an agent.
3. Observe terminal and browser activity.
4. Require approval for sensitive operations.
5. Review changes and supporting evidence.
6. Resume safely from another device.

Voice calls, Telegram orchestration, scaffolding, and secondary browser architectures should be deferred until that workflow is dependable.

### Product metrics to establish

- Time from installation to first managed project.
- Time to first successful agent task.
- Percentage of tasks completed without manual recovery.
- Approval frequency and approval latency.
- Task cancellation and recovery success.
- Weekly retained projects and operators.
- Agent/provider success rate by task type.
- Browser/terminal reconnect success.
- Incidents involving unintended commands, secrets, or out-of-root access.

## Daily engineer-user assessment

### What is attractive

- One dashboard for terminals, tasks, processes, and browser state.
- Mobile supervision and reconnect concepts.
- Project-aware presets and context.
- Local-first operation and storage.
- Support for multiple external agent tools.
- CLI and MCP entry points.

### What blocks daily adoption

- Security uncertainty on a workstation containing source code and credentials.
- Failed build and lint gates.
- No trustworthy regression suite.
- Too many overlapping browser and remote-view concepts.
- Unclear task guarantees, failure recovery, and cancellation semantics.
- Tight coupling to external CLIs and hard-coded model identifiers.
- Documentation drift following the rename.
- No supported upgrade, backup, migration, or rollback contract.

### User-experience opportunities

- Provide a single activity timeline connecting user request, approvals, commands, browser actions, file changes, and result.
- Make task state and recovery actions consistent across providers.
- Show why an agent is waiting and exactly what permission it requests.
- Add safe defaults and visible risk levels to terminal and browser actions.
- Replace competing browser panels with one capability-driven interface.
- Provide a clear offline/degraded mode when external providers are unavailable.
- Make backup, restore, and environment diagnostics accessible in the UI.

## Prioritized remediation roadmap

### P0: containment and minimum safety — 1 to 2 weeks

- Bind exclusively to loopback by default.
- Disable or quarantine remote-exposure guidance.
- Fix HTTP and WebSocket authentication.
- Make authorization fail closed and route-complete.
- Protect context, screenshots, VNC, browser, terminal, logs, and agent approvals.
- Remove traceback details from client responses.
- Restrict CORS, origins, hosts, and security policies.
- Add authentication and authorization regression tests.
- Fix the frontend build and establish a green CI gate.
- Add an appropriate project license, or explicitly mark the source proprietary pending legal review.

Exit criteria:

- Anonymous clients cannot access sensitive data or control functionality.
- Viewer tokens cannot mutate state or execute commands.
- All HTTP and WebSocket routes have explicit policies.
- Build, lint, and security tests pass in CI.

### P1: trustworthy release foundation — 2 to 6 weeks

- Split the server into domain routers and services.
- Replace module-global managers with lifecycle-managed dependencies.
- Add unit, integration, WebSocket, migration, and primary-flow tests.
- Standardize dependency and package management.
- Add secret scanning, vulnerability scanning, license scanning, and SBOM generation.
- Add structured logs, correlation IDs, and tested secret redaction.
- Define supported platforms, versions, installation, backup, restore, upgrade, and rollback.
- Publish a threat model and security policy.

Exit criteria:

- A clean machine can install and validate a pinned release reproducibly.
- Core workflows have automated coverage.
- Database migrations and rollback procedures are exercised.
- Operational failures produce actionable, non-sensitive diagnostics.

### P2: architecture and product coherence — 6 to 12 weeks

- Select one primary browser execution architecture.
- Unify agent providers behind a stable capability contract.
- Replace hard-coded model names with provider discovery and configuration.
- Add explicit policy and approval layers for shell, filesystem, browser, network, and messaging actions.
- Add task budgets, idempotency, cancellation guarantees, and durable event replay.
- Instrument the primary product metrics.
- Publish a supported deployment model.

Exit criteria:

- Provider changes do not require UI or task-state rewrites.
- The operator can understand and audit every consequential agent action.
- The primary workflow is measurably reliable across supported environments.

## Recommended acquisition conditions

A buyer should make closing or a meaningful holdback conditional on:

1. Clean founder, employee, and contractor IP assignments.
2. A complete dependency and bundled-asset SBOM with legal review of exceptions.
3. Remediation or verified containment of unauthenticated host-control paths.
4. A reproducible green build and test pipeline.
5. A meaningful automated security regression suite.
6. Transfer of repository, domains, accounts, package names, and relevant credentials.
7. A documented 90-day security, architecture, and product-focus plan.
8. Appropriate key-person retention and knowledge-transfer commitments.

## Final recommendation

RDC contains a credible product concept and a meaningful amount of implementation work. Its strongest assets are the integrated operator experience and the breadth of real development-control capabilities already explored.

The codebase should nevertheless be valued as a high-potential prototype, not as a secure or supportable production platform. The immediate priority is to contain the attack surface and establish reliable build, test, and IP foundations. After those conditions are met, the buyer can rationalize the architecture and concentrate the product around supervised, auditable agent execution.
