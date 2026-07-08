from pathlib import Path

import pytest

from confluid import ConfigFileNotFoundError, configurable, configure, configure_from_file, get_registry, load_config


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
    configure(model, config="!class:Model")
    assert model.val == 1


def test_configure_from_file(tmp_path: Path) -> None:
    @configurable
    class Model:
        def __init__(self, layers: int = 3, lr: float = 0.01):
            self.layers = layers
            self.lr = lr

    cfg = tmp_path / "experiment.yaml"
    cfg.write_text("Model:\n  layers: 50\n  lr: 0.001\n")

    model = Model()
    configure_from_file(model, path=cfg)

    assert model.layers == 50
    assert model.lr == 0.001


def test_configure_from_file_accepts_str_path(tmp_path: Path) -> None:
    @configurable
    class Model:
        def __init__(self, val: int = 1):
            self.val = val

    cfg = tmp_path / "cfg.yaml"
    cfg.write_text("Model:\n  val: 7\n")

    model = Model()
    configure_from_file(model, path=str(cfg))  # str, not Path
    assert model.val == 7


def test_configure_from_file_equivalent_to_load_config_plus_configure(tmp_path: Path) -> None:
    @configurable
    class Model:
        def __init__(self, val: int = 1):
            self.val = val

    cfg = tmp_path / "cfg.yaml"
    cfg.write_text("Model:\n  val: 42\n")

    a, b = Model(), Model()
    configure_from_file(a, path=cfg)
    configure(b, config=load_config(cfg))
    assert a.val == b.val == 42


def test_configure_from_file_honours_includes(tmp_path: Path) -> None:
    @configurable
    class Model:
        def __init__(self, layers: int = 3, dropout: float = 0.1):
            self.layers = layers
            self.dropout = dropout

    (tmp_path / "base.yaml").write_text("Model:\n  layers: 8\n  dropout: 0.2\n")
    top = tmp_path / "top.yaml"
    top.write_text('include:\n  - "base.yaml"\nModel:\n  dropout: 0.5\n')

    model = Model()
    configure_from_file(model, path=top)
    assert model.layers == 8  # from the included base
    assert model.dropout == 0.5  # overridden by the top file


def test_configure_from_file_missing_path_raises(tmp_path: Path) -> None:
    @configurable
    class Model:
        def __init__(self, val: int = 1):
            self.val = val

    with pytest.raises(ConfigFileNotFoundError):
        configure_from_file(Model(), path=tmp_path / "does-not-exist.yaml")
