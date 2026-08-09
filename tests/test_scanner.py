"""Unit pins for the ONE walk (``broadcast._scan_view``) — Phase A closing suite.

Phase A carried an equivalence harness here: a verbatim copy of the
pre-scanner ``_prepare_kwargs`` replayed against every real invocation over an
18-document corpus. It did its job (the rewrite landed equal on values, key
order, and scope tags) and was DELETED with the phase, as the plan requires —
a retained reference would be a third implementation of the rule, the exact
failure class the scanner removes. The parity suite plus these pins are the
permanent net.

These tests drive ``_scan_view`` directly with a ``_RecordingSink``, pinning
each branch of the ladder as an observable decision stream.
"""

from typing import Any, Dict, FrozenSet, List, Optional, Tuple

from confluid import LazyClass, NoBroadcast, configurable
from confluid.broadcast import _KeyScope, _receiver_for_target, _scan_view, _View
from confluid.lazy import Lazy


class _ScOptim:
    """A deferred-slot target (accepts lr/weight_decay)."""

    def __init__(self, lr: float = 0.01, weight_decay: float = 0.0) -> None:
        self.lr, self.weight_decay = lr, weight_decay


@configurable
class _ScChild:
    def __init__(self, power: int = 0, name: str = "", lr: float = 0.0) -> None:
        """A nested receiver.

        Args:
            power: A scalar knob.
            name: Instance name.
            lr: A knob shared with the trainer.
        """
        self.power, self.name, self.lr = power, name, lr


@configurable
class _ScTrainer:
    def __init__(self, lr: float = 0.1, name: str = "", child: Any = None) -> None:
        """The main pin receiver: scalar, marker slot, deferred body slot.

        Args:
            lr: A scalar knob.
            name: Instance name.
            child: A nested marker slot.
        """
        self.lr, self.name, self.child = lr, name, child
        self.optimizer: Lazy[Any] = LazyClass(_ScOptim, weight_decay=0.05)


@configurable
class _ScPinned:
    def __init__(self, secret: NoBroadcast[str] = "s", open_knob: int = 0) -> None:
        """A receiver with a NoBroadcast param.

        Args:
            secret: Excluded from bare/glob cascade.
            open_knob: An ordinary broadcastable knob.
        """
        self.secret, self.open_knob = secret, open_knob


class _RecordingSink:
    """Test-only sink: the decision stream as inspectable tuples, in order."""

    def __init__(self) -> None:
        self.events: List[Tuple[Any, ...]] = []

    def apply(self, key: str, value: Any, origin: str, scope: _KeyScope, own: bool, gated: bool) -> None:
        self.events.append(("apply", key, value, origin, scope, own, gated))

    def dict_at_slot(self, key: str, block: Dict[str, Any], origin: str, bare_before: FrozenSet[str]) -> None:
        self.events.append(("dict_at_slot", key, block, origin, bare_before))

    def route(self, key: str, block: Dict[str, Any]) -> None:
        self.events.append(("route", key, block))

    def unknown(self, key: str, value: Any, *, origin: str) -> None:
        self.events.append(("unknown", key, value, origin))

    def matched(self, name: str) -> None:
        self.events.append(("matched", name))

    def of(self, kind: str) -> List[Tuple[Any, ...]]:
        return [e for e in self.events if e[0] == kind]


def _scan(view: Dict[str, Any], cls: type, own_kwargs: Optional[Dict[str, Any]] = None) -> _RecordingSink:
    receiver = _receiver_for_target(cls.__name__, own_kwargs or {}, cls)
    sink = _RecordingSink()
    _scan_view(view, receiver, sink, own_kwargs=own_kwargs)
    return sink


def test_bare_keys_apply_and_the_noBroadcast_gates_hold() -> None:
    """A bare accepted key applies BARE; a NoBroadcast param never takes one."""
    sink = _scan({"open_knob": 1, "secret": "leak", "stranger": 2}, _ScPinned)
    assert sink.events == [("apply", "open_knob", 1, "bare", _KeyScope.BARE, False, True)]


def test_a_named_block_unrolls_inline_and_reports_the_match() -> None:
    """A class-name block marks matched and applies its scalars EXACT, in order."""
    sink = _scan({"_ScPinned": {"secret": "ok", "open_knob": 3}}, _ScPinned)
    assert sink.events == [
        ("matched", "_ScPinned"),
        ("apply", "secret", "ok", "block '_ScPinned'", _KeyScope.EXACT, False, False),
        ("apply", "open_knob", 3, "block '_ScPinned'", _KeyScope.EXACT, False, False),
    ]


def test_the_cls_inst_attr_form_unrolls_addressed_again() -> None:
    """``Cls: {inst: {attr: v}}`` re-addresses the same node — inline, ungated."""
    sink = _scan(
        {"_ScTrainer": {"main": {"lr": 0.6}}},
        _ScTrainer,
        own_kwargs={"name": "main"},
    )
    applies = sink.of("apply")
    assert ("apply", "lr", 0.6, "block 'main'", _KeyScope.EXACT, False, False) in applies


def test_a_floating_rider_applies_gated_and_keeps_named_dicts_floating() -> None:
    """``'**'`` contents apply like bare keys; a nested named dict is NOT hoisted."""
    sink = _scan({"**": {"lr": 0.3, "child": {"power": 5}}}, _ScTrainer)
    assert ("apply", "lr", 0.3, "glob '**'", _KeyScope.EXACT, False, True) in sink.events
    assert sink.of("route") == []  # floating: matched-or-ignored, never routed


