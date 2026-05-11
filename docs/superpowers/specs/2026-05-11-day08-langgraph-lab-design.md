# Day 08 LangGraph Lab — Design Spec

**Date:** 2026-05-11
**Target grade band:** 90–100 (production-quality + 1 bonus extension)
**Estimated effort:** ~3 hours

## Goal

Complete the Day 08 LangGraph agentic orchestration lab targeting the 90+ grade band by:

1. Fixing the known SQLite checkpointer bug in `persistence.py`.
2. Hardening `classify_node` to handle keyword priority conflicts and word-boundary matching.
3. Adding test coverage for routing edge cases.
4. Delivering the **crash-resume with SQLite** bonus extension as the headline demo.
5. Writing a complete `reports/lab_report.md` with architecture, metrics, failure analysis, and crash-resume evidence.

## Non-goals

- No changes to `graph.py` wiring (already correct, all paths reach `finalize → END`).
- No changes to `state.py` (reducers and `evaluation_result` already in place).
- No changes to `routing.py` (4 routing functions already implement bounded retry and evaluate gate correctly).
- No additional bonus extensions beyond crash-resume (focus over breadth).
- No refactor of keyword lists into a separate module (YAGNI).
- No Postgres support beyond what already exists in `build_checkpointer`.

## Architecture

The graph wiring is already correct per `docs/LAB_GUIDE.md`:

```
START → intake → classify → {simple→answer, tool→tool→evaluate→answer,
                              missing_info→clarify,
                              risky→risky_action→approval→tool→evaluate→answer,
                              error→retry→tool→evaluate→(loop)→dead_letter}
       → finalize → END
```

This spec changes only logic *inside* nodes (`classify_node`) and the persistence factory (`build_checkpointer`).

## Component changes

### `src/langgraph_agent_lab/nodes.py` — `classify_node`

Replace substring-based matching with whole-word matching via tokenization.

**New helper (module-private):**
```python
_PUNCT = "?!.,;:'\""
def _tokenize(query: str) -> set[str]:
    return {w.strip(_PUNCT) for w in query.lower().split()} - {""}
```

**Keyword sets (constants at module top):**
```python
_RISKY_KW = {"refund", "delete", "send", "cancel", "remove", "revoke"}
_TOOL_KW  = {"status", "order", "lookup", "check", "track", "find", "search"}
_ERROR_KW = {"timeout", "fail", "error", "crash", "unavailable"}
_PRONOUN_KW = {"it", "this", "that"}
```

**Classification rule (priority order — fixed):**
1. If `tokens & _RISKY_KW` → `RISKY`, `risk_level=high`.
2. Elif `tokens & _TOOL_KW` → `TOOL`.
3. Elif `len(tokens) < 5` and `tokens & _PRONOUN_KW` → `MISSING_INFO`.
4. Elif `tokens & _ERROR_KW` → `ERROR`.
5. Else → `SIMPLE`.

Other nodes (`intake`, `tool`, `evaluate`, `approval`, `retry`, `answer`, `dead_letter`, `clarify`, `finalize`, `risky_action`) are kept as-is.

### `src/langgraph_agent_lab/persistence.py` — sqlite branch fix

Replace broken `SqliteSaver.from_conn_string(...)` call with a working construction:

```python
if kind == "sqlite":
    try:
        from langgraph.checkpoint.sqlite import SqliteSaver
    except ImportError as exc:
        raise RuntimeError("...pip install langgraph-checkpoint-sqlite...") from exc
    import sqlite3
    conn = sqlite3.connect(database_url or "outputs/checkpoints.db", check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    return SqliteSaver(conn=conn)
```

`check_same_thread=False` is required because LangGraph may invoke checkpointer methods from worker threads. WAL mode lets readers proceed during writes — needed for the crash-resume demo where two processes touch the same file.

### `src/langgraph_agent_lab/cli.py` — new subcommand

Add `demo-crash-resume` with two phases. Phase 1 runs to a deliberate interrupt; phase 2 resumes from the same `thread_id` in a separate process.

```python
@app.command("demo-crash-resume")
def demo_crash_resume(
    phase: Annotated[str, typer.Argument()],  # "phase1" | "phase2"
    db_path: Annotated[Path, typer.Option("--db")] = Path("outputs/checkpoints.db"),
    log_path: Annotated[Path, typer.Option("--log")] = Path("outputs/crash_resume_log.txt"),
) -> None:
    ...
```

**phase1:** build SQLite checkpointer, build graph with `interrupt_after=["retry"]`, invoke for scenario S07, append snapshot info (`next`, `attempt`, `errors`) to log, exit.

**phase2:** open same SQLite file, rebuild graph (no interrupt), call `graph.get_state(config)` and append state values to log, call `graph.invoke(None, config)` to resume, append final state + history length.

Scenario used: S07_dead_letter (`max_attempts=1`, route `error`) — guarantees the path passes through the retry node and ultimately reaches `dead_letter`, giving the demo a satisfying terminal state.

### `configs/lab.yaml`

Add commented example for SQLite mode (default remains `memory` so CI runs without extras):

