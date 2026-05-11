# Day 08 LangGraph Lab Completion — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the Day 08 LangGraph agentic orchestration lab targeting the 90+ grade band by hardening `classify_node`, fixing the SQLite checkpointer, adding crash-resume demo, expanding test coverage, and writing the final report.

**Architecture:** No changes to the existing graph wiring, state schema, or routing functions — all already correct. Logic changes are isolated to `classify_node` (whole-word keyword matching with priority order risky→tool→missing_info→error→simple) and `build_checkpointer` (proper `SqliteSaver` construction). The crash-resume bonus is added as a new CLI subcommand `demo-crash-resume` with two phases (interrupt, then resume from same `thread_id` in a separate process).

**Tech Stack:** Python 3.11, LangGraph ≥0.3, `langgraph-checkpoint-sqlite`, Pydantic 2, Typer, pytest, ruff, mypy.

**Commit policy:** User has requested a single commit + push at the very end. Do **not** commit between tasks. Run `git status` periodically to confirm tree state, but hold all work until Task 9.

---

## File Structure

| Path | Status | Responsibility |
|---|---|---|
| `src/langgraph_agent_lab/nodes.py` | Modify | Replace `classify_node` body with tokenize-based whole-word matching + expanded keyword sets. Other nodes untouched. |
| `src/langgraph_agent_lab/persistence.py` | Modify | Fix `sqlite` branch of `build_checkpointer` to use `SqliteSaver(conn=...)` with WAL mode. |
| `src/langgraph_agent_lab/cli.py` | Modify | Add `demo-crash-resume` Typer subcommand with `phase1` / `phase2` argument. |
| `configs/lab.yaml` | Modify | Add commented example for sqlite checkpointer mode. |
| `tests/test_classify.py` | Create | 7 unit tests for the hardened classify_node (priority, word-boundary, expanded keywords, defaults). |
| `tests/test_persistence.py` | Create | 3 tests for `build_checkpointer` (memory, sqlite tmp_path, unknown raises). |
| `reports/lab_report.md` | Create | 7-section final report (architecture, routing, metrics, failure analysis, crash-resume evidence, improvements, hygiene). |
| `outputs/metrics.json` | Generated | `make run-scenarios` output. |
| `outputs/crash_resume_log.txt` | Generated | Output of `demo-crash-resume phase1` + `phase2`. |
| `outputs/checkpoints.db` | Generated | SQLite checkpoint store used by crash-resume demo. |
| `outputs/graph_diagram.mmd` | Generated | Mermaid graph diagram for the report. |

---

## Task 1 — Install SQLite extra

**Files:** None (environment setup)

- [ ] **Step 1.1:** Install the sqlite extra so `langgraph-checkpoint-sqlite` is importable.

Run:
```bash
pip install -e '.[dev,sqlite]'
```
Expected: pip resolves `langgraph-checkpoint-sqlite>=2.0` successfully.

- [ ] **Step 1.2:** Verify import works.

Run:
```bash
python -c "from langgraph.checkpoint.sqlite import SqliteSaver; print(SqliteSaver)"
```
Expected: prints `<class 'langgraph.checkpoint.sqlite.SqliteSaver'>` with no traceback.

---

## Task 2 — Harden `classify_node` (TDD)

**Files:**
- Create: `tests/test_classify.py`
- Modify: `src/langgraph_agent_lab/nodes.py` (only `classify_node` and new module-level constants)

- [ ] **Step 2.1: Write failing tests**

Create `tests/test_classify.py`:

```python
from langgraph_agent_lab.nodes import classify_node
from langgraph_agent_lab.state import Route


def _route(query: str) -> str:
    return classify_node({"query": query})["route"]


def test_classify_priority_risky_beats_tool():
    # contains "refund" (risky) AND "order"/"status" (tool) — risky must win
    assert _route("Refund order 123 status") == Route.RISKY.value


def test_classify_word_boundary_no_substring():
    # "item" must NOT match "it" missing-info pronoun
    assert _route("Buy an item") == Route.SIMPLE.value


def test_classify_extended_risky_cancel():
    assert _route("Cancel my subscription") == Route.RISKY.value


def test_classify_extended_tool_track():
    assert _route("Track my package") == Route.TOOL.value


def test_classify_extended_error_crash():
    assert _route("System crashed during checkout") == Route.ERROR.value


def test_classify_missing_info_short_pronoun():
    assert _route("Can you fix it?") == Route.MISSING_INFO.value


def test_classify_default_simple():
    assert _route("How do I reset my password?") == Route.SIMPLE.value
```

