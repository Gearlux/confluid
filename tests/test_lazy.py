"""Unit tests for ``confluid.Lazy`` and ``lazy_param_names``."""

from typing import Any

import pytest

from confluid import Lazy, configurable, lazy_param_names
from confluid.lazy import is_lazy_annotation


def test_lazy_marker_metadata() -> None:
    """``Lazy[T].__metadata__`` carries the confluid sentinel."""
    ann = Lazy[int]  # type: ignore[misc]
    assert ann.__metadata__ == ("__confluid_lazy__",)  # type: ignore[attr-defined]


def test_is_lazy_annotation_true_for_lazy() -> None:
    assert is_lazy_annotation(Lazy[int]) is True  # type: ignore[misc]
    assert is_lazy_annotation(Lazy[Any]) is True  # type: ignore[misc]


def test_is_lazy_annotation_false_for_plain_types() -> None:
    assert is_lazy_annotation(int) is False
    assert is_lazy_annotation(Any) is False
    assert is_lazy_annotation(None) is False


def test_lazy_param_names_finds_marked_params() -> None:
    @configurable
    class _C:
        def __init__(self, x: Lazy[Any], y: int = 0, z: Lazy[Any] = None) -> None: ...

    assert lazy_param_names(_C) == {"x", "z"}


def test_lazy_param_names_empty_when_no_markers() -> None:
    @configurable
    class _C:
        def __init__(self, x: int = 0, y: str = "") -> None: ...

    assert lazy_param_names(_C) == set()


def test_lazy_param_names_handles_class_without_init() -> None:
    class _NoInit:
        pass

    # Should not raise — returns an empty set or whatever the inherited
    # ``object.__init__`` reveals (no annotated params either way).
    assert lazy_param_names(_NoInit) == set()


def test_lazy_param_names_caches_result() -> None:
    """Cached on ``__confluid_lazy_params__`` so deep-flow walkers don't re-introspect."""

    @configurable
    class _C:
        def __init__(self, x: Lazy[Any]) -> None: ...

    first = lazy_param_names(_C)
    assert _C.__confluid_lazy_params__ is first  # type: ignore[attr-defined]
    # Mutating the cache (a real walker wouldn't, but a malicious caller might)
    # is reflected on the next call — the helper trusts the cache.
    _C.__confluid_lazy_params__ = {"poisoned"}  # type: ignore[attr-defined]
    assert lazy_param_names(_C) == {"poisoned"}


def test_lazy_alias_with_typing_any() -> None:
    """``Lazy[Any]`` resolves the same as ``Lazy[T]`` with concrete T for marker detection."""

    @configurable
    class _C:
        def __init__(self, x: Lazy[Any]) -> None: ...

    assert "x" in lazy_param_names(_C)


@pytest.mark.parametrize("hint", [int, str, "not a type"])
def test_is_lazy_annotation_handles_arbitrary_input(hint: Any) -> None:
    """Helper must not raise on weird input — just return False."""
    assert is_lazy_annotation(hint) is False


# ---------------------------------------------------------------------------
# The typed alias: Lazy[T] == Annotated[Union[T, Fluid], marker], so the
# preferred spelling is the INTERFACE the slot flows into (Lazy[Optimizer]),
# with a Fluid default that now type-checks under strict mypy.
# ---------------------------------------------------------------------------


class _Base:
    pass


class _Impl(_Base):
    def __init__(self, n: int = 1) -> None:
        self.n = n


def test_lazy_typed_alias_unions_fluid() -> None:
    """``Lazy[T]`` carries the honest ``Union[T, Fluid]`` static type."""
    from typing import Union, get_args

    from confluid.fluid import Fluid

    ann = Lazy[_Base]  # type: ignore[misc]
    inner = get_args(ann)[0]  # the Annotated payload
    assert inner == Union[_Base, Fluid]
    assert ann.__metadata__ == ("__confluid_lazy__",)  # type: ignore[attr-defined]


