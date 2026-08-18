from typing import Any

import pytest

import confluid
from confluid import Target, configurable, load


@pytest.fixture(autouse=True)
def setup_registry() -> None:
    confluid.get_registry().clear()

    @configurable
    class MockSource:
        def __init__(self, count: int = 10) -> None:
            self.count = count

    @configurable
    class MockStream:
        def __init__(self, source: Any = None) -> None:
            self.source = source

    @configurable
    class MockProcessor:
        def __init__(self, stream: Any = None) -> None:
            self.stream = stream


def test_repro_dotted_override_into_tagged_class() -> None:
    """
    Scenario: Root uses a tagged class, and we override a nested attribute of that class.
    Mirrors: DatasetProcessor.stream.source.count
    """
    config = {
        "MockProcessor": {"stream": Target("MockStream", source=Target("MockSource", count=10))},
        "MockProcessor.stream.source.count": 5,  # Dotted override
    }

    # 1. Load config (simulating Liquify bootstrap)
    resolved = load(config, until="document")

    # 2. Materialize the processor
    # We pass the block associated with the class name as an Target marker
    processor_block = resolved.get("MockProcessor")
    marker = Target("MockProcessor")
    marker.kwargs.update(processor_block if isinstance(processor_block, dict) else {})
    instance = load(marker)

    assert instance.stream.source.count == 5


# Scope-based repro tests moved to liquifai/tests/test_scope_advanced.py.
