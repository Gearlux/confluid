"""Pins the exact-scoping of a marker's own kwargs through wrapper classes.

Origin of these tests
=====================

While profiling a YOLO26 training startup in
``waivefront/config/train_yolo26_ultralytics.yaml`` we noticed that
the ``ops`` kwarg set on the outer ``Stream`` was being broadcast not only
to the outer Stream itself but ALSO to every inner Stream nested deep inside
a sibling wrapper class (``JointStream``), even though those inner Streams'
YAML blocks set no ``ops:`` at all. The leak made the supposedly-empty
inner Stream op chains carry the full heavy op list, turning an ~85ms JSON
walk into a 2½-minute eager iteration on the main thread.

That shape — outer-Target with kwarg X → wrapper-Target without kwarg X →
inner-Classes that ALSO take kwarg X — is the abstract case pinned here.

What this module tests (2026-07 exact-scoping semantics)
========================================================

Addressed keys are EXACT: a marker's own kwargs configure that marker
only and never cascade to descendants. The old leak is fixed by design:

1. ``test_own_kwargs_do_not_cascade_through_wrapper``
   The original leak reproduction, inverted: the outer ``ops`` stays on
   the outer node; the inner same-class children keep their defaults.

2. ``test_glob_restores_the_old_cascade_deliberately``
   The declare-once opt-in: ``'**'`` glob kwargs (``outer.**.ops``)
   reproduce the old reach — outer AND every accepting descendant.

3. ``test_inner_overrides_beat_the_glob_cascade``
   With a ``'**'`` cascade active, pinning ``ops: []`` on one inner
   Target shields that instance only — overrides are per-instance, and
   its sibling still receives the glob value (own kwargs unroll at the
   inner slot, later in document order than the outer glob).

4. ``test_override_at_wrapper_shields_inner_classes``
   A kwarg set on a wrapper block (even though the wrapper doesn't
   accept it) shields the wrapper's subtree from an outer ``'**'``
   cascade the wrapper OUT-POSITIONS — a sweep written after the wrapper
   wins instead (document order, last spec wins; user ruling 2026-08-20).
"""

from typing import Any, List, Optional

import pytest

from confluid import Target, configurable, get_registry, load


def _inst(target: str, /, **kwargs: Any) -> Target:
    """Build an Target marker with kwargs assigned post-construction.

    ``target`` is positional-only so test kwargs literally named ``name`` or
    ``target`` can't collide with it."""
    marker = Target(target)
    marker.kwargs.update(kwargs)
    return marker


# ---------------------------------------------------------------------------
# Shared module-level fixtures.
# AST scans + accept-list caches key off the class object, so the test
# classes need stable module-level identity (can't live inside test bodies).
# ---------------------------------------------------------------------------


@configurable
class _Outer:
    """Stand-in for ``recordstream.core.stream.Stream``: accepts an ``ops`` kwarg AND a
    ``source`` which can itself be another configurable. Both the
    top-level container and the leaf nodes in the broadcast tree are
    instances of this class (mirroring the train_set / inner Stream
    structure that triggered the original bug).
    """

    def __init__(self, source: Any = None, ops: Optional[List[str]] = None) -> None:
        self.source = source
        self.ops = list(ops) if ops is not None else []


@configurable
class _Wrapper:
    """Stand-in for ``recordstream.core.stream.JointStream``: holds a list of children
    but does NOT itself take an ``ops`` kwarg.
    """

    def __init__(self, children: Optional[List[Any]] = None) -> None:
        self.children = list(children) if children is not None else []


@pytest.fixture(autouse=True)
def _register_classes() -> None:
    """Re-register the test classes after any prior test clears the registry."""
    registry = get_registry()
    registry.register_class(_Outer, name="_Outer")
    registry.register_class(_Wrapper, name="_Wrapper")


# ---------------------------------------------------------------------------
# 1. Own kwargs are exact: no cascade through a wrapper class.
# ---------------------------------------------------------------------------


def test_own_kwargs_do_not_cascade_through_wrapper() -> None:
    """The original leak, fixed by design: setting ``ops`` on the outer
    ``_Outer`` configures the outer node ONLY. The intermediate ``_Wrapper``
    and the inner same-class children are untouched — an addressed kwarg
    never becomes ambient context for the subtree.
    """
    config = _inst(
        "_Outer",
        ops=["heavy_a", "heavy_b"],
        source=_inst("_Wrapper", children=[_inst("_Outer"), _inst("_Outer")]),
    )
    root = load(config)
    assert isinstance(root, _Outer)
    assert root.ops == ["heavy_a", "heavy_b"]
    assert isinstance(root.source, _Wrapper)
    assert isinstance(root.source.children[0], _Outer)
    assert isinstance(root.source.children[1], _Outer)
    assert root.source.children[0].ops == []
    assert root.source.children[1].ops == []


# ---------------------------------------------------------------------------
# 2. The '**' glob is the declare-once opt-in for the old reach.
# ---------------------------------------------------------------------------


