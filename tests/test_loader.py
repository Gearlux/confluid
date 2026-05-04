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
    from confluid.fluid import Class, Reference

    config_file = tmp_path / "tags.yaml"
    config_file.write_text("model: !class:Model\n  layers: 10\nref: !ref:base_lr")

    data = load_config(config_file)
    # Tags produce Class/Reference objects
    assert isinstance(data["model"], Class)
    assert data["model"].target == "Model"
    assert data["model"].kwargs["layers"] == 10
    assert isinstance(data["ref"], Reference)
    assert data["ref"].target == "base_lr"


def test_load_config_root_level_class(tmp_path: Path) -> None:
    """Top-level `!class:` documents must round-trip via the path loader.

    The text loader (`confluid.loader.load(text)`) already handles a root
    Fluid (loader.py:183); the path loader must be symmetric so callers
    that point at a YAML file containing a single class doc don't blow
    up in `_process_imports` (which assumes a dict).
    """
    from confluid.fluid import Class

    config_file = tmp_path / "root_class.yaml"
    config_file.write_text("!class:Model\nlayers: 10\nactivation: relu\n")

    data = load_config(config_file)
    assert isinstance(data, Class)
    assert data.target == "Model"
    assert data.kwargs["layers"] == 10
    assert data.kwargs["activation"] == "relu"