- [ ] **Step 2.2: Run tests to confirm initial state**

Run:
```bash
pytest tests/test_classify.py -v
```
Expected: at least 2 tests FAIL (`test_classify_word_boundary_no_substring` because current code does substring on full query, and `test_classify_extended_*` because keyword sets are smaller). Other tests may pass coincidentally.

- [ ] **Step 2.3: Implement the hardened `classify_node`**

In `src/langgraph_agent_lab/nodes.py`, replace the existing `classify_node` (and add the helpers/constants above the function definitions). Keep all other functions identical.

Replace the existing import block top with:

```python
"""Node skeletons for the LangGraph workflow.

Each function should be small, testable, and return a partial state update. Avoid mutating the
input state in place.
"""

from __future__ import annotations

from .state import AgentState, ApprovalDecision, Route, make_event

_PUNCT = "?!.,;:'\""
_RISKY_KW = {"refund", "delete", "send", "cancel", "remove", "revoke"}
_TOOL_KW = {"status", "order", "lookup", "check", "track", "find", "search"}
_ERROR_KW = {"timeout", "fail", "error", "crash", "unavailable"}
_PRONOUN_KW = {"it", "this", "that"}


def _tokenize(query: str) -> set[str]:
    return {w.strip(_PUNCT) for w in query.lower().split()} - {""}
```

Then replace the existing `classify_node` body (lines starting at `def classify_node`) with:

```python
def classify_node(state: AgentState) -> dict:
    """Classify the query into a route.

    Priority order is fixed: risky -> tool -> missing_info -> error -> simple.
    Uses whole-word token matching to avoid substring false positives (e.g. "it" in "item").
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
```

- [ ] **Step 2.4: Run new tests**

Run:
```bash
pytest tests/test_classify.py -v
```
Expected: all 7 tests PASS.

- [ ] **Step 2.5: Run the entire test suite to confirm no regressions**

Run:
```bash
pytest -v
```
Expected: all existing tests still PASS (11 existing + 7 new = 18 so far).

---

## Task 3 — Fix SQLite checkpointer (TDD)

**Files:**
- Create: `tests/test_persistence.py`
- Modify: `src/langgraph_agent_lab/persistence.py` (only the `sqlite` branch)

- [ ] **Step 3.1: Write failing tests**

Create `tests/test_persistence.py`:

```python
import importlib.util
from pathlib import Path

import pytest

from langgraph_agent_lab.persistence import build_checkpointer

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("langgraph") is None,
    reason="langgraph not installed",
)


def test_memory_checkpointer_builds():
    saver = build_checkpointer("memory")
    assert saver is not None
    assert hasattr(saver, "put")
    assert hasattr(saver, "get_tuple")


def test_sqlite_checkpointer_builds(tmp_path: Path):
    pytest.importorskip("langgraph.checkpoint.sqlite")
    db = tmp_path / "t.db"
    saver = build_checkpointer("sqlite", str(db))
    assert saver is not None
    assert hasattr(saver, "put")
    assert hasattr(saver, "get_tuple")


def test_unknown_kind_raises():
    with pytest.raises(ValueError, match="Unknown checkpointer kind"):
        build_checkpointer("bogus")
```

- [ ] **Step 3.2: Run tests to verify sqlite test fails**

Run:
```bash
pytest tests/test_persistence.py -v
```
Expected: `test_memory_checkpointer_builds` and `test_unknown_kind_raises` PASS, `test_sqlite_checkpointer_builds` FAILS (current code calls non-existent classmethod or returns context manager).

- [ ] **Step 3.3: Fix the sqlite branch**

Open `src/langgraph_agent_lab/persistence.py`. Replace the entire sqlite block:

Old:
```python
    if kind == "sqlite":
        try:
            from langgraph.checkpoint.sqlite import SqliteSaver
        except ImportError as exc:
            raise RuntimeError("SQLite checkpointer requires: pip install langgraph-checkpoint-sqlite") from exc
        return SqliteSaver.from_conn_string(database_url or "checkpoints.db")
```

