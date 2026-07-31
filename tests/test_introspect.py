"""Tests for ``confluid.introspect`` — the ONE shared ``__init__``-body AST scan.

The three projections replace what used to be three near-identical scanners
(loader names / pydantic annotations / pydantic lazy). The per-kind visibility
rules are the semantic contract: ``AugAssign`` and literal ``setattr`` slots
are broadcast-visible NAMES but never pydantic fields or lazy slots.
"""

from typing import Any

from confluid import LazyClass, configurable
from confluid.introspect import init_lazy_setattr_names, init_setattr_annotations, init_setattr_names, scan_init_body


class _AllKinds:
    def __init__(self) -> None:
        self.plain = 1
        self.annotated: int = 2
        self.plain_first = 3
        self.plain_first: int = 4  # type: ignore[no-redef]  # AnnAssign AFTER a plain Assign — first wins
        self.counter = 0
        self.counter += 1
        setattr(self, "via_setattr", 5)
        setattr(self, "_private_setattr", 6)
        self._private = 7
        if True:
            self.nested = 8  # inside a branch — ast.walk must see it


class _LazySlots:
    def __init__(self) -> None:
        self.optimizer: Any = LazyClass(dict)
        self.plain_slot: Any = dict()
        self.qualified: Any = _Ns.LazyClass(dict)


class _Ns:
    LazyClass: Any = staticmethod(LazyClass)


def test_scan_records_all_four_kinds_in_walk_order() -> None:
    slots = scan_init_body(_AllKinds.__init__)
    kinds = {(s.name, s.kind) for s in slots}
    assert ("plain", "assign") in kinds
    assert ("annotated", "annassign") in kinds
    assert ("counter", "augassign") in kinds
    assert ("via_setattr", "setattr") in kinds
    assert ("nested", "assign") in kinds  # nested-in-if pinned
    names = {s.name for s in slots}
    assert "_private" not in names and "_private_setattr" not in names


def test_names_projection_is_the_widest() -> None:
    names = init_setattr_names(_AllKinds.__init__)
    assert {"plain", "annotated", "plain_first", "counter", "via_setattr", "nested"} <= names


def test_annotations_projection_excludes_aug_and_setattr_and_is_first_wins() -> None:
    annotations = init_setattr_annotations(_AllKinds.__init__)
    assert "counter" not in annotations or annotations.get("counter") is None  # augassign never adds
    assert "via_setattr" not in annotations
    # plain Assign walked before the AnnAssign for the same name → None wins.
    assert "plain_first" in annotations and annotations["plain_first"] is None
    assert annotations["annotated"] is not None  # the int annotation node


def test_lazy_projection_matches_bare_and_qualified_calls_only() -> None:
    lazy = init_lazy_setattr_names(_LazySlots.__init__)
    assert lazy == {"optimizer", "qualified"}


def test_scan_returns_empty_without_source() -> None:
    assert scan_init_body(dict.__init__) == ()
    assert init_setattr_names(dict) == set()


def test_scan_sees_through_configurable_wrapper() -> None:
    """THE load-bearing pin: ``@configurable`` replaces ``__init__`` with a
    ``functools.wraps`` validation wrapper; ``inspect.getsource`` follows
    ``__wrapped__`` so the scan parses the ORIGINAL constructor body — never
    the wrapper's. If this breaks, every post-init body slot silently
    disappears from broadcasting AND ``to_pydantic``."""

    @configurable
    class Trainer:
        def __init__(self, lr: float = 0.01) -> None:
            self.lr = lr
            self.optimizer: Any = LazyClass(dict)
            self.loss_fn = "cross-entropy"

    wrapped_init = Trainer.__dict__["__init__"]
    assert getattr(wrapped_init, "__confluid_validated__", False)  # it IS the wrapper
    names = init_setattr_names(wrapped_init)
    assert {"lr", "optimizer", "loss_fn"} <= names
    assert "optimizer" in init_lazy_setattr_names(wrapped_init)
