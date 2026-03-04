from typing import Any, List

import pytest

from confluid import configurable, configure, get_registry


@pytest.fixture(autouse=True)
def setup_registry() -> None:
    get_registry().clear()


@configurable
class TransformClass:
    def __init__(self, name: str = "noise"):
        self.noise_std = 0.1
        self.name = name


@configurable
class Delegate:
    def __init__(self, delegate: Any, enabled: bool = True, name: str = "delegate"):
        self.delegate = delegate
        self.enabled = enabled
        self.name = name


@configurable
class Compose:
    def __init__(self, transforms: List[Any], name: str = "compose"):
        self.transforms = transforms
        self.name = name


def test_config_transforms_broadcast() -> None:
    """Verify that a top-level key applies to all matching attributes in a hierarchy."""
    pipeline = Compose(
        transforms=[TransformClass(name="noise1"), Delegate(delegate=TransformClass(name="noise2"), name="wrapper")]
    )

    configure(
        pipeline,
        config="""
noise_std: 0.5
""",
    )

    assert pipeline.transforms[0].noise_std == 0.5
    assert pipeline.transforms[1].delegate.noise_std == 0.5


def test_config_transforms_scoping() -> None:
    """Verify that scoped paths correctly target specific objects."""
    pipeline = Compose(
        transforms=[TransformClass(name="noise1"), Delegate(delegate=TransformClass(name="noise2"), name="wrapper")]
    )

    configure(
        pipeline,
        config="""
noise1.noise_std: 0.8
wrapper.delegate.noise_std: 0.9
""",
    )

    assert pipeline.transforms[0].noise_std == 0.8
    assert pipeline.transforms[1].delegate.noise_std == 0.9


def test_mixed_class_and_name_scoping() -> None:
    """Verify ClassName.name.attribute priority."""
    pipeline = Compose(transforms=[TransformClass(name="noise1"), TransformClass(name="noise2")])

    configure(
        pipeline,
        config="""
TransformClass.noise_std: 0.2
TransformClass.noise1.noise_std: 0.3
""",
    )

    assert pipeline.transforms[0].noise_std == 0.3  # Specific name path wins
    assert pipeline.transforms[1].noise_std == 0.2  # Generic class path wins
