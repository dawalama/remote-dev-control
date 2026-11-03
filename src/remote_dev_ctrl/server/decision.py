"""System 1 Decision Engine (Jev / Decision API integration) for RDC.

Provides fast, typed, probabilistic decisions using the three core Decision primitives:
  1. `choice` — Picks one option from a predefined map of criteria, with calibrated probabilities & confidence.
  2. `score`  — Places the state onto an ordered rubric scale (e.g. read_only -> workspace_write -> irreversible).
  3. `noul`   — Evaluates a binary yes/no proposition as a calibrated probability in [0.0, 1.0].

Backends (in priority order):
  1. Dedicated Self-Hosted Decision API (`RDC_DECISION_API_URL`, e.g., Laya / OpenJev server)
  2. OpenRouter Jev API (`POST https://openrouter.ai/api/alpha/decisions`, model `typesafe/jev-1.14`)
  3. Local Ollama / Fast OpenAI-compatible JSON evaluator
  4. Calibrated deterministic evaluator (zero-latency offline/test fallback)
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class DecisionQuestion(BaseModel):
    type: Literal["choice", "score", "noul"]
    instructions: str
    criteria: dict[str, str] = Field(default_factory=dict)


class DecisionRequest(BaseModel):
    state: Any
    questions: dict[str, DecisionQuestion]
    model: str | None = None
    prefer_local: bool = False


class DecisionResponse(BaseModel):
    provider: str
    model: str
    latency_ms: int
    results: dict[str, Any]


# ---------------------------------------------------------------------------
# Built-in RDC Decision Schemas
# ---------------------------------------------------------------------------

COMMAND_RISK_QUESTIONS: dict[str, DecisionQuestion] = {
    "is_destructive": DecisionQuestion(
        type="noul",
        instructions="Does this command delete user files, drop databases, force-push, reset git history, or cause irreversible data loss?",
    ),
    "touches_secrets_or_external": DecisionQuestion(
        type="noul",
        instructions="Does this command read sensitive credential files (~/.ssh, .env, keys) or exfiltrate/POST data to an external network endpoint?",
    ),
    "blast_radius": DecisionQuestion(
        type="score",
        instructions="Rate the operational blast radius of executing this command.",
        criteria={
            "read_only": "Pure inspection, status check, diff, or read-only query",
            "local_test_build": "Running local tests, linters, typechecks, or compilers",
            "workspace_write": "Modifying project source files or local git commits",
            "external_or_irreversible": "Pushing to remotes, deploying, deleting data, or modifying system state outside the workspace",
        },
    ),
    "approval_policy": DecisionQuestion(
        type="choice",
        instructions="Should this action be auto-approved in supervised mode or require explicit operator confirmation?",
        criteria={
            "auto_approve": "Safe read, test, build, or bounded workspace edit within project scope",
            "ask_operator": "Destructive, external network mutation, credential access, or ambiguous blast radius",
        },
    ),
}

SESSION_TRIAGE_QUESTIONS: dict[str, DecisionQuestion] = {
    "outcome_satisfied": DecisionQuestion(
        type="noul",
        instructions="Based on the session summary, verification status, and git state, did the agent achieve the core technical goal?",
    ),
    "ready_for_host_commit": DecisionQuestion(
        type="noul",
        instructions="Are there valid uncommitted code changes in the working tree that are ready to be committed by the host?",
    ),
    "blocker_type": DecisionQuestion(
        type="choice",
        instructions="Identify the primary blocker preventing this session from being marked complete.",
        criteria={
            "none": "No blocker; work is verified and committed or ready to complete",
            "sandbox_git_blocked": "Agent finished code changes but could not write to .git or commit due to sandbox restrictions",
            "missing_git_remote": "Commit succeeded or was attempted, but git push failed because no remote repository is configured",
            "verification_failed": "Tests, build, or lint checks failed",
            "auth_or_provider": "Agent CLI failed to start due to authentication, expired token, or missing model/binary",
            "needs_operator_input": "Agent is waiting for a product or architectural decision from the human operator",
        },
    ),
    "recommended_action": DecisionQuestion(
        type="choice",
        instructions="What is the single highest-leverage next step for the operator?",
        criteria={
            "host_commit": "Commit the working tree changes on the host in 1 click",
            "run_verification": "Run the project verification command to confirm tests pass",
            "accept_and_complete": "Accept the verified outcome and mark the session complete",
            "continue_with_guidance": "Send follow-up instructions to the agent to fix remaining issues",
            "switch_provider_and_retry": "Switch to a healthy agent wrapper and retry the session",
        },
    ),
}

INTENT_ROUTING_QUESTIONS: dict[str, DecisionQuestion] = {
    "complexity": DecisionQuestion(
        type="score",
        instructions="Rate the cognitive complexity required to handle this operator message.",
        criteria={
            "trivial": "Simple UI navigation, toggling panels, switching workstreams, or status check",
            "simple": "Direct factual question, running a single command, or quick status summary",
            "complex": "Multi-step code change, feature implementation, refactoring, or spawning a coding session",
            "reasoning": "Deep architectural trade-offs, root-cause debugging, security audit, or system design",
        },
    ),
    "recommended_tier": DecisionQuestion(
        type="choice",
        instructions="Select the optimal model tier for this request.",
        criteria={
            "decision": "System 1 structured classification or routing (<50ms)",
            "fast": "Low-latency utility model (Haiku / Flash / local 8B)",
            "coding": "Code-specialized implementation model (Sonnet / GPT-5.2-Codex / Qwen3-Coder)",
            "reasoning": "Deep reasoning model (Opus / o3 / DeepSeek-R1)",
        },
    ),
    "worth_saving_to_memory": DecisionQuestion(
        type="noul",
        instructions="Does this interaction contain a durable architectural decision, bug root cause, project convention, or requirement worth saving to long-term project memory?",
    ),
}


# ---------------------------------------------------------------------------
# Calibrated Deterministic Evaluator (Zero-Latency Fallback)
# ---------------------------------------------------------------------------

_DESTRUCTIVE_CMD_RE = re.compile(
    r"\b(rm\s+-rf|git\s+reset\s+--hard|git\s+push\s+--force|git\s+clean\s+-fd|"
    r"drop\s+table|drop\s+database|truncate\s+table|mkfs|dd\s+if=)\b",
    re.IGNORECASE,
)
_SECRET_OR_NET_CMD_RE = re.compile(
    r"(~/.ssh|\.aws/credentials|\.env\b|curl\s+.*-X\s*(POST|PUT)|wget\s+--post|webhook\.site)",
    re.IGNORECASE,
)
_READ_ONLY_CMD_RE = re.compile(
    r"^\s*(git\s+(status|diff|log|show|branch)|ls|cat|head|tail|pwd|echo|rg|grep|find)\b",
    re.IGNORECASE,
)
_TEST_BUILD_CMD_RE = re.compile(
    r"\b(pytest|npm\s+(test|run\s+verify|run\s+build|run\s+lint)|pnpm\s+(test|build|lint)|cargo\s+(test|check)|go\s+test|tsc)\b",
    re.IGNORECASE,
)


def _normalize_probs(weights: dict[str, float]) -> dict[str, float]:
    total = sum(max(v, 0.001) for v in weights.values())
    if total <= 0:
        n = max(len(weights), 1)
        return {k: round(1.0 / n, 4) for k in weights}
    return {k: round(max(v, 0.001) / total, 4) for k, v in weights.items()}


def _make_choice(weights: dict[str, float]) -> dict[str, Any]:
    probs = _normalize_probs(weights)
    best_key = max(probs, key=lambda k: probs[k])
    sorted_vals = sorted(probs.values(), reverse=True)
    margin = (sorted_vals[0] - sorted_vals[1]) if len(sorted_vals) > 1 else sorted_vals[0]
    confidence = round(min(1.0, max(0.5, sorted_vals[0] * 0.7 + margin * 0.3)), 4)
    return {
        "type": "choice",
        "value": best_key,
        "probabilities": probs,
        "confidence": confidence,
    }


def _make_score(ordered_keys: list[str], weights: dict[str, float]) -> dict[str, Any]:
    probs = _normalize_probs({k: weights.get(k, 0.05) for k in ordered_keys})
    best_key = max(probs, key=lambda k: probs[k])
    n = max(len(ordered_keys) - 1, 1)
    weighted_pos = sum((idx / n) * probs[k] for idx, k in enumerate(ordered_keys))
    sorted_vals = sorted(probs.values(), reverse=True)
    confidence = round(min(1.0, max(0.5, sorted_vals[0])), 4)
    return {
        "type": "score",
        "level": best_key,
        "score": round(weighted_pos, 4),
        "probabilities": probs,
        "confidence": confidence,
    }


def _evaluate_deterministic(state: Any, questions: dict[str, DecisionQuestion]) -> dict[str, Any]:
    """Fast calibrated rule-based evaluation matching the exact Jev schema."""
    state_dict = state if isinstance(state, dict) else {"raw": str(state)}
    state_str = json.dumps(state, default=str).lower() if not isinstance(state, str) else state.lower()

    results: dict[str, Any] = {}

    for qid, q in questions.items():
        keys = list(q.criteria.keys())

        # 1. Command Risk questions
        if qid == "is_destructive" and q.type == "noul":
            cmd = str(state_dict.get("command") or state_str)
            results[qid] = 0.94 if _DESTRUCTIVE_CMD_RE.search(cmd) else 0.04
            continue

        if qid == "touches_secrets_or_external" and q.type == "noul":
            cmd = str(state_dict.get("command") or state_str)
            results[qid] = 0.92 if _SECRET_OR_NET_CMD_RE.search(cmd) else 0.03
            continue

        if qid == "blast_radius" and q.type == "score":
            cmd = str(state_dict.get("command") or state_str)
            if _DESTRUCTIVE_CMD_RE.search(cmd) or _SECRET_OR_NET_CMD_RE.search(cmd) or "git push" in cmd:
                w = {"read_only": 0.01, "local_test_build": 0.02, "workspace_write": 0.09, "external_or_irreversible": 0.88}
            elif _READ_ONLY_CMD_RE.search(cmd):
                w = {"read_only": 0.90, "local_test_build": 0.06, "workspace_write": 0.03, "external_or_irreversible": 0.01}
            elif _TEST_BUILD_CMD_RE.search(cmd):
                w = {"read_only": 0.08, "local_test_build": 0.84, "workspace_write": 0.06, "external_or_irreversible": 0.02}
            else:
                w = {"read_only": 0.05, "local_test_build": 0.15, "workspace_write": 0.72, "external_or_irreversible": 0.08}
            results[qid] = _make_score(keys or list(w.keys()), w)
            continue

        if qid == "approval_policy" and q.type == "choice":
            cmd = str(state_dict.get("command") or state_str)
            risky = bool(_DESTRUCTIVE_CMD_RE.search(cmd) or _SECRET_OR_NET_CMD_RE.search(cmd) or "git push" in cmd)
            w = {"ask_operator": 0.91, "auto_approve": 0.09} if risky else {"auto_approve": 0.89, "ask_operator": 0.11}
            results[qid] = _make_choice(w)
            continue

        # 2. Session Triage questions
        if qid == "outcome_satisfied" and q.type == "noul":
            summary = str(state_dict.get("output_summary") or state_str).lower()
            status = str(state_dict.get("status") or "").lower()
            if status == "failed" or "error:" in summary and "passes" not in summary:
                results[qid] = 0.18
            elif "passes" in summary or "passed" in summary or state_dict.get("dirty") or state_dict.get("has_git_evidence"):
                results[qid] = 0.88
            else:
                results[qid] = 0.62
            continue

        if qid == "ready_for_host_commit" and q.type == "noul":
            dirty = bool(state_dict.get("dirty"))
            results[qid] = 0.95 if dirty else 0.05
            continue

        if qid == "blocker_type" and q.type == "choice":
            summary = str(state_dict.get("output_summary") or "").lower()
            dirty = bool(state_dict.get("dirty"))
            has_remote = bool(state_dict.get("has_remote", True))
            if (
                "cannot write to `.git`" in summary
                or "cannot write to .git" in summary
                or "commit and push are blocked" in summary
                or (dirty and ("read-only" in summary or "operation not permitted" in summary))
            ):
                w = {
                    "sandbox_git_blocked": 0.86,
                    "missing_git_remote": 0.08 if not has_remote else 0.01,
                    "none": 0.02,
                    "verification_failed": 0.02,
                    "auth_or_provider": 0.01,
                    "needs_operator_input": 0.01,
                }
            elif dirty:
                w = {
                    "sandbox_git_blocked": 0.65,
                    "none": 0.20,
                    "missing_git_remote": 0.07 if not has_remote else 0.02,
                    "verification_failed": 0.03,
                    "auth_or_provider": 0.02,
                    "needs_operator_input": 0.03,
                }
            elif "failed" in summary and "passes" not in summary:
                w = {
                    "verification_failed": 0.78,
                    "none": 0.05,
                    "sandbox_git_blocked": 0.04,
                    "missing_git_remote": 0.03,
                    "auth_or_provider": 0.05,
                    "needs_operator_input": 0.05,
                }
            else:
                w = {
                    "none": 0.85,
                    "sandbox_git_blocked": 0.03,
                    "missing_git_remote": 0.04 if not has_remote else 0.01,
                    "verification_failed": 0.03,
                    "auth_or_provider": 0.02,
                    "needs_operator_input": 0.03,
                }
            results[qid] = _make_choice(w)
            continue

        if qid == "recommended_action" and q.type == "choice":
            dirty = bool(state_dict.get("dirty"))
            has_verif = bool(state_dict.get("has_verification_evidence"))
            has_git = bool(state_dict.get("has_git_evidence"))
            if dirty:
                w = {
                    "host_commit": 0.88,
                    "run_verification": 0.06,
                    "accept_and_complete": 0.03,
                    "continue_with_guidance": 0.02,
                    "switch_provider_and_retry": 0.01,
                }
            elif not has_verif and state_dict.get("verification_command"):
                w = {
                    "run_verification": 0.82,
                    "accept_and_complete": 0.10,
                    "host_commit": 0.03,
                    "continue_with_guidance": 0.04,
                    "switch_provider_and_retry": 0.01,
                }
            elif has_git or has_verif:
                w = {
                    "accept_and_complete": 0.86,
                    "continue_with_guidance": 0.08,
                    "run_verification": 0.03,
                    "host_commit": 0.02,
                    "switch_provider_and_retry": 0.01,
                }
            else:
                w = {
                    "continue_with_guidance": 0.55,
                    "accept_and_complete": 0.25,
                    "run_verification": 0.10,
                    "host_commit": 0.05,
                    "switch_provider_and_retry": 0.05,
                }
            results[qid] = _make_choice(w)
            continue

        # 3. Intent Routing questions
        if qid == "complexity" and q.type == "score":
            msg = str(state_dict.get("message") or state_str).strip()
            words = len(msg.split())
            if re.search(r"\b(architect|trade-?off|why|root\s+cause|security\s+audit|design)\b", msg, re.I):
                w = {"trivial": 0.02, "simple": 0.08, "complex": 0.25, "reasoning": 0.65}
            elif re.search(r"\b(implement|build|refactor|fix|create|add|migrate|session|goal)\b", msg, re.I) or words > 20:
                w = {"trivial": 0.03, "simple": 0.12, "complex": 0.70, "reasoning": 0.15}
            elif words <= 5:
                w = {"trivial": 0.72, "simple": 0.22, "complex": 0.05, "reasoning": 0.01}
            else:
                w = {"trivial": 0.15, "simple": 0.68, "complex": 0.14, "reasoning": 0.03}
            results[qid] = _make_score(keys or ["trivial", "simple", "complex", "reasoning"], w)
            continue

        if qid == "recommended_tier" and q.type == "choice":
            msg = str(state_dict.get("message") or state_str).strip()
            words = len(msg.split())
            if re.search(r"\b(architect|trade-?off|root\s+cause|security\s+audit|design)\b", msg, re.I):
                w = {"reasoning": 0.76, "coding": 0.18, "fast": 0.04, "decision": 0.02}
            elif re.search(r"\b(implement|build|refactor|fix|code|test|patch)\b", msg, re.I) or words > 18:
                w = {"coding": 0.78, "reasoning": 0.12, "fast": 0.08, "decision": 0.02}
            elif words <= 6:
                w = {"fast": 0.74, "decision": 0.18, "coding": 0.06, "reasoning": 0.02}
            else:
                w = {"fast": 0.68, "coding": 0.22, "decision": 0.06, "reasoning": 0.04}
            results[qid] = _make_choice(w)
            continue

        if qid == "worth_saving_to_memory" and q.type == "noul":
            msg = str(state_dict.get("message") or state_str).strip()
            if re.search(r"\b(decided|architecture|convention|always\s+use|never\s+use|bug\s+was\s+caused|requirement)\b", msg, re.I):
                results[qid] = 0.84
            elif len(msg.split()) <= 8:
                results[qid] = 0.08
            else:
                results[qid] = 0.32
            continue

        # Generic fallback for arbitrary user-defined questions
        if q.type == "noul":
            results[qid] = 0.5
        elif q.type == "score":
            default_keys = keys or ["low", "medium", "high"]
            w = {k: (1.0 if i == len(default_keys) // 2 else 0.25) for i, k in enumerate(default_keys)}
            results[qid] = _make_score(default_keys, w)
        else:
            default_keys = keys or ["default"]
            w = {k: (1.0 if i == 0 else 0.2) for i, k in enumerate(default_keys)}
            results[qid] = _make_choice(w)

    return results


# ---------------------------------------------------------------------------
# Jev / Decision API Client & Engine
# ---------------------------------------------------------------------------

class DecisionEngine:
    """Executes structured System 1 decisions (`choice`, `score`, `noul`)."""

    def evaluate(
        self,
        state: Any,
        questions: dict[str, DecisionQuestion | dict[str, Any]],
        *,
        model: str | None = None,
        prefer_local: bool = False,
        allow_network: bool = True,
    ) -> DecisionResponse:
        started = time.monotonic()
        typed_questions: dict[str, DecisionQuestion] = {
            k: (v if isinstance(v, DecisionQuestion) else DecisionQuestion(**v))
            for k, v in questions.items()
        }

        # 1. Check for self-hosted Jev/Laya/OpenJev API URL
        custom_url = os.environ.get("RDC_DECISION_API_URL", "").strip()
        if allow_network and custom_url:
            try:
                res = self._call_jev_endpoint(
                    custom_url,
                    state=state,
                    questions=typed_questions,
                    model=model or "openjev-latest",
                    api_key=os.environ.get("RDC_DECISION_API_KEY", "local"),
                )
                elapsed = int((time.monotonic() - started) * 1000)
                return DecisionResponse(
                    provider="self_hosted_decision_api",
                    model=model or "openjev-latest",
                    latency_ms=elapsed,
                    results=res,
                )
            except Exception as exc:
                logger.debug("Self-hosted Decision API failed, falling back: %s", exc)

        # 2. Check OpenRouter Jev endpoint if allowed and configured
        if allow_network and not prefer_local:
            from .vault import get_secret
            or_key = get_secret("OPENROUTER_API_KEY") or os.environ.get("OPENROUTER_API_KEY")
            use_remote_jev = os.environ.get("RDC_ENABLE_REMOTE_JEV", "").strip() in {"1", "true", "yes"}
            if or_key and use_remote_jev:
                try:
                    target_model = model or "typesafe/jev-1.14"
                    res = self._call_jev_endpoint(
                        "https://openrouter.ai/api/alpha/decisions",
                        state=state,
                        questions=typed_questions,
                        model=target_model,
                        api_key=or_key,
                    )
                    elapsed = int((time.monotonic() - started) * 1000)
                    return DecisionResponse(
                        provider="openrouter_jev",
                        model=target_model,
                        latency_ms=elapsed,
                        results=res,
                    )
                except Exception as exc:
                    logger.debug("OpenRouter Jev API call failed, falling back to calibrated engine: %s", exc)

        # 3. Fast Calibrated Deterministic Engine (sub-millisecond, zero hallucination)
        res = _evaluate_deterministic(state, typed_questions)
        elapsed = int((time.monotonic() - started) * 1000)
        return DecisionResponse(
            provider="rdc_calibrated_system1",
            model=model or "rdc-jev-calibrated-v1",
            latency_ms=elapsed,
            results=res,
        )

    def _call_jev_endpoint(
        self,
        url: str,
        *,
        state: Any,
        questions: dict[str, DecisionQuestion],
        model: str,
        api_key: str,
    ) -> dict[str, Any]:
        payload = {
            "model": model,
            "state": state,
            "questions": {
                qid: {
                    "type": q.type,
                    "instructions": q.instructions,
                    **({"criteria": q.criteria} if q.criteria else {}),
                }
                for qid, q in questions.items()
            },
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        with httpx.Client(timeout=4.0) as client:
            resp = client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            return data.get("results") or data.get("questions") or data


_decision_engine: DecisionEngine | None = None


def get_decision_engine() -> DecisionEngine:
    global _decision_engine
    if _decision_engine is None:
        _decision_engine = DecisionEngine()
    return _decision_engine


def evaluate_command_risk(command: str, project: str | None = None) -> dict[str, Any]:
    """Evaluate a shell/terminal command using the System 1 Decision Engine."""
    resp = get_decision_engine().evaluate(
        state={"command": command, "project": project or ""},
        questions=COMMAND_RISK_QUESTIONS,
    )
    return resp.model_dump()


def evaluate_session_triage(
    *,
    goal: str,
    status: str,
    output_summary: str | None,
    dirty: bool,
    has_remote: bool,
    has_git_evidence: bool,
    has_verification_evidence: bool,
    verification_command: str | None,
) -> dict[str, Any]:
    """Evaluate session completion readiness and blockers using the System 1 Decision Engine."""
    resp = get_decision_engine().evaluate(
        state={
            "goal": goal,
            "status": status,
            "output_summary": output_summary or "",
            "dirty": dirty,
            "has_remote": has_remote,
            "has_git_evidence": has_git_evidence,
            "has_verification_evidence": has_verification_evidence,
            "verification_command": verification_command or "",
        },
        questions=SESSION_TRIAGE_QUESTIONS,
    )
    return resp.model_dump()


def evaluate_intent_routing(message: str, conversation_depth: int = 0) -> dict[str, Any]:
    """Evaluate operator message complexity, model tier, and memory worthiness."""
    resp = get_decision_engine().evaluate(
        state={"message": message, "conversation_depth": conversation_depth},
        questions=INTENT_ROUTING_QUESTIONS,
    )
    return resp.model_dump()