def test_a_star_glob_is_consumed_gated_for_this_node() -> None:
    """``'*'`` addresses THIS node; its contents are gated like bare keys."""
    sink = _scan({"*": {"open_knob": 2, "secret": "leak"}}, _ScPinned)
    assert sink.events == [("apply", "open_knob", 2, "glob '*'", _KeyScope.EXACT, False, True)]


def test_a_dict_at_a_declared_slot_carries_its_bare_before_verdict() -> None:
    """An addressed dict at a declared slot emits dict_at_slot with the bare
    keys positioned EARLIER — the ordering verdict the deferred-slot tune
    needs (later bare keys must still win, per document order)."""
    sink = _scan(
        {"lr": 0.9, "_ScTrainer": {"optimizer": {"lr": 0.5}, "not_a_slot": {"x": 1}}, "tag": "late"},
        _ScTrainer,
    )
    slots = sink.of("dict_at_slot")
    assert len(slots) == 1
    _, key, block, origin, bare_before = slots[0]
    assert key == "optimizer" and block == {"lr": 0.5}
    assert bare_before == {"lr"}  # 'tag' sits LATER than the block — not beaten
    assert ("route", "not_a_slot", {"x": 1}) in sink.events  # undeclared dict → routing


def test_unknown_fires_on_the_addressed_path_only() -> None:
    """A refused key in a NAMED block emits unknown; glob-delivered stays silent."""
    named = _scan({"_ScTrainer": {"typo": 1}}, _ScTrainer)
    assert named.of("unknown") == [("unknown", "typo", 1, "block '_ScTrainer'")]
    globbed = _scan({"**": {"typo": 1}}, _ScTrainer)
    assert globbed.of("unknown") == []


def test_own_kwargs_apply_as_definitions() -> None:
    """Own kwargs emit with ``own=True`` — sinks treat them as definitions."""
    sink = _scan({}, _ScTrainer, own_kwargs={"lr": 0.7})
    assert sink.events == [("apply", "lr", 0.7, "own", _KeyScope.EXACT, True, False)]


def test_addressed_scope_entries_consume_like_matched_content() -> None:
    """An ADDRESSED-tagged view entry (live-path attr recursion) is consumed inline."""
    view = _View()
    view.set("lr", 0.5, _KeyScope.ADDRESSED)
    sink = _scan(view, _ScTrainer)
    assert sink.events == [("apply", "lr", 0.5, "addressed", _KeyScope.EXACT, False, False)]


def test_exact_and_strict_entries_are_ordering_metadata_only() -> None:
    """EXACT (an ancestor's addressed value) and STRICT (sibling routing) never emit."""
    view = _View()
    view.set("lr", 0.5, _KeyScope.EXACT)
    view.set("sibling", {"lr": 1.0}, _KeyScope.STRICT)
    sink = _scan(view, _ScTrainer)
    assert sink.events == []


# --------------------------------------------------------------------------- #
# Phase B primitives — the splice pair's shared vocabulary
# --------------------------------------------------------------------------- #


def test_merge_routing_merges_routing_but_replaces_values() -> None:
    """D1, adjudicated: routing merges into ROUTING only; a value entry is replaced.

    The old marker-path hoist merged unconditionally — an EXACT slot value
    grew routing contents and leaked them to descendants it never addressed.
    """
    from confluid.broadcast import _merge_routing

    out = _View()
    out.set("opt", {"lr": 0.5}, _KeyScope.STRICT)  # existing ROUTING → merge
    _merge_routing(out, "opt", {"momentum": 0.9})
    assert out["opt"] == {"lr": 0.5, "momentum": 0.9}
    assert out.scope_of("opt") is _KeyScope.STRICT

    out2 = _View()
    out2.set("opt", {"lr": 0.5}, _KeyScope.EXACT)  # existing slot VALUE → replace
    _merge_routing(out2, "opt", {"momentum": 0.9})
    assert out2["opt"] == {"momentum": 0.9}
    assert out2.scope_of("opt") is _KeyScope.STRICT


def test_merge_routing_floats_a_rider_and_merges_it() -> None:
    """The ``'**'`` arm: BARE (keeps floating) and merges over the existing rider."""
    from confluid.broadcast import _merge_routing

    out = _View()
    _merge_routing(out, "**", {"lr": 0.1})
    _merge_routing(out, "**", {"wd": 0.2})
    assert out["**"] == {"lr": 0.1, "wd": 0.2}
    assert out.scope_of("**") is _KeyScope.BARE


def test_merge_rider_and_spent_at_boundary() -> None:
    """The two small primitives: rider merge (inner last-write) and the spend rule."""
    from confluid.broadcast import _merge_rider, _spent_at_boundary

    assert _merge_rider(None, {"a": 1}) == {"a": 1}
    assert _merge_rider({"a": 1, "b": 2}, {"b": 3}) == {"a": 1, "b": 3}

    assert _spent_at_boundary("x", {"y": 1}, _KeyScope.STRICT)  # one-level routing
    assert _spent_at_boundary("*", {"y": 1}, _KeyScope.BARE)  # a '*' glob dict
    assert not _spent_at_boundary("**", {"y": 1}, _KeyScope.BARE)  # a rider floats
    assert not _spent_at_boundary("x", 1, _KeyScope.BARE)  # plain values pass