New:
```python
    if kind == "sqlite":
        try:
            from langgraph.checkpoint.sqlite import SqliteSaver
        except ImportError as exc:
            raise RuntimeError(
                "SQLite checkpointer requires: pip install langgraph-checkpoint-sqlite"
            ) from exc
        import sqlite3

        conn = sqlite3.connect(
            database_url or "outputs/checkpoints.db", check_same_thread=False
        )
        conn.execute("PRAGMA journal_mode=WAL")
        return SqliteSaver(conn=conn)
```

- [ ] **Step 3.4: Run tests to verify fix**

Run:
```bash
pytest tests/test_persistence.py -v
```
Expected: all 3 tests PASS.

- [ ] **Step 3.5: Run full test suite**

Run:
```bash
pytest -v
```
Expected: 21 tests PASS (11 existing + 7 classify + 3 persistence).

---

## Task 4 — Add `demo-crash-resume` CLI command

**Files:**
- Modify: `src/langgraph_agent_lab/cli.py`

- [ ] **Step 4.1: Add the subcommand**

Open `src/langgraph_agent_lab/cli.py`. Append a new command after `validate_metrics`:

```python
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
            then resume by invoking with None state — proves state survived.
    """
    db.parent.mkdir(parents=True, exist_ok=True)
    log.parent.mkdir(parents=True, exist_ok=True)

    checkpointer = build_checkpointer("sqlite", str(db))
    scenarios = {s.id: s for s in load_scenarios("data/sample/scenarios.jsonl")}
    if "S07_dead_letter" not in scenarios:
        raise typer.BadParameter("Scenario S07_dead_letter not found in data/sample/scenarios.jsonl")
    scenario = scenarios["S07_dead_letter"]
    state = initial_state(scenario)
    thread_id = "demo-crash-resume-S07"
    state["thread_id"] = thread_id
    run_config = {"configurable": {"thread_id": thread_id}}

    lines: list[str] = []
    if phase == "phase1":
        graph = build_graph(checkpointer=checkpointer)
        # interrupt_after stops the graph after the named node writes its checkpoint
        # then exits — simulating a crash with state safely on disk.
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
        # Resume by passing None — graph reads from checkpointer.
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
```

Note: all required imports (`build_graph`, `build_checkpointer`, `initial_state`, `load_scenarios`, `typer`, `Path`, `Annotated`) are already present at the top of `cli.py`. No import changes needed.

- [ ] **Step 4.2: Smoke-test the CLI registers the command**

Run:
```bash
python -m langgraph_agent_lab.cli demo-crash-resume --help
```
Expected: help text shows `phase` argument, `--db`, `--log` options. No traceback.

---

## Task 5 — Update `configs/lab.yaml`

**Files:**
- Modify: `configs/lab.yaml`

- [ ] **Step 5.1: Add commented SQLite example**

Replace the file contents with:

```yaml
scenarios_path: data/sample/scenarios.jsonl
checkpointer: memory          # set to "sqlite" for crash-resume demo
# database_url: outputs/checkpoints.db   # used when checkpointer is sqlite
report_path: reports/lab_report.md
```

Default remains `memory` so `make run-scenarios` in CI does not require the sqlite extra.

---

## Task 6 — Run scenarios and validate metrics

**Files:**
- Generated: `outputs/metrics.json`

- [ ] **Step 6.1: Generate metrics**

Run:
```bash
make run-scenarios
```
Expected: prints `Wrote metrics to outputs/metrics.json` with no traceback.

- [ ] **Step 6.2: Inspect metrics for full coverage**

Run:
```bash
python -c "import json; d=json.load(open('outputs/metrics.json')); print('success_rate=', d['success_rate']); print('total=', d['total_scenarios']); print([{'id': m['scenario_id'], 'expected': m['expected_route'], 'actual': m['actual_route'], 'success': m['success']} for m in d['scenario_metrics']])"
```
Expected: `success_rate= 1.0`, `total= 7`, all 7 scenarios with `success: True` and `expected == actual`.

If any scenario fails, stop and inspect — usually means a classify keyword is missing for that scenario's query.

- [ ] **Step 6.3: Validate metrics schema**

Run:
```bash
make grade-local
```
Expected: prints `Metrics valid. success_rate=100.00%` with exit code 0.

---

## Task 7 — Run crash-resume demo

**Files:**
- Generated: `outputs/checkpoints.db`, `outputs/crash_resume_log.txt`

