"""Tests for :func:`confluid.schema.get_hierarchy_from_instance`.

These exercise the "walk the live flowed graph" path used by Liquify's
config-aware ``--help``. Fixtures stay small — each class isolates one
rule from the docstring so failures point at the exact rule.
"""

from typing import Any, List, Optional

import pytest

from confluid import configurable, get_hierarchy_from_instance, shortest_unique_paths


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


def test_named_instance_uses_name_as_path_segment() -> None:
    """When an @configurable instance has a `.name` attr, use it as the path segment."""
    obj = SimpleLeaf(x=7)
    obj.name = "my_leaf"  # type: ignore[attr-defined]
    h = get_hierarchy_from_instance(obj)
    # Path uses "my_leaf" instead of "SimpleLeaf"
    assert "my_leaf.x" in h
    assert not any(p.startswith("SimpleLeaf.") for p in h)


def test_sibling_named_instances_yield_distinct_paths() -> None:
    """Two Parents with distinct names → their children's paths don't collide."""
    a = Parent(child=SimpleLeaf(x=1), tag="A")
    b = Parent(child=SimpleLeaf(x=2), tag="B")
    a.name = "overlay"  # type: ignore[attr-defined]
    b.name = "labelstudio"  # type: ignore[attr-defined]
    container = {"a": a, "b": b}
    h = get_hierarchy_from_instance(container)
    # Each instance's children live under its name, not a shared class segment.
    assert "a.overlay.tag" in h
    assert "b.labelstudio.tag" in h
    assert "a.overlay.child.SimpleLeaf.x" in h
    assert "b.labelstudio.child.SimpleLeaf.x" in h


def test_unnamed_instance_falls_back_to_class_name() -> None:
    """Instances without `.name` keep class-name segments (back-compat)."""
    obj = Parent(child=SimpleLeaf(x=9), tag="T")
    h = get_hierarchy_from_instance(obj)
    # No .name set → class-name segments reinstate.
    assert "Parent.tag" in h
    assert "Parent.child.SimpleLeaf.x" in h


def test_primitive_root_returns_empty() -> None:
    assert get_hierarchy_from_instance(42) == {}
    assert get_hierarchy_from_instance("hello") == {}
    assert get_hierarchy_from_instance(None) == {}


class TestShortestUniquePaths:
    def test_empty_list(self) -> None:
        assert shortest_unique_paths([]) == {}

    def test_single_path_keeps_leaf(self) -> None:
        result = shortest_unique_paths(["Root.child.leaf"])
        assert result == {"Root.child.leaf": "leaf"}

    def test_non_colliding_leaves_reduce_to_leaf(self) -> None:
        paths = ["Root.a", "Root.b.c"]
        result = shortest_unique_paths(paths)
        assert result == {"Root.a": "a", "Root.b.c": "c"}

    def test_colliding_leaves_extend_suffix(self) -> None:
        paths = ["Root.optimizer.lr", "Root.scheduler.lr"]
        result = shortest_unique_paths(paths)
        # ``lr`` is ambiguous → use the parent segment
        assert result == {"Root.optimizer.lr": "optimizer.lr", "Root.scheduler.lr": "scheduler.lr"}

    def test_path_that_is_suffix_of_another_disambiguated(self) -> None:
        paths = ["Node.name", "Node.b.name"]
        result = shortest_unique_paths(paths)
        # ``name`` matches both; ``b.name`` only matches the second; ``Node.name`` only matches the first.
        assert result["Node.b.name"] == "b.name"
        assert result["Node.name"] == "Node.name"

    def test_unrelated_paths_each_unique(self) -> None:
        paths = ["LightningTrainer.experiment_name", "LightningTrainer.run_name"]
        result = shortest_unique_paths(paths)
        assert result == {
            "LightningTrainer.experiment_name": "experiment_name",
            "LightningTrainer.run_name": "run_name",
        }


class NonConfigurableLeakyParent:
    """Non-``@configurable`` parent whose ``__init__`` plants non-underscore attrs.

    Mimics ``torch.nn.Module`` / ``pytorch_lightning.LightningModule``: when a
    ``@configurable`` subclass calls ``super().__init__()``, these attributes
    end up on every instance via ``vars()`` but are NOT part of the
    configurable surface.
    """

    LEAKY_CLASS_CONSTANT = "noise"

    def __init__(self) -> None:
        self.parent_set = "should_not_leak"
        self.training = True


@configurable
class ConfigurableChildOfLeakyParent(NonConfigurableLeakyParent):
    """``@configurable`` subclass — ``ChildAttrs`` IS configurable surface, parent isn't."""

    def __init__(self, child_attr: int = 7) -> None:
        super().__init__()
        self.child_attr = child_attr
        self.child_post_init = "user_visible"


def test_inherited_non_configurable_init_attrs_filtered() -> None:
    """``parent_set`` / ``training`` set by non-``@configurable`` parent's ``__init__`` must NOT surface."""
    obj = ConfigurableChildOfLeakyParent(child_attr=42)
    h = get_hierarchy_from_instance(obj)
    assert "ConfigurableChildOfLeakyParent.child_attr" in h
    assert "ConfigurableChildOfLeakyParent.child_post_init" in h
    # Anything from the leaky parent is suppressed
    assert not any("parent_set" in path for path in h)
    assert not any("training" in path for path in h)
    assert not any("LEAKY_CLASS_CONSTANT" in path for path in h)


@configurable
class ConfigurableParent:
    """``@configurable`` parent — its post-init attrs MUST surface in subclasses."""

    def __init__(self, parent_param: int = 1) -> None:
        self.parent_param = parent_param
        self.parent_post_init = "should_surface"


@configurable
class ConfigurableChild(ConfigurableParent):
    def __init__(self, parent_param: int = 1, child_param: str = "kid") -> None:
        super().__init__(parent_param=parent_param)
        self.child_param = child_param


def test_configurable_parent_attrs_preserved() -> None:
    """The MRO chain walks through ``@configurable`` ancestors — their setattrs stay visible."""
    obj = ConfigurableChild(parent_param=99, child_param="hi")
    h = get_hierarchy_from_instance(obj)
    assert "ConfigurableChild.parent_param" in h
    assert "ConfigurableChild.child_param" in h
    assert "ConfigurableChild.parent_post_init" in h
    _, val, _ = h["ConfigurableChild.parent_post_init"]
    assert val == "should_surface"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
