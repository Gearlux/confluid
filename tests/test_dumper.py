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
        def __init__(self, layers: int = 3):
            self.layers = layers

    model = Model(layers=10)
    output = dump(model)
    data = yaml.safe_load(output)

    # Custom constructor returns ClassReference for !class: tags
    from confluid.resolver import ClassReference

    assert isinstance(data, ClassReference)
    assert data.cls_name == "Model"
    assert data.args_str == {"layers": 10}


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

    from confluid.resolver import ClassReference

    assert isinstance(data, ClassReference)
    assert data.cls_name == "Trainer"
    assert isinstance(data.args_str, dict)
    assert data.args_str["lr"] == 0.001
    assert isinstance(data.args_str["model"], ClassReference)


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

    # Standard YAML dumper will fail or stringify depending on its config.
    # In our case, we expect it to fail since we don't have a representer.
    with pytest.raises(yaml.representer.RepresenterError):
        dump(model)


def test_circular_reference() -> None:
    @configurable
    class Node:
        def __init__(self, next_node: Any = None):
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
        def __init__(self, items: list[Any], mapping: dict[str, Any]):
            self.items = items
            self.mapping = mapping

    obj = Container(items=[1, 2], mapping={"a": 1})
    data = yaml.safe_load(dump(obj))
    from confluid.resolver import ClassReference

    assert isinstance(data, ClassReference)
    assert data.cls_name == "Container"
    assert isinstance(data.args_str, dict)
    assert data.args_str["items"] == [1, 2]
    assert data.args_str["mapping"] == {"a": 1}


def test_dump_no_init() -> None:
    @configurable
    class Simple:
        pass

    obj = Simple()
    data = yaml.safe_load(dump(obj))
    from confluid.resolver import ClassReference

    assert isinstance(data, ClassReference)
    assert data.cls_name == "Simple"


def test_dump_none() -> None:
    assert "null" in dump(None)
