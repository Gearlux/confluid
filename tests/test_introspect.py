"""Tests for ``confluid.introspect`` — the ONE shared ``__init__``-body AST scan.

The three projections replace what used to be three near-identical scanners
(loader names / pydantic annotations / pydantic lazy). The per-kind visibility
rules are the semantic contract: ``AugAssign`` and literal ``setattr`` slots
are broadcast-visible NAMES but never pydantic fields or lazy slots.
"""

from typing import Any

from confluid import PartialClass, configurable
from confluid.broadcast import _get_param_kinds
from confluid.introspect import init_partial_setattr_names, init_setattr_names, scan_init_body, slots
from confluid.partial import Partial, partial_param_names


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
        self.optimizer: Any = PartialClass(dict)
        self.plain_slot: Any = dict()
        self.qualified: Any = _Ns.PartialClass(dict)


class _Ns:
    PartialClass: Any = staticmethod(PartialClass)


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


def test_lazy_projection_matches_bare_and_qualified_calls_only() -> None:
    lazy = init_partial_setattr_names(_LazySlots.__init__)
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
            self.optimizer: Any = PartialClass(dict)
            self.loss_fn = "cross-entropy"

    wrapped_init = Trainer.__dict__["__init__"]
    assert getattr(wrapped_init, "__confluid_validated__", False)  # it IS the wrapper
    names = init_setattr_names(wrapped_init)
    assert {"lr", "optimizer", "loss_fn"} <= names
    assert "optimizer" in init_partial_setattr_names(wrapped_init)


# ---------------------------------------------------------------------------
# A string annotation resolves, or degrades to Any — it never LEAKS
# (BUGS-2026-08-13 I4, I5)
#
# Two spellings put a string where a type belongs: a QUOTED annotation (the
# common "avoid a top-level import" habit) and PEP 563, which stringifies every
# annotation in a module. Both leaked the raw ``str`` as ``Slot.annotation``, so
# every reader that asks a question OF the annotation — is this slot deferred?
# does it take a list? — silently answered no.
#
# These classes are MODULE-level on purpose: ``get_type_hints`` resolves against
# the defining module's globals, so a class nested in a test function cannot see
# names the test imported locally.
# ---------------------------------------------------------------------------


class _Optim:
    """A stand-in flow target for the quoted-annotation pins."""


@configurable(validate=False)
class _QuotedBody:
    def __init__(self) -> None:
        self.optimizer: "Partial[_Optim]" = None  # type: ignore[assignment]


@configurable(validate=False)
class _BareBody:
    def __init__(self) -> None:
        self.optimizer: Partial[_Optim] = None  # type: ignore[assignment]


@configurable(validate=False)
class _QuotedParam:
    def __init__(self, optimizer: "Partial[_Optim]" = None) -> None:  # type: ignore[assignment]
        self.optimizer = optimizer


@configurable(validate=False)
class _MysteryBody:
    def __init__(self) -> None:
        self.thing: "NoSuchTypeAnywhere" = None  # type: ignore[assignment,name-defined] # noqa: F821


def test_a_quoted_body_slot_annotation_resolves_like_the_bare_one() -> None:
    """I4: the quoting is the ONLY difference between these two classes."""
    assert partial_param_names(_QuotedBody) == partial_param_names(_BareBody) == {"optimizer"}

    quoted = {s.name: s.annotation for s in slots(_QuotedBody)}["optimizer"]
    bare = {s.name: s.annotation for s in slots(_BareBody)}["optimizer"]
    assert not isinstance(quoted, str), "a raw string must never reach Slot.annotation"
    assert quoted == bare


def test_a_quoted_CONSTRUCTOR_PARAM_annotation_is_unchanged() -> None:
    """The half that always worked — ``get_type_hints`` resolves it. Pinned so the
    two declaration halves cannot drift apart again."""
    assert partial_param_names(_QuotedParam) == {"optimizer"}


def test_an_UNRESOLVABLE_quoted_annotation_degrades_to_Any() -> None:
    """Best-effort by contract: the slot is always surfaced and only its precision
    is lost — never as a leaked string."""
    assert {s.name: s.annotation for s in slots(_MysteryBody)}["thing"] is Any


def test_one_unresolvable_hint_does_not_punish_the_whole_signature() -> None:
    """I5: ``get_type_hints`` is all-or-nothing, so ONE bad name emptied the map and
    every parameter fell back to its raw PEP-563 string.

    ``PipeClean`` is the same class minus the bad parameter, so the two must agree
    on every parameter they share.
    """
    from pep563_helpers import Pipe, PipeClean

    assert partial_param_names(Pipe) == {"optimizer"}, "the deferral must survive"
    assert _get_param_kinds(Pipe)["stages"] == "list", "the list routing must survive"

    pipe = {s.name: s.annotation for s in slots(Pipe)}
    clean = {s.name: s.annotation for s in slots(PipeClean)}
    assert pipe["stages"] == clean["stages"]
    assert pipe["optimizer"] == clean["optimizer"]
    assert pipe["precision"] is Any, "only the unresolvable one degrades"

    for name, annotation in pipe.items():
        assert not isinstance(annotation, str), f"{name} leaked a raw string"


def test_pep563_body_slots_resolve_too() -> None:
    """The same module-level stringification reaches body slots by a different path."""
    from pep563_helpers import PipeBodySlots

    assert partial_param_names(PipeBodySlots) == {"optimizer"}
    annotations = {s.name: s.annotation for s in slots(PipeBodySlots)}
    assert annotations["precision"] is Any
    for name, annotation in annotations.items():
        assert not isinstance(annotation, str), f"{name} leaked a raw string"
