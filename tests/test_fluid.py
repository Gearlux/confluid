import pytest

from confluid import Fluid, configurable, get_registry, load, solidify


@pytest.fixture(autouse=True)
def setup_registry() -> None:
    get_registry().clear()

    @configurable
    class Model:
        def __init__(self, layers: int = 3):
            self.layers = layers


def test_fluid_init() -> None:
    @configurable
    class Simple:
        pass

    f = Fluid(Simple, val=1)
    assert f.target is Simple
    assert f.kwargs == {"val": 1}
    assert "Simple" in repr(f)


def test_flow_idempotency() -> None:
    @configurable
    class Model:
        def __init__(self, layers: int = 3):
            self.layers = layers

    m = Model(layers=5)
    # flowing an already instantiated object should return it
    assert solidify(m) is m


def test_flow_fluid() -> None:
    @configurable
    class Model:
        def __init__(self, layers: int = 3):
            self.layers = layers

    f = Fluid(Model, layers=10)
    instance = solidify(f)

    assert instance.layers == 10
    assert isinstance(instance, Model)


def test_flow_string_reference() -> None:
    @configurable
    class Model:
        def __init__(self, layers: int = 3) -> None:
            self.layers = layers

    # solidify resolves !class: patterns
    instance = solidify("!class:Model(layers=20)")
    assert instance.layers == 20
    assert isinstance(instance, Model)


def test_load_hierarchy() -> None:
    @configurable
    class Model:
        def __init__(self, layers: int = 3):
            self.layers = layers

    data = {"Model": {"layers": 15}}
    instance = load(data)
    assert isinstance(instance, Model)
    assert instance.layers == 15


def test_fluid_with_string_target() -> None:
    @configurable
    class Model:
        def __init__(self, layers: int = 3):
            self.layers = layers

    f = Fluid("Model", layers=7)
    instance = solidify(f)

    assert instance.layers == 7


def test_fluid_unknown_class_error() -> None:
    f = Fluid("Unknown", val=1)
    with pytest.raises(ValueError, match="not found in registry"):
        solidify(f)
