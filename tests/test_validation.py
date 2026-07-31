# mypy: disable-error-code="attr-defined,valid-type,arg-type,call-arg"
"""Tests for the ``@configurable`` validation hook + ``ValidationPolicy``.

Coverage targets:

* All three modes (``"strict"``, ``"warn"``, ``"off"``) at the ``__init__`` site
* Per-mode behaviour at the ``configure()`` (post-construction setattr) site
* Env-var driven policy initialisation
* Policy mode override during YAML materialization (``flow()`` swaps ``init``
  for ``yaml``)
* Category registration + ``list_classes(category=...)`` filter
* Validation does not re-wrap classes that are decorated twice (idempotent)
* The pydantic schema returned by ``to_pydantic`` is unchanged (no regression)
* Classes whose signatures are not introspectable are gracefully skipped
"""

from __future__ import annotations

from typing import Iterator, Optional

import pytest
from pydantic import ValidationError

import confluid
from confluid import (
    ValidationPolicy,
    configurable,
    configure,
    flow,
    get_policy,
    get_registry,
    load,
    reset_policy,
    set_policy,
    to_pydantic,
)
from confluid.fluid import Class

# ---------------------------------------------------------------------------
# Module-scope @configurable fixtures for the Fluid-marker regression tests.
#
# These can't live inside the test function: ``typing.get_type_hints`` (used
# by ``to_pydantic`` to resolve forward references) only sees names from
# module globals. A function-local ``class _Leaf`` would silently drop its
# annotation from the generated pydantic mirror, defeating the very
# is_instance_of check the regression depends on.
#
# Re-decoration during the autouse fixture's ``to_pydantic.cache_clear()`` /
# ``get_registry().clear()`` is harmless — the classes still carry their
# ``__confluid_configurable__`` marker, and ``to_pydantic`` rebuilds the
# schema on next call.
# ---------------------------------------------------------------------------


@configurable
class _FluidLeaf:
    def __init__(self, count: int = 0, label: str = "") -> None:
        self.count = count
        self.label = label


@configurable
class _FluidParent:
    def __init__(self, primary: _FluidLeaf, secondary: _FluidLeaf) -> None:
        self.primary = primary
        self.secondary = secondary


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Reset registry, pydantic cache, env vars, and the active policy."""
    # Drop env vars that could leak across tests.
    for var in ("CONFLUID_VALIDATE_INIT", "CONFLUID_VALIDATE_YAML", "CONFLUID_VALIDATE_TOOL"):
        monkeypatch.delenv(var, raising=False)
    get_registry().clear()
    to_pydantic.cache_clear()
    reset_policy()
    yield
    get_registry().clear()
    to_pydantic.cache_clear()
    reset_policy()


# ---------------------------------------------------------------------------
# __init__ validation
# ---------------------------------------------------------------------------


def test_strict_init_raises_on_bad_kwarg() -> None:
    @configurable
    class C:
        def __init__(self, n: int = 0) -> None:
            self.n = n

    with pytest.raises(ValidationError):
        C(n="not an int")


def test_strict_init_accepts_valid_kwargs() -> None:
    @configurable
    class C:
        def __init__(self, n: int = 0, s: str = "x") -> None:
            self.n = n
            self.s = s

    inst = C(n=5, s="hello")
    assert inst.n == 5
    assert inst.s == "hello"


def _capture_validation_warnings(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Patch the validation module logger with a collecting stub.

    loggair does not propagate into stdlib logging, so pytest's ``caplog``
    cannot capture it — the module logger is patched directly instead (the
    same pattern as test_configurator's typo-warning test). Asserting on the
    returned list covers positive AND negative cases without the false-green
    risk of an empty ``caplog.records``.
    """
    from types import SimpleNamespace

    import confluid.validation as validation_module

    seen: list[str] = []
    monkeypatch.setattr(validation_module, "logger", SimpleNamespace(warning=lambda msg: seen.append(msg)))
    return seen


