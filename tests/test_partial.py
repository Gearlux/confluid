"""Unit tests for ``confluid.Partial`` and ``partial_param_names``."""

from typing import Any

import pytest

from confluid import Partial, PartialClass, configurable, partial_param_names
from confluid.partial import is_partial_annotation


def test_lazy_marker_metadata() -> None:
    """``Partial[T].__metadata__`` carries the confluid sentinel."""
    ann = Partial[int]  # type: ignore[misc]
    assert ann.__metadata__ == ("__confluid_partial__",)  # type: ignore[attr-defined]


def test_is_partial_annotation_true_for_lazy() -> None:
    assert is_partial_annotation(Partial[int]) is True  # type: ignore[misc]
    assert is_partial_annotation(Partial[Any]) is True  # type: ignore[misc]


def test_is_partial_annotation_false_for_plain_types() -> None:
    assert is_partial_annotation(int) is False
    assert is_partial_annotation(Any) is False
    assert is_partial_annotation(None) is False


def test_partial_param_names_finds_marked_params() -> None:
    @configurable
    class _C:
        def __init__(self, x: Partial[Any], y: int = 0, z: Partial[Any] = None) -> None: ...

    assert partial_param_names(_C) == {"x", "z"}


def test_partial_param_names_empty_when_no_markers() -> None:
    @configurable
    class _C:
        def __init__(self, x: int = 0, y: str = "") -> None: ...

    assert partial_param_names(_C) == set()


def test_partial_param_names_handles_class_without_init() -> None:
    class _NoInit:
        pass

    # Should not raise — returns an empty set or whatever the inherited
    # ``object.__init__`` reveals (no annotated params either way).
    assert partial_param_names(_NoInit) == set()


def test_partial_param_names_caches_result() -> None:
    """Cached on ``__confluid_partial_params__`` so deep-flow walkers don't re-introspect."""

    @configurable
    class _C:
        def __init__(self, x: Partial[Any]) -> None: ...

    first = partial_param_names(_C)
    assert _C.__confluid_partial_params__ is first  # type: ignore[attr-defined]
    # Mutating the cache (a real walker wouldn't, but a malicious caller might)
    # is reflected on the next call — the helper trusts the cache.
    _C.__confluid_partial_params__ = {"poisoned"}  # type: ignore[attr-defined]
    assert partial_param_names(_C) == {"poisoned"}


def test_lazy_alias_with_typing_any() -> None:
    """``Partial[Any]`` resolves the same as ``Partial[T]`` with concrete T for marker detection."""

    @configurable
    class _C:
        def __init__(self, x: Partial[Any]) -> None: ...

    assert "x" in partial_param_names(_C)


@pytest.mark.parametrize("hint", [int, str, "not a type"])
def test_is_partial_annotation_handles_arbitrary_input(hint: Any) -> None:
    """Helper must not raise on weird input — just return False."""
    assert is_partial_annotation(hint) is False


# ---------------------------------------------------------------------------
# The typed alias: Partial[T] == Annotated[Union[T, Fluid], marker], so the
# preferred spelling is the INTERFACE the slot flows into (Partial[Optimizer]),
# with a Fluid default that now type-checks under strict mypy.
# ---------------------------------------------------------------------------


class _Base:
    pass


class _Impl(_Base):
    def __init__(self, n: int = 1) -> None:
        self.n = n


def test_lazy_typed_alias_unions_fluid() -> None:
    """``Partial[T]`` carries the honest ``Union[T, Fluid]`` static type."""
    from typing import Union, get_args

    from confluid.fluid import Fluid

    ann = Partial[_Base]  # type: ignore[misc]
    inner = get_args(ann)[0]  # the Annotated payload
    assert inner == Union[_Base, Fluid]
    assert ann.__metadata__ == ("__confluid_partial__",)  # type: ignore[attr-defined]


def test_lazy_typed_slot_accepts_fluid_default() -> None:
    """The docs' preferred form — ``Partial[Base] = Target(Impl, ...)`` — needs no
    ``type: ignore``: a ``Target`` IS a ``Fluid``, so the union admits it. The
    absence of an ignore comment here is itself the strict-mypy pin."""
    from confluid import Target

    @configurable
    class _C:
        def __init__(self, dep: Partial[_Base] = Target(_Impl, n=2)) -> None:
            self.dep = dep

    assert partial_param_names(_C) == {"dep"}
    from confluid import flow
    from confluid.fluid import Target as ClassFluid

    c = _C()
    assert isinstance(c.dep, ClassFluid)  # stays deferred at construction
    built = flow(c.dep)
    assert isinstance(built, _Impl) and built.n == 2


