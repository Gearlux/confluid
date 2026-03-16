from typing import Any

import pytest
from confluid import configurable, configure, get_registry


@pytest.fixture(autouse=True)
def setup_registry() -> None:
    get_registry().clear()


@configurable
class Delegate:
    def __init__(self, delegate: Any, enabled: bool = True, name: str = "noise"):
        self.delegate = delegate
        self.enabled = enabled
        self.name = name


@configurable
class TransformClass:
    def __init__(self, name: str = "noise"):
        self.noise_std = 0.1
        self.name = name


@configurable
class TrainClass:
    def __init__(self) -> None:
        self.transforms: list[Any] = [
            TransformClass(),
            Delegate(delegate=TransformClass(), enabled=True),
        ]


def test_config_transforms_broadcast() -> None:
    """Test that a global attribute key applies to all matching configurable objects."""
    train_instance = TrainClass()

    # Configure nested objects individually via broadcast
    configure(
        train_instance,
        config="""
noise_std: 0.5
""",
    )

    # Verify configuration was applied to both transforms
    assert train_instance.transforms[0].noise_std == 0.5
    assert train_instance.transforms[1].delegate.noise_std == 0.5


def test_named_config_transforms_scoping() -> None:
    """Test that instance_name.attribute correctly scopes the configuration."""
    train_instance = TrainClass()
    # transforms[0].name is "noise" (default)
    # transforms[1].name is "noise" (default)

    configure(
        train_instance,
        config="""
noise.noise_std: 0.8
""",
    )

    # Both have the name "noise", so both should be updated
    assert train_instance.transforms[0].noise_std == 0.8
    assert train_instance.transforms[1].delegate.noise_std == 0.8


def test_class_name_scoping() -> None:
    """Test that ClassName.attribute correctly scopes the configuration."""
    train_instance = TrainClass()

    configure(
        train_instance,
        config="""
TransformClass.noise_std: 0.2
""",
    )

    # Both are instances of TransformClass (one nested), so both should update
    assert train_instance.transforms[0].noise_std == 0.2
    assert train_instance.transforms[1].delegate.noise_std == 0.2


def test_named_config_transforms_mixed() -> None:
    """Test that Class.instance.attribute scoping works."""
    train_instance = TrainClass()
    train_instance.transforms[0].name = "first"
    train_instance.transforms[1].name = "second"

    configure(
        train_instance,
        config="""
TransformClass.first.noise_std: 0.9
second.enabled: False
""",
    )

    assert train_instance.transforms[0].noise_std == 0.9
    assert train_instance.transforms[1].enabled is False
    assert train_instance.transforms[1].delegate.noise_std == 0.1  # Unchanged
