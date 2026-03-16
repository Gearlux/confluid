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

    configure(
        obj,
        config="""
value: 1
a.value: 2
a.b.value: 3
a.b.c.value: 4
a.b.c.d.value: 5
""",
    )

    assert obj.value == 2  # Matches "a.value"
    assert obj.child.value == 3  # Matches "a.b.value"
    assert obj.child.child.value == 4  # Matches "a.b.c.value"
    assert obj.child.child.child.value == 5  # Matches "a.b.c.d.value"
