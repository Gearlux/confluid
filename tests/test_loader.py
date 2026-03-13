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


def test_load_config_with_import() -> None:
    import tempfile

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:

        # Use a standard module that is always available
        f.write("import: [os, sys]\n")
        path = f.name

    try:
        data = load_config(path)
        assert data == {}  # import is popped
    finally:
        import os

        os.unlink(path)


def test_load_with_custom_tags(tmp_path: Path) -> None:
    config_file = tmp_path / "tags.yaml"
    config_file.write_text("model: !class:Model(layers=10)\nref: !ref:base_lr")

    data = load_config(config_file)
    # Tags produce flat markers in the raw load_config output
    assert data["model"]["_confluid_class_"] == "Model"
    assert data["model"]["layers"] == "10"
    assert data["ref"]["_confluid_ref_"] == "base_lr"
