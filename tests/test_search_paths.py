"""Config-file search-path resolution — local tiers then XDG base dirs.

Every test isolates the process environment (``XDG_CONFIG_HOME``,
``XDG_CONFIG_DIRS``, ``HOME``) and chdirs into ``tmp_path`` so a developer's
real ``~/.config`` can never leak into (or out of) a run. The app name is a
process global restored by the autouse ``_app_name_isolation`` fixture in
``conftest.py``.
"""

from pathlib import Path

import pytest

from confluid import (
    ConfigFileNotFoundError,
    dump,
    load,
    load_config,
    load_config_with_paths,
    materialize,
    resolve_config_path,
    set_app_name,
)


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Sandbox CWD + every env var the XDG lookup reads; return the XDG home."""
    xdg_home = tmp_path / "xdg"
    xdg_home.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_home))
    monkeypatch.setenv("XDG_CONFIG_DIRS", str(tmp_path / "xdg_sys"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    return xdg_home


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


# ---------------------------------------------------------------------------
# Tier precedence
# ---------------------------------------------------------------------------


def test_sibling_beats_every_other_tier(tmp_path: Path, _isolated_env: Path) -> None:
    """An include next to the including file wins over CWD, CWD/config and XDG."""
    sub = tmp_path / "sub"
    _write(sub / "common.yaml", "who: sibling")
    _write(tmp_path / "common.yaml", "who: cwd")
    _write(tmp_path / "config" / "common.yaml", "who: cwd_config")
    _write(_isolated_env / "common.yaml", "who: xdg")
    main = _write(sub / "main.yaml", "include: common.yaml")

    assert load_config(main)["who"] == "sibling"


def test_cwd_beats_cwd_config_and_xdg(tmp_path: Path, _isolated_env: Path) -> None:
    _write(tmp_path / "common.yaml", "who: cwd")
    _write(tmp_path / "config" / "common.yaml", "who: cwd_config")
    _write(_isolated_env / "common.yaml", "who: xdg")
    main = _write(tmp_path / "sub" / "main.yaml", "include: common.yaml")

    assert load_config(main)["who"] == "cwd"


def test_cwd_config_beats_xdg(tmp_path: Path, _isolated_env: Path) -> None:
    _write(tmp_path / "config" / "common.yaml", "who: cwd_config")
    _write(_isolated_env / "common.yaml", "who: xdg")
    main = _write(tmp_path / "sub" / "main.yaml", "include: common.yaml")

    assert load_config(main)["who"] == "cwd_config"


def test_xdg_is_last_resort_for_includes(tmp_path: Path, _isolated_env: Path) -> None:
    _write(_isolated_env / "common.yaml", "who: xdg")
    main = _write(tmp_path / "sub" / "main.yaml", "include: common.yaml\nlocal: 1")

    data = load_config(main)
    assert data["who"] == "xdg"
    assert data["local"] == 1


# ---------------------------------------------------------------------------
# Top-level path resolution
# ---------------------------------------------------------------------------


def test_top_level_found_in_cwd_config(tmp_path: Path) -> None:
    _write(tmp_path / "config" / "exp.yaml", "val: 7")
    assert load_config("exp.yaml")["val"] == 7


def test_top_level_found_in_xdg(_isolated_env: Path) -> None:
    _write(_isolated_env / "exp.yaml", "val: 8")
    assert load_config("exp.yaml")["val"] == 8


def test_absolute_path_bypasses_search(tmp_path: Path, _isolated_env: Path) -> None:
    """An absolute path is used verbatim — even when an XDG twin exists."""
    _write(_isolated_env / "exp.yaml", "val: 8")
    target = _write(tmp_path / "elsewhere" / "exp.yaml", "val: 9")
    assert load_config(target)["val"] == 9

    missing = tmp_path / "nope" / "exp.yaml"
    with pytest.raises(ConfigFileNotFoundError):
        load_config(missing)


def test_miss_raises_with_searched_locations(tmp_path: Path) -> None:
    with pytest.raises(ConfigFileNotFoundError, match="searched:"):
        load_config("does_not_exist.yaml")


def test_resolve_config_path_returns_input_on_total_miss(tmp_path: Path) -> None:
    assert resolve_config_path("does_not_exist.yaml") == Path("does_not_exist.yaml")


# ---------------------------------------------------------------------------
# XDG specifics
# ---------------------------------------------------------------------------


def test_xdg_config_home_unset_falls_back_to_home_dot_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("XDG_CONFIG_HOME")
    _write(tmp_path / "home" / ".config" / "exp.yaml", "val: 10")
    assert load_config("exp.yaml")["val"] == 10


def test_empty_xdg_config_home_treated_as_unset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", "")
    _write(tmp_path / "home" / ".config" / "exp.yaml", "val: 11")
    assert load_config("exp.yaml")["val"] == 11


def test_xdg_config_dirs_multi_entry_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first = tmp_path / "sys_a"
    second = tmp_path / "sys_b"
    monkeypatch.setenv("XDG_CONFIG_DIRS", f"{first}:{second}")

    _write(second / "exp.yaml", "who: second")
    assert load_config("exp.yaml")["who"] == "second"

    _write(first / "exp.yaml", "who: first")
    assert load_config("exp.yaml")["who"] == "first"


def test_xdg_config_home_beats_config_dirs(
    tmp_path: Path, _isolated_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sys_dir = tmp_path / "xdg_sys"
    _write(sys_dir / "exp.yaml", "who: system")
    _write(_isolated_env / "exp.yaml", "who: home")
    assert load_config("exp.yaml")["who"] == "home"


# ---------------------------------------------------------------------------
# App-name namespacing
# ---------------------------------------------------------------------------


def test_app_name_dir_beats_confluid_dir(_isolated_env: Path) -> None:
    set_app_name("myapp")
    _write(_isolated_env / "confluid" / "exp.yaml", "who: confluid")
    assert load_config("exp.yaml")["who"] == "confluid"

    _write(_isolated_env / "myapp" / "exp.yaml", "who: myapp")
    assert load_config("exp.yaml")["who"] == "myapp"


def test_app_name_set_skips_bare_base_dir(_isolated_env: Path) -> None:
    """With an app name, the un-namespaced <base>/<file> tier is NOT consulted."""
    set_app_name("myapp")
    _write(_isolated_env / "exp.yaml", "who: bare")
    with pytest.raises(ConfigFileNotFoundError):
        load_config("exp.yaml")


def test_no_app_name_uses_bare_base_dir_only(_isolated_env: Path) -> None:
    set_app_name(None)
    _write(_isolated_env / "myapp" / "exp.yaml", "who: myapp")
    _write(_isolated_env / "confluid" / "exp.yaml", "who: confluid")
    with pytest.raises(ConfigFileNotFoundError):
        load_config("exp.yaml")

    _write(_isolated_env / "exp.yaml", "who: bare")
    assert load_config("exp.yaml")["who"] == "bare"


# ---------------------------------------------------------------------------
# Integration with the rest of the loader
# ---------------------------------------------------------------------------


def test_load_string_with_colon_never_triggers_lookup(_isolated_env: Path) -> None:
    """A scalar YAML string containing ':' parses as YAML, not as a file path."""
    _write(_isolated_env / "val" / "x.yaml", "should: never_load")
    assert load("who: inline", flow=False) == {"who": "inline"}


def test_load_bare_name_resolves_via_xdg(_isolated_env: Path) -> None:
    _write(_isolated_env / "exp.yaml", "val: 12")
    assert load("exp.yaml", flow=False) == {"val": 12}


def test_load_config_with_paths_records_xdg_resolved_path(_isolated_env: Path) -> None:
    xdg_file = _write(_isolated_env / "common.yaml", "base: 1")
    main = _write(Path.cwd() / "main.yaml", "include: common.yaml\nlocal: 2")

    data, paths = load_config_with_paths(main)
    assert data == {"base": 1, "local": 2}
    assert paths == [main.resolve(), xdg_file.resolve()]


def test_circular_include_across_tiers(tmp_path: Path, _isolated_env: Path) -> None:
    """A CWD file including an XDG file that includes it back is detected."""
    _write(tmp_path / "a.yaml", "include: b.yaml")
    # The XDG-side include names a.yaml, which resolves back to the CWD file.
    _write(_isolated_env / "b.yaml", "include: a.yaml")

    with pytest.raises(ValueError, match="Circular include"):
        load_config("a.yaml")


def test_round_trip_of_xdg_resolved_config(_isolated_env: Path) -> None:
    """A !class: config loaded via XDG materializes and dump/reloads identically."""
    from confluid import configurable

    @configurable
    class Widget:
        """A widget.

        Args:
            size: The widget size.
        """

        def __init__(self, size: int = 1) -> None:
            self.size = size

    _write(_isolated_env / "widget.yaml", "w: !class:Widget()\nsize: 3")

    built = materialize(load_config("widget.yaml"))
    assert isinstance(built["w"], Widget)
    assert built["w"].size == 3

    reloaded = materialize(load(dump(built), flow=False))
    assert isinstance(reloaded["w"], Widget)
    assert reloaded["w"].size == 3