def test_lazy_typed_slot_accepts_fluid_default() -> None:
    """The docs' preferred form — ``Lazy[Base] = Class(Impl, ...)`` — needs no
    ``type: ignore``: a ``Class`` IS a ``Fluid``, so the union admits it. The
    absence of an ignore comment here is itself the strict-mypy pin."""
    from confluid import Class

    @configurable
    class _C:
        def __init__(self, dep: Lazy[_Base] = Class(_Impl, n=2)) -> None:
            self.dep = dep

    assert lazy_param_names(_C) == {"dep"}
    from confluid import flow
    from confluid.fluid import Class as ClassFluid

    c = _C()
    assert isinstance(c.dep, ClassFluid)  # stays deferred at construction
    built = flow(c.dep)
    assert isinstance(built, _Impl) and built.n == 2


def test_optional_lazy_slot_is_detected() -> None:
    """``Optional[Lazy[T]] = None`` — the natural spelling for an optional deferred
    slot — is detected: marker detection walks Union arms (Optional included)."""
    from typing import Optional

    @configurable
    class _C:
        def __init__(self, dep: Optional[Lazy[_Base]] = None) -> None:
            self.dep = dep

    assert lazy_param_names(_C) == {"dep"}


def test_mandatory_lazy_composition_carries_both_markers() -> None:
    """``Mandatory[Lazy[T]]`` — nested Annotated flattens, both markers survive."""
    from typing import get_type_hints

    from confluid import Class
    from confluid.mandatory import Mandatory, is_mandatory_annotation, mandatory_param_names

    @configurable
    class _C:
        def __init__(self, dep: Mandatory[Lazy[_Base]] = Class(_Impl)) -> None:
            self.dep = dep

    assert lazy_param_names(_C) == {"dep"}
    assert mandatory_param_names(_C) == {"dep"}
    hint = get_type_hints(_C.__init__, include_extras=True)["dep"]
    assert is_lazy_annotation(hint) and is_mandatory_annotation(hint)


def test_lazy_typed_slot_round_trips_through_dump_load() -> None:
    """dump()→load() reconstructs a class with a typed Lazy slot (Serialization Symmetry)."""
    import sys
    import types

    from confluid import Class, LazyClass, dump, flow, load

    mod = types.ModuleType("_lazy_typed_probe")

    @configurable
    class _Owner:
        def __init__(self, dep: Lazy[_Base] = Class(_Impl, n=2), name: str = "o") -> None:
            self.dep = dep
            self.name = name

    mod._Owner = _Owner  # type: ignore[attr-defined]
    mod._Impl = _Impl  # type: ignore[attr-defined]
    sys.modules["_lazy_typed_probe"] = mod
    try:
        first = flow(
            load(
                "o: !class:_lazy_typed_probe._Owner\n"
                "  name: round\n"
                "  dep: !lazy:_lazy_typed_probe._Impl\n"
                "    n: 7\n"
            )["o"]
        )
        assert isinstance(first.dep, LazyClass)  # Lazy slot stays deferred
        reloaded = flow(load(dump(first)))
        assert reloaded.name == "round"
        assert isinstance(reloaded.dep, LazyClass)
        built = flow(reloaded.dep)
        assert isinstance(built, _Impl) and built.n == 7
    finally:
        del sys.modules["_lazy_typed_probe"]


# ---------------------------------------------------------------------------
# flow(lazy) semantics: an explicit flow() builds a Lazy (even with no runtime
# kwargs), while the auto-flow walkers (materialize / deep-flow) keep it deferred.
# ---------------------------------------------------------------------------


def test_explicit_flow_of_lazy_builds_without_runtime_kwargs() -> None:
    """A deliberate ``flow(lazy)`` call materializes it — no runtime kwargs needed.

    This is the contract a trainer relies on for a deferred slot that needs no
    runtime injection (e.g. ``flow(self.lightning)`` building a Trainer).
    """
    from confluid import LazyClass, flow

    class _Plain:
        def __init__(self, a: int = 1, b: int = 2) -> None:
            self.a = a
            self.b = b

    built = flow(LazyClass(_Plain, a=7))
    assert isinstance(built, _Plain)
    assert built.a == 7 and built.b == 2


