"""Workspace environment loader.

Walks up from a starting directory to locate the nearest ``.env`` file,
loads it via ``python-dotenv``, and validates that the keys the workspace
relies on (default: ``DATA_ROOT``) are present and -- for path-typed keys
-- point at an existing filesystem location.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

from confluid.exceptions import WorkspaceEnvError


def load_workspace_env(
    start: Path | None = None,
    *,
    require: tuple[str, ...] = ("DATA_ROOT",),
    require_paths: tuple[str, ...] = ("DATA_ROOT",),
    override: bool = False,
) -> dict[str, str]:
    """Locate and load the nearest workspace ``.env`` file.

    Walks from ``start`` (default: ``Path.cwd()``) up the parent chain,
    loads the first ``.env`` encountered, and returns the values for
    ``require`` keys after validating they are set. Keys also listed in
    ``require_paths`` must additionally resolve to an existing path.

    Raises :class:`confluid.WorkspaceEnvError` (a ``RuntimeError``) with an
    actionable message when no ``.env`` is found, a required key is missing,
    or a path-typed value does not exist on disk.
    """
    here = Path(start) if start is not None else Path.cwd()
    env_path: Path | None = None
    for candidate in [here, *here.parents]:
        probe = candidate / ".env"
        if probe.exists():
            env_path = probe
            load_dotenv(env_path, override=override)
            break
    if env_path is None:
        raise WorkspaceEnvError(f"No .env file found walking up from {here} -- create one at the workspace root.")

    resolved: dict[str, str] = {}
    for key in require:
        value = os.environ.get(key)
        if not value:
            raise WorkspaceEnvError(f"{key} is not set -- add it to {env_path} (e.g. {key}=/Volumes/Store).")
        if key in require_paths and not Path(value).exists():
            raise WorkspaceEnvError(
                f"{key}={value!r} does not exist on this machine -- mount the volume or update {env_path}."
            )
        resolved[key] = value
    return resolved
