from typing import Any

import pytest

from confluid import configurable, configure, get_registry, load, register


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


def test_an_addressed_list_at_an_annotated_body_slot_lands_on_the_LOAD_path() -> None:
    """The load path applies a class-block LIST to an annotated body slot — like configure().

    ``TrainClass`` declares ``self.transforms: list[Any] = [...]``, so ``slots()`` and the
    accept-list both carry the slot with its ``list`` type. ``configure()`` has always
    applied ``TrainClass: {transforms: [...]}``; the load path refused the same block as
    a value ("block has no attribute") because its ``_get_param_kinds`` walked the
    signature alone and never saw the body slot's annotation. One class, one block, two
    answers — the cross-path drift class this suite exists to pin.
    """
    register(TrainClass)  # the autouse fixture cleared the registry

    result = load(
        """
train:
  _target_: TrainClass
TrainClass:
  transforms: [7]
"""
    )
    assert result["train"].transforms == [7]

    # And configure() gives the same answer for the same block — the parity claim.
    live = TrainClass()
    configure(live, config="TrainClass:\n  transforms: [7]\n")
    assert live.transforms == [7]


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
