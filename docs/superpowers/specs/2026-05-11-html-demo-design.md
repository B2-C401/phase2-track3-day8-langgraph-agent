# HTML Demo Page — Design Spec

**Date:** 2026-05-11
**Audience:** Lab author + grading instructor
**Estimated effort:** ~3 hours

## Goal

Ship a single-file, static HTML demo (`demo/index.html`) that walks viewers through each of the seven lab scenarios step-by-step, with synchronized graph animation, state-panel diffs, and plain-English narration. Designed so a non-tech reader can follow the agent's decisions, and a grader can verify route correctness, retry behaviour, and HITL approval at a glance.

## Non-goals

- No backend, no live graph execution in the browser — uses pre-recorded `traces.json`.
- No build pipeline (no npm, no bundler) — open `index.html` by double-click.
- No third-party JS framework (React/Vue) — vanilla JS + inline CSS.
- No Mermaid live rendering — hand-drawn SVG for per-node animation control.
- No crash-resume timeline tab — `outputs/crash_resume_log.txt` is referenced in the report, not duplicated in this demo.
- No real-time stream of new scenarios — fixed 7 from `data/sample/scenarios.jsonl`.

## File structure

```
demo/
├── index.html          # single page: markup + inline CSS + inline JS (~600 lines)
└── traces.json         # pre-recorded trace data (~50 KB)

scripts/
└── export_traces.py    # one-shot Python script to regenerate traces.json
```

`demo/` is committed to the repo. `traces.json` is regenerable but committed because no-build means no CI step to recreate it.

## Data flow

```
graph.stream(state, stream_mode="values")   ← snapshot per node from LangGraph
        ↓ (in export_traces.py)
event-by-event trace assembly + narration lookup
        ↓
demo/traces.json   (committed)
        ↓ (fetch on page load)
index.html JS state machine
        ↓
DOM updates: SVG node classes, state panel re-render, narration text
```

## Trace data schema (`demo/traces.json`)

```json
{
  "generated_at": "ISO-8601 timestamp",
  "scenarios": [
    {
      "id": "S04_risky",
      "query": "Refund this customer and send confirmation email",
      "expected_route": "risky",
      "header": "Refund request — risky action requiring human approval before execution.",
      "requires_approval": true,
      "max_attempts": 3,
      "steps": [
        {
          "index": 0,
          "node": "intake",
          "narration": "<plain-English text per node, ~2-4 sentences>",
          "state_after": {
            "route": "", "attempt": 0, "approval": null,
            "final_answer": null, "tool_results": [], "errors": [],
            "events": [...], "messages": [...]
          },
          "diff_keys": ["messages", "events"]
        }
      ],
      "final_metrics": {
        "route": "risky", "success": true,
        "retry_count": 0, "interrupt_count": 1
      }
    }
  ]
}
```

`diff_keys` is computed in `export_traces.py` by comparing `state_after` with the previous step's `state_after` — fields whose top-level value changed go into the list. Used by the UI to highlight just-changed values.

## `scripts/export_traces.py` design

Single-file script, ~120 lines:

1. Load `data/sample/scenarios.jsonl` via existing `load_scenarios`.
2. Build the graph with a `MemorySaver` checkpointer (no SQLite needed for trace generation).
3. For each scenario:
   - `initial = initial_state(scenario)`
   - Iterate `graph.stream(initial, config={...}, stream_mode="values")`. Each yielded dict is a state snapshot AFTER a node completes.
   - For each snapshot, derive the node name from the *latest* event in `events` (`events[-1]["node"]`). The first snapshot has no events from the run, so use scenario header / `"start"`.
   - Compute `diff_keys` against the previous snapshot (top-level fields whose value changed).
   - Look up narration from the `NARRATIONS` dict (keyed on node name).
   - Append a step object.
4. Assemble `final_metrics` from the last snapshot (route, retry_count, interrupt_count derived from events).
5. Write everything to `demo/traces.json` with `json.dump(..., indent=2)`.

`NARRATIONS` dict + `SCENARIO_HEADERS` dict live inside `export_traces.py` (so narrations regenerate atomically with the trace).

## `demo/index.html` design

