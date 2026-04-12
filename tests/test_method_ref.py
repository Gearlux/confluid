"""Tests for !ref:obj.method() — method call references."""

from typing import Any

import pytest

from confluid import configurable, get_registry, load
from confluid.loader import _register_constructors


@pytest.fixture(autouse=True)
def setup_registry() -> None:
    get_registry().clear()
    _register_constructors()


def test_ref_method_call_basic() -> None:
    """!ref:obj.method() calls method on a flowed instance."""

    @configurable
    class Source:
        def __init__(self, items: Any = None) -> None:
            self.items = items or [1, 2, 3]

        def get_items(self) -> list[int]:
            return list(self.items)

    yaml_str = """
source: !class:Source()
  items: [10, 20, 30]
result: !ref:source.get_items()
"""
    result = load(yaml_str)
    assert result["result"] == [10, 20, 30]


def test_ref_method_call_returns_dict() -> None:
    """!ref:obj.method() works when method returns a dict."""

    @configurable
    class Factory:
        def __init__(self, prefix: str = "") -> None:
            self.prefix = prefix

        def to_dict(self) -> dict[str, int]:
            return {f"{self.prefix}a": 1, f"{self.prefix}b": 2}

    yaml_str = """
factory: !class:Factory()
  prefix: "x_"
data: !ref:factory.to_dict()
"""
    result = load(yaml_str)
    assert result["data"] == {"x_a": 1, "x_b": 2}


def test_ref_method_call_independent_results() -> None:
    """Each !ref:obj.method() call produces an independent result."""

    @configurable
    class Counter:
        def __init__(self) -> None:
            self._count = 0

        def next_list(self) -> list[int]:
            self._count += 1
            return [self._count]

    yaml_str = """
counter: !class:Counter()
a: !ref:counter.next_list()
b: !ref:counter.next_list()
"""
    result = load(yaml_str)
    # Each call to next_list() is independent
    assert isinstance(result["a"], list)
    assert isinstance(result["b"], list)


def test_ref_method_call_in_class_kwargs() -> None:
    """!ref:obj.method() works as a kwarg inside a !class: tag."""

    @configurable
    class Provider:
        def __init__(self) -> None:
            pass

        def get_value(self) -> int:
            return 42

    @configurable
    class Consumer:
        def __init__(self, value: int = 0) -> None:
            self.value = value

    yaml_str = """
provider: !class:Provider()
consumer: !class:Consumer()
  value: !ref:provider.get_value()
"""
    result = load(yaml_str)
    assert result["consumer"].value == 42


def test_ref_method_call_unresolvable_passthrough() -> None:
    """Unresolvable method refs are passed through as Reference objects."""
    from confluid.fluid import Reference

    yaml_str = """
result: !ref:nonexistent.method()
"""
    result = load(yaml_str)
    assert isinstance(result["result"], Reference)


def test_ref_dotted_attribute() -> None:
    """!ref:obj.attr resolves attribute access on a flowed object."""

    @configurable
    class Config:
        def __init__(self, name: str = "default") -> None:
            self.name = name

    yaml_str = """
cfg: !class:Config()
  name: hello
val: !ref:cfg.name
"""
    result = load(yaml_str)
    assert result["val"] == "hello"
