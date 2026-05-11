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
