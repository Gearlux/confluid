"""Tests for ``confluid.env.load_workspace_env``."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from confluid.env import load_workspace_env


def _write_env(env_dir: Path, contents: str) -> Path:
    env_path = env_dir / ".env"
    env_path.write_text(contents)
    return env_path


def test_loads_nearest_env_and_returns_required_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATA_ROOT", raising=False)
    _write_env(tmp_path, f"DATA_ROOT={tmp_path}\n")

    result = load_workspace_env(start=tmp_path)

    assert result == {"DATA_ROOT": str(tmp_path)}


def test_walks_up_to_find_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATA_ROOT", raising=False)
    _write_env(tmp_path, f"DATA_ROOT={tmp_path}\n")
    deep = tmp_path / "a" / "b" / "c"
    deep.mkdir(parents=True)

    result = load_workspace_env(start=deep)

    assert result == {"DATA_ROOT": str(tmp_path)}


def test_raises_when_no_env_found(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Isolate from any host .env files higher up the tree.
    original_exists = Path.exists

    def patched_exists(self: Path) -> bool:
        if self.name == ".env":
            return False
        return original_exists(self)

    monkeypatch.setattr(Path, "exists", patched_exists)

    with pytest.raises(RuntimeError, match="No .env file found"):
        load_workspace_env(start=tmp_path)


def test_raises_when_required_key_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATA_ROOT", raising=False)
    _write_env(tmp_path, "OTHER=value\n")

    with pytest.raises(RuntimeError, match="DATA_ROOT is not set"):
        load_workspace_env(start=tmp_path)


def test_raises_when_required_path_does_not_exist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    missing = tmp_path / "does_not_exist"
    monkeypatch.delenv("DATA_ROOT", raising=False)
    _write_env(tmp_path, f"DATA_ROOT={missing}\n")

    with pytest.raises(RuntimeError, match="does not exist on this machine"):
        load_workspace_env(start=tmp_path)


def test_custom_require_skips_path_validation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FOO", raising=False)
    _write_env(tmp_path, "FOO=bar\n")

    result = load_workspace_env(start=tmp_path, require=("FOO",), require_paths=())

    assert result == {"FOO": "bar"}


def test_override_flag_controls_existing_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    old = tmp_path / "old"
    new = tmp_path / "new"
    old.mkdir()
    new.mkdir()
    _write_env(tmp_path, f"DATA_ROOT={new}\n")

    monkeypatch.setenv("DATA_ROOT", str(old))
    assert load_workspace_env(start=tmp_path) == {"DATA_ROOT": str(old)}

    monkeypatch.setenv("DATA_ROOT", str(old))
    assert load_workspace_env(start=tmp_path, override=True) == {"DATA_ROOT": str(new)}


def test_not_reexported_from_top_level() -> None:
    """``load_workspace_env`` is deliberately NOT on the curated top-level
    surface (2026-07 API pruning) — import it from ``confluid.env``."""
    import confluid

    assert "load_workspace_env" not in confluid.__all__


def test_default_start_uses_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATA_ROOT", raising=False)
    _write_env(tmp_path, f"DATA_ROOT={tmp_path}\n")
    monkeypatch.chdir(tmp_path)

    assert load_workspace_env() == {"DATA_ROOT": str(tmp_path)}
    # Sanity check that os.environ was actually mutated by dotenv.
    assert os.environ["DATA_ROOT"] == str(tmp_path)
