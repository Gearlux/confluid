from typing import Any

import pytest

from confluid import Clone, configurable, dump, flow, get_registry, load
from confluid.loader import _register_constructors


@pytest.fixture(autouse=True)
def setup_registry() -> None:
    get_registry().clear()
    _register_constructors()


def test_clone_basic_deepcopy() -> None:
    """!clone: produces an independent deep copy of the referenced value."""

    @configurable
    class Counter:
        def __init__(self, count: int = 0) -> None:
            self.count = count

    yaml_str = """
counter: !class:Counter()
  count: 5
copy1: !clone:counter
copy2: !clone:counter
"""
    result = load(yaml_str)
    assert result["counter"].count == 5
    assert result["copy1"].count == 5
    assert result["copy2"].count == 5

    # Verify independence — mutating one does not affect the others
    result["copy1"].count = 99
    assert result["counter"].count == 5
    assert result["copy2"].count == 5


def test_clone_with_kwargs() -> None:
    """!clone: with additional kwargs merges them into the cloned object."""

    @configurable
    class Widget:
        def __init__(self, size: int = 3, color: str = "red") -> None:
            self.size = size
            self.color = color

    yaml_str = """
base: !class:Widget()
  size: 10
  color: blue
modified: !clone:base
  color: green
"""
    result = load(yaml_str)
    assert result["base"].color == "blue"
    assert result["base"].size == 10
    assert result["modified"].color == "green"
    assert result["modified"].size == 10


def test_clone_list_value() -> None:
    """!clone: works with list values (deep copy of lists)."""
    yaml_str = """
items:
  - 1
  - 2
  - 3
copy: !clone:items
"""
    result = load(yaml_str)
    assert result["items"] == [1, 2, 3]
    assert result["copy"] == [1, 2, 3]

    # Verify deep independence
    result["copy"].append(99)
    assert 99 not in result["items"]


def test_clone_with_class_kwargs() -> None:
    """Pattern B: !clone: of a Class/Instance with kwargs override (MetricCollection pattern)."""

    @configurable
    class Collection:
        def __init__(self, metrics: Any = None, prefix: str = "") -> None:
            self.metrics = metrics
            self.prefix = prefix

    yaml_str = """
base: !class:Collection()
  metrics:
    - one
    - two
  prefix: ""
train: !clone:base
  prefix: "train/"
val: !clone:base
  prefix: "val/"
"""
    result = load(yaml_str)
    assert result["base"].prefix == ""
    assert result["train"].prefix == "train/"
    assert result["val"].prefix == "val/"
    assert result["train"].metrics == ["one", "two"]
    assert result["val"].metrics == ["one", "two"]

    # Independence
    result["train"].metrics.append("three")
    assert len(result["val"].metrics) == 2
    assert len(result["base"].metrics) == 2


def test_clone_dump_round_trip() -> None:
    """Clone objects that haven't been resolved yet survive dump/load."""
    clone = Clone("metrics", prefix="train/")
    output = dump(clone)
    assert "!clone:metrics" in output
    assert "prefix" in output


def test_clone_flow_directly() -> None:
    """flow() resolves Clone by resolving the reference then deepcopying."""

    @configurable
    class Item:
        def __init__(self, value: int = 0) -> None:
            self.value = value

    # Set up a context with a resolvable reference
    from confluid.loader import _state

    item = Item(value=42)
    old_ctx = getattr(_state, "context", None)
    _state.context = {"thing": item}
    try:
        clone = Clone("thing")
        result = flow(clone)
        assert result.value == 42
        # Verify independence
        result.value = 100
        assert item.value == 42
    finally:
        _state.context = old_ctx
