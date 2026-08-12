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
    register,
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
    # The deferred Target stub received broadcasting during the holder's flow.
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
    """Deliberate asymmetry with Partial/Mandatory: NoBroadcast is a routing gate for
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
    from confluid.fluid import Target

    marker = Target("MarkedParam")
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


# --- register() carries the same accept-list controls as @configurable ------ #


def test_register_can_opt_a_third_party_class_out_of_broadcasting() -> None:
    """`register(cls, broadcast=False)` must work, because you cannot decorate a class you don't own.

    The accept-list controls existed only on `@configurable`, so a class you own
    could be shielded from cascade keys and a third-party one could not — exactly
    backwards. A library constructor taking `**kwargs` has NO accept-list, so
    confluid errs permissive and every bare key in the document reaches it; its
    author never chose that, having never seen confluid. `register` is the only
    place the person wiring it up can say otherwise.
    """

    class ThirdParty:
        def __init__(self, lr: float = 0.0) -> None:
            self.lr = lr

    register(ThirdParty, name="ThirdPartyPinned", broadcast=False)

    assert accepts_key(ThirdParty, "lr"), "an addressed block must still reach it"
    assert not accepts_broadcast(ThirdParty, "lr"), "a bare key must not cascade in"


def test_register_can_declare_body_slots_for_a_third_party_class() -> None:
    """`register(cls, broadcast_attrs=[...])` — the frozen-deployment escape hatch.

    Body slots are found by AST-scanning `__init__` SOURCE, which is absent in
    compiled / frozen / zip deployments. The declaration is the documented fix and
    was reachable only through the decorator, so a class you don't own had no fix
    at all. Declared names UNION with the scan, so this can never lose one.
    """

    class ThirdPartyBody:
        def __init__(self) -> None:
            self.scanned = 1

    register(ThirdPartyBody, name="ThirdPartyBodyPinned", broadcast_attrs=["declared_only"])

    assert accepts_key(ThirdPartyBody, "declared_only"), "the declaration reached the accept-list"
    assert accepts_key(ThirdPartyBody, "scanned"), "and did NOT replace what the scan found"


def test_no_broadcast_cache_is_per_class_never_inherited() -> None:
    """A subclass overriding ``__init__`` without the marker must not inherit the block.

    The cache was read with ``getattr`` (an MRO walk), so after the parent was
    queried a bare key was silently blocked on every subclass — including one
    whose own constructor never declared ``NoBroadcast``. Own-``__dict__`` read
    now; a subclass that INHERITS the parent's constructor still computes the
    parent's markers, which is the semantically correct answer.
    """
    from confluid import NoBroadcast, no_broadcast_param_names

    class _Base:
        def __init__(self, n: NoBroadcast[int] = 0) -> None: ...

    class _Sub(_Base):
        def __init__(self, n: int = 0) -> None: ...

    class _Inherits(_Base):
        pass

    assert no_broadcast_param_names(_Base) == frozenset({"n"})  # parent primed FIRST
    assert no_broadcast_param_names(_Sub) == frozenset()
    assert no_broadcast_param_names(_Inherits) == frozenset({"n"})


def test_declares_key_ignores_the_kwargs_catchall() -> None:
    """The question between accepts_key and accepts_any_key — named surface only.

    A ``**kwargs`` target cannot REFUSE any key (`accepts_key` says yes to
    everything), but a library forwarding its catchall somewhere strict rejects
    undeclared names far from the config. `declares_key` answers what the
    target NAMES; a consumer sizing torchmetrics re-derived exactly this.
    """
    from confluid import accepts_key, declares_key

    class Forwarding:
        def __init__(self, top_k: int = 1, **kwargs: object) -> None:
            self.top_k = top_k

    assert accepts_key(Forwarding, "banana")  # cannot refuse ...
    assert declares_key(Forwarding, "top_k")  # ... but declares only what it names
    assert not declares_key(Forwarding, "banana")


def test_declares_key_agrees_with_accepts_key_without_a_catchall() -> None:
    from confluid import accepts_key, declares_key

    class Plain:
        def __init__(self, lr: float = 0.1) -> None:
            self.lr = lr

    for key in ("lr", "typo"):
        assert declares_key(Plain, key) == accepts_key(Plain, key)


def test_declares_key_sees_body_slots_and_refuses_unresolvable_targets() -> None:
    from confluid import configurable, declares_key

    @configurable
    class WithSlot:
        def __init__(self, **kw: object) -> None:
            self.optimizer = None

    assert declares_key(WithSlot, "optimizer")  # an __init__-body slot is declared surface
    assert not declares_key("not.importable.Anywhere", "lr")  # unresolvable declares nothing