def test_optional_lazy_slot_is_detected() -> None:
    """``Optional[Partial[T]] = None`` — the natural spelling for an optional deferred
    slot — is detected: marker detection walks Union arms (Optional included)."""
    from typing import Optional

    @configurable
    class _C:
        def __init__(self, dep: Optional[Partial[_Base]] = None) -> None:
            self.dep = dep

    assert partial_param_names(_C) == {"dep"}


def test_mandatory_lazy_composition_carries_both_markers() -> None:
    """``Mandatory[Partial[T]]`` — nested Annotated flattens, both markers survive."""
    from typing import get_type_hints

    from confluid import Target
    from confluid.mandatory import Mandatory, is_mandatory_annotation, mandatory_param_names

    @configurable
    class _C:
        def __init__(self, dep: Mandatory[Partial[_Base]] = Target(_Impl)) -> None:
            self.dep = dep

    assert partial_param_names(_C) == {"dep"}
    assert mandatory_param_names(_C) == {"dep"}
    hint = get_type_hints(_C.__init__, include_extras=True)["dep"]
    assert is_partial_annotation(hint) and is_mandatory_annotation(hint)


def test_lazy_typed_slot_round_trips_through_dump_load() -> None:
    """dump()→load() reconstructs a class with a typed Partial slot (Serialization Symmetry)."""
    import sys
    import types

    from confluid import PartialClass, Target, dump, flow, load

    mod = types.ModuleType("_lazy_typed_probe")

    @configurable
    class _Owner:
        def __init__(self, dep: Partial[_Base] = Target(_Impl, n=2), name: str = "o") -> None:
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
        assert isinstance(first.dep, PartialClass)  # Partial slot stays deferred
        reloaded = flow(load(dump(first)))
        assert reloaded.name == "round"
        assert isinstance(reloaded.dep, PartialClass)
        built = flow(reloaded.dep)
        assert isinstance(built, _Impl) and built.n == 7
    finally:
        del sys.modules["_lazy_typed_probe"]


# ---------------------------------------------------------------------------
# flow(lazy) semantics: an explicit flow() builds a Partial (even with no runtime
# kwargs), while the auto-flow walkers (materialize / deep-flow) keep it deferred.
# ---------------------------------------------------------------------------


def test_explicit_flow_of_lazy_builds_without_runtime_kwargs() -> None:
    """A deliberate ``flow(lazy)`` call materializes it — no runtime kwargs needed.

    This is the contract a trainer relies on for a deferred slot that needs no
    runtime injection (e.g. ``flow(self.lightning)`` building a Trainer).
    """
    from confluid import PartialClass, flow

    class _Plain:
        def __init__(self, a: int = 1, b: int = 2) -> None:
            self.a = a
            self.b = b

    built = flow(PartialClass(_Plain, a=7))
    assert isinstance(built, _Plain)
    assert built.a == 7 and built.b == 2


def test_explicit_flow_of_lazy_merges_runtime_kwargs() -> None:
    """Runtime kwargs still merge (and win) when flowing a Partial — the optimizer pattern."""
    from confluid import PartialClass, flow

    class _Opt:
        def __init__(self, params: Any, lr: float = 0.0) -> None:
            self.params = params
            self.lr = lr

    built = flow(PartialClass(_Opt, lr=0.01), params=[1, 2, 3])
    assert isinstance(built, _Opt)
    assert built.params == [1, 2, 3] and built.lr == 0.01


def test_materialize_keeps_lazy_post_init_attr_deferred() -> None:
    """A ``!lazy:`` value landing on a post-init (non-ctor) attribute stays a Partial.

    Without this, confluid's eager post-init attr flow would try to build a
    runtime-injection slot (e.g. an optimizer) before its args exist and crash.
    """
    import sys
    import types

    from confluid import PartialClass, configurable, flow, load

    mod = types.ModuleType("_lazy_postinit_probe")

    @configurable
    class _LazyPostInitTrainer:
        def __init__(self, name: str = "x") -> None:
            self.name = name
            self.optimizer: Any = None  # post-init slot, not a ctor param

    mod._LazyPostInitTrainer = _LazyPostInitTrainer  # type: ignore[attr-defined]
    sys.modules["_lazy_postinit_probe"] = mod
    try:
        trainer = flow(
            load(
                "t: !class:_lazy_postinit_probe._LazyPostInitTrainer\n"
                "  name: t\n"
                "  optimizer: !lazy:_lazy_postinit_probe._LazyPostInitTrainer\n"
                "    name: inner\n"
            )["t"]
        )
        assert isinstance(trainer.optimizer, PartialClass)
        # The owning code flows it explicitly when ready.
        assert isinstance(flow(trainer.optimizer), _LazyPostInitTrainer)
    finally:
        del sys.modules["_lazy_postinit_probe"]


