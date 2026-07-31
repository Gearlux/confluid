"""Tests for the typed exception hierarchy (``confluid.exceptions``).

Two contracts are pinned here:

1. **Dual inheritance** — every concrete exception subclasses both the
   :class:`confluid.ConfluidError` root AND the builtin it semantically
   replaces, so pre-existing ``except ValueError`` / ``except RuntimeError``
   call sites (and ``pytest.raises(ValueError)`` in older tests) keep working.
2. **Raise sites** — each error condition empirically raises the new type.
"""

from pathlib import Path
from typing import Any, Type

import pytest

import confluid
from confluid import (
    CircularIncludeError,
    Class,
    ConfigFileNotFoundError,
    ConfigurableDefinitionError,
    ConfigurationError,
    ConfluidError,
    ConstructionError,
    IntrospectionError,
    Reference,
    ReferenceResolutionError,
    ScopeError,
    UnknownClassError,
    ValidationModeError,
    WorkspaceEnvError,
    configurable,
    flow,
    get_registry,
)
from confluid.env import load_workspace_env
from confluid.scopes import _resolve_aliases
from confluid.validation import _normalize_mode


@pytest.fixture(autouse=True)
def setup_registry() -> None:
    get_registry().clear()

    @configurable
    class Model:
        def __init__(self, layers: int = 3) -> None:
            self.layers = layers


# ---------------------------------------------------------------------------
# 1. Hierarchy contract
# ---------------------------------------------------------------------------

HIERARCHY = [
    (ConfigurationError, ValueError),
    (CircularIncludeError, ValueError),
    (ReferenceResolutionError, ValueError),
    (UnknownClassError, ValueError),
    (ConfigurableDefinitionError, ValueError),
    (ValidationModeError, ValueError),
    (ScopeError, ValueError),
    (ConfigFileNotFoundError, FileNotFoundError),
    (ConstructionError, RuntimeError),
    (WorkspaceEnvError, RuntimeError),
    (IntrospectionError, TypeError),
]


@pytest.mark.parametrize("exc_cls,builtin", HIERARCHY)
def test_dual_inheritance(exc_cls: Type[Exception], builtin: Type[Exception]) -> None:
    assert issubclass(exc_cls, ConfluidError)
    assert issubclass(exc_cls, builtin)


@pytest.mark.parametrize(
    "exc_cls",
    [
        CircularIncludeError,
        ReferenceResolutionError,
        UnknownClassError,
        ConfigurableDefinitionError,
        ValidationModeError,
        ScopeError,
    ],
)
def test_config_content_errors_are_configuration_errors(exc_cls: Type[Exception]) -> None:
    assert issubclass(exc_cls, ConfigurationError)


# ---------------------------------------------------------------------------
# 2. Raise sites
# ---------------------------------------------------------------------------


def test_load_config_missing_file_raises_config_file_not_found(tmp_path: Path) -> None:
    with pytest.raises(ConfigFileNotFoundError) as ei:
        confluid.load_config(tmp_path / "missing.yaml")
    assert isinstance(ei.value, FileNotFoundError)


def test_circular_include_raises_circular_include_error(tmp_path: Path) -> None:
    cfg = tmp_path / "a.yaml"
    cfg.write_text("include: a.yaml\nkey: 1\n")
    with pytest.raises(CircularIncludeError) as ei:
        confluid.load_config(cfg)
    assert isinstance(ei.value, ValueError)


def test_unknown_class_target_raises_unknown_class_error() -> None:
    with pytest.raises(UnknownClassError) as ei:
        flow(Class("DefinitelyNotARegisteredClass"))
    assert isinstance(ei.value, ValueError)


def test_unresolvable_reference_raises_reference_resolution_error() -> None:
    with pytest.raises(ReferenceResolutionError) as ei:
        flow(Reference("no_such_key_anywhere"))
    assert isinstance(ei.value, ValueError)


def test_self_referential_ref_raises_reference_resolution_error() -> None:
    yaml_doc = "model: !class:Model\n  layers: !ref:layers\n"
    with pytest.raises(ReferenceResolutionError, match="Self-referential"):
        confluid.load(yaml_doc)


def test_contradictory_configurable_flags_raise_definition_error() -> None:
    with pytest.raises(ConfigurableDefinitionError) as ei:
        configurable(constant=True, random=True)
    assert isinstance(ei.value, ValueError)


def test_bad_validation_mode_raises_validation_mode_error() -> None:
    with pytest.raises(ValidationModeError) as ei:
        _normalize_mode("stict", env_var="CONFLUID_VALIDATE_INIT")
    assert isinstance(ei.value, ValueError)


def test_circular_scope_alias_raises_scope_error() -> None:
    with pytest.raises(ScopeError) as ei:
        _resolve_aliases(["a"], {"a": "b", "b": "a"})
    assert isinstance(ei.value, ValueError)


class _Unrebuildable(Exception):
    """Exception whose constructor cannot be rebuilt from a plain message."""

    def __init__(self, a: int, b: int) -> None:
        super().__init__(f"{a},{b}")


class _BoomUnrebuildable:
    def __init__(self) -> None:
        raise _Unrebuildable(1, 2)


class _BoomValueError:
    def __init__(self) -> None:
        raise ValueError("bad ctor value")


def test_unrebuildable_constructor_failure_raises_construction_error() -> None:
    with pytest.raises(ConstructionError) as ei:
        flow(Class(_BoomUnrebuildable))
    assert isinstance(ei.value, RuntimeError)
    assert isinstance(ei.value.__cause__, _Unrebuildable)


def test_rebuildable_constructor_failure_preserves_original_class() -> None:
    # The ``type(exc)(msg)`` preserving path must stay untouched: a plain
    # ValueError from a constructor re-raises as ValueError, NOT ConstructionError.
    with pytest.raises(ValueError) as ei:
        flow(Class(_BoomValueError))
    assert not isinstance(ei.value, ConfluidError)


def test_missing_required_env_key_raises_workspace_env_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".env").write_text("")
    monkeypatch.setenv("CONFLUID_TEST_REQUIRED_KEY", "")
    with pytest.raises(WorkspaceEnvError) as ei:
        load_workspace_env(tmp_path, require=("CONFLUID_TEST_REQUIRED_KEY",), require_paths=())
    assert isinstance(ei.value, RuntimeError)


def test_missing_env_path_raises_workspace_env_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".env").write_text("")
    monkeypatch.setenv("CONFLUID_TEST_PATH_KEY", str(tmp_path / "does-not-exist"))
    with pytest.raises(WorkspaceEnvError) as ei:
        load_workspace_env(
            tmp_path,
            require=("CONFLUID_TEST_PATH_KEY",),
            require_paths=("CONFLUID_TEST_PATH_KEY",),
        )
    assert isinstance(ei.value, RuntimeError)


def test_to_pydantic_non_callable_raises_introspection_error() -> None:
    to_pydantic: Any = confluid.to_pydantic
    with pytest.raises(IntrospectionError) as ei:
        to_pydantic(42)
    assert isinstance(ei.value, TypeError)