```yaml
scenarios_path: data/sample/scenarios.jsonl
checkpointer: memory          # set to "sqlite" for crash-resume demo
# database_url: outputs/checkpoints.db   # used when checkpointer is sqlite
report_path: reports/lab_report.md
```

## Tests

Existing tests (`test_routing.py`, `test_state.py`, `test_graph_smoke.py`) remain unchanged and must continue to pass.

### New file: `tests/test_classify.py`

| # | Test | Input → Expected |
|---|---|---|
| 1 | `test_classify_priority_risky_beats_tool` | `"Refund order 123 status"` → `route=risky` |
| 2 | `test_classify_word_boundary_no_substring` | `"Buy an item"` → `route=simple` (not `missing_info`) |
| 3 | `test_classify_extended_risky_cancel` | `"Cancel my subscription"` → `route=risky` |
| 4 | `test_classify_extended_tool_track` | `"Track my package"` → `route=tool` |
| 5 | `test_classify_extended_error_crash` | `"System crashed during checkout"` → `route=error` |
| 6 | `test_classify_missing_info_short_pronoun` | `"Can you fix it?"` → `route=missing_info` |
| 7 | `test_classify_default_simple` | `"How do I reset my password?"` → `route=simple` |

All tests call `classify_node({"query": ...})` directly and assert on the returned dict — no graph needed.

### New file: `tests/test_persistence.py`

| # | Test | Purpose |
|---|---|---|
| 1 | `test_memory_checkpointer_builds` | `build_checkpointer("memory")` returns object with `put` and `get_tuple` attrs. |
| 2 | `test_sqlite_checkpointer_builds` | `build_checkpointer("sqlite", str(tmp_path/"t.db"))` succeeds and returns saver. Uses `pytest.importorskip("langgraph.checkpoint.sqlite")`. |
| 3 | `test_unknown_kind_raises` | `build_checkpointer("bogus")` raises `ValueError`. |

Existing tests (kept unchanged): 4 routing + 2 state + 3 graph-smoke (parametrized) + 2 metrics = **11 tests**. New: 7 classify + 3 persistence = **10 tests**. Total after this work: **21 tests**.

## Data flow

For each scenario in `data/sample/scenarios.jsonl`:

```
load_scenarios → initial_state(scenario)
  → graph.invoke(state, config={"configurable": {"thread_id": scenario.id}})
  → metric_from_state(final_state, expected_route, requires_approval)
  → summarize_metrics(list) → write_metrics(report, "outputs/metrics.json")
```

`make grade-local` then validates the JSON against `MetricsReport` schema and asserts ≥6 scenarios.

## Error handling

- **Retry loop bound:** `route_after_retry` returns `"dead_letter"` when `attempt >= max_attempts`. S07 (`max_attempts=1`) exercises this immediately.
- **Approval rejection:** `route_after_approval` returns `"clarify"` if `approval.approved` is falsy — handled by existing code.
- **Tool failure detection:** `evaluate_node` sets `evaluation_result="needs_retry"` if latest tool result contains `"ERROR"` substring.
- **SQLite import missing:** `build_checkpointer("sqlite")` raises `RuntimeError` with install hint if `langgraph-checkpoint-sqlite` not installed.
- **Unknown route from classify:** `route_after_classify` falls back to `"answer"` for unknown route strings (already implemented).

## Report structure (`reports/lab_report.md`)

Seven sections, ~700–800 words total:

1. **Architecture overview** — Mermaid diagram from `graph.get_graph().draw_mermaid()` + state schema rationale + node boundary explanation (~150 words).
2. **Routing logic** — priority order table + word-boundary explanation (~100 words).
3. **Metrics table** — generated from `outputs/metrics.json`, per-scenario rows + summary stats.
4. **Failure analysis** — 2–3 failure modes (unbounded retry, keyword collision, SQLite API bug) with detection method + prevention (~150 words).
5. **Crash-resume evidence** — paste `outputs/crash_resume_log.txt`, highlight `attempt`/`state_history` length, explain WAL + thread-safety (~200 words).
6. **Improvements** — 3 concrete next steps: LLM-as-judge evaluate, exponential backoff metadata, classifier-rules module (~100 words).
7. **Production hygiene checklist** — `make lint`/`typecheck`/`test` output, `.env.example` coverage (~50 words).

## Success criteria

- `make test` → 16 tests pass.
- `make lint` → clean.
- `make typecheck` → clean.
- `make run-scenarios` → `outputs/metrics.json` with `success_rate == 1.0` across all 7 sample scenarios.
- `make grade-local` → validates without error.
- `agent-lab demo-crash-resume phase1` followed by `agent-lab demo-crash-resume phase2` produces `outputs/crash_resume_log.txt` showing state survived the process boundary.
- `reports/lab_report.md` filled with all 7 sections including pasted crash-resume log.

## Out of scope (explicitly deferred)

- Real LLM integration for `evaluate_node` (LLM-as-judge).
- Streamlit UI for HITL.
- Parallel fan-out with `Send()`.
- Time-travel replay using `get_state_history()`.
- Postgres checkpointer testing.
- Exponential backoff in `retry_or_fallback_node`.

These are listed in the report's "Improvements" section as next steps, but not implemented.
