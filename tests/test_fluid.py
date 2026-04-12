import pytest

import confluid
from confluid import configurable, flow, load, materialize


@pytest.fixture(autouse=True)
def setup_registry() -> None:
    confluid.get_registry().clear()


def test_basic_flow_idempotent() -> None:
    @configurable
    class Model:
        def __init__(self, layers: int = 3) -> None:
            self.layers = layers

    # flow on already-live instance returns it unchanged
    model = Model(layers=10)
    assert flow(model).layers == 10


def test_flow_string_reference() -> None:
    @configurable
    class Model:
        def __init__(self, layers: int = 3) -> None:
            self.layers = layers

    # flow resolves !class: patterns
    instance = flow("!class:Model(layers=20)")
    assert instance.layers == 20
    assert isinstance(instance, Model)


def test_load_hierarchy() -> None:
    @configurable
    class Model:
        def __init__(self, layers: int = 3) -> None:
            self.layers = layers

    # raw load returns dictionary markers in Dictionary-First pattern
    data = {"Model": {"layers": 15}}
    config_data = load(data, flow=False)

    # Explicit materialize to get the instance
    instance = materialize({"_confluid_class_": "Model", **config_data["Model"]})
    assert isinstance(instance, Model)
    assert instance.layers == 15


def test_materialize_shorthand() -> None:
    @configurable
    class Simple:
        def __init__(self, val: int = 0) -> None:
            self.val = val

    # materialize accepts flat markers
    obj = materialize({"_confluid_class_": "Simple", "val": 42})
    assert obj.val == 42
