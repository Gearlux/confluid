"""Tests for :func:`confluid.schema.get_hierarchy_from_instance`.

These exercise the "walk the live flowed graph" path used by Liquify's
config-aware ``--help``. Fixtures stay small — each class isolates one
rule from the docstring so failures point at the exact rule.
"""

from typing import Any, List, Optional

import pytest

from confluid import configurable, get_hierarchy_from_instance


@configurable
class SimpleLeaf:
    """Simple @configurable with plain primitives.

    Args:
        x: First number.
        y: Second value, optional.
    """

    def __init__(self, x: int = 1, y: Optional[str] = None) -> None:
        self.x = x
        self.y = y


@configurable
class Parent:
    """Parent with a configurable child.

    Args:
        child: The leaf to nest.
        tag: A label.
    """

    def __init__(self, child: SimpleLeaf, tag: str = "root") -> None:
        self.child = child
        self.tag = tag


class PlainClass:
    """Not @configurable — walker must show ctor args as leaves only."""

    def __init__(self, a: int = 7, b: str = "hi") -> None:
        self.a = a
        self.b = b


@configurable
class ConfigWithPlain:
    """Configurable that holds a plain (non-configurable) attribute."""

    def __init__(self, plain: PlainClass) -> None:
        self.plain = plain


@configurable
class PostConstructionToggle:
    """Mimics the Enable wrapper — kwarg set via setattr after __init__."""

    def __init__(self, op: Any) -> None:
        self.op = op
        # `visualize` is NOT declared in __init__; Confluid / tests set it later.


@configurable
class WithList:
    """Holds a list of @configurable children."""

    def __init__(self, items: List[SimpleLeaf]) -> None:
        self.items = items


def _flags_for(hierarchy: dict) -> dict:
    """Helper: map each hierarchy path to its short leaf name (last segment) for assertions."""
    return {path.split(".")[-1]: path for path in hierarchy}


def test_simple_configurable_enumerates_ctor_params() -> None:
    obj = SimpleLeaf(x=42, y="hello")
    h = get_hierarchy_from_instance(obj)
    assert "SimpleLeaf.x" in h
    assert "SimpleLeaf.y" in h
    type_str, value, doc = h["SimpleLeaf.x"]
    assert value == 42
    assert "First number" in doc


def test_default_values_visible_even_when_not_explicitly_set() -> None:
    """The user never passed x=... but the default should still surface."""
    obj = SimpleLeaf()
    h = get_hierarchy_from_instance(obj)
    _, x_val, _ = h["SimpleLeaf.x"]
    _, y_val, _ = h["SimpleLeaf.y"]
    assert x_val == 1
    assert y_val is None


def test_configurable_recursion_builds_dotted_path() -> None:
    obj = Parent(child=SimpleLeaf(x=9), tag="T")
    h = get_hierarchy_from_instance(obj)
    # Parent's own leaf
    assert "Parent.tag" in h
    # Child's params reachable via dotted path
    assert "Parent.child.SimpleLeaf.x" in h
    _, v, _ = h["Parent.child.SimpleLeaf.x"]
    assert v == 9


def test_non_configurable_is_one_level_deep() -> None:
    obj = ConfigWithPlain(plain=PlainClass(a=100, b="end"))
    h = get_hierarchy_from_instance(obj)
    # Plain's ctor args must appear exactly once (one level)
    assert "ConfigWithPlain.plain.PlainClass.a" in h
    assert "ConfigWithPlain.plain.PlainClass.b" in h
    # No deeper recursion — PlainClass is not @configurable, so no post-construction keys.
    assert not any(p.startswith("ConfigWithPlain.plain.PlainClass.a.") for p in h)


def test_post_construction_setattr_surfaces() -> None:
    obj = PostConstructionToggle(op=None)
    obj.visualize = True  # type: ignore[attr-defined]  # the Enable pattern
    h = get_hierarchy_from_instance(obj)
    assert "PostConstructionToggle.visualize" in h
    type_str, val, doc = h["PostConstructionToggle.visualize"]
    assert val is True
    assert type_str == "bool"
    assert doc == ""  # no class-level docstring for setattr keys


def test_list_of_configurables_uses_index_suffix() -> None:
    obj = WithList(items=[SimpleLeaf(x=1), SimpleLeaf(x=2)])
    h = get_hierarchy_from_instance(obj)
    assert "WithList.items[0].SimpleLeaf.x" in h
    assert "WithList.items[1].SimpleLeaf.x" in h
    _, v0, _ = h["WithList.items[0].SimpleLeaf.x"]
    _, v1, _ = h["WithList.items[1].SimpleLeaf.x"]
    assert (v0, v1) == (1, 2)


def test_cycle_safe() -> None:
    """A self-referential loop in vars() must not hang the walker."""
    obj = SimpleLeaf()
    obj.loop = obj  # type: ignore[attr-defined]  # cycle via post-construction attr
    h = get_hierarchy_from_instance(obj)  # must return (not recurse forever)
    # The ctor params still surface.
    assert "SimpleLeaf.x" in h


def test_dict_input_maps_to_key_prefixed_paths() -> None:
    """LiquifyApp.liquify returns a dict; top-level keys prefix the paths."""
    container = {"processor": Parent(child=SimpleLeaf(x=5), tag="T")}
    h = get_hierarchy_from_instance(container)
    # Keys of the input dict become path prefixes
    assert any("processor.Parent.tag" in p for p in h)
    assert any("processor.Parent.child.SimpleLeaf.x" in p for p in h)


def test_primitive_root_returns_empty() -> None:
    assert get_hierarchy_from_instance(42) == {}
    assert get_hierarchy_from_instance("hello") == {}
    assert get_hierarchy_from_instance(None) == {}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
