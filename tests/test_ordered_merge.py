"""Tests for the flat-view, ordered last-write-wins materialization model.

Each entry in the YAML has a document position. When a class materializes,
its visible context is the original document with the descent-path keys
popped; matching scalars are applied in document order, last-wins. Explicit
kwargs are no longer privileged — they take their slot at their YAML position
just like any broadcast.
"""

from typing import Any

import pytest

from confluid import configurable, get_registry, load


@configurable
class Store:
    def __init__(self, version: str = "default") -> None:
        self.version = version


@configurable
class Exporter:
    def __init__(self, store: Any = None) -> None:
        self.store = store


@pytest.fixture(autouse=True)
def setup_registry() -> None:
    """Re-register module-level classes after any prior test clears the registry."""
    get_registry().register_class(Store, name="Store")
    get_registry().register_class(Exporter, name="Exporter")


def test_top_level_after_nested_wins() -> None:
    """Top-level ``version`` appears AFTER the nested store's ``version`` in
    document order, so it wins under flat-view ordered last-wins."""
    yaml_text = """
exporter: !class:Exporter()
  store: !class:Store()
    version: train
version: test
"""
    result = load(yaml_text)
    assert result["exporter"].store.version == "test"


def test_top_level_before_nested_loses() -> None:
    """Top-level ``version`` appears BEFORE the nested store's ``version``,
    so the nested explicit value wins (it's later in doc order)."""
    yaml_text = """
version: test
exporter: !class:Exporter()
  store: !class:Store()
    version: train
"""
    result = load(yaml_text)
    assert result["exporter"].store.version == "train"


def test_intermediate_class_does_not_filter() -> None:
    """``Exporter`` does NOT accept ``version``, but the store still sees the
    top-level ``version`` directly via flat-view (no parent-chain accept-list
    filtering at intermediate hops)."""
    yaml_text = """
exporter: !class:Exporter()
  store: !class:Store()
    version: train
version: prod
"""
    result = load(yaml_text)
    # version landed on store even though Exporter has no `version` kwarg.
    assert result["exporter"].store.version == "prod"
