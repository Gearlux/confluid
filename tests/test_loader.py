from pathlib import Path

import pytest

from confluid import load_config


def test_load_config_valid(tmp_path: Path) -> None:
    yaml_file = tmp_path / "config.yaml"
    yaml_file.write_text("Model:\n  layers: 10")

    data = load_config(yaml_file)
    assert data["Model"]["layers"] == 10


def test_load_config_empty(tmp_path: Path) -> None:
    yaml_file = tmp_path / "empty.yaml"
    yaml_file.write_text("")

    data = load_config(yaml_file)
    assert data == {}


def test_load_config_not_found() -> None:
    with pytest.raises(FileNotFoundError):
        load_config("non_existent.yaml")
