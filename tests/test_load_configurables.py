"""Pins for :func:`confluid.load_configurables` — the entry-point bootstrap.

The ``confluid.configurables`` entry-point group carries confluid's name, so
confluid owns the loader: one call imports every declared module (running its
``@configurable`` side effects) with PER-ENTRY error collection — a broken
package logs one warning and lands in the returned dict as the exception
instance, and the other packages' registrations still happen.

The fake plugins are real files on a ``tmp_path`` sys.path entry (not
pre-seeded ``sys.modules`` stubs), so the import — and therefore the
registration side effect — genuinely happens INSIDE ``load_configurables``.
``importlib.metadata.entry_points`` is monkeypatched to serve ONLY the fakes
for the group; the real workspace entries must never be imported by this
suite. Log assertions monkeypatch ``confluid.registry.logger`` with a
collecting stub — loggair does not propagate into stdlib logging, so
``caplog`` cannot capture it (the ``test_validation.py`` pattern).
"""

import importlib.metadata
import inspect
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, List, Sequence

import pytest

from confluid import get_registry, load_configurables

_GROUP = "confluid.configurables"


def _serve_entries(monkeypatch: pytest.MonkeyPatch, entries: Sequence[importlib.metadata.EntryPoint]) -> None:
    """Serve exactly ``entries`` for the confluid group; every other group stays real."""
    real_entry_points = importlib.metadata.entry_points

    def patched(**params: Any) -> Any:
        if params.get("group") == _GROUP:
            return list(entries)
        return real_entry_points(**params)

    monkeypatch.setattr(importlib.metadata, "entry_points", patched)


def _write_module(tmp_path: Path, name: str, body: str) -> None:
    (tmp_path / f"{name}.py").write_text(body)


def test_load_configurables_imports_the_group_and_registers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Loading the group imports each entry's module; its decorators fill the registry."""
    _write_module(
        tmp_path,
        "fake_cfg_ok_plugin",
        "from confluid import configurable\n"
        "\n"
        "\n"
        "@configurable(category='loss')\n"
        "class FakeEntryPointLoss:\n"
        "    def __init__(self, weight: float = 1.0) -> None:\n"
        "        self.weight = weight\n",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    _serve_entries(
        monkeypatch,
        [importlib.metadata.EntryPoint(name="ok", value="fake_cfg_ok_plugin", group=_GROUP)],
    )
    assert "fake_cfg_ok_plugin" not in sys.modules  # the import must happen inside the loader
    try:
        loaded = load_configurables()
    finally:
        module = sys.modules.pop("fake_cfg_ok_plugin", None)

    assert set(loaded) == {"ok"}
    assert loaded["ok"] is module and module is not None
    assert "FakeEntryPointLoss" in get_registry().list_classes(category="loss")


def test_a_broken_entry_is_collected_and_the_rest_still_load(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """One broken package: ONE warning, the exception in the dict, the other entry unharmed.

    The broken entry deliberately sorts FIRST so the pin also proves the loop
    continues past a failure instead of blanking the remaining registrations.
    """
    _write_module(tmp_path, "fake_cfg_broken_plugin", "raise ImportError('boom at import')\n")
    _write_module(
        tmp_path,
        "fake_cfg_survivor_plugin",
        "from confluid import configurable\n"
        "\n"
        "\n"
        "@configurable\n"
        "class FakeSurvivor:\n"
        "    def __init__(self, n: int = 0) -> None:\n"
        "        self.n = n\n",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    _serve_entries(
        monkeypatch,
        [
            importlib.metadata.EntryPoint(name="broken", value="fake_cfg_broken_plugin", group=_GROUP),
            importlib.metadata.EntryPoint(name="survivor", value="fake_cfg_survivor_plugin", group=_GROUP),
        ],
    )

    import confluid.registry as registry_module

    seen: List[str] = []
    monkeypatch.setattr(
        registry_module,
        "logger",
        SimpleNamespace(warning=seen.append, debug=lambda m: None, trace=lambda m: None),
    )

    try:
        loaded = load_configurables()
    finally:
        sys.modules.pop("fake_cfg_survivor_plugin", None)
        sys.modules.pop("fake_cfg_broken_plugin", None)

    assert isinstance(loaded["broken"], ImportError)
    assert "boom at import" in str(loaded["broken"])
    assert not isinstance(loaded["survivor"], BaseException)
    assert "FakeSurvivor" in get_registry().list_classes()
    assert [msg for msg in seen if "broken" in msg and "boom at import" in msg] == seen  # exactly the one warning
    assert len(seen) == 1


def test_the_default_group_is_confluid_configurables() -> None:
    """The group default is the convention the workspace's pyproject blocks declare."""
    assert inspect.signature(load_configurables).parameters["group"].default == "confluid.configurables"
