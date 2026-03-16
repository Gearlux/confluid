from typing import Any

import pytest
from confluid import configurable, configure, get_registry, ignore_config


@pytest.fixture(autouse=True)
def setup_registry() -> None:
    get_registry().clear()


@configurable
class SimpleModel:
    def __init__(self, layers: int = 3, activation: str = "relu") -> None:
        self.layers = layers
        self.activation = activation


@configurable(name="CustomName")
class CustomNamedClass:
    def __init__(self, value: int = 10) -> None:
        self.value = value


@configurable
class ClassWithProperty:
    def __init__(self, base_value: int = 10) -> None:
        self._value = base_value

    @property
    def value(self) -> int:
        return self._value

    @value.setter
    def value(self, val: int) -> None:
        self._value = val

    @property
    @ignore_config
    def computed(self) -> int:
        return self._value * 2


def test_configure_single_object() -> None:
    model = SimpleModel()
    assert model.layers == 3

    configure(
        model,
        config="""
SimpleModel:
  layers: 10
  activation: 'tanh'
""",
    )
    assert model.layers == 10
    assert model.activation == "tanh"


def test_configure_multiple_objects() -> None:
    model = SimpleModel()
    custom = CustomNamedClass()

    configure(
        model,
        custom,
        config="""
SimpleModel:
  layers: 5
CustomName:
  value: 99
""",
    )
    assert model.layers == 5
    assert custom.value == 99


def test_ignored_property_not_configured() -> None:
    obj = ClassWithProperty()
    configure(
        obj,
        config="""
ClassWithProperty:
  computed: 999
""",
    )
    assert obj.computed == 20  # 10 * 2, unchanged


def test_custom_name_in_config() -> None:
    obj = CustomNamedClass()
    configure(
        obj,
        config="""
CustomName:
  value: 77
""",
    )
    assert obj.value == 77


def test_fluid_solidify_protocol() -> None:
    @configurable
    class LazyContainer:
        def __init__(self) -> None:
            self.children: list[Any] = []
            self._fluid = True

        def _is_fluid(self) -> bool:
            return self._fluid

        def _solidify(self) -> None:
            # Materialize children
            self.children = [SimpleModel(layers=1)]
            self._fluid = False

    container = LazyContainer()
    assert len(container.children) == 0

    # Configurator should trigger solidification
    configure(
        container,
        config="""
SimpleModel:
  layers: 100
""",
    )

    assert len(container.children) == 1
    assert container.children[0].layers == 100
    assert container._is_fluid() is False
