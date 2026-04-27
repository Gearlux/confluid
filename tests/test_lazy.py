"""Unit tests for ``confluid.Lazy`` and ``lazy_param_names``."""

from typing import Any

import pytest

from confluid import Lazy, configurable, is_lazy_annotation, lazy_param_names


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
