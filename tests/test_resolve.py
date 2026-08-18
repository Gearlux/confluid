"""Tests for ``confluid.resolve`` (marker walk) and the ``solidify=False`` flag.

These back StreamStudio's YAML→graph import, which needs a config's *structure*
(broadcast-resolved markers / live-but-unsolidified objects) without paying for
the expensive ``solidify()`` finalize (e.g. building a model backbone).
"""

from typing import Any

import pytest

from confluid import ConfigurationError, PartialClass, Reference, Target, configurable, flow, load


def test_resolve_returns_markers_without_instantiating() -> None:
    """``resolve`` yields Fluid markers, never live objects (no construction)."""
    built: list[str] = []

    @configurable
    class _R1:
        def __init__(self, name: str = "") -> None:
            built.append(name)
            self.name = name

    doc = {"a": Target(_R1, name="x")}
    out = load(doc, until="settled")
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
    out = load(doc, until="settled")
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
    out = load(doc, until="settled")
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
    out = load(doc, until="settled")
    assert isinstance(out["node"].kwargs["opt"], PartialClass)


def test_materialize_solidify_false_builds_but_skips_solidify() -> None:
    """``load(solidify=False)`` constructs objects but never solidifies."""
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

    g = load(doc, solidify=False)
    assert isinstance(g["m"], _R5)  # constructed
    assert g["m"].built is False
    assert calls == []

    calls.clear()
    g2 = load(doc)  # default: solidify fires
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


def test_resolve_refuses_a_dotted_ATTRIBUTE_ref_exactly_as_load_does() -> None:
    """``load(until="settled")`` constructs nothing — and since record 19 phase 2 an attribute reference
    (``${ref:s.train}``, reading a property of the object built at ``s``) is REFUSED on both
    paths with one message, instead of being deferred here and built there.

    History: reading ``split.train`` used to mean BUILDING ``split`` — the one place a Reference
    triggered construction — and it ran on this path too, so the "introspection without cost" API
    walked a dataset (measured 3.91s on a real config). A ``structural`` engine flag then gated it
    off for ``load(until="settled")`` alone. Both the construction and the flag are gone: the resolver never
    builds a cursor any more, so the two paths cannot disagree.
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

    doc = "s: {_target_: _Split, source: disk}\nuse: {_target_: _Split, source: '${ref:s.train}'}"
    with pytest.raises(ConfigurationError) as via_resolve:
        load(doc, until="settled")
    with pytest.raises(ConfigurationError) as via_load:
        load(doc)
    assert str(via_resolve.value) == str(via_load.value)
    assert "ATTRIBUTE `train`" in str(via_load.value)
    assert built == [], "refused before anything is built, on either path"
