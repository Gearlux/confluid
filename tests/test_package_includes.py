"""``pkg://<distribution>/<path>`` — a config file named by the installed distribution that ships it.

Every test installs fake distributions into a site folder of its own (``tmp_path``) and puts it first on
``sys.path``, so ``importlib.metadata`` finds them exactly as it finds a real install:

* an EDITABLE install carries ``direct_url.json`` (PEP 610) naming its checkout — the file is read there;
* a WHEEL install lists its files in ``RECORD`` — a shipped config sits at ``<prefix>/share/<distribution>/<path>``.

The CWD and the XDG tiers are sandboxed as in ``test_search_paths.py``, so no real config can leak in.
"""

import importlib
import json
from pathlib import Path
from typing import Dict, Optional

import pytest
from click.testing import CliRunner

from confluid import ConfigFileNotFoundError, ConfigurationError, load, resolve_config_path
from confluid.cli import hydraide


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A sandboxed CWD and XDG lookup, and an empty site folder first on ``sys.path``; returns the prefix."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("XDG_CONFIG_DIRS", str(tmp_path / "xdg_sys"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    prefix = tmp_path / "venv"
    site = prefix / "lib" / "python3" / "site-packages"
    site.mkdir(parents=True)
    monkeypatch.syspath_prepend(str(site))
    importlib.invalidate_caches()
    return prefix


def _site(prefix: Path) -> Path:
    return prefix / "lib" / "python3" / "site-packages"


