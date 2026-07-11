import os
from typing import Any

import pytest

from confluid import configurable, get_registry
from confluid.resolver import Resolver


@pytest.fixture(autouse=True)
def setup_registry() -> None:
    get_registry().clear()

    @configurable
    class Model:
        def __init__(self, layers: int = 3) -> None:
            self.layers = layers

    @configurable
    class Trainer:
        def __init__(self, model: Any, lr: float = 0.01) -> None:
            self.model = model
            self.lr = lr


def test_resolve_env_var_default() -> None:
    resolver = Resolver()
    assert resolver.resolve("${MISSING_VAR:default_val}") == "default_val"


def test_resolve_string_reference() -> None:
    """Verify that !ref: strings are resolved against the context."""
    resolver = Resolver(context={"base_lr": 0.001})
    assert resolver.resolve("!ref:base_lr") == 0.001


def test_resolve_string_instantiation_marker() -> None:
    """Verify that !class: strings are resolved into eager Instance Fluids."""
    from confluid.fluid import Instance

    resolver = Resolver()
    marker = resolver.resolve("!class:Model(layers=10)")
    assert isinstance(marker, Instance)
    assert marker.target == "Model"
    assert marker.kwargs["layers"] == 10


def test_recursive_string_instantiation_marker() -> None:
    """Verify nested !class: and !ref: strings produce nested Instance Fluids."""
    from confluid.fluid import Instance

    resolver = Resolver(context={"global_lr": 0.5})
    marker = resolver.resolve("!class:Trainer(model=!class:Model(layers=5), lr=!ref:global_lr)")

    assert isinstance(marker, Instance)
    assert marker.target == "Trainer"
    assert marker.kwargs["lr"] == 0.5
    assert isinstance(marker.kwargs["model"], Instance)
    assert marker.kwargs["model"].target == "Model"
    assert marker.kwargs["model"].kwargs["layers"] == 5


def test_resolve_empty_instantiation_marker() -> None:
    from confluid.fluid import Instance

    resolver = Resolver()
    marker = resolver.resolve("!class:Model()")
    assert isinstance(marker, Instance)
    assert marker.target == "Model"


def test_resolve_dict_and_list_strings() -> None:
    resolver = Resolver(context={"val": 42})
    data = {"a": "!ref:val", "b": ["!ref:val", "${HOME}"]}
    resolved = resolver.resolve(data)
    assert resolved["a"] == 42
    assert resolved["b"][0] == 42
    assert os.environ["HOME"] in resolved["b"][1]


# --- ${key.path} config-key string interpolation ---------------------------


def test_interpolate_config_key_whole_match_keeps_native_type() -> None:
    """A whole-string ``${a.b}`` returns the config value with its real type."""
    resolver = Resolver(context={"train": {"dataset": "RFUAV", "epochs": 5}})
    assert resolver.resolve("${train.dataset}") == "RFUAV"
    epochs = resolver.resolve("${train.epochs}")
    assert epochs == 5 and isinstance(epochs, int)


def test_interpolate_config_key_embedded_in_string() -> None:
    """Embedded ``${a.b}`` placeholders substitute as strings, mixing with env."""
    os.environ["CONFLUID_TEST_DATA_ROOT"] = "/data"
    try:
        resolver = Resolver(context={"train": {"dataset": "RFUAV", "version": "v3"}})
        out = resolver.resolve("${CONFLUID_TEST_DATA_ROOT}/${train.dataset}/${train.version}/x")
        assert out == "/data/RFUAV/v3/x"
    finally:
        del os.environ["CONFLUID_TEST_DATA_ROOT"]


def test_interpolate_config_key_bracket_index() -> None:
    resolver = Resolver(context={"items": [10, 20, 30]})
    assert resolver.resolve("val=${items[0]}") == "val=10"
    assert resolver.resolve("${items[-1]}") == 30


def test_interpolate_plain_name_is_still_env_var() -> None:
    """A name without a dot/bracket is an env var even if a config key exists."""
    resolver = Resolver(context={"HOME_DIR": "/from/config"})
    # No dot → env var lookup, NOT the config key of the same shape.
    assert resolver.resolve("${HOME_DIR:fallback}") == "fallback"


def test_interpolate_config_key_missing_uses_default() -> None:
    resolver = Resolver(context={"train": {"dataset": "RFUAV"}})
    assert resolver.resolve("${train.missing:fallback}") == "fallback"
    # A parsed default keeps its type on a whole match.
    port = resolver.resolve("${db.port:5432}")
    assert port == 5432 and isinstance(port, int)


def test_interpolate_config_key_missing_no_default_leaves_literal() -> None:
    resolver = Resolver(context={"train": {"dataset": "RFUAV"}})
    assert resolver.resolve("${train.nope}/x") == "${train.nope}/x"
    assert resolver.resolve("${train.nope}") == "${train.nope}"


def test_interpolate_config_key_prefers_local_context() -> None:
    """Sibling (local) keys win over global, mirroring ``!ref:``."""
    resolver = Resolver(context={"a": {"b": "global"}})
    data = {"a": {"b": "local"}, "out": "${a.b}"}
    resolved = resolver.resolve(data)
    assert resolved["out"] == "local"


def test_interpolate_non_scalar_target_left_literal() -> None:
    """Embedding a dict/list config value is a no-op (stays literal)."""
    resolver = Resolver(context={"cfg": {"nested": {"x": 1}}})
    assert resolver.resolve("prefix-${cfg.nested}") == "prefix-${cfg.nested}"