Single HTML file with three sections:

1. `<style>` block (inline CSS, ~150 lines).
2. `<body>` markup (~200 lines).
3. `<script>` block (inline JS, ~250 lines).

### Layout (CSS grid)

```
┌─────────────── HEADER (sticky) ─────────────────────────────────┐
│ [Scenario ▼]  Step 5/8  [<<] [>>] [▶/⏸]                          │
│ Query: "<scenario.query>"                                        │
│ <em>Scenario header sentence</em>                                │
└─────────────────────────────────────────────────────────────────┘
┌──────────── LEFT (graph) ──────────┬──── RIGHT (panels) ───────┐
│  <svg viewBox="0 0 800 600">       │  ┌─ STATE ──────────────┐ │
│    11 <g id="node-NAME"> groups    │  │ key: value  (🆕 if   │ │
│    ~15 <path> edges                │  │  diff)                │ │
│  </svg>                            │  └──────────────────────┘ │
│                                    │  ┌─ NARRATION ──────────┐ │
│                                    │  │ <step.narration>     │ │
│                                    │  └──────────────────────┘ │
└────────────────────────────────────┴───────────────────────────┘
┌─────────────── FOOTER (legend) ─────────────────────────────────┐
│ ● active  ◌ pending  ✓ visited  | colors by route               │
│ ↗ Lab report ↗ GitHub ↗ metrics.json                            │
└─────────────────────────────────────────────────────────────────┘
```

### SVG graph

11 nodes at hand-tuned absolute coordinates (matching the Mermaid layout in `reports/lab_report.md` section 1):

| Node | Approx (x, y) | Notes |
|---|---|---|
| START | (400, 30) | small circle, label "START" |
| intake | (400, 100) | |
| classify | (400, 180) | branching point |
| answer | (150, 280) | terminal |
| tool | (320, 280) | reachable from classify, approval, retry |
| clarify | (490, 280) | |
| risky_action | (610, 280) | |
| evaluate | (320, 380) | |
| approval | (610, 380) | |
| retry | (320, 480) | |
| dead_letter | (490, 480) | |
| finalize | (400, 560) | |
| END | (400, 620) | |

Each node group:
```html
<g id="node-classify" class="node pending" transform="translate(400,180)">
  <rect x="-50" y="-22" width="100" height="44" rx="22" />
  <text>classify</text>
</g>
```

Edges as `<path>` with stable `id` (e.g. `id="edge-classify-tool"`) so JS can highlight by ID.

### Node states (CSS classes)

| Class | Border | Fill | Opacity | Transform |
|---|---|---|---|---|
| `.pending` | 1px solid #ccc | white | 0.5 | scale(1) |
| `.active` | 3px solid #fbbf24 | #fef3c7 | 1.0 | scale(1.15), box-shadow glow |
| `.visited` | 2px solid (route color) | (route color, 10% alpha) | 1.0 | scale(1) |

Transition: `all 300ms ease-in-out` on the `<rect>`.

### Edges

- Default: `stroke="#ccc"` width 1.5.
- After a step traverses an edge: `stroke="<route-color>"` width 3, kept until scenario reset.
- New edge being traversed: add `.edge-active-pulse` class for 300ms (stroke-dasharray + animated dashoffset) to make direction obvious.

### State panel

Renders `step.state_after` as a list of `<dl>` entries:

```html
<dl>
  <dt>route</dt><dd class="just-changed">risky</dd>
  <dt>attempt</dt><dd>0</dd>
  ...
</dl>
```

`.just-changed` applied when key is in `step.diff_keys`. CSS animates background-color from yellow to transparent over 1000ms.

For long array fields (`events`, `messages`, `tool_results`, `errors`), render a collapsed line "events: 7 items ▸" that expands on click.

### JS state machine

