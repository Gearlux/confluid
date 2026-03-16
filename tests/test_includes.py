from pathlib import Path

import pytest
from confluid import load, load_config


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


def test_load_with_active_scopes(tmp_path: Path) -> None:
    config_file = tmp_path / "app.yaml"
    config_file.write_text("""
val: 1
debug:
  val: 10
""")

    # Test load with programmatic scope
    data = load(config_file, scopes=["debug"])
    assert data["val"] == 10
