from typing import Optional

from confluid import configurable
from confluid.schema import get_hierarchy


def test_basic_hierarchy():
    @configurable
    class Leaf:
        def __init__(self, val: int = 1):
            """
            Args:
                val: A leaf value.
            """
            self.val = val

    @configurable
    class Root:
        def __init__(self, leaf: Leaf, ratio: float = 0.5):
            """
            Args:
                leaf: The leaf component.
                ratio: The root ratio.
            """
            self.leaf = leaf
            self.ratio = ratio

    hierarchy = get_hierarchy(Root)

    assert "Root.ratio" in hierarchy
    assert hierarchy["Root.ratio"] == ("float", 0.5, "The root ratio.")
    assert "Root.leaf" in hierarchy
    # type_str for Leaf might be 'Leaf' or its string representation
    assert "Root.Leaf.val" in hierarchy
    assert hierarchy["Root.Leaf.val"] == ("int", 1, "A leaf value.")


def test_hierarchy_instance():
    @configurable
    class MyClass:
        def __init__(self, name: str = "default"):
            self.name = name

    instance = MyClass(name="instance_name")
    hierarchy = get_hierarchy(instance)
    # The code uses instance.name if available
    assert "instance_name.name" in hierarchy
