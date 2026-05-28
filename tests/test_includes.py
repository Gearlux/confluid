from pathlib import Path

import pytest

from confluid import load_config, load_config_with_paths


def test_file_includes(tmp_path: Path) -> None:
    common = tmp_path / "common.yaml"
    common.write_text("base_val: 1\nshared: True")

    main = tmp_path / "main.yaml"
    main.write_text("include: common.yaml\nmain_val: 2\nshared: False")

    data = load_config(main)
    assert data["base_val"] == 1
    assert data["main_val"] == 2
    # main should override common
    assert data["shared"] is False


def test_circular_include_error(tmp_path: Path) -> None:
    a = tmp_path / "a.yaml"
    b = tmp_path / "b.yaml"

    a.write_text("include: b.yaml")
    b.write_text("include: a.yaml")

    with pytest.raises(ValueError, match="Circular include"):
        load_config(a)


def test_load_config_with_paths_single_file(tmp_path: Path) -> None:
    """Single-file load returns the entrypoint as the only path."""
    main = tmp_path / "main.yaml"
    main.write_text("foo: 1\nbar: 2")

    data, paths = load_config_with_paths(main)
    assert data == {"foo": 1, "bar": 2}
    assert paths == [main.resolve()]


def test_load_config_with_paths_returns_full_tree(tmp_path: Path) -> None:
    """Nested includes appear in load order, deduplicated, with the entrypoint first."""
    common = tmp_path / "common.yaml"
    common.write_text("base_val: 1")

    extra = tmp_path / "extra.yaml"
    extra.write_text("extra_val: 9")

    main = tmp_path / "main.yaml"
    main.write_text("include:\n  - common.yaml\n  - extra.yaml\nmain_val: 2")

    data, paths = load_config_with_paths(main)
    assert data["base_val"] == 1
    assert data["extra_val"] == 9
    assert data["main_val"] == 2

    resolved = [p.resolve() for p in paths]
    assert resolved[0] == main.resolve()
    assert common.resolve() in resolved
    assert extra.resolve() in resolved
    # Deduplicated
    assert len(resolved) == len(set(resolved))


def test_load_config_with_paths_threadlocal_isolation(tmp_path: Path) -> None:
    """A bare ``load_config`` call inside a ``load_config_with_paths`` block does
    not pollute outer-scope accumulators, and successive calls each get a
    fresh list (no leakage between invocations)."""
    main_a = tmp_path / "a.yaml"
    main_a.write_text("a: 1")
    main_b = tmp_path / "b.yaml"
    main_b.write_text("b: 2")

    _, paths_a = load_config_with_paths(main_a)
    _, paths_b = load_config_with_paths(main_b)

    assert paths_a == [main_a.resolve()]
    assert paths_b == [main_b.resolve()]
    # No bleed-through.
    assert main_b.resolve() not in paths_a
    assert main_a.resolve() not in paths_b


def test_load_config_with_paths_circular_error(tmp_path: Path) -> None:
    """Circular includes still raise even when going through ``load_config_with_paths``."""
    a = tmp_path / "a.yaml"
    b = tmp_path / "b.yaml"
    a.write_text("include: b.yaml")
    b.write_text("include: a.yaml")

    with pytest.raises(ValueError, match="Circular include"):
        load_config_with_paths(a)
