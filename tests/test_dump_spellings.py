"""``register_dump_spelling`` — registered document spellings for third-party VALUE types.

``dump()`` has three built-in faithful spellings (a PathLike dumps as its string,
an Enum as its value, a numpy scalar as ``.item()``); every other opaque value
degrades to a bare ``{_target_: <module.qualname>}`` placeholder that reloads
DEFAULT-constructed. The registry generalizes the built-ins: a consumer registers
``spell(value) -> document form`` for a type it knows how to respell — a
``Target`` marker (rebuilt by the ordinary load machinery, so the round trip
needs no load-side change) or a plain YAML-clean value — or ``None`` to decline,
keeping the placeholder and its warning.

The in-memory document (``to_markers`` / ``configure()``) is deliberately NOT
affected: live values stay live there, because identity is what ``configure()``
maps settled values back onto.
"""

from pathlib import PurePosixPath
from types import SimpleNamespace
from typing import Any, List, Optional

import pytest

from confluid import ConfigurableDefinitionError, Target, configurable, dump, load, register
from confluid.dumper import register_dump_spelling, to_markers


class _Vector:
    """The opaque third-party stand-in — not configurable, not registered."""

    def __init__(self, values: List[float]) -> None:
        self.values = list(values)


def _vector_from_values(values: List[float]) -> _Vector:
    return _Vector(values)


def _make_host() -> Any:
    @configurable(name="SpellingHost")
    class Host:
        def __init__(self) -> None:
            self.payload: Any = None

    return Host


def test_a_registered_spelling_replaces_the_placeholder_and_round_trips() -> None:
    Host = _make_host()
    register(_vector_from_values, name="VectorFromValues")
    register_dump_spelling(_Vector, lambda v: Target(_vector_from_values, values=v.values))

    host = Host()
    host.payload = _Vector([1.0, 2.5])
    text = dump(host)
    assert "VectorFromValues" in text
    assert "_Vector" not in text, "the placeholder is gone"

    reloaded = load(text)
    assert isinstance(reloaded.payload, _Vector)
    assert reloaded.payload.values == [1.0, 2.5]


def test_a_spelling_may_return_a_plain_value() -> None:
    Host = _make_host()
    register_dump_spelling(_Vector, lambda v: v.values)

    host = Host()
    host.payload = _Vector([3.0, 4.0])
    text = dump(host)
    assert "payload:" in text and "_Vector" not in text
    assert load(text).payload == [3.0, 4.0]


def test_a_declining_spelling_keeps_the_placeholder_and_the_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    """``None`` means "this value has no faithful spelling after all" — e.g. a size cap."""

    class _Huge:
        pass

    Host = _make_host()
    register_dump_spelling(_Huge, lambda v: None)

    from confluid import dumper

    warnings: List[str] = []
    monkeypatch.setattr(dumper, "logger", SimpleNamespace(warning=lambda msg, *a, **k: warnings.append(msg)))
    host = Host()
    host.payload = _Huge()
    text = dump(host)
    assert "_Huge" in text, "the placeholder stays"
    assert any("_Huge" in w for w in warnings), "and it stays LOUD"


def test_a_subclass_hits_its_bases_spelling() -> None:
    """The lookup walks the MRO — ``nn.Parameter`` must hit a ``Tensor`` recipe."""

    class _SubVector(_Vector):
        pass

    Host = _make_host()
    register(_vector_from_values, name="VectorFromValues")
    register_dump_spelling(_Vector, lambda v: Target(_vector_from_values, values=v.values))

    host = Host()
    host.payload = _SubVector([9.0])
    assert load(dump(host)).payload.values == [9.0]


def test_an_exact_match_wins_over_a_base() -> None:
    class _SubVector(_Vector):
        pass

    Host = _make_host()
    register_dump_spelling(_Vector, lambda v: "base")
    register_dump_spelling(_SubVector, lambda v: "sub")

    host = Host()
    host.payload = _SubVector([1.0])
    assert load(dump(host)).payload == "sub"


def test_an_unregistered_opaque_keeps_todays_placeholder(monkeypatch: pytest.MonkeyPatch) -> None:
    """The con case: nothing registered, nothing changes."""

    class _Opaque:
        pass

    Host = _make_host()
    from confluid import dumper

    warnings: List[str] = []
    monkeypatch.setattr(dumper, "logger", SimpleNamespace(warning=lambda msg, *a, **k: warnings.append(msg)))
    host = Host()
    host.payload = _Opaque()
    text = dump(host)
    assert "_Opaque" in text
    assert any("_Opaque" in w for w in warnings)


