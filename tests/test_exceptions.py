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
    AmbiguousClassError,
    CircularIncludeError,
    ConfigFileNotFoundError,
    ConfigurableDefinitionError,
    ConfigurationError,
    ConfluidError,
    ConstructionError,
    IntrospectionError,
    Reference,
    ReferenceResolutionError,
    ScopeError,
    Target,
    UnknownClassError,
    ValidationModeError,
    WorkspaceEnvError,
    configurable,
    flow,
    get_registry,
    load,
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


def test_load_missing_file_raises_config_file_not_found(tmp_path: Path) -> None:
    with pytest.raises(ConfigFileNotFoundError) as ei:
        confluid.load(tmp_path / "missing.yaml", until="raw")
    assert isinstance(ei.value, FileNotFoundError)


def test_circular_include_raises_circular_include_error(tmp_path: Path) -> None:
    cfg = tmp_path / "a.yaml"
    cfg.write_text("include: a.yaml\nkey: 1\n")
    with pytest.raises(CircularIncludeError) as ei:
        confluid.load(cfg, until="raw")
    assert isinstance(ei.value, ValueError)


def test_unknown_class_target_raises_unknown_class_error() -> None:
    with pytest.raises(UnknownClassError) as ei:
        flow(Target("DefinitelyNotARegisteredClass"))
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
        flow(Target(_BoomUnrebuildable))
    assert isinstance(ei.value, RuntimeError)
    assert isinstance(ei.value.__cause__, _Unrebuildable)


def test_rebuildable_constructor_failure_preserves_original_class() -> None:
    # The ``type(exc)(msg)`` preserving path must stay untouched: a plain
    # ValueError from a constructor re-raises as ValueError, NOT ConstructionError.
    with pytest.raises(ValueError) as ei:
        flow(Target(_BoomValueError))
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


# --------------------------------------------------------------------------------------
# Every error raised while processing a document names its file:line:col.
#
# These pin the rule against the defect that produced it: a renamed class left
# `UnknownClassError: Cannot resolve class: waivefront.sources.HDF5WindwoSource` with no
# file and no line, while `ConstructionError` a few frames later had been printing
# `.../evaluate_yolo26.yaml:20:5` the whole time. Asserting the LINE (not just "some
# location") is the point — a message naming only the file still sends the reader grepping.
# --------------------------------------------------------------------------------------


def test_unknown_class_names_the_yaml_file_and_line(tmp_path: Path) -> None:
    cfg = tmp_path / "pipeline.yaml"
    cfg.write_text("# a comment line\nnode:\n  _target_: does.not.Exist\n")  # target on line 3

    with pytest.raises(UnknownClassError) as ei:
        confluid.load(str(cfg))

    msg = str(ei.value)
    assert "does.not.Exist" in msg
    assert "pipeline.yaml:3:" in msg, f"no file:line in the message: {msg}"


def test_unknown_class_NESTED_in_another_markers_kwargs_names_its_own_line(tmp_path: Path) -> None:
    """The shape a real config has: the typo is a source inside a wrapper's kwargs.

    This path runs through `_resolve_kwarg_value` -> `flow` -> `_flow_target`, so the
    reported line must be the NESTED node's, not the outer marker's.
    """
    cfg = tmp_path / "nested.yaml"
    cfg.write_text("outer:\n  _target_: Model\n  layers:\n    _target_: pkg.Typoed\n")  # inner on line 4

    with pytest.raises(UnknownClassError) as ei:
        confluid.load(str(cfg))

    assert "nested.yaml:4:" in str(ei.value), f"reported the wrong node: {ei.value}"


def test_ambiguous_class_names_the_yaml_file_and_line(tmp_path: Path) -> None:
    """The registry raises this one, and it has never seen the document."""

    @configurable(name="Twin", framework="torch")
    class TwinA:
        def __init__(self) -> None: ...

    @configurable(name="Twin", framework="keras")
    class TwinB:
        def __init__(self) -> None: ...

    cfg = tmp_path / "ambiguous.yaml"
    cfg.write_text("a: 1\nb: 2\nnode:\n  _target_: Twin\n")  # target on line 4

    with pytest.raises(AmbiguousClassError) as ei:
        confluid.load(str(cfg))

    assert "ambiguous.yaml:4:" in str(ei.value), f"no file:line in the message: {ei.value}"


