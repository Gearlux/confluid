from typing import Any

import pytest
import yaml

from confluid import configurable, dump, get_registry
from confluid.loader import _register_constructors


@pytest.fixture(autouse=True)
def setup_registry() -> None:
    get_registry().clear()
    _register_constructors()  # Ensure tags are readable in tests


def test_basic_dump() -> None:
    @configurable
    class Model:
        def __init__(self, layers: int = 3) -> None:
            self.layers = layers

    model = Model(layers=10)
    output = dump(model)

    # Instances dump with () for instant construction on reload
    assert "!class:Model()" in output
    assert "layers: 10" in output

    # Round-trip via confluid.load produces a live instance
    from confluid import load

    data = load(output)
    assert isinstance(data, Model)
    assert data.layers == 10


def test_hierarchical_dump() -> None:
    @configurable
    class Model:
        def __init__(self, layers: int = 3) -> None:
            self.layers = layers

    @configurable
    class Trainer:
        def __init__(self, model: Model, lr: float = 0.01) -> None:
            self.model = model
            self.lr = lr

    model = Model(layers=5)
    trainer = Trainer(model=model, lr=0.001)

    output = dump(trainer)

    assert "!class:Trainer" in output
    assert "!class:Model" in output
    assert "lr: 0.001" in output
    assert "layers: 5" in output


def test_opaque_fallback_emits_class_marker() -> None:
    """Non-@configurable objects degrade to a ``!class:<name>`` scalar marker."""

    class InternalThing:
        def __str__(self) -> str:
            return "internal"

    @configurable
    class Model:
        def __init__(self, thing: Any) -> None:
            self.thing = thing

    model = Model(thing=InternalThing())
    output = dump(model)
    assert "!class:" in output
    assert "InternalThing" in output


def test_circular_reference() -> None:
    @configurable
    class Node:
        def __init__(self, next_node: Any = None) -> None:
            self.next_node = next_node

    node1 = Node()
    node2 = Node(next_node=node1)
    node1.next_node = node2  # Cycle

    output = dump(node1)
    # Check for YAML anchors/aliases indicating circularity
    assert "&id" in output or "*id" in output


def test_dump_list_and_dict() -> None:
    @configurable
    class Container:
        def __init__(self, items: list[Any], mapping: dict[str, Any]) -> None:
            self.items = items
            self.mapping = mapping

    obj = Container(items=[1, 2], mapping={"a": 1})
    output = dump(obj)

    assert "!class:Container" in output
    assert "- 1" in output
    assert "a: 1" in output


def test_dump_no_init() -> None:
    @configurable
    class Simple:
        pass

    obj = Simple()
    output = dump(obj)
    assert "!class:Simple" in output


def test_dump_none() -> None:
    assert "null" in dump(None)


def test_dump_non_configurable_with_confluid_origin() -> None:
    """Objects created via Instance/flow() retain origin metadata for dump."""
    from confluid.fluid import Instance, flow

    class Metric:
        def __init__(self, num_classes: int = 10) -> None:
            self.num_classes = num_classes

    inst = Instance(Metric, num_classes=5)
    live = flow(inst)

    output = dump(live)
    assert "!class:" in output
    assert "num_classes: 5" in output


def test_dump_non_configurable_in_configurable_parent() -> None:
    """Non-configurable objects nested inside configurable ones serialize correctly."""
    from confluid.fluid import Instance, flow

    class Metric:
        def __init__(self, average: str = "macro") -> None:
            self.average = average

    @configurable
    class Trainer:
        def __init__(self, metrics: Any = None) -> None:
            self.metrics = metrics

    metric = Instance(Metric, average="weighted")
    trainer = Trainer(metrics=[flow(metric)])

    output = dump(trainer)
    assert "!class:Trainer" in output
    assert "!class:" in output
    assert "average: weighted" in output


def test_dump_non_configurable_round_trip() -> None:
    """Dump/load round-trip for non-configurable objects preserves kwargs."""
    from confluid.fluid import Instance, flow

    class Widget:
        def __init__(self, size: int = 3, color: str = "red") -> None:
            self.size = size
            self.color = color

    inst = Instance(Widget, size=7, color="blue")
    live = flow(inst)

    output = dump(live)
    assert "size: 7" in output
    assert "color: blue" in output


def test_dump_function_reference_round_trip() -> None:
    """Module-level callables dump as ``!ref:module.qualname`` and reload as the live function."""
    import os.path

    from confluid import load

    @configurable
    class Loader:
        def __init__(self, joiner: Any = None) -> None:
            self.joiner = joiner

    obj = Loader(joiner=os.path.join)
    output = dump(obj)

    assert "!ref 'posixpath.join'" in output or "!ref 'ntpath.join'" in output

    reloaded = load(output)
    assert isinstance(reloaded, Loader)
    assert reloaded.joiner is os.path.join


def test_dump_lambda_rejected() -> None:
    """Anonymous callables (lambdas, closures) cannot be referenced — dump raises."""

    @configurable
    class Holder:
        def __init__(self, fn: Any = None) -> None:
            self.fn = fn

    obj = Holder(fn=lambda x: x)
    with pytest.raises(yaml.representer.RepresenterError):
        dump(obj)


def test_dump_builtin_function_reference() -> None:
    """Built-in callables (e.g., ``len``) dump as ``!ref:builtins.len``."""

    @configurable
    class Holder:
        def __init__(self, fn: Any = None) -> None:
            self.fn = fn

    obj = Holder(fn=len)
    output = dump(obj)
    assert "!ref 'builtins.len'" in output
