"""Tests for the broadcast opt-out: ``NoBroadcast[T]`` + ``@configurable(broadcast=False)``.

The contract: BARE top-level keys never land on opted-out targets, while every
ADDRESSED form keeps working — ``ClassName:`` blocks in YAML materialization
AND ``configure()`` blocks. The accept-list itself is untouched (broadcast-only
overlay), and the marker never leaks into generated JSON schemas.
"""

from types import SimpleNamespace
from typing import Any

import pytest

from confluid import (
    NoBroadcast,
    accepts_any_key,
    accepts_broadcast,
    accepts_key,
    configurable,
    configure,
    load,
    no_broadcast_param_names,
)

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
    class NoBroadcastHolder:
        def __init__(self, child: Any = None):
            self.child = child

    doc = load(
        """
name: stray
strength: 3.0
holder: !class:NoBroadcastHolder()
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


def test_no_broadcast_alias_has_no_fluid_arm() -> None:
    """Deliberate asymmetry with Lazy/Mandatory: NoBroadcast is a routing gate for
    generically-named SCALAR knobs, so its alias stays ``Annotated[T, marker]`` —
    no ``Union[..., Fluid]`` arm (which would misdescribe a plain scalar)."""
    from typing import get_args

    from confluid import NoBroadcast

    ann = NoBroadcast[str]  # type: ignore[misc]
    assert get_args(ann)[0] is str


def test_no_broadcast_detected_inside_union_carrying_marker() -> None:
    """``Mandatory[NoBroadcast[T]]``-style composition: the NoBroadcast marker sits
    inside Mandatory's Union arm and is still detected (recursive walk)."""
    from confluid import Mandatory, NoBroadcast
    from confluid.no_broadcast import is_no_broadcast_annotation

    assert is_no_broadcast_annotation(Mandatory[NoBroadcast[str]]) is True  # type: ignore[misc]


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
    import confluid.broadcast as engine_module  # broadcast owns the accept-list + merge diagnostics

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


# ---------------------------------------------------------------------------
# Public settability predicates — the ONE answer external config front-ends ask
# ---------------------------------------------------------------------------
# A CLI layer turning `--lr 0.1` into a config change needs the same
# "may this key set this attribute?" answer the engine uses internally. These
# predicates export it so no consumer re-derives an accept-list (which would
# miss **kwargs targets, __init__-body slots, and both broadcast opt-outs).


@configurable
class _PredicateKwargs:
    def __init__(self, **kw: Any):
        self.kw = kw


@configurable
class _PredicateBodySlot:
    def __init__(self, a: int = 1):
        self.a = a
        self.optimizer: Any = None


def test_accepts_key_covers_ctor_params_and_body_slots() -> None:
    assert accepts_key(_PredicateBodySlot, "a")  # ctor param
    assert accepts_key(_PredicateBodySlot, "optimizer")  # __init__-body slot
    assert not accepts_key(_PredicateBodySlot, "typo")


def test_accepts_key_is_true_for_kwargs_constructor() -> None:
    """A **kwargs ctor makes the accept-list unknowable -> accept everything."""
    assert accepts_key(_PredicateKwargs, "whatever")
    assert accepts_broadcast(_PredicateKwargs, "whatever")


def test_accepts_any_key_separates_declaring_from_being_unable_to_refuse() -> None:
    """The distinction the other two predicates cannot express.

    Both return True for EVERY key on a `**kwargs` target, so a caller asking
    "may this land?" gets the same yes whether the class declared the key or
    merely has no way to say no. An external front-end deciding whether a key was
    ADDRESSED here needs the difference — see docs/broadcasting.md.
    """
    assert accepts_key(_PredicateKwargs, "run_name")  # cannot refuse it ...
    assert accepts_any_key(_PredicateKwargs)  # ... precisely because it has no accept-list

    assert not accepts_key(_PredicateBodySlot, "run_name")  # nothing to set
    assert not accepts_any_key(_PredicateBodySlot)  # it has an accept-list


def test_accepts_any_key_normalizes_like_its_siblings() -> None:
    """Same target normalization, and an unresolvable target accepts NOTHING."""
    assert accepts_any_key(_PredicateKwargs())  # a live instance
    assert not accepts_any_key("no.such.ClassAnywhere")
    assert not accepts_any_key(None)


def test_accepts_any_key_matches_what_the_constructor_actually_receives() -> None:
    """Drift pin: the predicate answers the question the engine acts on.

    A bare key reaches a declaring class's CONSTRUCTOR and a `**kwargs` class's
    ATTRIBUTES, which is exactly the split `accepts_any_key` reports.
    """
    doc = load("a: 42\nslot: !class:_PredicateBodySlot()\nkw: !class:_PredicateKwargs()\n")
    assert doc["slot"].a == 42  # declared -> a constructor argument
    assert doc["kw"].kw == {}  # no accept-list -> NOT a constructor argument ...
    assert doc["kw"].a == 42  # ... a post-init attribute instead


def test_accepts_key_normalizes_class_instance_and_dotted_name() -> None:
    assert accepts_key(MarkedParam, "strength")
    assert accepts_key(MarkedParam(), "strength")  # a live instance
    assert accepts_key("MarkedParam", "strength")  # a registered dotted/short name
    assert not accepts_key("no.such.ClassAnywhere", "strength")
    assert not accepts_key(None, "strength")


def test_accepts_broadcast_honors_the_class_level_opt_out() -> None:
    """`broadcast=False` blocks every BARE key while addressed writes stay legal."""
    assert accepts_key(OptedOut, "size")
    assert not accepts_broadcast(OptedOut, "size")


def test_accepts_broadcast_honors_the_param_level_opt_out() -> None:
    assert accepts_key(MarkedParam, "name")
    assert not accepts_broadcast(MarkedParam, "name")  # NoBroadcast[str]
    assert accepts_broadcast(MarkedParam, "strength")  # its sibling is unaffected


def test_accepts_broadcast_matches_what_materialization_actually_does() -> None:
    """Drift pin: the predicate's answer == the engine's observed behaviour."""
    doc = load("size: 99\nstrength: 2.0\nname: broadcast\nopted: !class:OptedOut()\nmarked: !class:MarkedParam()\n")
    assert doc["opted"].size == 1  # accepts_broadcast(OptedOut, "size") is False
    assert doc["marked"].name == "default"  # accepts_broadcast(MarkedParam, "name") is False
    assert doc["marked"].strength == 2.0  # accepts_broadcast(MarkedParam, "strength") is True
