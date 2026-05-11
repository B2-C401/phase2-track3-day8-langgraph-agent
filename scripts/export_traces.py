"""Export per-step state snapshots for each lab scenario as JSON for the HTML demo.

Run: python3 scripts/export_traces.py
Output: demo/traces.json

Includes alternative paths for HITL (approved/rejected) and time-travel (forked S05).
"""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langgraph_agent_lab import graph as graph_mod
from langgraph_agent_lab.persistence import build_checkpointer
from langgraph_agent_lab.scenarios import load_scenarios
from langgraph_agent_lab.state import ApprovalDecision, initial_state, make_event


NARRATIONS: dict[str, str] = {
    "start": (
        "The customer's message just arrived. The agent is about to start processing it."
    ),
    "intake": (
        "The agent receives the customer's message. It cleans up the text "
        "(removes extra spaces) and gets it ready for analysis."
    ),
    "classify": (
        "The agent scans the message for trigger words and decides which path "
        "to take. Risky actions (refund, delete, send) get top priority, then "
        "tool requests (status, order, lookup), then vague questions, then "
        "errors. If none match, it's a simple question."
    ),
    "answer": (
        "The agent writes a final response — either grounded in tool results "
        "(if a tool was called) or a safe canned reply for simple questions."
    ),
    "tool": (
        "The agent calls a backend system to fetch data (order status, account "
        "info, etc.). For demo purposes this is a mock — but in production it "
        "would hit a real API. If the route is 'error', this node fails on "
        "purpose for the first few attempts to demonstrate retries."
    ),
    "evaluate": (
        "After the tool runs, the agent checks: did it work? If the result "
        "contains 'ERROR', the agent flags it as needing a retry. Otherwise it "
        "moves on to writing the answer. This is the 'done?' check that makes "
        "the retry loop possible."
    ),
    "clarify": (
        "The agent realized the question is too vague (short, contains "
        "'it'/'this'/'that' with no context). Instead of guessing, it asks a "
        "clarifying question. Hallucination prevention 101."
    ),
    "risky_action": (
        "The customer asked for something destructive (refund, delete account, "
        "send email). The agent prepares a proposed action — but does NOT "
        "execute it yet. It needs human approval first."
    ),
    "approval": (
        "Human-in-the-loop checkpoint. The agent pauses, presents the proposed "
        "action, and waits for a human to approve or reject. In this demo "
        "you can choose Approve or Reject and watch the graph branch."
    ),
    "retry": (
        "The previous step failed. The agent increments the attempt counter "
        "and decides what to do next. If we haven't exceeded max_attempts, try "
        "again. Otherwise, escalate to dead-letter."
    ),
    "dead_letter": (
        "Too many retries — the agent gives up gracefully. It writes a 'we "
        "couldn't help, please contact support' message and logs the failure "
        "for manual review. Better than retrying forever or silently failing."
    ),
    "finalize": (
        "Wrap-up step: emit a final audit event so the metrics system knows "
        "the run is done. Every path ends here, then exits to END."
    ),
}

SCENARIO_HEADERS: dict[str, str] = {
    "S01_simple": "A customer asks a generic question — no tool call needed.",
    "S02_tool": "Customer wants to know their order status — the graph must call a backend tool.",
    "S03_missing": "Customer's message is too vague to act on — graph asks for clarification.",
    "S04_risky": "Refund request — risky action requiring human approval before execution.",
    "S05_error": "Tool call fails transiently — graph retries up to 3 times before succeeding.",
    "S06_delete": "Account deletion request — another risky action gated by approval.",
    "S07_dead_letter": "Repeated failure with max_attempts=1 — graph gives up and escalates.",
}

TRACKED_KEYS = (
    "route",
    "risk_level",
    "attempt",
    "max_attempts",
    "final_answer",
    "pending_question",
    "proposed_action",
    "approval",
    "evaluation_result",
    "messages",
    "tool_results",
    "errors",
    "events",
)


def _project_state(state: dict[str, Any]) -> dict[str, Any]:
    return {k: state.get(k) for k in TRACKED_KEYS}


def _diff_keys(prev: dict[str, Any] | None, curr: dict[str, Any]) -> list[str]:
    if prev is None:
        return [k for k, v in curr.items() if v not in (None, "", [], {})]
    return [k for k in curr if curr[k] != prev.get(k)]


def _latest_node(state: dict[str, Any]) -> str | None:
    events = state.get("events") or []
    if not events:
        return None
    return events[-1].get("node")


