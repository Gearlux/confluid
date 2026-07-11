"""Shared test fixtures — process-global state isolation.

The confluid registry is a process-wide singleton, so without isolation a
test that registers (or clears) classes bleeds into every later test — the
suite's health then depends on execution order (pytest-randomly makes this a
live hazard, and 17 of 39 test files historically carried no cleanup fixture
at all).

``_registry_isolation`` (autouse, function-scoped) SNAPSHOTS the registry's
backing indices before every test and RESTORES them afterwards. Snapshot +
restore — not ``clear()`` — so module-level ``@configurable`` registrations
(which many test files rely on) survive into each test, while anything a test
adds or removes is undone on teardown. Per-file ``setup_registry`` clear
fixtures remain valid: they run inside the snapshot window, and the restore
puts the world back regardless.
"""

from typing import Iterator

import pytest

from confluid import get_registry

# The registry's backing index dicts (mirrors marainer/tests/test_presets.py's
# snapshot pattern). Values are copied one level deep so re-registration in a
# test can't mutate the snapshot's category/group/task/role buckets.
_REGISTRY_INDEXES = ("_classes", "_objects", "_by_category", "_by_group", "_by_task", "_by_role")


@pytest.fixture(autouse=True)
def _registry_isolation() -> Iterator[None]:
    reg = get_registry()
    snapshot = {
        name: {k: (v.copy() if hasattr(v, "copy") else v) for k, v in getattr(reg, name).items()}
        for name in _REGISTRY_INDEXES
    }
    try:
        yield
    finally:
        for name, value in snapshot.items():
            index = getattr(reg, name)
            index.clear()
            index.update(value)