def test_builtin_faithful_spellings_win_over_a_registered_one() -> None:
    """A PathLike keeps its string spelling — the built-ins are not overridable."""
    Host = _make_host()
    called: List[Any] = []

    def _recipe(v: Any) -> str:
        called.append(v)
        return "recipe"

    register_dump_spelling(PurePosixPath, _recipe)

    host = Host()
    host.payload = PurePosixPath("/data/run")
    text = dump(host)
    assert "/data/run" in text
    assert not called, "the built-in fired first; the recipe never ran"


def test_to_markers_keeps_a_live_opaque_value_live() -> None:
    """The configure() path is untouched: identity, not a respelling."""
    Host = _make_host()
    register_dump_spelling(_Vector, lambda v: Target(_vector_from_values, values=v.values))

    host = Host()
    vector = _Vector([1.0])
    host.payload = vector
    marker = to_markers(host)
    assert marker.kwargs["payload"] is vector


def test_a_spelling_may_embed_live_registered_instances() -> None:
    """A recipe's marker may carry LIVE registered objects in its kwargs (a metric
    collection's metrics dict). Those surface only DURING serialization, so the
    discovery pre-walk never saw them — the catch-all must still render them via
    the object representer, with their real kwargs, not a bare placeholder."""

    class _Inner:
        def __init__(self, size: int = 0) -> None:
            self.size = size

    class _Bundle:
        def __init__(self, items: Any = None) -> None:
            self.items = items

    register(_Inner, name="SpellingInner")
    register(_Bundle, name="SpellingBundle")
    register_dump_spelling(_Bundle, lambda b: Target(_Bundle, items=b.items))

    Host = _make_host()
    host = Host()
    host.payload = _Bundle(items={"a": _Inner(size=7)})
    text = dump(host)
    assert "size: 7" in text, "the embedded live instance kept its kwargs"

    reloaded = load(text)
    assert reloaded.payload.items["a"].size == 7


def test_a_spelling_wins_over_the_generic_reconstruction_for_a_registered_class() -> None:
    """A spelling exists precisely because the generic slot walk gets the type
    wrong (a ``**kwargs`` base whose body slots the ctor chain refuses back) — so
    it must win even when the instance is REGISTERED and the discovery pre-walk
    gave its class the generic representer."""

    class _Tricky:
        def __init__(self, size: int = 0) -> None:
            self.size = size
            self.junk = "derived-state-the-ctor-refuses"

    register(_Tricky, name="SpellingTricky")
    register_dump_spelling(_Tricky, lambda t: Target(_Tricky, size=t.size))

    Host = _make_host()
    host = Host()
    host.payload = _Tricky(size=4)
    text = dump(host)
    assert "junk" not in text, "the spelling's kwargs, not the generic walk's"
    assert load(text).payload.size == 4


def test_registration_refuses_a_non_type_and_a_non_callable() -> None:
    with pytest.raises(ConfigurableDefinitionError):
        register_dump_spelling("not a type", lambda v: v)  # type: ignore[arg-type]
    with pytest.raises(ConfigurableDefinitionError):
        register_dump_spelling(_Vector, "not callable")  # type: ignore[arg-type]


def test_a_spelled_value_nested_inside_a_marker_kwarg_round_trips() -> None:
    """The user-shaped case: the opaque value sits INSIDE a registered class's kwargs
    (a loss's ``weight``), not at a top-level slot."""

    class _Loss:
        def __init__(self, weight: Optional[_Vector] = None, reduction: str = "mean") -> None:
            self.weight = weight
            self.reduction = reduction

    register(_Loss, name="SpellingLoss")
    register(_vector_from_values, name="VectorFromValues")
    register_dump_spelling(_Vector, lambda v: Target(_vector_from_values, values=v.values))

    Host = _make_host()
    host = Host()
    host.payload = _Loss(weight=_Vector([0.5, 2.0]))
    reloaded = load(dump(host))
    assert isinstance(reloaded.payload, _Loss)
    assert reloaded.payload.weight is not None
    assert reloaded.payload.weight.values == [0.5, 2.0]