def _build_steps(snapshots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    prev: dict[str, Any] | None = None
    for index, snapshot in enumerate(snapshots):
        projected = _project_state(snapshot)
        node = _latest_node(snapshot) or "start"
        steps.append({
            "index": index,
            "node": node,
            "narration": NARRATIONS.get(node, NARRATIONS["start"]),
            "state_after": projected,
            "diff_keys": _diff_keys(prev, projected),
        })
        prev = projected
    return steps


def _final_metrics(last_state: dict[str, Any]) -> dict[str, Any]:
    events = last_state.get("events") or []
    nodes = [e.get("node") for e in events]
    return {
        "route": last_state.get("route"),
        "success": bool(last_state.get("final_answer") or last_state.get("pending_question")),
        "retry_count": sum(1 for n in nodes if n == "retry"),
        "interrupt_count": sum(1 for n in nodes if n == "approval"),
        "nodes_visited": len(events),
    }


def _make_approval_node(approve: bool):  # noqa: ANN202
    """Factory returning a mock approval node that always approves or always rejects."""

    def approval_node(state: dict[str, Any]) -> dict[str, Any]:
        decision = ApprovalDecision(
            approved=approve,
            comment=f"mock {'approve' if approve else 'reject'} for demo",
        )
        return {
            "approval": decision.model_dump(),
            "events": [
                make_event("approval", "completed", f"approved={approve}")
            ],
        }

    return approval_node


def _stream_scenario(scenario, approve: bool = True) -> list[dict[str, Any]]:
    """Run the graph for a scenario. `approve` controls the mocked HITL decision."""
    original = graph_mod.approval_node
    graph_mod.approval_node = _make_approval_node(approve)
    try:
        checkpointer = build_checkpointer("memory")
        g = graph_mod.build_graph(checkpointer=checkpointer)
        init = initial_state(scenario)
        cfg = {"configurable": {"thread_id": f"trace-{scenario.id}-{'app' if approve else 'rej'}"}}
        return list(g.stream(init, config=cfg, stream_mode="values"))
    finally:
        graph_mod.approval_node = original


def _build_time_travel_trace(scenario) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Run S05 normally, then fork at the checkpoint after first retry by injecting attempt=2.

    Returns (forked_snapshots, fork_metadata).
    """
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "tt.db"
        checkpointer = build_checkpointer("sqlite", str(db_path))
        g = graph_mod.build_graph(checkpointer=checkpointer)
        init = initial_state(scenario)
        cfg = {"configurable": {"thread_id": "trace-S05-time-travel"}}

        # First, run the scenario fully so we have a checkpoint history.
        original_snapshots = list(g.stream(init, config=cfg, stream_mode="values"))

        # Find a checkpoint to fork from: the earliest one where attempt == 1.
        # get_state_history yields newest first; iterate to find oldest matching.
        history = list(g.get_state_history(cfg))
        target = None
        for snapshot in reversed(history):  # oldest first
            if snapshot.values.get("attempt") == 1:
                target = snapshot
                break
        if target is None:
            return original_snapshots, {"error": "no fork point found"}

        fork_index = next(
            (i for i, s in enumerate(original_snapshots) if s.get("attempt") == 1),
            None,
        )

        # Update state at the target checkpoint to inject attempt=2 — pretending the
        # graph already retried once more, so the next tool call will succeed.
        fork_cfg = target.config
        g.update_state(fork_cfg, {"attempt": 2})

        # Resume from the updated checkpoint.
        new_state = g.get_state(fork_cfg)
        forked_continuation = list(g.stream(None, config=new_state.config, stream_mode="values"))

        # Combine: original prefix up to and including the fork point + forked continuation
        # (skip the first forked snapshot if it duplicates the fork-point state).
        prefix = original_snapshots[: (fork_index or 0) + 1]
        forked_full = prefix + forked_continuation

        meta = {
            "fork_step_index": fork_index,
            "fork_description": (
                "At this checkpoint we used graph.update_state() to inject attempt=2. "
                "When the tool node runs next it sees attempt>=2 and returns success "
                "instead of the simulated transient ERROR — so the second retry is skipped."
            ),
        }
        return forked_full, meta


def _scenario_payload(scenario) -> dict[str, Any]:
    snapshots = _stream_scenario(scenario, approve=True)
    steps = _build_steps(snapshots)
    payload = {
        "id": scenario.id,
        "query": scenario.query,
        "expected_route": scenario.expected_route.value,
        "header": SCENARIO_HEADERS.get(scenario.id, ""),
        "requires_approval": scenario.requires_approval,
        "max_attempts": scenario.max_attempts,
        "steps": steps,
        "final_metrics": _final_metrics(snapshots[-1] if snapshots else {}),
        "alt_paths": {},
    }

    # HITL alternative for risky scenarios: rejected path
    if scenario.requires_approval:
        rejected_snapshots = _stream_scenario(scenario, approve=False)
        payload["alt_paths"]["rejected"] = {
            "label": "rejected by reviewer",
            "description": (
                "Reviewer clicked REJECT. The graph routes to clarify instead of "
                "executing the risky action — no refund/deletion happens."
            ),
            "steps": _build_steps(rejected_snapshots),
            "final_metrics": _final_metrics(rejected_snapshots[-1] if rejected_snapshots else {}),
        }

    # Time-travel alternative for S05 (the retry-loop scenario)
    if scenario.id == "S05_error":
        forked_snapshots, fork_meta = _build_time_travel_trace(scenario)
        payload["alt_paths"]["time_travel_fork"] = {
            "label": "time-travel fork (inject attempt=2)",
            "description": fork_meta.get("fork_description", "time-travel fork"),
            "fork_step_index": fork_meta.get("fork_step_index"),
            "steps": _build_steps(forked_snapshots),
            "final_metrics": _final_metrics(forked_snapshots[-1] if forked_snapshots else {}),
        }

    return payload


def export() -> dict[str, Any]:
    scenarios = load_scenarios("data/sample/scenarios.jsonl")
    out = [_scenario_payload(sc) for sc in scenarios]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scenarios": out,
    }


def main() -> None:
    payload = export()
    out_path = Path("demo/traces.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    alt_count = sum(len(s.get("alt_paths", {})) for s in payload["scenarios"])
    print(
        f"Wrote {out_path} ({out_path.stat().st_size} bytes, "
        f"{len(payload['scenarios'])} scenarios, {alt_count} alt paths)"
    )


if __name__ == "__main__":
    main()
