"""Pins broadcast-override propagation through *non-target* wrapper classes.

Origin of these tests
=====================

While profiling a YOLO26 training startup in
``waivefront-rfuav/config/train_yolo26_ultralytics.yaml`` we noticed that
the ``ops`` kwarg set on the outer ``Flux`` was being broadcast not only
to the outer Flux itself but ALSO to every inner Flux nested deep inside
a sibling wrapper class (``JointFlux``), even though those inner Fluxes'
YAML blocks set no ``ops:`` at all. The leak made the supposedly-empty
inner Flux op chains carry the full ``[LoadIQForWindowOp,
SpectrogramOp, ...]`` list, which then ran on every sample
emitted by the inner sources — turning what should have been an ~85ms
JSON walk into a 2½-minute eager iteration of the entire heavy op chain
on the main thread.

That shape — outer-Class with kwarg X → wrapper-Class without kwarg X →
inner-Classes that ALSO take kwarg X — is the abstract case pinned here.

What this module tests
======================

Three positive baselines (today's behaviour, must not regress):

1. ``test_broadcast_reaches_same_class_descendants_through_wrapper``
   Establishes that the broadcast leak actually happens through a
   wrapper class — sanity check + reproduction of the observed bug.

2. ``test_override_at_inner_stops_broadcast_for_that_inner``
   Confirms the workaround: pinning ``ops: []`` on each inner Class
   blocks the broadcast for that specific inner. This is what the
   waivefront-rfuav YAML now does.

3. ``test_inner_overrides_are_independent``
   Pinning the override on only ONE of several inner siblings must
   leave the others still receiving the broadcast — overrides are
   per-instance, not per-list.

One xfail (desired but currently unsupported behaviour):

4. ``test_override_at_wrapper_should_shield_inner_classes``
   The user's intuition: putting ``ops: []`` on the wrapper class
   (even though that wrapper doesn't itself accept ``ops``) should
   shield ALL of its inner descendants from the outer-level broadcast.
   This currently does NOT work — Confluid's broadcaster ignores the
   wrapper-level override because the wrapper class's accept-list
   doesn't include ``ops``, so the broadcast skips through it and
   reaches the inner Classes directly.

The xfail keeps the test green today but flips to a regular pass once
Confluid honours wrapper-level overrides — making this file the natural
home of a future "wrapper kwarg shadows the broadcast for descendants"
feature.
"""

from typing import Any, List, Optional

import pytest

from confluid import configurable, get_registry, materialize

# ---------------------------------------------------------------------------
# Shared module-level fixtures.
# AST scans + accept-list caches key off the class object, so the test
# classes need stable module-level identity (can't live inside test bodies).
# ---------------------------------------------------------------------------


@configurable
class _Outer:
    """Stand-in for ``dataflux.core.Flux``: accepts an ``ops`` kwarg AND a
    ``source`` which can itself be another configurable. Both the
    top-level container and the leaf nodes in the broadcast tree are
    instances of this class (mirroring the train_set / inner Flux
    structure that triggered the original bug).
    """

    def __init__(self, source: Any = None, ops: Optional[List[str]] = None) -> None:
        self.source = source
        self.ops = list(ops) if ops is not None else []


