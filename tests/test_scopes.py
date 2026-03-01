import pytest

from confluid import resolve_scopes


def test_basic_scope_resolution() -> None:
    config = {"val": 1, "debug": {"val": 10}, "prod": {"val": 100}}

    # No scopes
    assert resolve_scopes(config, [])["val"] == 1

    # Debug scope
    assert resolve_scopes(config, ["debug"])["val"] == 10

    # Prod scope
    assert resolve_scopes(config, ["prod"])["val"] == 100


def test_hierarchical_scopes() -> None:
    config = {"val": 1, "prod": {"val": 100}, "prod.gpu": {"gpu": True}}

    resolved = resolve_scopes(config, ["prod.gpu"])
    assert resolved["val"] == 100
    assert resolved["gpu"] is True


def test_scope_aliases() -> None:
    config = {"scope_aliases": {"dev": ["debug", "local"]}, "debug": {"lr": 0.1}, "local": {"db": "sqlite"}}

    resolved = resolve_scopes(config, ["dev"])
    assert resolved["lr"] == 0.1
    assert resolved["db"] == "sqlite"


def test_negative_scopes() -> None:
    config = {"lr": 0.001, "not debug": {"lr": 0.0001}, "debug": {"lr": 0.1}}

    # No active scope -> 'not debug' applies
    assert resolve_scopes(config, [])["lr"] == 0.0001

    # Debug active -> 'not debug' does NOT apply
    assert resolve_scopes(config, ["debug"])["lr"] == 0.1


def test_circular_alias_error() -> None:
    config = {"scope_aliases": {"a": "b", "b": "a"}}
    with pytest.raises(ValueError, match="Circular scope alias"):
        resolve_scopes(config, ["a"])
