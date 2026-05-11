# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

A **graded lab skeleton** for a LangGraph support-ticket agent. `TODO(student)` markers throughout the source are the assignment — do not silently "clean them up" unless the user is asking you to implement that step. The user is the student; assume they are working through `docs/LAB_GUIDE.md` phase-by-phase.

## Commands

```bash
make install         # pip install -e '.[dev]'
make test            # pytest (skips langgraph tests if package missing)
make lint            # ruff check src tests
make typecheck       # mypy src
make run-scenarios   # runs cli.py against configs/lab.yaml → outputs/metrics.json
make grade-local     # validates outputs/metrics.json schema (needs ≥6 scenarios)
```

Single test: `pytest tests/test_routing.py::test_route_after_classify -q`.

Optional extras: `pip install -e '.[sqlite]'` or `'.[postgres]'` to enable those checkpointers.

## Architecture

Pipeline lives under `src/langgraph_agent_lab/` and flows:

```
scenarios.jsonl → scenarios.load_scenarios → cli.run_scenarios
  → state.initial_state(scenario) → graph.build_graph().invoke(state, config={thread_id})
  → metrics.metric_from_state → metrics.summarize_metrics → outputs/metrics.json
```

Module roles:
- `state.py` — `AgentState` TypedDict with `Annotated[list, add]` append-only reducers for `messages`/`tool_results`/`errors`/`events`. `Route` StrEnum and `Scenario` pydantic model live here too.
- `nodes.py` — small functions returning **partial state dicts**, never mutating input. The `evaluate_node` sets `evaluation_result` ∈ {`needs_retry`, `success`} — this is the retry-loop gate.
- `routing.py` — pure functions used as conditional edges. `route_after_evaluate` closes the retry loop; `route_after_retry` enforces the `attempt < max_attempts` bound (without it, error scenarios loop forever).
- `graph.py` — wires nodes/edges. Imports `langgraph` lazily inside `build_graph` so import-only tests run without it.
- `persistence.py` — `build_checkpointer(kind)` for `memory`/`sqlite`/`postgres`/`none`.
- `metrics.py` — `ScenarioMetric.success` requires both `actual_route == expected_route` **and** a `final_answer` or `pending_question`; if `approval_required`, `state["approval"]` must also be present.
- `data/sample/scenarios_hidden.jsonl` — instructor-supplied grading set (15 scenarios, IDs G01–G15). `configs/lab.yaml` currently points at this file; results land in `outputs/metrics1.json` (100% pass). Switch the path back to `scenarios.jsonl` if you only want the original 7.
- `scripts/export_traces.py` — replays each scenario via `graph.stream()` and produces `demo/traces.json` for the static HTML demo. Includes alt-path generation: monkey-patches `approval_node` to record a rejected branch for risky scenarios, and uses `get_state_history()` + `update_state()` + `stream(None, ...)` to record a time-travel fork for S05. Re-run after any change to `nodes.py` or `scenarios.jsonl`.
- `demo/index.html` — single-file static demo (vanilla JS + inline SVG). Pre-records traces; provides HITL Approve/Reject buttons on S04/S06 and a time-travel Fork button on S05. Serve via `python3 -m http.server --directory demo` (file:// works in Safari, not Chrome).

Target graph (from `docs/LAB_GUIDE.md`):

```
intake → classify → {simple→answer, tool→tool→evaluate→answer, missing_info→clarify,
                     risky→risky_action→approval→tool→evaluate→answer,
                     error→retry→tool→evaluate→(loop)→dead_letter} → finalize → END
```

## Critical rules (graded)

1. **Never hard-code scenario IDs.** Routing must be keyword-based — grading uses hidden scenarios with the same routing rules but different queries. The `classify_node` priority order is fixed: **risky → tool → missing_info → error → simple** (a query like "check order status" must route to `tool`, but "refund and check order" must route to `risky`).
2. **Match whole words, not substrings.** "it" must not match "item"; strip punctuation before splitting (see `clean_words` in `classify_node`).
3. **Bound the retry loop.** `route_after_retry` must compare `attempt >= max_attempts` and return `"dead_letter"` — without this the `error` route never terminates.
4. **Every path ends at `finalize → END`.** A missing edge silently leaves a scenario hanging.

## Known starter bug

`persistence.py`'s `sqlite` branch calls `SqliteSaver.from_conn_string(...)` — in `langgraph-checkpoint-sqlite` 3.x that returns a context manager, not a checkpointer. The fix (per README pitfall #4) is `SqliteSaver(conn=sqlite3.connect(database_url, check_same_thread=False))` with WAL mode. Mention this if the user enables sqlite persistence.

## Conventions

- Python 3.11+, ruff line-length 100, ruff rules `E,F,I,B,UP,N,ANN` with `ANN101/ANN102` ignored.
- Nodes return `dict` partial updates. Append-only fields take a **list of new items** which the reducer concatenates — do not return the full prior list.
- `make run-scenarios` passes `config={"configurable": {"thread_id": state["thread_id"]}}` to `graph.invoke` — keep this when extending the CLI, otherwise checkpointing loses its thread key.
