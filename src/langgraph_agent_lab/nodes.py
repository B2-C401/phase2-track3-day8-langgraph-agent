"""Node skeletons for the LangGraph workflow.

Each function should be small, testable, and return a partial state update. Avoid mutating the
input state in place.
"""

from __future__ import annotations

from .state import AgentState, ApprovalDecision, Route, make_event

_PUNCT = "?!.,;:'\""

# Enumerate common inflections so whole-word matching catches conjugations
# without falling into substring traps like "check" matching "checkout".
_RISKY_KW = {
    "refund", "refunds", "refunded", "refunding",
    "delete", "deletes", "deleted", "deleting", "deletion",
    "send", "sends", "sent", "sending",
    "cancel", "cancels", "cancelled", "canceled", "cancelling", "canceling",
    "remove", "removes", "removed", "removing", "removal",
    "revoke", "revokes", "revoked", "revoking",
}
_TOOL_KW = {
    "status", "statuses",
    "order", "orders",
    "lookup", "lookups",
    "check", "checks", "checked", "checking",
    "track", "tracks", "tracked", "tracking",
    "find", "finds", "found", "finding",
    "search", "searches", "searched", "searching",
}
_ERROR_KW = {
    "timeout", "timeouts",
    "fail", "fails", "failed", "failing", "failure", "failures",
    "error", "errors",
    "crash", "crashes", "crashed", "crashing",
    "unavailable", "broken", "broke",
}
_PRONOUN_KW = {"it", "this", "that"}


def _tokenize(query: str) -> set[str]:
    return {w.strip(_PUNCT) for w in query.lower().split()} - {""}


def intake_node(state: AgentState) -> dict:
    """Normalize raw query into state fields.

    TODO(student): add normalization, PII checks, and metadata extraction.
    """
    query = state.get("query", "").strip()
    return {
        "query": query,
        "messages": [f"intake:{query[:40]}"],
        "events": [make_event("intake", "completed", "query normalized")],
    }


def classify_node(state: AgentState) -> dict:
    """Classify the query into a route.

    Priority order is fixed: risky -> tool -> missing_info -> error -> simple.
    Whole-word token matching against enumerated inflections — catches "crashed"
    without matching "check" in "checkout".
    """
    tokens = _tokenize(state.get("query", ""))
    if tokens & _RISKY_KW:
        route, risk_level = Route.RISKY, "high"
    elif tokens & _TOOL_KW:
        route, risk_level = Route.TOOL, "low"
    elif len(tokens) < 5 and tokens & _PRONOUN_KW:
        route, risk_level = Route.MISSING_INFO, "low"
    elif tokens & _ERROR_KW:
        route, risk_level = Route.ERROR, "low"
    else:
        route, risk_level = Route.SIMPLE, "low"
    return {
        "route": route.value,
        "risk_level": risk_level,
        "events": [make_event("classify", "completed", f"route={route.value}")],
    }


def ask_clarification_node(state: AgentState) -> dict:
    """Ask for missing information instead of hallucinating.

    TODO(student): generate a specific clarification question from state.
    """
    question = "Can you provide the order id or the missing context?"
    return {
        "pending_question": question,
        "final_answer": question,
        "events": [make_event("clarify", "completed", "missing information requested")],
    }


def tool_node(state: AgentState) -> dict:
    """Call a mock tool.

    Simulates transient failures for error-route scenarios to demonstrate retry loops.
    Fail threshold is derived from scenario.max_attempts so each scenario controls how
    many transient failures it simulates: fail until attempt == max_attempts - 1, then
    succeed. This keeps S05 (max_attempts=3 → fail twice, succeed on third try) and
    S07 (max_attempts=1 → never reaches tool because retry exhausts immediately) both
    correct without hardcoding the count.
    """
    attempt = int(state.get("attempt", 0))
    max_attempts = int(state.get("max_attempts", 3))
    scenario_id = state.get("scenario_id", "unknown")
    fail_threshold = max(0, max_attempts - 1)
    if state.get("route") == Route.ERROR.value and attempt < fail_threshold:
        result = (
            f"ERROR: transient failure attempt={attempt}/{max_attempts} scenario={scenario_id}"
        )
    else:
        result = f"mock-tool-result for scenario={scenario_id}"
    return {
        "tool_results": [result],
        "events": [make_event("tool", "completed", f"tool executed attempt={attempt}")],
    }