- [ ] **Step 7.1: Clean any stale checkpoint**

Run:
```bash
rm -f outputs/checkpoints.db outputs/crash_resume_log.txt
```
Expected: no output.

- [ ] **Step 7.2: Run phase1**

Run:
```bash
python -m langgraph_agent_lab.cli demo-crash-resume phase1
```
Expected: prints `Phase 1 complete. Wrote outputs/crash_resume_log.txt. Now run: agent-lab demo-crash-resume phase2`. Creates `outputs/checkpoints.db`.

- [ ] **Step 7.3: Confirm phase1 wrote a checkpoint**

Run:
```bash
python -c "import sqlite3; c=sqlite3.connect('outputs/checkpoints.db'); print(c.execute('SELECT COUNT(*) FROM checkpoints').fetchone())"
```
Expected: prints a tuple with count ≥ 1 (e.g. `(3,)` or higher).

- [ ] **Step 7.4: Run phase2 in a separate Python process**

Run:
```bash
python -m langgraph_agent_lab.cli demo-crash-resume phase2
```
Expected: prints `Phase 2 complete. Appended to outputs/crash_resume_log.txt.`

- [ ] **Step 7.5: Inspect the log**

Run:
```bash
cat outputs/crash_resume_log.txt
```
Expected: contains both `=== PHASE 1 ===` block (with `attempt=1`) and `=== PHASE 2 ===` block (with `recovered_attempt=1` and `final_answer=Request could not be completed after maximum retry attempts. Logged for manual review.` and `state_history_len` ≥ 2).

If `recovered_attempt` is missing or `None`, the checkpointer is not persisting — re-check Task 3 fix.

---

## Task 8 — Write the lab report

**Files:**
- Create: `reports/lab_report.md`
- Generated: `outputs/graph_diagram.mmd`

- [ ] **Step 8.1: Export the Mermaid diagram**

Run:
```bash
python -c "from langgraph_agent_lab.graph import build_graph; print(build_graph().get_graph().draw_mermaid())" > outputs/graph_diagram.mmd
```
Expected: file written. Verify:
```bash
head -5 outputs/graph_diagram.mmd
```
Expected: starts with `---` or `graph TD` (LangGraph emits Mermaid).

- [ ] **Step 8.2: Read template + metrics + log into your editing context**

Run:
```bash
ls reports/ && cat outputs/metrics.json | head -50 && cat outputs/crash_resume_log.txt
```
This gives you the raw numbers and log snippets needed for sections 3 and 5.

- [ ] **Step 8.3: Create `reports/lab_report.md`**

Write the file with these 7 sections (use real numbers/log content from Step 8.2; do not invent values):

