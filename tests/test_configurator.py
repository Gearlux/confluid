from pathlib import Path
from typing import Any

import pytest

from confluid import ConfigFileNotFoundError, configurable, configure, configure_from_file, get_registry, load_config


@pytest.fixture(autouse=True)
def setup_registry() -> None:
    get_registry().clear()


def test_post_construction_configure() -> None:
    from confluid import ConfigurationReport

    @configurable
    class Model:
        def __init__(self, layers: int = 3, lr: float = 0.01):
            self.layers = layers
            self.lr = lr

    model = Model()
    assert model.layers == 3

    # Configure existing instance; the call returns the ConfigurationReport.
    report = configure(model, config={"Model": {"layers": 50, "lr": 0.001}})

    assert model.layers == 50
    assert model.lr == 0.001
    assert isinstance(report, ConfigurationReport)


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


# --- last-write-wins rebuild pins -------------------------------------------


def test_configure_sets_none() -> None:
    """A present key with value None SETS None (the old priority matcher used
    None as its no-match sentinel, making null values impossible to apply)."""

    @configurable
    class Model:
        def __init__(self, dropout: Any = 0.1):
            self.dropout = dropout

    model = Model()
    configure(model, config={"Model": {"dropout": None}})
    assert model.dropout is None


def test_configure_never_executes_property_getters() -> None:
    """configure() walks vars(obj) — a property getter must NEVER fire (the
    old dir()+getattr walk executed every getter, the documented gotcha)."""
    calls = {"n": 0}

    @configurable
    class Model:
        def __init__(self, layers: int = 3):
            self.layers = layers

        @property
        def expensive(self) -> int:
            calls["n"] += 1
            return 42

    model = Model()
    configure(model, config={"Model": {"layers": 7}})
    assert model.layers == 7
    assert calls["n"] == 0


def test_configure_unknown_block_key_warns(monkeypatch: pytest.MonkeyPatch) -> None:
    """A typo'd (non-dict) key inside the object's own block warns and no-ops.

    The module logger is patched directly — loggair does not propagate into
    stdlib logging, so pytest's ``caplog`` cannot capture it.
    """
    from types import SimpleNamespace

    import confluid.configurator as configurator_module

    warnings_seen: list[str] = []
    monkeypatch.setattr(configurator_module, "logger", SimpleNamespace(warning=lambda msg: warnings_seen.append(msg)))

    @configurable
    class Model:
        def __init__(self, layers: int = 3):
            self.layers = layers

    model = Model()
    configure(model, config={"Model": {"layerz": 50}})
    assert model.layers == 3
    assert any("layerz" in msg and "no attribute" in msg for msg in warnings_seen)


def test_configure_settable_property_still_configured() -> None:
    """A property WITH a setter stays configurable (shared accept-list keeps
    writable properties; only setter-less ones are skipped)."""

    @configurable
    class Model:
        def __init__(self, base: int = 10):
            self._base = base

        @property
        def base(self) -> int:
            return self._base

        @base.setter
        def base(self, val: int) -> None:
            self._base = val

    model = Model()
    configure(model, config={"Model": {"base": 99}})
    assert model.base == 99


def test_configure_last_write_wins_document_order() -> None:
    """The confluid rule: NO priority tiers — whichever assignment comes last
    in document order wins, even when a generic key follows a specific block."""

    @configurable
    class Model:
        def __init__(self, lr: float = 0.01):
            self.lr = lr

    # Specific block first, generic broadcast LATER → the broadcast wins.
    m1 = Model()
    configure(m1, config={"Model": {"lr": 0.5}, "lr": 0.9})
    assert m1.lr == 0.9

    # Generic broadcast first, specific block LATER → the block wins.
    m2 = Model()
    configure(m2, config={"lr": 0.9, "Model": {"lr": 0.5}})
    assert m2.lr == 0.5
