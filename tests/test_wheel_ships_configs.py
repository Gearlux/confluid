"""The wheel ships every YAML config of ``config/``, ``examples/`` and ``graphs/`` where ``pkg://`` finds it.

``pkg://<distribution>/<path>`` (confluid) reads a wheel-installed config from ``share/<distribution>/<path>``
(``setup.py``). The wheel is built from a COPY of the files git sees here (tracked, plus untracked ones not ignored),
never from the checkout itself: pip builds in place, and setuptools copies whatever a leftover ``build/lib`` holds into
the next wheel, so a stale tree could hide a missing file.
"""

import importlib.util
import shutil
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path
from typing import List, Tuple

import pytest

ROOT = Path(__file__).resolve().parent.parent
DISTRIBUTION = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["name"].lower().replace("_", "-")


def _source_copy(dest: Path) -> Path:
    """The files git sees in this project, copied to ``dest`` — no ``build/``, no ``*.egg-info``."""
    listing = ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"]
    names = subprocess.run(listing, cwd=ROOT, check=True, capture_output=True, text=True).stdout.split("\0")
    for name in names:
        source = ROOT / name
        if name and source.is_file():  # a tracked file deleted in the working tree is listed, but gone
            (dest / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, dest / name)
    return dest


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Tuple[Path, List[str]]:
    """The source copy and the names of the wheel built from it — one build for every test of the module."""
    tmp_path = tmp_path_factory.mktemp("wheel")
    source = _source_copy(tmp_path / "source")
    isolation = [] if importlib.util.find_spec("setuptools") is None else ["--no-build-isolation"]
    out = tmp_path / "wheel"
    command = [sys.executable, "-m", "pip", "wheel", str(source), "--no-deps", "-q", "-w", str(out), *isolation]
    subprocess.run(command, check=True, capture_output=True)
    return source, zipfile.ZipFile(next(out.glob("*.whl"))).namelist()


def test_the_wheel_ships_every_yaml_config_where_a_pkg_reference_finds_it(built: Tuple[Path, List[str]]) -> None:
    source, names = built
    expected = sorted(
        path.relative_to(source).as_posix()
        for top in ("config", "examples", "graphs")
        if (source / top).is_dir()
        for path in (source / top).rglob("*.yaml")
        if not any(part.startswith(".") for part in path.relative_to(source).parts)
    )
    marker = f".data/data/share/{DISTRIBUTION}/"
    shipped = sorted(name.split(marker, 1)[1] for name in names if marker in name)
    assert expected, "the project has no YAML config to ship — the test reads the wrong folders"
    assert shipped == expected


def test_the_wheel_ships_the_package_and_not_the_editor_extension(built: Tuple[Path, List[str]]) -> None:
    source, names = built
    assert (source / "editors" / "vscode" / "package.json").is_file(), "the extension is not where the test looks"
    assert "confluid/locate.py" in names
    assert not [name for name in names if "editors/" in name or name.endswith((".ts", ".vsix"))]