def test_glob_restores_the_old_cascade_deliberately() -> None:
    """``'**': {ops: …}`` on the outer marker (the ``outer.**.ops`` dotted
    form) applies to the outer node itself (zero levels) AND floats to every
    accepting descendant — the pre-2026-07 cascade, now explicit."""
    config = _inst(
        "_Outer",
        source=_inst("_Wrapper", children=[_inst("_Outer"), _inst("_Outer")]),
        **{"**": {"ops": ["heavy_a", "heavy_b"]}},
    )
    root = load(config)
    assert root.ops == ["heavy_a", "heavy_b"]
    assert root.source.children[0].ops == ["heavy_a", "heavy_b"]
    assert root.source.children[1].ops == ["heavy_a", "heavy_b"]
    # Routing metadata never leaks onto instances.
    assert not hasattr(root, "**")


def test_inner_overrides_beat_an_EARLIER_glob_cascade() -> None:
    """Pinning ``ops: []`` on one inner Target shields that instance from a ``'**'`` rider
    written BEFORE it — document order, last spec wins; its sibling, which pins nothing,
    still receives the glob value.

    Until 2026-08-17 the inner marker won whatever the order, because a marker nested in a
    LIST had no slot in the parent view and its own kwargs were appended after every key
    (F10); it now sits at the list's slot, so the contest is decided by position like any
    other. The rider therefore goes FIRST here, and the con case follows.
    """
    config = _inst(
        "_Outer",
        **{"**": {"ops": ["heavy_a", "heavy_b"]}},
        source=_inst(
            "_Wrapper",
            children=[
                _inst("_Outer", ops=[]),
                _inst("_Outer"),  # no override
            ],
        ),
    )
    root = load(config)
    assert root.ops == ["heavy_a", "heavy_b"]
    assert root.source.children[0].ops == []
    assert root.source.children[1].ops == ["heavy_a", "heavy_b"]


def test_a_LATER_glob_cascade_beats_an_inner_own_value() -> None:
    """The con case of the pin above: the rider written AFTER the subtree wins on the inner
    marker too — its own kwarg competes on position, not on being 'own'."""
    config = _inst(
        "_Outer",
        source=_inst("_Wrapper", children=[_inst("_Outer", ops=[]), _inst("_Outer")]),
        **{"**": {"ops": ["heavy_a", "heavy_b"]}},
    )
    root = load(config)
    assert root.source.children[0].ops == ["heavy_a", "heavy_b"]


# ---------------------------------------------------------------------------
# 3. Wrapper-level override shields its subtree from a glob cascade.
# ---------------------------------------------------------------------------


def test_override_at_wrapper_shields_inner_classes() -> None:
    """A kwarg set on a ``@configurable`` wrapper block shields that
    wrapper's same-class descendants from an ancestor-level ``'**'``
    cascade the wrapper OUT-POSITIONS (user ruling 2026-08-20, BC12:
    document order, last spec wins — a sweep written AFTER the wrapper
    beats the shield; that half is pinned in
    ``test_a_shield_only_beats_the_sweeps_it_out_positions``).

    The wrapper class itself does not need ``ops`` in its ``__init__``
    accept-list — declaring ``ops: []`` on the wrapper's block is a
    statement about the WRAPPER'S SUBTREE: the wrapper's own kwarg wins the
    slot in the spliced child view (it is not a typed param of the wrapper,
    so the splice collision rule lets it shadow the earlier broadcast), and
    as an EXACT own kwarg it does not itself cascade — so the inner children
    fall back to their defaults.
    """
    config = _inst(
        "_Outer",
        **{"**": {"ops": ["heavy_a", "heavy_b"]}},  # the rider the wrapper out-positions
        source=_inst(
            "_Wrapper",
            ops=[],  # wrapper-level shield, written AFTER the rider
            children=[_inst("_Outer"), _inst("_Outer")],
        ),
    )
    root = load(config)
    assert root.ops == ["heavy_a", "heavy_b"]
    assert root.source.children[0].ops == []
    assert root.source.children[1].ops == []


def test_a_shield_only_beats_the_sweeps_it_out_positions() -> None:
    """BC12 (BUGS-2026-08-19; user ruling 2026-08-20 — document order, last spec
    wins, no shield exemption): a wrapper kwarg the wrapper does not accept
    shields its subtree from sweeps written ABOVE it; a bare key or rider
    written BELOW it wins, in both spellings alike."""

    @configurable
    class OptBC12:
        def __init__(self, lr: float = 0.0) -> None:
            self.lr = lr

    @configurable
    class WrapperBC12:
        def __init__(self, inner: Any = None) -> None:
            self.inner = inner

    wrapper = "w: !class:WrapperBC12\n  lr: 1.0\n  inner: !class:OptBC12 {lr: 0.5}\n"
    assert load(wrapper + "lr: 9.0\n")["w"].inner.lr == 9.0
    assert load(wrapper + "'**': {lr: 9.0}\n")["w"].inner.lr == 9.0
    # written ABOVE the wrapper, the sweep loses to inner's own later kwarg
    assert load("lr: 9.0\n" + wrapper)["w"].inner.lr == 0.5
    assert load("'**': {lr: 9.0}\n" + wrapper)["w"].inner.lr == 0.5