def test_explicit_flow_of_lazy_merges_runtime_kwargs() -> None:
    """Runtime kwargs still merge (and win) when flowing a Lazy — the optimizer pattern."""
    from confluid import LazyClass, flow

    class _Opt:
        def __init__(self, params: Any, lr: float = 0.0) -> None:
            self.params = params
            self.lr = lr

    built = flow(LazyClass(_Opt, lr=0.01), params=[1, 2, 3])
    assert isinstance(built, _Opt)
    assert built.params == [1, 2, 3] and built.lr == 0.01


def test_materialize_keeps_lazy_post_init_attr_deferred() -> None:
    """A ``!lazy:`` value landing on a post-init (non-ctor) attribute stays a Lazy.

    Without this, confluid's eager post-init attr flow would try to build a
    runtime-injection slot (e.g. an optimizer) before its args exist and crash.
    """
    import sys
    import types

    from confluid import LazyClass, configurable, flow, load

    mod = types.ModuleType("_lazy_postinit_probe")

    @configurable
    class _Trainerish:
        def __init__(self, name: str = "x") -> None:
            self.name = name
            self.optimizer: Any = None  # post-init slot, not a ctor param

    mod._Trainerish = _Trainerish  # type: ignore[attr-defined]
    sys.modules["_lazy_postinit_probe"] = mod
    try:
        trainer = flow(
            load(
                "t: !class:_lazy_postinit_probe._Trainerish\n"
                "  name: t\n"
                "  optimizer: !lazy:_lazy_postinit_probe._Trainerish\n"
                "    name: inner\n"
            )["t"]
        )
        assert isinstance(trainer.optimizer, LazyClass)
        # The owning code flows it explicitly when ready.
        assert isinstance(flow(trainer.optimizer), _Trainerish)
    finally:
        del sys.modules["_lazy_postinit_probe"]


def test_class_into_lazy_default_slot_is_deferred_with_warning(monkeypatch: Any) -> None:
    """A ``!class:`` value landing in a slot whose own default is ``Lazy`` is
    auto-deferred (kept ``!lazy:``) with a warning — not eagerly built.

    Guards the minimal-ctor footgun: a deferred ``Class`` (``!class:`` no parens)
    wired into a runtime-injection body slot (e.g. ``optimizer``) would otherwise
    be eagerly materialized on assignment and crash (``Adam()`` with no params).

    The warning is asserted by patching the ENGINE module logger directly —
    loggair does not propagate into stdlib logging, so ``caplog`` cannot see it.
    """
    import sys
    import types
    from types import SimpleNamespace

    import confluid.engine as engine_module
    from confluid import LazyClass, configurable, flow, load

    warnings_seen: list[str] = []
    monkeypatch.setattr(engine_module, "logger", SimpleNamespace(warning=lambda msg: warnings_seen.append(msg)))

    mod = types.ModuleType("_lazy_slot_probe")

    @configurable
    class _Needsy:
        def __init__(self, required: Any = None, lr: float = 0.0) -> None:
            if required is None:
                raise ValueError("required")  # mimics SGD needing params
            self.required = required
            self.lr = lr

    @configurable
    class _Owner:
        def __init__(self, name: str = "x") -> None:
            self.name = name
            self.optimizer: Any = LazyClass(_Needsy, lr=0.01)  # deferred slot

    mod._Needsy = _Needsy  # type: ignore[attr-defined]
    mod._Owner = _Owner  # type: ignore[attr-defined]
    sys.modules["_lazy_slot_probe"] = mod
    try:
        owner = flow(
            load(
                "o: !class:_lazy_slot_probe._Owner\n"
                "  name: t\n"
                "  optimizer: !class:_lazy_slot_probe._Needsy\n"  # !class: footgun
                "    lr: 0.05\n"
            )["o"]
        )
        # Auto-deferred — not eagerly built — and a warning was emitted.
        assert isinstance(owner.optimizer, LazyClass)
        assert any("deferred (lazy)" in msg for msg in warnings_seen)
        # The owning code injects the runtime arg and builds it.
        built = flow(owner.optimizer, required=[1])
        assert isinstance(built, _Needsy) and built.lr == 0.05
    finally:
        del sys.modules["_lazy_slot_probe"]
