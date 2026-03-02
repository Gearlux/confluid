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
        def __init__(self, layers: int = 3):
            self.layers = layers

    @configurable
    class Trainer:
        def __init__(self, model: Any, lr: float = 0.01):
            self.model = model
            self.lr = lr


def test_resolve_env_vars() -> None:
    os.environ["TEST_VAR"] = "production"
    resolver = Resolver()
    assert resolver.resolve("${TEST_VAR}") == "production"
    assert resolver.resolve("path/${TEST_VAR}/logs") == "path/production/logs"
    del os.environ["TEST_VAR"]


def test_resolve_env_var_default() -> None:
    resolver = Resolver()
    # Missing var should use default
    assert resolver.resolve("${MISSING_VAR:-default_val}") == "default_val"
    # Missing var without default should remain as is
    assert resolver.resolve("${MISSING_NO_DEFAULT}") == "${MISSING_NO_DEFAULT}"


def test_resolve_config_context() -> None:
    resolver = Resolver(context={"base_lr": 0.001})
    assert resolver.resolve("@base_lr") == 0.001


def test_resolve_class_reference() -> None:
    resolver = Resolver()
    # Should return the class type
    from confluid.registry import get_registry

    Model = get_registry().get_class("Model")
    assert resolver.resolve("@Model") is Model


def test_resolve_instantiation() -> None:
    resolver = Resolver()
    # Should return an instance
    obj = resolver.resolve("@Model(layers=10)")
    assert obj.layers == 10
    assert obj.__class__.__name__ == "Model"


def test_recursive_instantiation() -> None:
    resolver = Resolver()
    # Nesting: Trainer with a Model
    obj = resolver.resolve("@Trainer(model=@Model(layers=5), lr=0.1)")
    assert obj.lr == 0.1
    assert obj.model.layers == 5
    assert obj.model.__class__.__name__ == "Model"


def test_resolve_empty_instantiation() -> None:
    resolver = Resolver()
    obj = resolver.resolve("@Model()")
    assert obj.layers == 3


def test_resolve_invalid_reference() -> None:
    resolver = Resolver()
    # No match for pattern
    assert resolver.resolve("@") == "@"
    # Not in registry
    assert resolver.resolve("@UnknownClass") == "@UnknownClass"


def test_resolve_object_call_error() -> None:
    resolver = Resolver()
    get_registry().register_object({"a": 1}, "MyDict")
    with pytest.raises(ValueError, match="Cannot call registered object"):
        resolver.resolve("@MyDict()")


def test_resolve_literal_fallback() -> None:
    resolver = Resolver()
    # String that is not a valid python literal but not a reference
    # Should fall back to strip_quotes
    obj = resolver.resolve("@Model(layers='10')")
    assert obj.layers == "10"

    # Complex string
    obj = resolver.resolve("@Model(layers=some_string)")
    assert obj.layers == "some_string"


def test_resolve_nested_list() -> None:
    resolver = Resolver(context={"val": 1})
    data = [[["@val"]]]
    assert resolver.resolve(data) == [[[1]]]


def test_resolve_literal_syntax_error() -> None:
    resolver = Resolver()
    # Current MVP parser splits by comma, so [1,2) becomes [1
    obj = resolver.resolve("@Model(layers=[1,2)")
    assert obj.layers == "[1"


def test_resolve_dict_and_list() -> None:
    resolver = Resolver(context={"val": 42})
    data = {"a": "@val", "b": ["@val", "${HOME}"], "c": {"nested": "@val"}}
    resolved = resolver.resolve(data)
    assert resolved["a"] == 42
    assert resolved["b"][0] == 42
    assert os.environ["HOME"] in resolved["b"][1]
    assert resolved["c"]["nested"] == 42
