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
