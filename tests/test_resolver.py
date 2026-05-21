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
    """Verify that !class: strings are resolved into flat markers."""
    resolver = Resolver()
    marker = resolver.resolve("!class:Model(layers=10)")
    assert isinstance(marker, dict)
    assert marker["_confluid_class_"] == "Model"
    assert marker["layers"] == 10


def test_recursive_string_instantiation_marker() -> None:
    """Verify nested !class: and !ref: strings produce nested markers."""
    resolver = Resolver(context={"global_lr": 0.5})
    marker = resolver.resolve("!class:Trainer(model=!class:Model(layers=5), lr=!ref:global_lr)")

    assert marker["_confluid_class_"] == "Trainer"
    assert marker["lr"] == 0.5
    assert marker["model"]["_confluid_class_"] == "Model"
    assert marker["model"]["layers"] == 5


def test_resolve_empty_instantiation_marker() -> None:
    resolver = Resolver()
    marker = resolver.resolve("!class:Model()")
    assert marker["_confluid_class_"] == "Model"


def test_resolve_dict_and_list_strings() -> None:
    resolver = Resolver(context={"val": 42})
    data = {"a": "!ref:val", "b": ["!ref:val", "${HOME}"]}
    resolved = resolver.resolve(data)
    assert resolved["a"] == 42
    assert resolved["b"][0] == 42
    assert os.environ["HOME"] in resolved["b"][1]
