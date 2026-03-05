import pytest

from confluid import configurable, configure, get_registry


@pytest.fixture(autouse=True)
def setup_registry() -> None:
    get_registry().clear()


def test_post_construction_configure() -> None:
    @configurable
    class Model:
        def __init__(self, layers: int = 3, lr: float = 0.01):
            self.layers = layers
            self.lr = lr

    model = Model()
    assert model.layers == 3

    # Configure existing instance
    configure(model, config={"Model": {"layers": 50, "lr": 0.001}})

    assert model.layers == 50
    assert model.lr == 0.001


def test_type_coercion() -> None:
    @configurable
    class Model:
        def __init__(self, layers: int = 3) -> None:
            self.layers = layers

    model = Model()

    # Pass string "100", Resolver should use parse_value to coerce to int 100
    configure(model, config={"Model": {"layers": "100"}})

    assert model.layers == 100
    assert isinstance(model.layers, int)


def test_scoped_configuration() -> None:
    @configurable
    class Model:
        def __init__(self, val: int = 1):
            self.val = val

    model = Model()

    # Nested data
    data = {"Model": {"val": 42}, "Other": {"val": 99}}
    configure(model, config=data)
    assert model.val == 42


def test_unscoped_configuration() -> None:
    @configurable
    class Model:
        def __init__(self, val: int = 1):
            self.val = val

    model = Model()

    # Direct dict
    configure(model, config={"val": 7})
    assert model.val == 7


def test_configure_none() -> None:
    @configurable
    class Model:
        def __init__(self, val: int = 1):
            self.val = val

    model = Model()
    configure(model, config=None)
    assert model.val == 1


def test_configure_dotted_keys() -> None:
    @configurable
    class Model:
        def __init__(self, layers: int = 3, dropout: float = 0.1):
            self.layers = layers
            self.dropout = dropout

    model = Model()
    # Helios style flat-dotted keys
    data = {"Model.layers": 10, "Model.dropout": 0.5}
    configure(model, config=data)
    assert model.layers == 10
    assert model.dropout == 0.5


def test_configure_non_dict() -> None:
    @configurable
    class Model:
        def __init__(self, val: int = 1):
            self.val = val

    model = Model()
    # Passing a reference that resolves to a class, not a dict
    configure(model, config="@Model")
    assert model.val == 1