def _dist_info(prefix: Path, name: str, version: str = "1.0") -> Path:
    """An installed distribution's metadata folder, named as an installer names it (``demo_5g-1.0.dist-info``)."""
    info = _site(prefix) / f"{name.replace('-', '_')}-{version}.dist-info"
    info.mkdir()
    (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n")
    importlib.invalidate_caches()
    return info


def _install_editable(prefix: Path, name: str, checkout: Path, files: Dict[str, str]) -> Path:
    """``uv pip install -e <checkout>``: metadata naming the checkout, the files in the checkout only."""
    for rel, text in files.items():
        (checkout / rel).parent.mkdir(parents=True, exist_ok=True)
        (checkout / rel).write_text(text)
    info = _dist_info(prefix, name)
    (info / "direct_url.json").write_text(
        json.dumps({"url": checkout.resolve().as_uri(), "dir_info": {"editable": True}})
    )
    (info / "RECORD").write_text(f"{info.name}/METADATA,,\n")
    return checkout


def _install_wheel(
    prefix: Path, name: str, files: Dict[str, str], share_name: Optional[str] = None, direct_url: bool = False
) -> Path:
    """``pip install <wheel>``: each config under ``<prefix>/share/<share_name>/``, listed in ``RECORD``."""
    share = prefix / "share" / (share_name or name)
    info = _dist_info(prefix, name)
    record = [f"{info.name}/METADATA,,"]
    for rel, text in files.items():
        (share / rel).parent.mkdir(parents=True, exist_ok=True)
        (share / rel).write_text(text)
        record.append(f"../../../share/{share_name or name}/{rel},sha256=x,{len(text)}")
    (info / "RECORD").write_text("\n".join(record) + "\n")
    if direct_url:  # `pip install ./checkout` (not editable): built into a wheel, then installed
        (info / "direct_url.json").write_text(json.dumps({"url": "file:///somewhere/demo", "dir_info": {}}))
    return share


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


# ---------------------------------------------------------------------------
# Where the file is read
# ---------------------------------------------------------------------------


def test_an_editable_distribution_is_read_from_its_checkout(tmp_path: Path, _isolated: Path) -> None:
    checkout = _install_editable(_isolated, "demo", tmp_path / "checkout", {"config/a.yaml": "who: checkout"})

    assert resolve_config_path("pkg://demo/config/a.yaml") == checkout.resolve() / "config" / "a.yaml"
    main = _write(tmp_path / "main.yaml", "include: pkg://demo/config/a.yaml\n")
    assert load(main, until="raw")["who"] == "checkout"


def test_a_wheel_installed_distribution_is_read_from_the_file_it_shipped(tmp_path: Path, _isolated: Path) -> None:
    share = _install_wheel(_isolated, "demo", {"config/a.yaml": "who: wheel"})

    assert resolve_config_path("pkg://demo/config/a.yaml") == (share / "config" / "a.yaml").resolve()
    main = _write(tmp_path / "main.yaml", "include: pkg://demo/config/a.yaml\n")
    assert load(main, until="raw")["who"] == "wheel"


def test_a_distribution_installed_from_a_folder_but_not_editable_reads_its_wheel_files(
    tmp_path: Path, _isolated: Path
) -> None:
    """``pip install ./checkout`` records the folder in ``direct_url.json`` without ``editable``: it is a wheel."""
    share = _install_wheel(_isolated, "demo", {"config/a.yaml": "who: built"}, direct_url=True)

    assert resolve_config_path("pkg://demo/config/a.yaml") == (share / "config" / "a.yaml").resolve()


def test_a_relative_include_inside_a_shipped_file_reads_its_shipped_neighbour(tmp_path: Path, _isolated: Path) -> None:
    """A distribution's own includes stay relative: the shipped copy keeps the checkout's layout."""
    _install_wheel(
        _isolated,
        "demo",
        {
            "config/sets/a.yaml": "include: ../../examples/base.yaml\nwho: a\n",
            "examples/base.yaml": "base: shipped\nwho: base\n",
        },
    )
    _write(tmp_path / "work" / "examples" / "base.yaml", "base: cwd\n")  # a same-named file elsewhere is not read

    main = _write(tmp_path / "main.yaml", "include: pkg://demo/config/sets/a.yaml\n")
    assert load(main, until="raw") == {"base": "shipped", "who": "a"}


def test_a_shipped_file_may_include_another_distributions_file(tmp_path: Path, _isolated: Path) -> None:
    _install_wheel(_isolated, "demo", {"config/a.yaml": "include: pkg://demo-5g/examples/b.yaml\nwho: a\n"})
    _install_wheel(_isolated, "demo-5g", {"examples/b.yaml": "from_5g: true\n"})

    main = _write(tmp_path / "main.yaml", "include: pkg://demo/config/a.yaml\n")
    assert load(main, until="raw") == {"from_5g": True, "who": "a"}


# ---------------------------------------------------------------------------
# Distributions whose names share a prefix (`demo`, `demo-5g`, `demo-lte`)
# ---------------------------------------------------------------------------


def test_distributions_sharing_a_name_prefix_each_read_their_own_wheel_files(tmp_path: Path, _isolated: Path) -> None:
    for name in ("demo", "demo-5g", "demo-lte"):
        _install_wheel(_isolated, name, {"config/a.yaml": f"who: {name}\n"})

    for name in ("demo", "demo-5g", "demo-lte"):
        assert load(f"pkg://{name}/config/a.yaml", until="raw") == {"who": name}


def test_distributions_sharing_a_name_prefix_each_read_their_own_checkout(tmp_path: Path, _isolated: Path) -> None:
    for name in ("demo", "demo-5g", "demo-lte"):
        _install_editable(_isolated, name, tmp_path / name, {"config/a.yaml": f"who: {name}\n"})

    for name in ("demo", "demo-5g", "demo-lte"):
        assert load(f"pkg://{name}/config/a.yaml", until="raw") == {"who": name}


def test_a_file_only_a_prefixed_neighbour_ships_is_not_found_for_the_shorter_name(
    tmp_path: Path, _isolated: Path
) -> None:
    """``demo`` ships no ``examples/b.yaml``; ``demo-5g`` does — ``pkg://demo/examples/b.yaml`` must not find it."""
    _install_wheel(_isolated, "demo", {"config/a.yaml": "who: demo\n"})
    _install_wheel(_isolated, "demo-5g", {"examples/b.yaml": "who: demo-5g\n"})

    with pytest.raises(
        ConfigFileNotFoundError, match=r"pkg://demo/examples/b\.yaml: demo 1\.0 ships no examples/b\.yaml"
    ):
        resolve_config_path("pkg://demo/examples/b.yaml")


def test_the_name_is_matched_as_installers_normalize_it(tmp_path: Path, _isolated: Path) -> None:
    """``demo_5g``, ``Demo.5G`` and ``demo-5g`` are one distribution (PEP 503), shipping under ``share/demo-5g``."""
    _install_wheel(_isolated, "demo-5g", {"config/a.yaml": "who: demo-5g\n"})

    for spelling in ("demo-5g", "demo_5g", "Demo.5G"):
        assert load(f"pkg://{spelling}/config/a.yaml", until="raw") == {"who": "demo-5g"}


# ---------------------------------------------------------------------------
# The config handed to load(), and scope blocks
# ---------------------------------------------------------------------------


def test_load_takes_a_package_reference_as_its_config(tmp_path: Path, _isolated: Path) -> None:
    share = _install_wheel(_isolated, "demo", {"config/a.yaml": "who: entry\n"})

    data, paths = load("pkg://demo/config/a.yaml", until="raw", return_paths=True)
    assert data == {"who": "entry"}
    assert paths == [(share / "config" / "a.yaml").resolve()]


def test_a_package_include_inside_a_scope_block_is_read_when_the_block_is_active(
    tmp_path: Path, _isolated: Path
) -> None:
    _install_wheel(_isolated, "demo", {"config/fast.yaml": "speed: fast\n"})
    main = _write(
        tmp_path / "main.yaml", "speed: slow\nquick:\n  _scope_: { fast: }\n  include: pkg://demo/config/fast.yaml\n"
    )

    assert load(main, until="document")["speed"] == "slow"
    assert load(main, until="document", scopes=["fast"])["speed"] == "fast"


def test_an_inactive_scope_block_never_looks_its_package_up(tmp_path: Path, _isolated: Path) -> None:
    main = _write(
        tmp_path / "main.yaml", "speed: slow\nquick:\n  _scope_: { fast: }\n  include: pkg://not-installed/x.yaml\n"
    )

    assert load(main, until="document")["speed"] == "slow"


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


def test_an_include_of_a_distribution_not_installed_names_the_including_file(tmp_path: Path, _isolated: Path) -> None:
    main = _write(tmp_path / "main.yaml", "include: pkg://not-installed/config/a.yaml\n")

    with pytest.raises(ConfigFileNotFoundError) as caught:
        load(main, until="raw")
    assert str(caught.value) == (
        f"{main} includes pkg://not-installed/config/a.yaml: the distribution 'not-installed' is not installed"
    )


def test_a_file_the_wheel_does_not_ship_is_named_with_the_distributions_version(
    tmp_path: Path, _isolated: Path
) -> None:
    _install_wheel(_isolated, "demo", {"config/a.yaml": "who: wheel\n"})
    main = _write(tmp_path / "main.yaml", "include: pkg://demo/config/b.yaml\n")

    with pytest.raises(ConfigFileNotFoundError) as caught:
        load(main, until="raw")
    assert str(caught.value) == f"{main} includes pkg://demo/config/b.yaml: demo 1.0 ships no config/b.yaml"


def test_a_file_missing_from_the_editable_checkout_names_the_checkout(tmp_path: Path, _isolated: Path) -> None:
    checkout = _install_editable(_isolated, "demo", tmp_path / "checkout", {"config/a.yaml": "who: checkout\n"})

    with pytest.raises(ConfigFileNotFoundError) as caught:
        load("pkg://demo/config/b.yaml", until="raw")
    assert str(caught.value) == (
        f"pkg://demo/config/b.yaml: demo 1.0 (editable, {checkout.resolve()}) has no config/b.yaml"
    )


@pytest.mark.parametrize(
    "reference, why",
    [
        ("pkg://", "names no distribution"),
        ("pkg:///config/a.yaml", "names no distribution"),
        ("pkg://demo", "names no file of demo"),
        ("pkg://demo/", "names no file of demo"),
        ("pkg://demo/../other/a.yaml", "uses '..'"),
        ("pkg://demo/config/../a.yaml", "uses '..'"),
    ],
)
def test_a_malformed_package_reference_is_refused(reference: str, why: str) -> None:
    with pytest.raises(ConfigurationError) as caught:
        resolve_config_path(reference)
    assert str(caught.value) == f"{reference}: a pkg:// reference {why} — write pkg://<distribution>/<path>"


def test_a_malformed_package_include_names_the_including_file(tmp_path: Path) -> None:
    main = _write(tmp_path / "main.yaml", "include: pkg://demo\n")

    with pytest.raises(ConfigurationError, match=rf"^{main} includes pkg://demo: a pkg:// reference names no file"):
        load(main, until="raw")


def test_an_ordinary_path_never_reads_installed_distributions(tmp_path: Path, _isolated: Path) -> None:
    """Only the ``pkg://`` prefix reaches the distributions: a plain relative include keeps its tiers."""
    _install_wheel(_isolated, "demo", {"config/a.yaml": "who: wheel\n"})
    _write(tmp_path / "work" / "config" / "a.yaml", "who: cwd\n")

    main = _write(tmp_path / "main.yaml", "include: config/a.yaml\n")
    assert load(main, until="raw")["who"] == "cwd"


# ---------------------------------------------------------------------------
# hydraide
# ---------------------------------------------------------------------------


def test_hydraide_emits_a_config_named_by_its_package(tmp_path: Path, _isolated: Path) -> None:
    _install_wheel(_isolated, "demo", {"config/a.yaml": "who: wheel\n"})

    result = CliRunner().invoke(hydraide, ["emit", "pkg://demo/config/a.yaml"])
    assert result.exit_code == 0, result.output
    assert result.output == "who: wheel\n"


def test_hydraide_refuses_a_package_config_it_cannot_find_as_a_usage_error(_isolated: Path) -> None:
    result = CliRunner().invoke(hydraide, ["emit", "pkg://not-installed/a.yaml"])
    assert result.exit_code == 2
    assert "pkg://not-installed/a.yaml: the distribution 'not-installed' is not installed" in result.output