```js
let state = { scenarioIdx: 0, stepIdx: 0, playing: false, intervalId: null };
let traces = null;  // fetched once

async function init() {
  traces = await (await fetch('traces.json')).json();
  populateScenarioPicker();
  render();
  wireKeyboardShortcuts();
}

function render() {
  const sc = traces.scenarios[state.scenarioIdx];
  const step = sc.steps[state.stepIdx];
  renderHeader(sc);
  renderGraph(sc, state.stepIdx);   // mark nodes pending/active/visited
  renderStatePanel(step);
  renderNarration(step);
  updateControls();
}

function next() { state.stepIdx = Math.min(state.stepIdx + 1, sc.steps.length - 1); render(); }
function prev() { state.stepIdx = Math.max(state.stepIdx - 1, 0); render(); }
function togglePlay() { ... setInterval(next, 1500) ... }
function selectScenario(i) { state.scenarioIdx = i; state.stepIdx = 0; render(); }
```

### Keyboard shortcuts

- `←` / `→` — prev / next
- `Space` — toggle play/pause
- `1`–`7` — jump to scenario by index

## Narration content

Defined as a Python dict `NARRATIONS` in `scripts/export_traces.py`. Each entry is 2-4 sentences, plain English, written for a reader who knows software basics but not LangGraph. Full content reproduced in `docs/superpowers/specs/2026-05-11-html-demo-design.md` (this file, see section "Narration source" below for the canonical text).

`SCENARIO_HEADERS` dict provides a one-sentence framing per scenario, displayed under the query in the page header.

## Narration source (canonical text)

```python
NARRATIONS = {
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
        "action, and waits for a human to approve or reject. In this demo the "
        "reviewer is mocked (auto-approves), but with LANGGRAPH_INTERRUPT=true "
        "the graph actually pauses for a real human."
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

SCENARIO_HEADERS = {
    "S01_simple": "A customer asks a generic question — no tool call needed.",
    "S02_tool": "Customer wants to know their order status — the graph must call a backend tool.",
    "S03_missing": "Customer's message is too vague to act on — graph asks for clarification.",
    "S04_risky": "Refund request — risky action requiring human approval before execution.",
    "S05_error": "Tool call fails transiently — graph retries up to 3 times before succeeding.",
    "S06_delete": "Account deletion request — another risky action gated by approval.",
    "S07_dead_letter": "Repeated failure with max_attempts=1 — graph gives up and escalates.",
}
```

## Animation specifics

| Element | Trigger | Animation |
|---|---|---|
| Node `<rect>` | class change pending→active→visited | `transition: all 300ms ease-in-out` on transform, box-shadow, border-color, fill |
| Edge `<path>` | traversal | stroke + width change; `.edge-active-pulse` adds dashoffset animation 300ms |
| State panel `<dd>` | key in `diff_keys` | `.just-changed` background yellow → transparent over 1000ms ease-out |
| Auto-play | `▶` clicked | `setInterval(nextStep, 1500)`; auto-pauses at last step |

GPU-accelerated via CSS transforms. No JS animation library.

## Edge cases

- **Retry loop** (S05): node `tool`/`evaluate`/`retry` highlight multiple times. Each re-entry triggers class re-set; CSS animation fires again (browser respects `animation-name` re-application).
- **Empty events list at step 0**: `events[-1]` undefined → fallback to `node="start"` with the scenario header as narration.
- **Long array fields**: collapsed `<details>` summary "events: 7 items ▸" to keep state panel compact.
- **Browser without fetch**: not supported (skip — modern browsers only).

## Success criteria

- `python3 scripts/export_traces.py` produces `demo/traces.json` with 7 scenarios, each with `steps.length >= scenario.nodes_visited`.
- Opening `demo/index.html` by double-click (file:// URL) shows the page with no console errors.
- All 7 scenarios selectable from dropdown; Prev/Next/Auto-play work; keyboard shortcuts work.
- For S05_error, the demo visibly shows the retry loop (`tool` and `evaluate` highlight twice before answer).
- For S04_risky and S06_delete, the `approval` node is visibly highlighted in the path.
- For S07_dead_letter, the `dead_letter` node is highlighted before `finalize`.

## Out of scope (deferred)

- Live keyword input form (re-classify a custom query in browser).
- Real graph execution via WebAssembly Python.
- Mobile-responsive layout (desktop only — grader views on laptop).
- Dark mode toggle (rely on `prefers-color-scheme` defaults if any).
- i18n (Vietnamese narrations) — single language for now.
