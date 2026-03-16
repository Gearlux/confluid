from pathlib import Path

from confluid import configurable
from confluid.loader import load, materialize


@configurable
class MockComponent:
    def __init__(self, settings: dict):
        self.settings = settings
        # This will fail if settings['value'] is a string instead of an int
        self.result = settings["value"] + 10


def test_recursive_include_with_nested_refs(tmp_path: Path) -> None:
    """
    Reproduces the Waivefront/TorchSig failure:
    A class argument 'config' includes a file with internal references.
    """
    from confluid import register

    register(MockComponent)

    # 1. Official template
    template_content = """
base_val: 100
value: !ref:base_val
"""
    template_file = tmp_path / "template.yaml"
    template_file.write_text(template_content)

    # 2. Main config
    main_content = f"""
comp: !class:MockComponent
  settings:
    include:
      - {template_file.name}
    base_val: 50  # Local override
"""
    main_file = tmp_path / "main.yaml"
    main_file.write_text(main_content)

    import os

    orig_cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        # Load returns a dict containing our instances
        result = load("main.yaml", flow=False)
        instance = materialize(result["comp"], context=result)

        assert instance.settings["base_val"] == 50
        assert instance.settings["value"] == 50
        assert instance.result == 60
    finally:
        os.chdir(orig_cwd)
