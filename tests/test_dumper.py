from typing import Any

import pytest
import yaml

from confluid import configurable, dump, get_registry


@pytest.fixture(autouse=True)
def setup_registry() -> None:
    get_registry().clear()


def test_basic_dump() -> None:
    @configurable
    class Model:
        def __init__(self, layers: int = 3):
            self.layers = layers

    model = Model(layers=10)
    output = dump(model)
    data = yaml.safe_load(output)

    assert data["Model"]["layers"] == 10


def test_hierarchical_dump() -> None:
    @configurable
    class Model:
        def __init__(self, layers: int = 3):
            self.layers = layers

    @configurable
    class Trainer:
        def __init__(self, model: Model, lr: float = 0.01):
            self.model = model
            self.lr = lr

    model = Model(layers=5)
    trainer = Trainer(model=model, lr=0.001)

    output = dump(trainer)
    data = yaml.safe_load(output)

    assert data["Trainer"]["lr"] == 0.001
    assert data["Trainer"]["model"]["Model"]["layers"] == 5


def test_strict_gating() -> None:
    class InternalThing:
        def __str__(self) -> str:
            return "internal"

    @configurable
    class Model:
        def __init__(self, thing: Any):
            self.thing = thing

    # InternalThing is NOT @configurable
    model = Model(thing=InternalThing())

    output = dump(model)
    data = yaml.safe_load(output)

    # Should stop at the non-configurable object and stringify it
    assert data["Model"]["thing"] == "internal"


def test_circular_reference() -> None:
    @configurable
    class Node:
        def __init__(self, next_node: Any = None):
            self.next_node = next_node

    node1 = Node()
    node2 = Node(next_node=node1)
    node1.next_node = node2  # Cycle

    output = dump(node1)
    assert "Circular reference" in output


def test_dump_list_and_dict() -> None:
    @configurable
    class Container:
        def __init__(self, items: list[Any], mapping: dict[str, Any]):
            self.items = items
            self.mapping = mapping

    obj = Container(items=[1, 2], mapping={"a": 1})
    data = yaml.safe_load(dump(obj))
    assert data["Container"]["items"] == [1, 2]
    assert data["Container"]["mapping"] == {"a": 1}


def test_dump_no_init() -> None:
    @configurable
    class Simple:
        pass

    obj = Simple()
    data = yaml.safe_load(dump(obj))
    assert data == {"Simple": {}}


def test_dump_none() -> None:
    assert "null" in dump(None)
