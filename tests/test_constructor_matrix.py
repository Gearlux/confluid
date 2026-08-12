"""Every constructor shape x every delivery path — the coverage matrix.

The point is systematic rather than anecdotal coverage. Two real bugs in this area
were found by ACCIDENT while demonstrating something unrelated — a zero-parameter
constructor rejecting every config key, and positional-only parameters doing the
same — and both had been reachable for a long time. Ad-hoc discovery finds the
case you happen to write; a matrix finds the case you would not have thought of.

Each host class exposes one knob named `k`, reachable in whatever way its shape
allows. Each delivery path sets it to 42. Every cell must arrive: a shape that
cannot be configured through a path the grammar offers is a gap, whether it raises
(loud) or silently keeps the default (worse).

Four further axes are covered at the end of the file — inheritance, `!ref:`/
`!clone:`, scopes, and `configure()` crossed with the first two — because each is a
different ROUTE to the same slot, and a route is exactly where a delivery rule
diverges. All four passed on first run; the tests exist to keep it that way, not
because they found something. That is worth stating: the load and post-construction
paths have diverged four separate times, so "they agree today" is a fact with a
short shelf life unless it is pinned.

Deliberately NOT covered here, and why:

* **CLI overrides** belong to the app framework, not to confluid — it applies them
  to the document before `load()`, so they arrive as one of the paths below.
* **Required (non-defaulted) parameters** are excluded by the zero-arg construction
  convention: every parameter is defaulted, so `Cls()` works and validation of a
  genuinely-required value happens lazily at use.
"""

from dataclasses import dataclass
from typing import Any, List

import pytest

import confluid
from confluid import PartialClass, configurable, configure, flow, get_registry, load, register


@configurable
class ZeroParams:
    def __init__(self) -> None:
        self.k: Any = None


@configurable
class Defaulted:
    def __init__(self, k: Any = None) -> None:
        self.k = k


@configurable
class Required:
    def __init__(self, k: Any = "unset") -> None:  # defaulted per the zero-arg mandate
        self.k = k


@configurable
class VarKw:
    def __init__(self, **kw: Any) -> None:
        self.k = kw.get("k")


@configurable
class VarPos:
    def __init__(self, *items: Any, k: Any = None) -> None:
        self.items, self.k = items, k


@configurable
class VarPosKw:
    def __init__(self, *items: Any, **kw: Any) -> None:
        self.items = items
        self.k = kw.get("k")


@configurable
class BodyOnly:
    def __init__(self) -> None:
        self.k: Any = None


@configurable
class BodyPlusParam:
    def __init__(self, other: str = "x") -> None:
        self.other = other
        self.k: Any = None


@configurable
class KeywordOnly:
    def __init__(self, *, k: Any = None) -> None:
        self.k = k


@configurable
class PositionalOnly:
    def __init__(self, k: Any = None, /) -> None:
        self.k = k


@configurable
class LazySlot:
    def __init__(self) -> None:
        self.k: Any = None
        self.dep: Any = PartialClass(Defaulted, k=1)


SHAPES = [
    ZeroParams,
    Defaulted,
    VarKw,
    VarPos,
    VarPosKw,
    BodyOnly,
    BodyPlusParam,
    KeywordOnly,
    PositionalOnly,
    LazySlot,
]

#: Every way a document can aim a value at a node. `configure()` and runtime
#: injection are separate because they are not document paths at all.
DOCUMENT_PATHS = {
    "marker_kwarg": lambda n: f"o: !class:{n}(k=42)\n",
    "inline_block": lambda n: f"o: !class:{n}()\n  k: 42\n",
    "class_block": lambda n: f"o: !class:{n}()\n{n}:\n  k: 42\n",
    "bare_key": lambda n: f"o: !class:{n}()\nk: 42\n",
    "dotted": lambda n: f"o: !class:{n}()\no.k: 42\n",
    "glob": lambda n: f"o: !class:{n}()\n'**.k': 42\n",
}


@pytest.fixture(autouse=True)
def _register() -> None:
    for cls in SHAPES:
        get_registry().register_class(cls, name=cls.__name__)