def test_class_into_lazy_default_slot_is_deferred_with_warning(monkeypatch: Any) -> None:
    """A ``!class:`` value landing in a slot whose own default is ``Partial`` is
    auto-deferred (kept ``!lazy:``) with a warning — not eagerly built.

    Guards the minimal-ctor footgun: a deferred ``Target`` (``!class:`` no parens)
    wired into a runtime-injection body slot (e.g. ``optimizer``) would otherwise
    be eagerly materialized on assignment and crash (``Adam()`` with no params).

    The warning is asserted by patching the ENGINE module logger directly —
    loggair does not propagate into stdlib logging, so ``caplog`` cannot see it.
    """
    import sys
    import types
    from types import SimpleNamespace

    import confluid.engine as engine_module
    from confluid import PartialClass, configurable, flow, load

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
            self.optimizer: Any = PartialClass(_Needsy, lr=0.01)  # deferred slot

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
        assert isinstance(owner.optimizer, PartialClass)
        assert any("deferred runtime-injection" in msg and "_partial_: true" in msg for msg in warnings_seen)
        # The owning code injects the runtime arg and builds it.
        built = flow(owner.optimizer, required=[1])
        assert isinstance(built, _Needsy) and built.lr == 0.05
    finally:
        del sys.modules["_lazy_slot_probe"]


# --------------------------------------------------------------------------- #
# Body slots — a deferred slot declared in the __init__ BODY, not the signature
# --------------------------------------------------------------------------- #
# A class with many deferred dependencies takes a minimal constructor and assigns
# the rest as body attributes (AGENTS rule 4). Scanning only the signature made the
# marker load-bearing on params and decorative in the body: a trainer whose deferred
# slots ALL live in the body reported an empty set while annotating every one of them.


def test_a_lazy_annotated_body_slot_is_reported() -> None:
    from confluid import PartialClass, configurable

    @configurable
    class _T:
        def __init__(self) -> None:
            self.optimizer: Partial[Any] = PartialClass(dict)

    assert partial_param_names(_T) == {"optimizer"}


def test_a_body_slot_lazy_by_VALUE_is_reported_too() -> None:
    """`PartialClass(...)` says "deferred" even when the annotation does not."""
    from confluid import PartialClass, configurable

    @configurable
    class _T:
        def __init__(self) -> None:
            self.optimizer: Any = PartialClass(dict)

    assert partial_param_names(_T) == {"optimizer"}


def test_a_plain_body_slot_is_not_lazy() -> None:
    from confluid import configurable

    @configurable
    class _T:
        def __init__(self) -> None:
            self.batch_size: int = 32

    assert partial_param_names(_T) == set()


def test_params_and_body_slots_are_unioned() -> None:
    """The realistic shape: a required input in the signature, infra in the body."""
    from confluid import PartialClass, configurable

    @configurable
    class _T:
        def __init__(self, model: Partial[Any] = None, batch_size: int = 8) -> None:
            self.model = model
            self.batch_size = batch_size
            self.optimizer: Partial[Any] = PartialClass(dict)
            self.lightning: Any = PartialClass(list)

    assert partial_param_names(_T) == {"model", "optimizer", "lightning"}


def test_body_slots_are_collected_across_the_configurable_mro() -> None:
    from confluid import PartialClass, configurable

    @configurable
    class _Base:
        def __init__(self) -> None:
            self.optimizer: Partial[Any] = PartialClass(dict)

    @configurable
    class _Child(_Base):
        def __init__(self) -> None:
            super().__init__()
            self.lightning: Partial[Any] = PartialClass(list)

    assert partial_param_names(_Child) == {"optimizer", "lightning"}


def test_an_unresolvable_body_annotation_degrades_rather_than_raising() -> None:
    """Best-effort: an annotation that will not evaluate is simply not lazy."""
    from confluid import configurable

    @configurable
    class _T:
        def __init__(self) -> None:
            self.thing: "NoSuchTypeAnywhere" = 1  # type: ignore[name-defined]  # noqa: F821

    assert partial_param_names(_T) == set()