@configurable
class _Wrapper:
    """Stand-in for ``dataflux.core.JointFlux``: holds a list of children
    but does NOT itself take an ``ops`` kwarg. Its accept-list therefore
    excludes ``ops`` — and that exclusion is what causes the broadcaster
    to skip the wrapper and propagate the outer-level ``ops`` straight
    into the wrapper's children.
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
# 1. Baseline: broadcast leaks through a wrapper class to same-class descendants.
# ---------------------------------------------------------------------------


def test_broadcast_reaches_same_class_descendants_through_wrapper() -> None:
    """Repro of the leak observed in ``train_yolo26_ultralytics.yaml``:

    Setting ``ops`` on the outer ``_Outer`` propagates through the
    intermediate ``_Wrapper`` (which has no ``ops`` kwarg) and lands on
    the inner ``_Outer`` children — even though neither child sets
    ``ops:`` in its own block.

    If this test ever STOPS asserting the leak, the regression we hunted
    for two days has finally been fixed and the xfail below can be
    flipped to a passing test. Until then, this exists to nail the
    current behaviour so changes downstream don't quietly drift.
    """
    config = {
        "_confluid_class_": "_Outer",
        "ops": ["heavy_a", "heavy_b"],
        "source": {
            "_confluid_class_": "_Wrapper",
            "children": [
                {"_confluid_class_": "_Outer"},
                {"_confluid_class_": "_Outer"},
            ],
        },
    }
    root = materialize(config)
    assert isinstance(root, _Outer)
    assert root.ops == ["heavy_a", "heavy_b"]
    assert isinstance(root.source, _Wrapper)
    # The leak: inner _Outer children inherit the outer's ops list, even
    # though their YAML blocks don't set `ops:` themselves.
    assert isinstance(root.source.children[0], _Outer)
    assert isinstance(root.source.children[1], _Outer)
    assert root.source.children[0].ops == ["heavy_a", "heavy_b"]
    assert root.source.children[1].ops == ["heavy_a", "heavy_b"]


# ---------------------------------------------------------------------------
# 2. Override at the inner level: workaround used in waivefront-rfuav YAML.
# ---------------------------------------------------------------------------


def test_override_at_inner_stops_broadcast_for_that_inner() -> None:
    """Setting ``ops: []`` directly on each inner ``_Outer`` blocks the
    broadcast from reaching them — the explicit value wins over the
    inherited broadcast. This is the workaround the waivefront-rfuav
    YAML uses today.
    """
    config = {
        "_confluid_class_": "_Outer",
        "ops": ["heavy_a", "heavy_b"],
        "source": {
            "_confluid_class_": "_Wrapper",
            "children": [
                {"_confluid_class_": "_Outer", "ops": []},
                {"_confluid_class_": "_Outer", "ops": []},
            ],
        },
    }
    root = materialize(config)
    assert root.ops == ["heavy_a", "heavy_b"]
    assert root.source.children[0].ops == []
    assert root.source.children[1].ops == []


def test_inner_overrides_are_independent() -> None:
    """Pinning the override on one child must NOT affect its siblings."""
    config = {
        "_confluid_class_": "_Outer",
        "ops": ["heavy_a", "heavy_b"],
        "source": {
            "_confluid_class_": "_Wrapper",
            "children": [
                {"_confluid_class_": "_Outer", "ops": []},
                {"_confluid_class_": "_Outer"},  # no override
            ],
        },
    }
    root = materialize(config)
    assert root.source.children[0].ops == []
    assert root.source.children[1].ops == ["heavy_a", "heavy_b"]


# ---------------------------------------------------------------------------
# 3. xfail: wrapper-level override SHOULD shield its children.
# ---------------------------------------------------------------------------


def test_override_at_wrapper_should_shield_inner_classes() -> None:
    """A kwarg set on a ``@configurable`` wrapper block MUST shield that
    wrapper's same-class descendants from an ancestor-level broadcast.

    The wrapper class itself does not need ``ops`` in its ``__init__``
    accept-list — declaring ``ops: []`` (or any value) on the wrapper's
    YAML block is a statement about what the WRAPPER'S SUBTREE looks
    like, not about the wrapper instance's own attributes. The
    broadcaster must treat the wrapper-block kwarg as a sibling
    broadcast scoped to that subtree, shadowing the outer broadcast for
    everything beneath it.

    Today's confluid behaviour silently drops the wrapper-level kwarg
    because the wrapper's accept-list excludes ``ops``, so the
    outer-level broadcast leaks straight through. This test fails red
    until the broadcaster honours kwargs set on configurable wrappers.
    """
    config = {
        "_confluid_class_": "_Outer",
        "ops": ["heavy_a", "heavy_b"],
        "source": {
            "_confluid_class_": "_Wrapper",
            "ops": [],  # wrapper-level shield; currently ignored
            "children": [
                {"_confluid_class_": "_Outer"},
                {"_confluid_class_": "_Outer"},
            ],
        },
    }
    root = materialize(config)
    assert root.ops == ["heavy_a", "heavy_b"]
    # Desired (currently failing): wrapper-level `ops: []` blocks broadcast.
    assert root.source.children[0].ops == []
    assert root.source.children[1].ops == []
