from typing import Any

import pytest

from confluid import configurable, configure, get_registry


@pytest.fixture(autouse=True)
def setup_registry() -> None:
    get_registry().clear()


@configurable
class Transform:
    def __init__(self, name: str = "noise") -> None:
        self.noise_std = 0.1
        self.name = name


@configurable
class Container:
    def __init__(self) -> None:
        self.items = [
            Transform(name="noise"),
            Transform(name="blur"),
        ]


def test_simple_name_matching() -> None:
    """Test that objects can be configured by their name attribute."""
    container = Container()

    configure(
        container,
        config="""
noise.noise_std: 0.5
""",
    )

    assert container.items[0].noise_std == 0.5  # name='noise'
    assert container.items[1].noise_std == 0.1  # name='blur', unchanged


def test_hierarchical_name_path() -> None:
    @configurable
    class Inner:
        def __init__(self, name: str = "inner") -> None:
            self.value = 1
            self.name = name

    @configurable
    class Middle:
        def __init__(self, name: str = "middle") -> None:
            self.inner = Inner(name="inner")
            self.value = 2
            self.name = name

    @configurable
    class Outer:
        def __init__(self) -> None:
            self.middle = Middle(name="middle")
            self.value = 3

    obj = Outer()
    configure(
        obj,
        config="""
value: 10
middle.value: 20
middle.inner.value: 30
""",
    )

    assert obj.value == 10
    assert obj.middle.value == 20
    assert obj.middle.inner.value == 30


def test_deeply_nested_name_path() -> None:
    @configurable
    class Level:
        def __init__(self, name: str = "level", child: Any = None) -> None:
            self.value = 0
            self.name = name
            if child:
                self.child = child

    obj = Level(
        name="a",
        child=Level(name="b", child=Level(name="c", child=Level(name="d"))),
    )

    # A chain of INSTANCE names (`a.b.c.value`) was a configure()-only grammar — the load path
    # never routed by names past the first level, and configure() runs through the document
    # since record 19 phase 4. The spelling both paths share is the ATTRIBUTE path from a
    # NAMED object. (A bare `value:` beside these would win at every level it reaches — a
    # dotted override merges into the marker at the MARKER's position, so a bare key written
    # after the object outranks it, on both paths; the ordering suites pin that.)
    configure(
        a=obj,
        config="""
a.value: 2
a.child.value: 3
a.child.child.value: 4
a.child.child.child.value: 5
""",
    )

    assert obj.value == 2
    assert obj.child.value == 3
    assert obj.child.child.value == 4
    assert obj.child.child.child.value == 5
