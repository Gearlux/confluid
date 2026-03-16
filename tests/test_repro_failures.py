from typing import Any

import pytest

import confluid
from confluid import configurable, load, materialize


@pytest.fixture(autouse=True)
def setup_registry() -> None:
    confluid.get_registry().clear()

    @configurable
    class MockSource:
        def __init__(self, count: int = 10) -> None:
            self.count = count

    @configurable
    class MockFlux:
        def __init__(self, source: Any = None) -> None:
            self.source = source

    @configurable
    class MockProcessor:
        def __init__(self, flux: Any = None) -> None:
            self.flux = flux


def test_repro_dotted_override_into_tagged_class() -> None:
    """
    Scenario: Root uses a tagged class, and we override a nested attribute of that class.
    Mirrors: DatasetProcessor.flux.source.count
    """
    config = {
        "MockProcessor": {"flux": "!class:MockFlux(source=!class:MockSource(count=10))"},
        "MockProcessor.flux.source.count": 5,  # Dotted override
    }

    # 1. Load config (simulating Liquify bootstrap)
    resolved = load(config, flow=False)

    # 2. Materialize the processor
    # We pass the block associated with the class name, injecting the marker
    processor_block = resolved.get("MockProcessor")
    marker_dict = {
        "_confluid_class_": "MockProcessor",
        **(processor_block if isinstance(processor_block, dict) else {}),
    }
    instance = materialize(marker_dict)

    assert instance.flux.source.count == 5


def test_repro_scope_override_into_tagged_class() -> None:
    """
    Scenario: A scope provides a dotted override for a tagged class in the root.
    """
    config = {
        "MockProcessor": {"flux": "!class:MockFlux(source=!class:MockSource(count=10))"},
        "debug": {"MockProcessor.flux.source.count": 2},
    }

    # Load with 'debug' scope
    resolved = load(config, scopes=["debug"], flow=False)

    processor_block = resolved.get("MockProcessor")
    marker_dict = {
        "_confluid_class_": "MockProcessor",
        **(processor_block if isinstance(processor_block, dict) else {}),
    }
    instance = materialize(marker_dict)

    assert instance.flux.source.count == 2


def test_repro_scope_replacing_class() -> None:
    """
    Scenario: A scope replaces an entire tagged class with another one.
    """

    @configurable
    class SimpleModel:
        def __init__(self, layers: int = 3) -> None:
            self.layers = layers

    @configurable
    class ComplexModel:
        def __init__(self, layers: int = 10) -> None:
            self.layers = layers

    @configurable
    class Trainer:
        def __init__(self, model: Any = None) -> None:
            self.model = model

    config = {
        "Trainer": {"model": "!class:SimpleModel"},
        "heavy": {"Trainer.model": "!class:ComplexModel"},
    }

    # Load with 'heavy' scope
    resolved = load(config, scopes=["heavy"], flow=False)

    trainer_block = resolved.get("Trainer")
    marker_dict = {
        "_confluid_class_": "Trainer",
        **(trainer_block if isinstance(trainer_block, dict) else {}),
    }
    instance = materialize(marker_dict)

    assert isinstance(instance.model, ComplexModel)
    assert instance.model.layers == 10
