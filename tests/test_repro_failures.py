from typing import Any

import pytest

import confluid
from confluid import Instance, configurable, load, materialize


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
    # We pass the block associated with the class name as an Instance marker
    processor_block = resolved.get("MockProcessor")
    marker = Instance("MockProcessor")
    marker.kwargs.update(processor_block if isinstance(processor_block, dict) else {})
    instance = materialize(marker)

    assert instance.flux.source.count == 5


# Scope-based repro tests moved to liquifai/tests/test_scope_advanced.py.
