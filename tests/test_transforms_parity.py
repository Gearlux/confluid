from typing import Any, List

import pytest

from confluid import configurable, configure, get_registry

# Class names are deliberately distinct from ``test_parity.py``'s: confluid's registry
# keys by name, so two same-named, same-tagged classes in two test modules are a genuine
# clobber — one silently replaced the other (and now warns) even though each file clears
# the registry in its own fixture, because module-level decorators run at IMPORT.


@pytest.fixture(autouse=True)
def setup_registry() -> None:
    get_registry().clear()


@configurable
class NoiseTransform:
    def __init__(self, name: str = "noise"):
        self.noise_std = 0.1
        self.name = name


@configurable
class TransformDelegate:
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
        transforms=[
            NoiseTransform(name="noise1"),
            TransformDelegate(delegate=NoiseTransform(name="noise2"), name="wrapper"),
        ]
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
        transforms=[
            NoiseTransform(name="noise1"),
            TransformDelegate(delegate=NoiseTransform(name="noise2"), name="wrapper"),
        ]
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
    pipeline = Compose(transforms=[NoiseTransform(name="noise1"), NoiseTransform(name="noise2")])

    configure(
        pipeline,
        config="""
NoiseTransform.noise_std: 0.2
NoiseTransform.noise1.noise_std: 0.3
""",
    )

    assert pipeline.transforms[0].noise_std == 0.3  # Specific name path wins
    assert pipeline.transforms[1].noise_std == 0.2  # Generic class path wins