def risky_action_node(state: AgentState) -> dict:
    """Prepare a risky action for approval.

    TODO(student): create a proposed action with evidence and risk justification.
    """
    return {
        "proposed_action": "prepare refund or external action; approval required",
        "events": [make_event("risky_action", "pending_approval", "approval required")],
    }


def approval_node(state: AgentState) -> dict:
    """Human approval step with optional LangGraph interrupt().

    Set LANGGRAPH_INTERRUPT=true to use real interrupt() for HITL demos.
    Default uses mock decision so tests and CI run offline.

    TODO(student): implement reject/edit decisions and timeout escalation.
    """
    import os

    if os.getenv("LANGGRAPH_INTERRUPT", "").lower() == "true":
        from langgraph.types import interrupt

        value = interrupt({
            "proposed_action": state.get("proposed_action"),
            "risk_level": state.get("risk_level"),
        })
        if isinstance(value, dict):
            decision = ApprovalDecision(**value)
        else:
            decision = ApprovalDecision(approved=bool(value))
    else:
        decision = ApprovalDecision(approved=True, comment="mock approval for lab")
    return {
        "approval": decision.model_dump(),
        "events": [make_event("approval", "completed", f"approved={decision.approved}")],
    }


def retry_or_fallback_node(state: AgentState) -> dict:
    """Record a retry attempt or fallback decision.

    TODO(student): implement bounded retry, exponential backoff metadata, and fallback route.
    """
    attempt = int(state.get("attempt", 0)) + 1
    errors = [f"transient failure attempt={attempt}"]
    return {
        "attempt": attempt,
        "errors": errors,
        "events": [make_event("retry", "completed", "retry attempt recorded", attempt=attempt)],
    }


def answer_node(state: AgentState) -> dict:
    """Produce a final response.

    TODO(student): ground the answer in tool_results and approval where relevant.
    """
    if state.get("tool_results"):
        answer = f"I found: {state['tool_results'][-1]}"
    else:
        answer = "This is a safe mock answer. Replace with your agent response."
    return {
        "final_answer": answer,
        "events": [make_event("answer", "completed", "answer generated")],
    }


def evaluate_node(state: AgentState) -> dict:
    """Evaluate tool results — the 'done?' check that enables retry loops.

    TODO(student): replace heuristic with LLM-as-judge or structured validation.
    """
    tool_results = state.get("tool_results", [])
    latest = tool_results[-1] if tool_results else ""
    if "ERROR" in latest:
        return {
            "evaluation_result": "needs_retry",
            "events": [
                make_event("evaluate", "completed", "tool result indicates failure, retry needed")
            ],
        }
    return {
        "evaluation_result": "success",
        "events": [make_event("evaluate", "completed", "tool result satisfactory")],
    }


def dead_letter_node(state: AgentState) -> dict:
    """Log unresolvable failures for manual review.

    Third layer of error strategy: retry -> fallback -> dead letter.
    TODO(student): persist to dead-letter queue, alert on-call, or create support ticket.
    """
    attempt = state.get("attempt", 0)
    return {
        "final_answer": (
            "Request could not be completed after maximum retry attempts. "
            "Logged for manual review."
        ),
        "events": [
            make_event("dead_letter", "completed", f"max retries exceeded, attempt={attempt}")
        ],
    }


def finalize_node(state: AgentState) -> dict:
    """Finalize the run and emit a final audit event."""
    return {"events": [make_event("finalize", "completed", "workflow finished")]}