# ---------------------------------------------------------------------------
# The located-error sweep (BUGS-2026-08-19 SR14 / SR15 / ENG-4 / ENG-10 / PA25):
# every raise below had the marker or the file in hand and dropped it.
# ---------------------------------------------------------------------------


def test_a_missing_selector_key_names_the_markers_line() -> None:
    """SR14 — the registry raise had no node; the engine funnel re-locates it."""

    @configurable(framework="torch")
    class _SelLoss:
        def __init__(self, k: int = 1) -> None:
            self.k = k

    with pytest.raises(ConfigurationError, match=r"no key 'engine'.*at <unicode string>:1:7"):
        load("loss: !class:_SelLoss@framework=$engine\n")


def test_an_undeclared_scope_value_names_where_the_values_are_declared(tmp_path: Path) -> None:
    """SR15 — the declaring blocks carry `_yaml_loc`; the refusal now lists them."""
    config = tmp_path / "dims.yaml"
    config.write_text("a: !scope:fw=x\n  v: 1\nb: !scope:fw=y\n  v: 2\n")
    with pytest.raises(ScopeError, match=r"declares fw with: x, y \(declared at .*dims\.yaml:1:4, .*dims\.yaml:3:4\)"):
        load(str(config), scopes=["fw=z"])


def test_a_malformed_default_scopes_names_its_file(tmp_path: Path) -> None:
    """SR15 — the loader holds the path; the refusal now leads with it."""
    config = tmp_path / "bad_defaults.yaml"
    config.write_text("default_scopes: fw=x\na: !scope:fw=x\n  v: 1\n")
    with pytest.raises(ScopeError, match=r"bad_defaults\.yaml.*`default_scopes` must be a list"):
        load(str(config))


def test_flowing_an_unresolvable_reference_names_its_line() -> None:
    """ENG-4 — `flow(ref)` raised `Cannot resolve Reference: nothing` with no location
    while `load()` of the same document located it."""
    document = load("x: 1\nr: {_ref_: nothing}\n", until="document")
    with pytest.raises(ReferenceResolutionError, match=r"nothing at <unicode string>:2:4"):
        flow(document["r"])


def test_a_validating_setattr_TypeError_becomes_a_located_construction_error() -> None:
    """ENG-10 — torch-style `cannot assign 'int' as child module` escaped RAW from a
    line the author never wrote; it now joins the AttributeError wrap."""

    class _Validating:
        def __init__(self, width: int = 4) -> None:
            self.width = width

        def __setattr__(self, name: str, value: object) -> None:
            if name == "head" and not isinstance(value, str):
                raise TypeError(f"cannot assign {type(value).__name__!r} as head (str expected)")
            object.__setattr__(self, name, value)

    configurable(_Validating)
    with pytest.raises(ConstructionError, match=r"cannot accept 'head'.*<unicode string>:1:6.*str expected"):
        load("net: {_target_: _Validating, width: 8, head: 3}")


def test_an_include_miss_names_the_including_file_and_the_real_search_tiers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PA25 — the message dropped the including file AND the including-dir tier it
    actually probed first."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "a.yaml").write_text("include: missing.yaml\n")
    (tmp_path / "main.yaml").write_text("include: sub/a.yaml\n")
    with pytest.raises(
        ConfigFileNotFoundError, match=r"a\.yaml includes missing\.yaml.*searched: .*sub[/\\]missing\.yaml"
    ):
        load("main.yaml")


def test_a_circular_include_renders_the_whole_chain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """PA25 — `Circular include: c1.yaml` left the reader to rediscover the cycle."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "c1.yaml").write_text("include: c2.yaml\n")
    (tmp_path / "c2.yaml").write_text("include: c1.yaml\n")
    with pytest.raises(CircularIncludeError, match=r"c1\.yaml -> .*c2\.yaml -> .*c1\.yaml"):
        load("c1.yaml")