def test_partial_param_names_is_the_one_deferred_slot_authority() -> None:
    """The class-side scan is THE answer — the generated-model stamp that used to
    mirror it (``partial_param_names_of`` / ``_confluid_lazy_params``) was a parallel
    mechanism nobody consumed and was removed 2026-08-13; a generated model carries
    no lazy metadata to drift from this."""
    from confluid import PartialClass, configurable
    from confluid.pydantic_export import to_pydantic

    @configurable
    class _T:
        def __init__(self) -> None:
            self.optimizer: Partial[Any] = PartialClass(dict)

    model = to_pydantic(_T)
    assert partial_param_names(_T) == {"optimizer"}
    assert not hasattr(model, "_confluid_lazy_params")


def test_lazy_param_cache_is_per_class_never_inherited() -> None:
    """The per-class cache must not leak across the MRO — in either direction.

    It was read with ``getattr``, which walks the MRO: a subclass queried after
    its parent returned the PARENT's stamped answer (measured: a Sub declaring
    ``opt: Partial[Any]`` reported ``set()`` because Base was queried first), and a
    subclass overriding ``__init__`` withOUT markers inherited the parent's
    non-empty set. The read is now the class's OWN ``__dict__``, the same guard
    the registry uses for ``__confluid_name__``.
    """

    class _Base:
        def __init__(self, x: int = 1) -> None: ...

    class _Sub(_Base):
        def __init__(self, opt: Partial[Any] = None, x: int = 1) -> None: ...

    assert partial_param_names(_Base) == set()  # parent primed FIRST — the failing order
    assert partial_param_names(_Sub) == {"opt"}

    class _Marked:
        def __init__(self, opt: Partial[Any] = None) -> None: ...

    class _Plain(_Marked):
        def __init__(self, plain: int = 3) -> None: ...

    assert partial_param_names(_Marked) == {"opt"}
    assert partial_param_names(_Plain) == set()  # its own __init__ declares no Partial slot


def test_partial_param_names_reads_a_builder_functions_own_signature() -> None:
    """A registered builder FUNCTION's ``Partial[...]`` params are reported.

    The scan used to read ``getattr(target, "__init__")`` — for a function
    that is ``object.__init__`` (``*args, **kwargs``), so an identical
    annotation was reported on a class and silently DROPPED on a function
    (measured: ``{'model'}`` vs ``set()``). The one dispatch is
    ``introspect.marked_param_names`` via ``init_callable``.
    """

    def builder(model: Partial[Any] = None, weights: str = "x") -> object:
        return object()

    class Cls:
        def __init__(self, model: Partial[Any] = None) -> None:
            self.model = model

    assert partial_param_names(builder) == {"model"} == partial_param_names(Cls)


# --------------------------------------------------------------------------- #
# The deprecated aliases are GONE (phase 5)
# --------------------------------------------------------------------------- #


def test_the_deprecated_aliases_are_removed() -> None:
    """`Class` / `Instance` / `Lazy` / `LazyClass` / `lazy_param_names` no longer exist.

    They were the pre-merge / pre-rename spellings, kept while both YAML formats were
    supported so consumers need not change in the same release. The whole workspace is
    on the canonical names now, so an import of the old one must fail LOUDLY rather
    than resolve to something that quietly behaves differently.
    """
    import confluid

    for gone in ("Class", "Instance", "Lazy", "LazyClass", "lazy_param_names"):
        assert not hasattr(confluid, gone), f"{gone} should have been deleted"
        assert gone not in confluid.__all__

    with pytest.raises(ImportError):
        import confluid.lazy  # type: ignore[import-not-found]  # noqa: F401 — deleted with the names


def test_one_marker_type_two_modes() -> None:
    """What replaced the eager/deferred PAIR: one class, and `partial` decides."""
    from confluid.fluid import Partial, Target

    assert Target(int).partial is False
    assert Partial(int).partial is True
    assert isinstance(Partial(int), Target)  # Partial IS a Target that is not built


def test_the_body_slot_scan_matches_the_canonical_call_names_only() -> None:
    """The scan matches on the call NAME in the SOURCE, which is why the list is pinned.

    Dropping a name here does not raise — the slot is simply built, and an optimizer
    reaches its constructor without the params it was waiting for. That is what made
    removing the deprecated spellings a real change rather than a tidy-up.
    """
    from confluid.introspect import _PARTIAL_CALL_NAMES

    assert _PARTIAL_CALL_NAMES == ("PartialClass", "Partial")

    @configurable
    class _Holder:
        def __init__(self, n: int = 1) -> None:
            self.n = n
            self.slot = PartialClass(int)

    assert "slot" in partial_param_names(_Holder)
