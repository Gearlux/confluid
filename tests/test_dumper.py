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


def test_strict_gating() -> None:
    class InternalThing:
        def __str__(self) -> str:
            return "internal"

    @configurable
    class Model:
        def __init__(self, thing: Any) -> None:
            self.thing = thing

    # InternalThing is NOT @configurable
    model = Model(thing=InternalThing())

    # Standard YAML dumper will fail since we don't have a representer.
    with pytest.raises(yaml.representer.RepresenterError):
        dump(model)


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