@pytest.mark.parametrize("shape", SHAPES, ids=lambda c: c.__name__)
@pytest.mark.parametrize("path", sorted(DOCUMENT_PATHS), ids=str)
def test_every_document_path_reaches_every_constructor_shape(shape: type, path: str) -> None:
    """The 60-cell core of the matrix: six document spellings x ten shapes."""
    document = DOCUMENT_PATHS[path](shape.__name__)

    assert load(document)["o"].k == 42


@pytest.mark.parametrize("shape", SHAPES, ids=lambda c: c.__name__)
def test_configure_reaches_every_constructor_shape(shape: type) -> None:
    """The post-construction path must agree with the load paths, shape for shape.

    It was `configure()` succeeding where every load path failed that identified
    positional-only parameters as an engine bug rather than a Python limitation.
    """
    obj = shape()

    configure(obj, config=f"{shape.__name__}:\n  k: 42\n")

    assert obj.k == 42


@pytest.mark.parametrize("shape", SHAPES, ids=lambda c: c.__name__)
def test_runtime_injection_reaches_every_constructor_shape(shape: type) -> None:
    """`flow(marker, k=42)` — the channel that bypasses the document entirely."""
    assert flow(confluid.Target(shape), k=42).k == 42


# --------------------------------------------------------------------------- #
# Registration kind, mutability, and the delivery rule
#
# "Last path that fits wins": a value is delivered by the LAST mechanism that can
# accept it — a constructor parameter when the target declares one, otherwise a
# post-init setattr. These pin that the rule does not depend on HOW the class was
# registered, and that a target where NO path fits fails where the config is
# visible rather than deep inside the engine.
# --------------------------------------------------------------------------- #


class ThirdPartyDeclared:
    """Registered, never decorated — the shape you do not own."""

    def __init__(self, k: Any = None) -> None:
        self.k = k


class ThirdPartyZeroParams:
    def __init__(self) -> None:
        self.k: Any = None


class ThirdPartyKwargs:
    def __init__(self, **kw: Any) -> None:
        self.k = kw.get("k")


class SlottedWithSlot:
    """No `__dict__` at all, but a slot for the key."""

    __slots__ = ("k",)

    def __init__(self, k: Any = None) -> None:
        self.k = k


class SlottedWithoutSlot:
    """No `__dict__` and NO slot for the key — the case where no path fits."""

    __slots__ = ("other",)

    def __init__(self, other: Any = None) -> None:
        self.other = other


@dataclass(frozen=True)
class FrozenDataclass:
    """Immutable after construction: the constructor is the only path that fits."""

    k: Any = None


THIRD_PARTY: List[type] = [ThirdPartyDeclared, ThirdPartyZeroParams, ThirdPartyKwargs]
IMMUTABLE_OK: List[type] = [SlottedWithSlot, FrozenDataclass]


@pytest.fixture(autouse=True)
def _register_third_party() -> None:
    for cls in THIRD_PARTY + IMMUTABLE_OK + [SlottedWithoutSlot]:
        register(cls, name=cls.__name__)


@pytest.mark.parametrize("shape", THIRD_PARTY + IMMUTABLE_OK, ids=lambda c: c.__name__)
@pytest.mark.parametrize("path", sorted(DOCUMENT_PATHS), ids=str)
def test_registration_kind_and_mutability_do_not_change_delivery(shape: type, path: str) -> None:
    """A `register()`-ed class configures exactly like a decorated one.

    Including the ones that cannot take a post-init attribute: for a frozen
    dataclass or a slotted class the constructor is the last path that fits, and
    the engine must use it rather than reaching for a setattr that would fail.
    """
    assert load(DOCUMENT_PATHS[path](shape.__name__))["o"].k == 42


def test_a_target_where_no_path_fits_fails_at_the_config_not_in_the_engine() -> None:
    """An addressed key with nowhere to go must name the key, the target and the line.

    The constructor did not declare it and the object refuses the attribute, so
    nothing fits. That IS an error — the author aimed the key at this node — but the
    raw `AttributeError` said "'S' object has no attribute '__dict__'" from a line
    they never wrote, pointing at the engine rather than at their config.
    """
    with pytest.raises(confluid.ConstructionError) as excinfo:
        load("o: !class:SlottedWithoutSlot()\n  k: 42\n")

    message = str(excinfo.value)
    assert "SlottedWithoutSlot" in message and "'k'" in message
    assert "constructor" in message  # says what to do about it


