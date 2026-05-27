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

import logging
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


def test_warn_init_logs_and_proceeds(caplog: pytest.LogCaptureFixture) -> None:
    set_policy(init="warn")

    @configurable
    class C:
        def __init__(self, n: int = 0) -> None:
            # Coerce so the body doesn't itself raise on the bad value.
            self.n = n

    with caplog.at_level(logging.WARNING, logger="confluid.validation"):
        inst = C(n="not an int")
    assert inst.n == "not an int"  # body ran, body just accepted the value
    assert any("invalid configuration" in rec.message for rec in caplog.records)


def test_off_init_skips_validation(caplog: pytest.LogCaptureFixture) -> None:
    set_policy(init="off")

    @configurable
    class C:
        def __init__(self, n: int = 0) -> None:
            self.n = n

    with caplog.at_level(logging.WARNING, logger="confluid.validation"):
        inst = C(n="not an int")
    assert inst.n == "not an int"
    assert not caplog.records  # no warning emitted under "off"


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


def test_configure_setattr_warn_proceeds(caplog: pytest.LogCaptureFixture) -> None:
    set_policy(init="warn")

    @configurable
    class C:
        def __init__(self, n: int = 0) -> None:
            self.n = n

    inst = C(n=1)
    with caplog.at_level(logging.WARNING, logger="confluid.validation"):
        configure(inst, config={"C": {"n": "not an int"}})
    assert inst.n == "not an int"
    assert any("invalid value" in rec.message for rec in caplog.records)


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


def test_yaml_warn_logs_but_proceeds(caplog: pytest.LogCaptureFixture) -> None:
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
    with caplog.at_level(logging.WARNING, logger="confluid.validation"):
        result = flow(loaded["obj"])
    # WARN allows the constructor to run; the bad value flows through.
    assert result.n == "not_an_int"
    assert any("invalid configuration" in rec.message for rec in caplog.records)


def test_yaml_off_skips_validation_even_when_init_strict(caplog: pytest.LogCaptureFixture) -> None:
    """Inverse asymmetry: yaml='off' wins over init='strict' inside flow()."""
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
    with caplog.at_level(logging.WARNING, logger="confluid.validation"):
        result = flow(loaded["obj"])
    assert result.n == "not_an_int"
    assert not caplog.records


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
    """The new symbols are accessible from the top-level ``confluid`` package."""
    for name in (
        "ValidationMode",
        "ValidationPolicy",
        "get_policy",
        "set_policy",
        "reset_policy",
        "override_init_mode",
        "validate_kwargs",
        "validate_setattr",
        "validate_model",
    ):
        assert hasattr(confluid, name), f"missing export: {name}"
