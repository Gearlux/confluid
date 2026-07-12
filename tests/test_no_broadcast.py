"""Tests for the broadcast opt-out: ``NoBroadcast[T]`` + ``@configurable(broadcast=False)``.

The contract: BARE top-level keys never land on opted-out targets, while every
ADDRESSED form keeps working — ``ClassName:`` blocks in YAML materialization
AND ``configure()`` blocks. The accept-list itself is untouched (broadcast-only
overlay), and the marker never leaks into generated JSON schemas.
"""

from types import SimpleNamespace
from typing import Any

import pytest

from confluid import NoBroadcast, configurable, configure, load, no_broadcast_param_names

# No per-file registry clear: the module-level @configurable classes below must
# stay resolvable by name; conftest's autouse snapshot/restore provides isolation.


@configurable
class MarkedParam:
    def __init__(self, name: NoBroadcast[str] = "default", strength: float = 1.0):
        self.name = name
        self.strength = strength


@configurable(broadcast=False)
class OptedOut:
    def __init__(self, size: int = 1, label: str = "x"):
        self.size = size
        self.label = label


def test_no_broadcast_param_names_scan() -> None:
    assert no_broadcast_param_names(MarkedParam) == {"name"}


def test_bare_key_does_not_reach_marked_param_but_block_does() -> None:
    doc = load(
        """
name: stray-top-level
strength: 2.0
obj: !class:MarkedParam()
"""
    )
    obj = doc["obj"]
    assert obj.name == "default"  # bare ``name:`` blocked by NoBroadcast
    assert obj.strength == 2.0  # unmarked param still broadcasts

    addressed = load(
        """
name: stray-top-level
MarkedParam:
  name: addressed
obj: !class:MarkedParam()
"""
    )
    assert addressed["obj"].name == "addressed"  # addressed block always works


def test_class_level_opt_out_blocks_all_bare_keys() -> None:
    doc = load(
        """
size: 99
label: stray
obj: !class:OptedOut()
"""
    )
    obj = doc["obj"]
    assert obj.size == 1 and obj.label == "x"  # nothing bare lands

    addressed = load(
        """
size: 99
OptedOut:
  size: 7
obj: !class:OptedOut()
"""
    )
    assert addressed["obj"].size == 7  # addressed block still works


def test_nested_class_stub_broadcast_honors_marker() -> None:
    @configurable
    class Holder:
        def __init__(self, child: Any = None):
            self.child = child

    doc = load(
        """
name: stray
strength: 3.0
holder: !class:Holder()
  child: !class:MarkedParam
"""
    )
    child = doc["holder"].child
    # The deferred Class stub received broadcasting during the holder's flow.
    from confluid import flow

    built = flow(child) if not isinstance(child, MarkedParam) else child
    assert built.name == "default"  # marker honored in the nested-stub loop
    assert built.strength == 3.0


def test_configure_respects_marker_and_class_flag() -> None:
    m = MarkedParam()
    configure(m, config={"name": "stray", "strength": 5.0})
    assert m.name == "default" and m.strength == 5.0
    configure(m, config={"MarkedParam": {"name": "addressed"}})
    assert m.name == "addressed"  # configure() blocks still set it

    o = OptedOut()
    configure(o, config={"size": 42, "label": "stray"})
    assert o.size == 1 and o.label == "x"
    configure(o, config={"OptedOut": {"size": 42}})
    assert o.size == 42


def test_to_pydantic_strips_marker_but_keeps_field() -> None:
    from confluid import to_pydantic

    model = to_pydantic(MarkedParam)
    assert set(model.model_fields) == {"name", "strength"}
    schema = model.model_json_schema()
    assert "name" in schema["properties"]
    assert "__confluid_no_broadcast__" not in str(schema)


def test_round_trip_of_marked_class() -> None:
    from confluid import dump, flow
    from confluid.fluid import Instance

    marker = Instance("MarkedParam")
    marker.kwargs.update({"name": "kept", "strength": 4.0})
    reloaded = load(dump(flow(marker)), flow=False)
    rebuilt = flow(reloaded)
    assert rebuilt.name == "kept" and rebuilt.strength == 4.0


def test_broadcast_trace_fires(monkeypatch: pytest.MonkeyPatch) -> None:
    """A bare broadcast emits the trace diagnostic (patched logger — loggair
    is not caplog-capturable)."""
    import confluid.engine as engine_module

    traces: list[str] = []
    monkeypatch.setattr(
        engine_module,
        "logger",
        SimpleNamespace(trace=lambda msg: traces.append(msg), warning=lambda msg: None),
    )

    @configurable
    class Plain:
        def __init__(self, alpha: float = 0.0):
            self.alpha = alpha

    doc = load("alpha: 1.5\nobj: !class:Plain()\n")
    assert doc["obj"].alpha == 1.5
    assert any("'alpha'" in msg and "Plain" in msg and "bare" in msg for msg in traces)
