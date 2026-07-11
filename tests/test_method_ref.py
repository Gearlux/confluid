"""Tests for !ref:obj.method() — method call references."""

from typing import Any

import pytest

from confluid import configurable, get_registry, load


@pytest.fixture(autouse=True)
def setup_registry() -> None:
    get_registry().clear()


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


# --- unified rich resolver (resolve_reference_path) pins ---------------------


def test_resolve_reference_path_multi_level_attribute_walk() -> None:
    """``a.b.c`` walks THROUGH object attributes (the old resolver only did
    single-level ``<context-key>.<attr>``)."""

    @configurable
    class Inner:
        def __init__(self, value: int = 7) -> None:
            self.value = value

    @configurable
    class Holder:
        def __init__(self, inner: object = None) -> None:
            self.inner = inner

    yaml_str = """
holder: !class:Holder()
  inner: !class:Inner()
    value: 42
deep: !ref:holder.inner.value
"""
    result = load(yaml_str)
    assert result["deep"] == 42


def test_resolve_reference_path_bracket_then_attribute() -> None:
    """``packs[0].name`` mixes a list index with an attribute step."""

    @configurable
    class Pack:
        def __init__(self, name: str = "") -> None:
            self.name = name

    yaml_str = """
packs:
  - !class:Pack()
    name: alpha
  - !class:Pack()
    name: beta
chosen: !ref:packs[1].name
"""
    result = load(yaml_str)
    assert result["chosen"] == "beta"


def test_resolve_reference_path_dict_key_wins_over_attribute() -> None:
    """On a dict cursor the walk uses KEY lookup only — a same-named attribute
    (e.g. ``dict.values``) is never reached, and a missing key is a miss."""
    from confluid.resolver import resolve_reference_path

    ctx = {"cfg": {"values": [1, 2, 3]}}
    assert resolve_reference_path("cfg.items", ctx) is None  # dict.items NOT called


def test_resolve_reference_path_pure_structural_stays_deferred() -> None:
    """A purely structural path (dict/list only) returns None from the rich
    resolver — the deferred-Reference machinery owns it (late-binding for
    post-load overrides like ``--drone_index``)."""
    from confluid.resolver import resolve_reference_path

    ctx = {"labels": ["a", "b"], "idx": 1, "nested": {"x": 5}}
    assert resolve_reference_path("labels[idx]", ctx) is None
    assert resolve_reference_path("nested.x", ctx) is None