def test_warn_init_logs_and_proceeds(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _capture_validation_warnings(monkeypatch)
    set_policy(init="warn")

    @configurable
    class C:
        def __init__(self, n: int = 0) -> None:
            # Coerce so the body doesn't itself raise on the bad value.
            self.n = n

    inst = C(n="not an int")
    assert inst.n == "not an int"  # body ran, body just accepted the value
    assert any("invalid configuration" in msg for msg in seen)
    assert any("C" in msg for msg in seen)  # the class name survives the f-string conversion


def test_off_init_skips_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _capture_validation_warnings(monkeypatch)
    set_policy(init="off")

    @configurable
    class C:
        def __init__(self, n: int = 0) -> None:
            self.n = n

    inst = C(n="not an int")
    assert inst.n == "not an int"
    assert seen == []  # no warning emitted under "off" (stub-based — no caplog false green)


def test_extra_kwarg_rejected_in_strict() -> None:
    @configurable
    class C:
        def __init__(self, n: int = 0) -> None:
            self.n = n

    with pytest.raises(ValidationError):
        C(n=1, bogus=2)


def test_validate_false_disables_wrapping() -> None:
    @configurable(validate=False)
    class C:
        def __init__(self, n: int = 0) -> None:
            self.n = n

    # No pydantic ValidationError raised even though n is the wrong type —
    # decorator did not wrap __init__ at all.
    inst = C(n="not an int")
    assert inst.n == "not an int"


def test_double_decoration_is_idempotent() -> None:
    """Re-decorating a class must not double-wrap __init__."""

    @configurable
    class C:
        def __init__(self, n: int = 0) -> None:
            self.n = n

    first_init = C.__init__
    configurable(C)
    assert C.__init__ is first_init  # same wrapper, not nested


def test_class_without_init_is_skipped() -> None:
    """Classes inheriting __init__ from object accept no kwargs — skip wrapping."""

    @configurable
    class C:
        pass

    # Should not raise: there are no kwargs to validate, and the un-wrapped
    # object.__init__ is what gets called.
    C()


def test_no_typehints_class_skipped_gracefully() -> None:
    """A class whose __init__ has no annotations must not crash the wrapper."""

    @configurable
    class C:
        def __init__(self, n=0):  # type: ignore[no-untyped-def]
            self.n = n

    # Pydantic treats unannotated params as Any, so anything goes.
    inst = C(n="whatever")
    assert inst.n == "whatever"


# ---------------------------------------------------------------------------
# configure() setattr validation
# ---------------------------------------------------------------------------


def test_configure_setattr_strict_raises() -> None:
    @configurable
    class C:
        def __init__(self, n: int = 0) -> None:
            self.n = n

    inst = C(n=1)
    with pytest.raises(ValidationError):
        configure(inst, config={"C": {"n": "not an int"}})


def test_configure_setattr_warn_proceeds(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _capture_validation_warnings(monkeypatch)
    set_policy(init="warn")

    @configurable
    class C:
        def __init__(self, n: int = 0) -> None:
            self.n = n

    inst = C(n=1)
    configure(inst, config={"C": {"n": "not an int"}})
    assert inst.n == "not an int"
    assert any("invalid value" in msg for msg in seen)


def test_configure_unknown_attr_silent() -> None:
    """Attributes outside the __init__ signature are not validated."""

    @configurable
    class C:
        def __init__(self, n: int = 0) -> None:
            self.n = n
            self.extra = "default"

    inst = C(n=1)
    configure(inst, config={"C": {"extra": 12345}})
    assert inst.extra == 12345


# ---------------------------------------------------------------------------
# YAML materialization honours policy.yaml
# ---------------------------------------------------------------------------


def test_yaml_materialization_uses_yaml_mode() -> None:
    """A bad YAML value triggers ``policy.yaml``, not ``policy.init``."""
    set_policy(init="off", yaml="strict")

    @configurable(name="YamlValidatedClass")
    class C:
        def __init__(self, n: int = 0) -> None:
            self.n = n

    yaml_doc = """
obj: !class:YamlValidatedClass
  n: not_an_int
"""
    loaded = load(yaml_doc)
    # ``init='off'`` would let any value through if flow() didn't swap modes;
    # the YAML path must surface this as an error under ``yaml='strict'``.
    with pytest.raises(Exception) as excinfo:
        flow(loaded["obj"])
    assert "YamlValidatedClass" in str(excinfo.value) or "validation" in str(excinfo.value).lower()


def test_yaml_warn_logs_but_proceeds(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _capture_validation_warnings(monkeypatch)
    set_policy(init="off", yaml="warn")

    @configurable(name="YamlWarnClass")
    class C:
        def __init__(self, n: int = 0) -> None:
            self.n = n  # accepts whatever pydantic let through

    yaml_doc = """
obj: !class:YamlWarnClass
  n: not_an_int
"""
    loaded = load(yaml_doc)
    result = flow(loaded["obj"])
    # WARN allows the constructor to run; the bad value flows through.
    assert result.n == "not_an_int"
    assert any("invalid configuration" in msg for msg in seen)


def test_yaml_off_skips_validation_even_when_init_strict(monkeypatch: pytest.MonkeyPatch) -> None:
    """Inverse asymmetry: yaml='off' wins over init='strict' inside flow()."""
    seen = _capture_validation_warnings(monkeypatch)
    set_policy(init="strict", yaml="off")

    @configurable(name="YamlOffClass")
    class C:
        def __init__(self, n: int = 0) -> None:
            self.n = n

    yaml_doc = """
obj: !class:YamlOffClass
  n: not_an_int
"""
    loaded = load(yaml_doc)
    result = flow(loaded["obj"])
    assert result.n == "not_an_int"
    assert seen == []  # stub-based negative assertion — no caplog false green


# ---------------------------------------------------------------------------
# Env-var initialization
# ---------------------------------------------------------------------------


def test_env_var_init_overrides_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONFLUID_VALIDATE_INIT", "warn")
    reset_policy()
    assert get_policy().init == "warn"


def test_env_var_yaml_independent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONFLUID_VALIDATE_YAML", "off")
    reset_policy()
    p = get_policy()
    assert p.init == "strict"
    assert p.yaml == "off"
    assert p.tool == "strict"


def test_env_var_invalid_value_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONFLUID_VALIDATE_INIT", "loud")
    reset_policy()
    with pytest.raises(ValueError):
        get_policy()


def test_set_policy_partial_update() -> None:
    set_policy(init="warn")
    p = get_policy()
    assert p.init == "warn"
    assert p.yaml == "strict"  # untouched


# ---------------------------------------------------------------------------
# Category support on the registry
# ---------------------------------------------------------------------------


def test_category_registered_and_filterable() -> None:
    @configurable(category="loss")
    class CE:
        def __init__(self, label_smoothing: float = 0.0) -> None:
            self.label_smoothing = label_smoothing

    @configurable(category="model")
    class Net:
        def __init__(self, depth: int = 4) -> None:
            self.depth = depth

    @configurable
    class Misc:
        def __init__(self) -> None:
            pass

    reg = get_registry()
    assert reg.list_classes(category="loss") == {"CE"}
    assert reg.list_classes(category="model") == {"Net"}
    assert reg.list_classes(category="missing") == set()
    # No-arg call still returns all classes
    assert {"CE", "Net", "Misc"}.issubset(reg.list_classes())
    # Category index visible
    assert reg.list_categories() == {"loss", "model"}
    # Class attribute set
    assert getattr(CE, "__confluid_category__") == "loss"


# ---------------------------------------------------------------------------
# to_pydantic regression — schema generation untouched
# ---------------------------------------------------------------------------


def test_to_pydantic_still_returns_a_cached_schema() -> None:
    @configurable
    class C:
        def __init__(self, n: int = 0, label: Optional[str] = None) -> None:
            self.n = n
            self.label = label

    model = to_pydantic(C)
    again = to_pydantic(C)
    assert model is again
    inst = model(n=3, label="x")
    assert inst.n == 3
    assert inst.label == "x"


def test_default_policy_strict_for_all_levels() -> None:
    p = ValidationPolicy()
    assert p.init == p.yaml == p.tool == "strict"


def test_confluid_validation_exports_available() -> None:
    """The user-facing policy knobs stay on the top-level ``confluid`` package;
    the validation MACHINERY (``validate_kwargs``/``validate_setattr``/
    ``override_init_mode``) is deliberately NOT re-exported (2026-07 API
    pruning) — it lives in ``confluid.validation``."""
    for name in (
        "ValidationMode",
        "ValidationPolicy",
        "get_policy",
        "set_policy",
        "reset_policy",
        "validate_model",
    ):
        assert hasattr(confluid, name), f"missing export: {name}"
    for internal in ("override_init_mode", "validate_kwargs", "validate_setattr"):
        assert internal not in confluid.__all__, f"internal machinery re-exported: {internal}"


# -------------------------------------------------------------------------
# Fluid-marker kwargs — validation must skip deferred values
# -------------------------------------------------------------------------
#
# Regression cluster for the bug discovered when a YAML config with a nested
# ``!class:_Leaf`` flowed into a ``_Parent(@configurable)``. flow() passes the
# inner ``Class`` marker through to ``_Parent(**ctor)`` so the wrapped __init__
# can flow it lazily with runtime injection. The pydantic validator was
# applying ``is_instance_of(_Leaf)`` to the Class marker and rejecting
# instantiation outright — but Fluids are deferred-construction markers, not
# bad values; the inner ``_Leaf.__init__`` (also @configurable) will validate
# itself when the marker flows. validate_kwargs MUST therefore skip Fluid
# kwargs while still checking concrete kwargs alongside them.


def test_fluid_kwarg_skipped_in_strict() -> None:
    """A deferred Class marker as a kwarg value must not trip strict validation.

    Function-local classes work here because pydantic-lax-mode validation
    of ``title: str`` doesn't depend on a forward-ref lookup. The
    module-scope variant in :func:`test_fluid_kwarg_skipped_in_strict_module_scope`
    covers the stricter ``is_instance_of(_Leaf)`` path.
    """

    @configurable
    class _Leaf:
        def __init__(self, count: int = 0) -> None:
            self.count = count

    @configurable
    class _Parent:
        def __init__(self, title: str, leaf: _Leaf) -> None:
            self.title = title
            self.leaf = leaf

    set_policy(init="strict")
    leaf_marker = Class(_Leaf, count=99)
    # Pass the Class marker — pydantic's annotation would normally reject it
    # if it were resolvable. validate_kwargs must skip Fluids regardless.
    instance = _Parent(title="hello", leaf=leaf_marker)  # type: ignore[arg-type]
    assert instance.title == "hello"
    assert isinstance(instance.leaf, Class)  # still deferred


def test_fluid_kwarg_alongside_bad_concrete_still_raises() -> None:
    """Mixed bag: Fluid for one field, wrong-instance for another → the wrong-instance still raises.

    Skipping Fluids must not cascade into skipping everything. A non-Fluid
    kwarg whose value isn't an instance of the annotated class is the
    concrete configuration error this validation layer exists to catch.

    The fixtures live at module scope (see ``_FluidLeaf`` / ``_FluidParent``
    above) because ``typing.get_type_hints`` — used by ``to_pydantic`` to
    resolve forward references — only sees class names in module globals,
    not in a test function's local scope. Locally-defined classes would
    silently lose their ``_Leaf`` annotation and the strict
    ``is_instance_of`` check we depend on for the regression wouldn't fire.
    """
    set_policy(init="strict")
    secondary_marker = Class(_FluidLeaf, count=99)  # deferred — must be skipped
    with pytest.raises(ValidationError):
        _FluidParent(primary="not-a-leaf", secondary=secondary_marker)  # type: ignore[arg-type]


def test_fluid_kwarg_skipped_in_strict_module_scope() -> None:
    """Module-scope variant of test_fluid_kwarg_skipped_in_strict.

    The earlier function-local version works because pydantic only enforces
    its annotation when ``get_type_hints`` can resolve it. The module-scope
    version exercises the path where the annotation IS resolved — i.e. the
    pydantic ``is_instance_of`` check actively runs against the Fluid marker
    and must be skipped by ``validate_kwargs``.
    """
    set_policy(init="strict")
    leaf_marker = Class(_FluidLeaf, count=99)
    instance = _FluidParent(primary=_FluidLeaf(count=1), secondary=leaf_marker)  # type: ignore[arg-type]
    assert instance.primary.count == 1
    assert isinstance(instance.secondary, Class)


def test_fluid_kwarg_in_yaml_load_does_not_explode() -> None:
    """End-to-end: YAML with a nested !class:_Leaf inside !class:_Parent loads cleanly.

    Reproduces the exact failure mode the user saw running
    ``liquifai/tests/test_help_with_config.py::test_liquify_and_show_end_to_end``
    against confluid pre-fix.
    """
    import sys

    # Confluid's `!class:` loader resolves classes via importable module
    # paths. Alias the test module under a stable name so the YAML below can
    # reference the module-scope _FluidLeaf / _FluidParent classes.
    sys.modules["test_validation_fluid_module"] = sys.modules[__name__]
    try:
        yaml_text = """
parent:
  !class:test_validation_fluid_module._FluidParent
  primary:
    !class:test_validation_fluid_module._FluidLeaf
    count: 1
    label: "primary"
  secondary:
    !class:test_validation_fluid_module._FluidLeaf
    count: 99
    label: "secondary"
"""
        result = load(yaml_text)
        # The single point of the regression: ``flow()`` must not crash with
        # ``ValidationError: primary.is-instance[_FluidLeaf]`` (or the masked
        # ``TypeError: ValidationError.__new__() missing 1 required positional
        # argument: 'line_errors'``) just because the nested ``Class(_FluidLeaf, …)``
        # markers haven't been materialised yet. ``@configurable`` targets keep
        # nested Class kwargs deferred by design; the wrapped __init__ is
        # responsible for flowing them lazily later. Asserting only that the
        # outer flow() returns a live instance tests the bug without depending
        # on whether the test's _FluidParent happens to flow its children eagerly.
        parent = flow(result["parent"])
        assert isinstance(parent, _FluidParent)
    finally:
        sys.modules.pop("test_validation_fluid_module", None)


# -------------------------------------------------------------------------
# fluid.flow() re-raise must tolerate ValidationError
# -------------------------------------------------------------------------


@configurable
class _StoreLeaf:
    def __init__(self, name: str = "") -> None:
        self.name = name


@configurable
class _StoreParent:
    """List-of-stores receiver, like ``CompositeAnnotationStore(stores=[...])``."""

    def __init__(self, stores: list) -> None:  # type: ignore[type-arg]
        self.stores = stores


@configurable
class _MappingParent:
    """Dict-of-stores receiver — the second container shape confluid YAMLs use."""

    def __init__(self, stores: dict) -> None:  # type: ignore[type-arg]
        self.stores = stores


def test_fluid_nested_in_list_kwarg_is_skipped() -> None:
    """Fluid markers inside a ``list`` value must defer validation of the whole field.

    Regression: ``CompositeAnnotationStore(stores=[Class(_Store, ...)])`` —
    the list itself is concrete, but the inner ``Class`` marker would trip
    ``is_instance_of(_Store)`` on the per-element validator. The whole field
    has to be treated as deferred.
    """
    set_policy(init="strict")
    fluid_leaf = Class(_StoreLeaf, name="deferred")
    instance = _StoreParent(stores=[fluid_leaf])
    assert instance.stores == [fluid_leaf]


def test_fluid_nested_in_dict_kwarg_is_skipped() -> None:
    """Same as the list case, but for dict values — covers the
    ``data={"train": Class(...), "val": Class(...)}`` shape used by
    ultralytics-style trainer configs.
    """
    set_policy(init="strict")
    fluid_leaf = Class(_StoreLeaf, name="deferred")
    instance = _MappingParent(stores={"train": fluid_leaf})
    assert instance.stores == {"train": fluid_leaf}


def test_flow_wraps_validation_error_in_runtime_error() -> None:
    """flow() re-raises construction failures with a message that names the
    target class. The naive ``raise type(exc)(msg) from exc`` pattern breaks
    for pydantic's :class:`ValidationError` (whose ``__new__`` demands
    ``line_errors``); the fallback must wrap it in a ``RuntimeError`` rather
    than crashing with ``TypeError: __new__() missing 1 required positional argument``.
    """
    from confluid.fluid import Class

    @configurable
    class _StrictModel:
        def __init__(self, count: int) -> None:
            self.count = count

    set_policy(init="strict")
    marker = Class(_StrictModel, count="not-an-int")
    # The raw underlying ValidationError can't be reconstructed from a string;
    # the fallback path raises RuntimeError instead. Either way, the original
    # ValidationError chains via __cause__ so the structured info isn't lost.
    with pytest.raises((ValidationError, RuntimeError)) as info:
        flow(marker)
    # Whichever path fired, the original pydantic error must be one of:
    #   - the exception itself (type-preserving path succeeded — modern pydantic)
    #   - the .__cause__ (fallback wrapped it)
    raised = info.value
    chained = isinstance(raised, ValidationError) or isinstance(raised.__cause__, ValidationError)
    assert (
        chained
    ), f"expected ValidationError in chain, got {type(raised).__name__} → {type(raised.__cause__).__name__}"