```markdown
# Day 08 LangGraph Lab Report

## 1. Architecture overview

```mermaid
<paste contents of outputs/graph_diagram.mmd here>
```

The state schema uses `Annotated[list, add]` reducers for `messages`, `tool_results`,
`errors`, and `events` — these are append-only audit trails so reducers concatenate
partial updates from each node. Scalar fields (`route`, `attempt`, `evaluation_result`,
`final_answer`) overwrite, reflecting "current state" rather than history.

Each node is a small function returning a partial dict; the StateGraph runtime merges
it via the declared reducers. This means no node mutates input state, keeping the
graph deterministic and replayable from any checkpoint.

## 2. Routing logic

Priority order (fixed): **risky → tool → missing_info → error → simple**.

| Route | Keywords |
|---|---|
| risky | refund, delete, send, cancel, remove, revoke |
| tool  | status, order, lookup, check, track, find, search |
| missing_info | short query (<5 tokens) containing it/this/that |
| error | timeout, fail, error, crash, unavailable |
| simple | default |

Matching is whole-word via tokenization: the query is lowercased, split on whitespace,
punctuation stripped, then intersected with each keyword set. This prevents
substring false positives like "it" matching "item" or "fail" matching "failsafe".
Priority order matters because real queries mix keywords from multiple buckets —
e.g. "Refund and check order" contains both `refund` (risky) and `check`/`order` (tool);
checking risky first guarantees the safer route.

## 3. Metrics

<insert per-scenario table from outputs/metrics.json — example row format:>

| Scenario ID | Expected | Actual | Nodes | Retries | Approval | Success |
|---|---|---|---:|---:|---|---|
| S01_simple | simple | simple | … | 0 | n/a | ✓ |
| … | … | … | … | … | … | … |

Summary:
- success_rate: <value>
- total_scenarios: 7
- total_retries: <value>
- total_interrupts: <value>
- avg_nodes_visited: <value>

## 4. Failure analysis

**Unbounded retry loop.** Without the `attempt >= max_attempts` guard in
`route_after_retry`, error-route scenarios loop forever between `retry → tool → evaluate
→ retry`. Detected by `tests/test_routing.py::test_route_after_retry_bound`. Prevention:
the routing function is a pure function tested in isolation, decoupled from node logic.

**Keyword collision.** Queries containing both risky and tool keywords (e.g. S04
"Refund this customer and send confirmation email" contains `send` AND nothing tool-y,
but a query like "Refund and check order 123" mixes both) must route to risky.
Detected by `tests/test_classify.py::test_classify_priority_risky_beats_tool`.
Prevention: fixed priority order enforced as `if/elif` chain.

**SQLite checkpointer API drift.** `langgraph-checkpoint-sqlite` 3.x removed the
`SqliteSaver.from_conn_string` classmethod (it now returns a context manager).
The starter code called it directly and returned an unusable wrapper. Fix: construct
the SQLite connection explicitly with `check_same_thread=False` + WAL mode and
pass via `SqliteSaver(conn=conn)`. Lesson: pin checkpointer minor versions or
test the actual `put`/`get_tuple` round-trip in CI.

## 5. Crash-resume evidence

The `demo-crash-resume` CLI subcommand runs two separate Python processes against
the same SQLite checkpoint file. Phase 1 invokes the graph for scenario S07
(`max_attempts=1`, error route) with `interrupt_after=["retry"]` and exits.
Phase 2 opens the same file in a fresh process and resumes by calling
`graph.invoke(None, config={thread_id})`.

Log output:

```
<paste contents of outputs/crash_resume_log.txt>
```

Key evidence:
- `recovered_attempt=1` in phase 2 matches `attempt=1` in phase 1 — state crossed
  the process boundary intact.
- `state_history_len ≥ 2` proves multiple checkpoints persisted.
- `final_answer` after resume is the dead_letter message, confirming the graph
  continued from the right node rather than restarting.

The fix in `persistence.py` requires `check_same_thread=False` because LangGraph
may invoke checkpointer methods from worker threads, and `PRAGMA journal_mode=WAL`
so a reader in phase 2 can open the file even if phase 1 left a stray lock.

## 6. Improvements

- **LLM-as-judge for `evaluate_node`** — replace the `"ERROR" in result` substring
  heuristic with a structured LLM call that classifies tool output success/failure
  with reasoning, enabling smarter retry decisions.
- **Exponential backoff metadata** — `retry_or_fallback_node` should record
  `backoff_ms = 100 * 2**attempt` in events, and the tool node should respect it
  for real network calls.
- **Classifier rules module** — once keyword lists grow past 10 items per route or
  routing logic needs per-route thresholds, extract to `classifier_rules.py`.
  Held off here (YAGNI).

## 7. Production hygiene

Pre-submission checklist:
- `make lint` — clean (ruff `E,F,I,B,UP,N,ANN`).
- `make typecheck` — clean (mypy on `src/`).
- `make test` — 21 tests pass.
- `make run-scenarios && make grade-local` — `success_rate == 100%`, schema valid.
- `.env.example` documents `DATABASE_URL`, `CHECKPOINTER`, `LOG_LEVEL`; secrets
  excluded from repo.
- `configs/lab.yaml` defaults to `memory` so CI runs without optional deps.
```

- [ ] **Step 8.4: Verify the report file**

Run:
```bash
wc -l reports/lab_report.md && grep -c "^## " reports/lab_report.md
```
Expected: line count ≥ 80; section count == 7.

---

## Task 9 — Final verification and single commit + push

**Files:** All

- [ ] **Step 9.1: Run lint, type check, and tests**

Run each in sequence (stop on first failure and fix):
```bash
make lint
```
Expected: no output / `All checks passed!`

```bash
make typecheck
```
Expected: `Success: no issues found in N source files`.

```bash
make test
```
Expected: `21 passed`.

If any fails, fix and re-run the failing command before continuing.

- [ ] **Step 9.2: Verify all artifacts present**