@pytest.mark.parametrize(
    "shape", [Defaulted, ThirdPartyDeclared, ThirdPartyZeroParams, SlottedWithoutSlot], ids=lambda c: c.__name__
)
def test_a_key_no_target_declares_is_dropped_not_raised(shape: type) -> None:
    """A BARE key nothing accepts is filtered by the accept-list, silently and correctly.

    This is the counterpart to the test above and the reason that one is scoped to
    ADDRESSED keys: a bare key is aimed at the whole document, so a node that cannot
    take it is not a mistake — it is the normal case for every other node in the tree.
    """
    obj = load(f"o: !class:{shape.__name__}()\nnobody_declares_this: 9\n")["o"]

    assert not hasattr(obj, "nobody_declares_this")


def test_a_kwargs_target_still_receives_what_nobody_declared() -> None:
    """The documented exception: no accept-list means nothing can be filtered out."""
    obj = load("o: !class:ThirdPartyKwargs()\nnobody_declares_this: 9\n")["o"]

    assert obj.nobody_declares_this == 9


# --------------------------------------------------------------------------- #
# The three axes the shape matrix does not cross: inheritance, !ref:/!clone:,
# and scopes. Each is a place where a value takes a different route to the same
# slot, so each is a place the delivery rule could diverge.
# --------------------------------------------------------------------------- #


@configurable
class InheritBase:
    def __init__(self, k: Any = None) -> None:
        self.k = k


@configurable
class InheritsInit(InheritBase):
    """No `__init__` of its own — the parent's signature is the accept-list."""


@configurable
class CallsSuper(InheritBase):
    def __init__(self, k: Any = None, extra: str = "e") -> None:
        super().__init__(k)
        self.extra = extra


@configurable
class BodySlotParent:
    def __init__(self) -> None:
        self.k: Any = None


@configurable
class InheritsBodySlot(BodySlotParent):
    """The slot is assigned in the PARENT's `__init__` body.

    The AST scan walks the MRO, so the child's accept-list must include a slot it
    never mentions — otherwise subclassing silently narrows what config can reach.
    """

    def __init__(self) -> None:
        super().__init__()
        self.other: Any = None


@configurable
class ShadowsBodySlot(BodySlotParent):
    """Re-assigns the parent's slot after `super().__init__()`.

    The child's value is a default like any other, so config must still win.
    """

    def __init__(self) -> None:
        super().__init__()
        self.k = "child-default"


INHERITANCE = [InheritBase, InheritsInit, CallsSuper, BodySlotParent, InheritsBodySlot, ShadowsBodySlot]


@pytest.fixture(autouse=True)
def _register_inheritance() -> None:
    for cls in INHERITANCE:
        get_registry().register_class(cls, name=cls.__name__)


@pytest.mark.parametrize("shape", INHERITANCE, ids=lambda c: c.__name__)
@pytest.mark.parametrize("path", ["marker_kwarg", "inline_block", "class_block", "bare_key"], ids=str)
def test_inheritance_does_not_narrow_what_config_can_reach(shape: type, path: str) -> None:
    """A subclass must be configurable everywhere its parent is."""
    assert load(DOCUMENT_PATHS[path](shape.__name__))["o"].k == 42


def test_a_ref_shares_one_configured_object() -> None:
    """`!ref:` is identity, not a copy — both names see the same configured instance."""
    graph = load("src: !class:InheritBase()\n  k: 42\ndst: !ref:src\n")

    assert graph["dst"] is graph["src"]
    assert graph["dst"].k == 42


def test_a_clone_is_configured_independently() -> None:
    """`!clone:` opts OUT of the sharing, and must still carry the configuration.

    A clone that arrived unconfigured would be the more dangerous failure: it looks
    like the original everywhere except in the values that matter.
    """
    graph = load("src: !class:InheritBase()\n  k: 42\ndst: !clone:src\n")

    assert graph["dst"] is not graph["src"]
    assert graph["dst"].k == 42 and graph["src"].k == 42


