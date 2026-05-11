"""CLI for the lab."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
import yaml

from .graph import build_graph
from .metrics import MetricsReport, metric_from_state, summarize_metrics, write_metrics
from .persistence import build_checkpointer
from .report import write_report
from .scenarios import load_scenarios
from .state import initial_state

app = typer.Typer(no_args_is_help=True)


@app.command("run-scenarios")
def run_scenarios(
    config: Annotated[Path, typer.Option("--config")],
    output: Annotated[Path, typer.Option("--output")],
) -> None:
    """Run all grading scenarios and write metrics JSON."""
    cfg = yaml.safe_load(config.read_text(encoding="utf-8"))
    scenarios = load_scenarios(cfg["scenarios_path"])
    checkpointer = build_checkpointer(cfg.get("checkpointer", "memory"), cfg.get("database_url"))
    graph = build_graph(checkpointer=checkpointer)
    metrics = []
    for scenario in scenarios:
        state = initial_state(scenario)
        run_config = {"configurable": {"thread_id": state["thread_id"]}}
        final_state = graph.invoke(state, config=run_config)
        metrics.append(
            metric_from_state(
                final_state, scenario.expected_route.value, scenario.requires_approval
            )
        )
    report = summarize_metrics(metrics)
    write_metrics(report, output)
    if cfg.get("report_path"):
        write_report(report, cfg["report_path"])
    typer.echo(f"Wrote metrics to {output}")


@app.command("validate-metrics")
def validate_metrics(metrics: Annotated[Path, typer.Option("--metrics")]) -> None:
    """Validate metrics JSON schema for grading."""
    payload = json.loads(metrics.read_text(encoding="utf-8"))
    report = MetricsReport.model_validate(payload)
    if report.total_scenarios < 6:
        raise typer.BadParameter("Expected at least 6 scenarios")
    typer.echo(f"Metrics valid. success_rate={report.success_rate:.2%}")


@app.command("demo-crash-resume")
def demo_crash_resume(
    phase: Annotated[str, typer.Argument(help="phase1 or phase2")],
    db: Annotated[Path, typer.Option("--db")] = Path("outputs/checkpoints.db"),
    log: Annotated[Path, typer.Option("--log")] = Path("outputs/crash_resume_log.txt"),
) -> None:
    """Two-phase crash-resume demo using a SQLite checkpointer.

    phase1: invoke the graph for scenario S07 with interrupt_after=['retry'], then exit
            (simulates a crash after a checkpoint is written).
    phase2: open the SAME sqlite file in a fresh process, read state via get_state,
            then resume by invoking with None state -- proves state survived.
    """
    db.parent.mkdir(parents=True, exist_ok=True)
    log.parent.mkdir(parents=True, exist_ok=True)

    checkpointer = build_checkpointer("sqlite", str(db))
    scenarios = {s.id: s for s in load_scenarios("data/sample/scenarios.jsonl")}
    if "S07_dead_letter" not in scenarios:
        raise typer.BadParameter(
            "Scenario S07_dead_letter not found in data/sample/scenarios.jsonl"
        )
    scenario = scenarios["S07_dead_letter"]
    state = initial_state(scenario)
    thread_id = "demo-crash-resume-S07"
    state["thread_id"] = thread_id
    run_config = {"configurable": {"thread_id": thread_id}}

    lines: list[str] = []
    if phase == "phase1":
        graph = build_graph(checkpointer=checkpointer)
        # interrupt_after stops the graph after the named node writes its checkpoint,
        # simulating a crash with state safely on disk.
        result = graph.invoke(state, config=run_config, interrupt_after=["retry"])
        snapshot = graph.get_state(run_config)
        lines.append("=== PHASE 1 (pre-crash) ===")
        lines.append(f"thread_id={thread_id}")
        lines.append(f"attempt={result.get('attempt')}")
        lines.append(f"errors={result.get('errors')}")
        lines.append(f"next_nodes={snapshot.next}")
        lines.append("")
        log.write_text("\n".join(lines), encoding="utf-8")
        typer.echo(f"Phase 1 complete. Wrote {log}. Now run: agent-lab demo-crash-resume phase2")
        return

    if phase == "phase2":
        graph = build_graph(checkpointer=checkpointer)
        snapshot = graph.get_state(run_config)
        lines.append("=== PHASE 2 (post-resume) ===")
        lines.append(f"recovered_attempt={snapshot.values.get('attempt')}")
        lines.append(f"recovered_errors={snapshot.values.get('errors')}")
        lines.append(f"next_nodes_before_resume={snapshot.next}")
        # Resume by passing None -- graph reads from checkpointer.
        final = graph.invoke(None, config=run_config)
        history = list(graph.get_state_history(run_config))
        lines.append(f"final_answer={final.get('final_answer')}")
        lines.append(f"state_history_len={len(history)}")
        lines.append("")
        with log.open("a", encoding="utf-8") as fh:
            fh.write("\n".join(lines))
        typer.echo(f"Phase 2 complete. Appended to {log}.")
        return

    raise typer.BadParameter("phase must be 'phase1' or 'phase2'")


if __name__ == "__main__":
    app()
