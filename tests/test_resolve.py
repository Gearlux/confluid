"""Tests for ``confluid.resolve`` (marker walk) and the ``solidify=False`` flag.

These back StreamStudio's YAML→graph import, which needs a config's *structure*
(broadcast-resolved markers / live-but-unsolidified objects) without paying for
the expensive ``solidify()`` finalize (e.g. building a model backbone).
"""

from typing import Any

from confluid import PartialClass, Reference, Target, configurable, flow, load, materialize, resolve


def test_resolve_returns_markers_without_instantiating() -> None:
    """``resolve`` yields Fluid markers, never live objects (no construction)."""
    built: list[str] = []

    @configurable
    class _R1:
        def __init__(self, name: str = "") -> None:
            built.append(name)
            self.name = name

    doc = {"a": Target(_R1, name="x")}
    out = resolve(doc)
    assert isinstance(out["a"], Target)
    assert out["a"].kwargs["name"] == "x"
    assert built == []  # never constructed


def test_resolve_merges_broadcast_into_kwargs() -> None:
    """A flat config's sibling keys are merged into the receiver's marker kwargs.

    Mirrors the flat-broadcast shape of sonair's train config: a top-level
    ``trainer`` plus sibling ``inner`` / ``n`` that broadcast into it. The
    deferred ``Target`` (no-parens ``!class:``) is the form real configs use.
    """

    @configurable
    class _R2Trainer:
        def __init__(self, name: str = "", n: int = 0, inner: Any = None) -> None:
            self.name = name
            self.n = n
            self.inner = inner

    @configurable
    class _R2Inner:
        def __init__(self, k: int = 0) -> None:
            self.k = k

    doc = {
        "trainer": Target(_R2Trainer, name="t"),  # deferred (no-parens !class:) form
        "inner": Target(_R2Inner, k=3),
        "n": 9,
    }
    out = resolve(doc)
    trainer = out["trainer"]
    assert isinstance(trainer, (Target, Target))
    # `n` (scalar) and `inner` (Fluid) both broadcast into the trainer's accept-list.
    assert trainer.kwargs["n"] == 9
    assert trainer.kwargs["inner"] is out["inner"]  # shared by identity (fan-out detectable)


def test_resolve_shares_ref_by_identity_for_fanout() -> None:
    """A ``!ref:`` reached from two places resolves to the SAME marker object."""

    @configurable
    class _R3:
        def __init__(self, src: Any = None, alt: Any = None) -> None:
            self.src = src
            self.alt = alt

    @configurable
    class _R3Src:
        def __init__(self, p: str = "") -> None:
            self.p = p

    doc = {
        "node": Target(_R3, src=Reference("shared"), alt=Reference("shared")),
        "shared": Target(_R3Src, p="z"),
    }
    out = resolve(doc)
    assert out["node"].kwargs["src"] is out["node"].kwargs["alt"]
    assert out["node"].kwargs["src"] is out["shared"]


def test_resolve_preserves_lazy_markers() -> None:
    """A ``!lazy:`` slot stays a ``PartialClass`` marker through ``resolve``."""

    @configurable
    class _R4:
        def __init__(self, opt: Any = None) -> None:
            self.opt = opt

    @configurable
    class _R4Opt:
        def __init__(self, lr: float = 0.0) -> None:
            self.lr = lr

    doc = {"node": Target(_R4, opt=PartialClass(_R4Opt, lr=0.1))}
    out = resolve(doc)
    assert isinstance(out["node"].kwargs["opt"], PartialClass)


def test_materialize_solidify_false_builds_but_skips_solidify() -> None:
    """``materialize(solidify=False)`` constructs objects but never solidifies."""
    calls: list[str] = []

    @configurable
    class _R5:
        def __init__(self, name: str = "") -> None:
            self.name = name
            self.built = False

        def solidify(self) -> None:
            calls.append(self.name)
            self.built = True

    doc = {"m": Target(_R5, name="h")}

    g = materialize(doc, solidify=False)
    assert isinstance(g["m"], _R5)  # constructed
    assert g["m"].built is False
    assert calls == []

    calls.clear()
    g2 = materialize(doc)  # default: solidify fires
    assert g2["m"].built is True
    assert calls == ["h"]


def test_flow_solidify_false_skips_nested_solidify() -> None:
    """``flow(obj, solidify=False)`` suppresses solidify across the whole subtree."""
    calls: list[str] = []

    @configurable
    class _R6Leaf:
        def __init__(self, name: str = "") -> None:
            self.name = name

        def solidify(self) -> None:
            calls.append(self.name)

    @configurable
    class _R6Root:
        def __init__(self, name: str = "", leaf: Any = None) -> None:
            self.name = name
            self.leaf = leaf

        def solidify(self) -> None:
            calls.append(self.name)

    root = flow(Target(_R6Root, name="root", leaf=Target(_R6Leaf, name="leaf")), solidify=False)
    assert isinstance(root, _R6Root)
    assert calls == []  # neither root nor nested leaf solidified

    # Default path still solidifies (and the flag is restored — no leakage).
    calls.clear()
    flow(Target(_R6Leaf, name="again"))
    assert calls == ["again"]


def test_resolve_leaves_a_DOTTED_ref_deferred() -> None:
    """``resolve()`` constructs nothing — including for a dotted ``!ref:a.b``.

    Reading ``split.train`` means BUILDING ``split``, which is the one place a
    Reference triggers construction. It used to happen on this path too, so
    ``resolve()`` — the "introspection without cost" API — walked a dataset:
    measured 3.91s on a real config whose split scans 37 archives, for a call
    documented as constructing nothing. Two external consumers (a visual editor's
    YAML importer, a flow-graph builder) already documented that they need
    markers, and two projects' config suites hand-rolled a tag-stubbing parser
    because ``resolve()`` "is not an escape".
    """
    built: list[str] = []

    @configurable
    class _Split:
        def __init__(self, source: str = "") -> None:
            built.append(source)
            self.source = source

        @property
        def train(self) -> str:
            return f"train-of-{self.source}"

    doc = "s: {_target_: _Split, source: disk}\nuse: '${ref:s.train}'"
    out = resolve(doc)

    assert built == [], "resolve() must construct nothing, dotted refs included"
    assert isinstance(out["use"], Reference)
    assert out["use"].target == "s.train"


def test_a_dotted_ref_still_resolves_through_load() -> None:
    """The deferral is `resolve()`-only: `load()` still reads the attribute off ONE
    shared instance, which is the whole point of writing `split.train`/`split.val`."""
    built: list[str] = []

    @configurable
    class _Split:
        def __init__(self, source: str = "") -> None:
            built.append(source)
            self.source = source

        @property
        def train(self) -> str:
            return f"train-of-{self.source}"

        @property
        def val(self) -> str:
            return f"val-of-{self.source}"

    graph = load("s: {_target_: _Split, source: disk}\na: '${ref:s.train}'\nb: '${ref:s.val}'")

    assert built == ["disk"], "exactly one construction, shared by both refs"
    assert graph["a"] == "train-of-disk"
    assert graph["b"] == "val-of-disk"