def test_a_bare_key_reaches_both_a_ref_and_a_clone() -> None:
    """Broadcasting does not stop at an aliased node."""
    shared = load("src: !class:InheritBase()\ndst: !ref:src\nk: 42\n")
    cloned = load("src: !class:InheritBase()\ndst: !clone:src\nk: 42\n")

    assert shared["dst"].k == 42
    assert cloned["dst"].k == 42 and cloned["src"].k == 42


@pytest.mark.parametrize(
    ("label", "document"),
    [
        ("top level", "o: !class:InheritBase()\nsel: !scope:mode=fast\n  k: 42\n"),
        ("inside the marker", "o: !class:InheritBase()\n  sel: !scope:mode=fast\n    k: 42\n"),
        ("zero-arg host", "o: !class:BodySlotParent()\nsel: !scope:mode=fast\n  k: 42\n"),
    ],
    ids=["top_level", "inside_the_marker", "zero_arg_host"],
)
def test_a_scope_block_delivers_like_any_other_source(label: str, document: str) -> None:
    """A value spliced in by an active scope is an ordinary key by the time it lands.

    Worth pinning rather than assuming: scope resolution runs before materialization,
    so a splice that produced the right keys in the wrong POSITION would deliver
    correctly here and order wrongly against a bare key — the failure this suite's
    sibling (`test_document_order.py`) exists for.
    """
    assert load(document, scopes=["mode=fast"])["o"].k == 42


# --------------------------------------------------------------------------- #
# The last untested corner: configure() crossed with inheritance and with
# aliased nodes. The load path covers both above; this is the post-construction
# path over the same shapes, and the two have diverged four times already.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("shape", INHERITANCE, ids=lambda c: c.__name__)
@pytest.mark.parametrize("style", ["class_block", "bare_key"], ids=str)
def test_configure_reaches_every_inheritance_shape(shape: type, style: str) -> None:
    """A subclass must be as configurable post-construction as it is at load.

    `ShadowsBodySlot` is the case worth having: the child re-assigns the parent's
    slot after `super().__init__()`, so a walker reading the child's own body would
    see a plain string where the parent declared a configurable slot.
    """
    obj = shape()
    config = f"{shape.__name__}:\n  k: 42\n" if style == "class_block" else "k: 42\n"

    configure(obj, config=config)

    assert obj.k == 42


@configurable
class AliasHolder:
    """Holds two slots so a ref/clone pair can be reached through one parent."""

    def __init__(self, a: Any = None, b: Any = None) -> None:
        self.a, self.b = a, b


def test_configure_reaches_a_shared_ref_once_and_both_names_see_it() -> None:
    """`!ref:` makes two names for one object — configuring it must not depend on which.

    The graph walk carries a visited set, so the second name is skipped; the pin is
    that skipping it does not mean the value fails to arrive.
    """
    get_registry().register_class(AliasHolder, name="AliasHolder")
    graph = load("a: !class:InheritBase()\nb: !ref:a\n")
    assert graph["a"] is graph["b"]

    configure(AliasHolder(a=graph["a"], b=graph["b"]), config="k: 42\n")

    assert graph["a"].k == 42 and graph["b"].k == 42


def test_configure_reaches_both_halves_of_a_clone() -> None:
    """`!clone:` makes two distinct objects — BOTH must be configured.

    The complement of the test above: there the visited set must not lose the value,
    here it must not mistake two objects for one.
    """
    get_registry().register_class(AliasHolder, name="AliasHolder")
    graph = load("a: !class:InheritBase()\nb: !clone:a\n")
    assert graph["a"] is not graph["b"]

    configure(AliasHolder(a=graph["a"], b=graph["b"]), config="k: 42\n")

    assert graph["a"].k == 42 and graph["b"].k == 42


def test_configure_reaches_a_child_through_an_addressed_block_on_its_parent() -> None:
    """A `ClassName:` block finds the child by class name, not by being top level."""
    get_registry().register_class(AliasHolder, name="AliasHolder")
    graph = load("a: !class:InheritBase()\n")

    configure(AliasHolder(a=graph["a"]), config="InheritBase:\n  k: 42\n")

    assert graph["a"].k == 42