Run:
```bash
ls -la outputs/ reports/ docs/superpowers/specs/ docs/superpowers/plans/
```
Expected: `outputs/metrics.json`, `outputs/crash_resume_log.txt`, `outputs/checkpoints.db`, `outputs/graph_diagram.mmd`, `reports/lab_report.md`, plus the spec and plan files all present.

- [ ] **Step 9.3: Inspect git status before committing**

Run:
```bash
git status
git diff --stat
```
Confirm the changed files match the file structure table above. No surprises like accidental edits to `state.py`, `routing.py`, or `graph.py`.

- [ ] **Step 9.4: Stage only intended files (no caches)**

Run:
```bash
git add CLAUDE.md \
        docs/superpowers/specs/2026-05-11-day08-langgraph-lab-design.md \
        docs/superpowers/plans/2026-05-11-day08-langgraph-lab.md \
        src/langgraph_agent_lab/nodes.py \
        src/langgraph_agent_lab/persistence.py \
        src/langgraph_agent_lab/cli.py \
        configs/lab.yaml \
        tests/test_classify.py \
        tests/test_persistence.py \
        reports/lab_report.md \
        outputs/metrics.json \
        outputs/crash_resume_log.txt \
        outputs/graph_diagram.mmd
git status
```
Expected: staged list matches above, nothing else (no `__pycache__`, no `.mypy_cache`, no `outputs/checkpoints.db` which is binary state).

If `outputs/checkpoints.db` shows up untracked but you want to exclude it (it's a regenerable binary), add `outputs/*.db` to `.gitignore` first.

- [ ] **Step 9.5: Confirm with the user before committing**

The user has asked for a single commit + push at the end. Show the user the staged file list and ask for go-ahead before running `git commit` and `git push`. **Do not commit until the user confirms.**

- [ ] **Step 9.6: Create the commit (after user approval)**

Run:
```bash
git commit -m "$(cat <<'EOF'
Complete Day 08 LangGraph lab — hardened classify, SQLite fix, crash-resume bonus

- classify_node: whole-word matching via tokenization, fixed priority order
  risky->tool->missing_info->error->simple, expanded keyword sets
- persistence.py: fix SqliteSaver construction (3.x API), enable WAL mode
  and check_same_thread=False for cross-thread checkpointer access
- cli.py: add `demo-crash-resume` subcommand for two-phase HITL-style
  crash-recovery demo using SQLite checkpoints
- tests: +7 classify edge cases, +3 persistence factory tests (21 total)
- report: metrics table, failure analysis, crash-resume evidence, Mermaid diagram
- docs: spec and plan under docs/superpowers/
EOF
)"
```
Expected: commit hash printed, no hook failures. **Do not** include a `Co-Authored-By` trailer.

- [ ] **Step 9.7: Push**

Run:
```bash
git push origin main
```
Expected: branch updated on remote. Stop if remote rejects (e.g. requires PR) and report back.

- [ ] **Step 9.8: Final confirmation**

Run:
```bash
git log -1 --stat
```
Expected: single commit listing all the modified files; remote and local now agree.

---

## Success Criteria (final check)

- [ ] `make lint`, `make typecheck`, `make test` all pass.
- [ ] `outputs/metrics.json` has `success_rate == 1.0` across 7 scenarios.
- [ ] `make grade-local` validates the metrics schema.
- [ ] `outputs/crash_resume_log.txt` contains both phase blocks with matching `attempt` values across processes.
- [ ] `reports/lab_report.md` has all 7 sections filled with real data (no placeholders).
- [ ] Single commit on `main` containing all intended files; pushed to remote.
- [ ] No accidental edits to `state.py`, `routing.py`, or `graph.py`.

---

## Notes / Pitfalls

- **Do not commit between tasks.** User policy: one commit + push at the very end.
- **Do not include `Co-Authored-By: Claude` in commit messages.** User policy.
- **Word-boundary matching is the most common regression source.** If a future query change breaks a test, the suspect is almost always priority order or a missing keyword in the right set — not the tokenizer.
- **The `outputs/checkpoints.db` file is regenerable.** Treat as build artifact; consider adding to `.gitignore` if it appears in `git status`.
- **`graph.py` already wires every path to `finalize → END`.** Resist the urge to "refactor" — it's correct and any change risks failing a hidden test scenario.
