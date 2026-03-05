import tempfile
from pathlib import Path

import pytest

from confluid import configurable, get_registry, load


@pytest.fixture(autouse=True)
def setup_registry() -> None:
    get_registry().clear()


@configurable
class Service:
    def __init__(self, port: int = 80, env: str = "dev") -> None:
        self.port = port
        self.env = env


def test_hierarchical_and_negative_scopes() -> None:
    """Verify prod.gpu inherits from prod, and 'not prod' works."""
    config = """
env: 'base'
port: 80

prod:
  env: 'production'
  port: 443

prod.gpu:
  gpu_enabled: True

not prod:
  env: 'development'
"""
    # 1. Test 'not prod' (Active scope: debug)
    obj = load(config, scopes=["debug"])
    # Should have applied 'not prod' because 'prod' is not active
    assert obj["env"] == "development"
    assert obj["port"] == 80

    # 2. Test 'prod.gpu' inheritance (Active scope: prod.gpu)
    obj = load(config, scopes=["prod.gpu"])
    # Should have applied 'prod' (parent) then 'prod.gpu' (child)
    assert obj["env"] == "production"
    assert obj["port"] == 443
    assert obj["gpu_enabled"] is True
    # Should NOT have applied 'not prod'
    assert obj["env"] != "development"


def test_recursive_includes_with_scopes() -> None:
    """Verify that scopes in included files are correctly merged and resolved."""
    with tempfile.TemporaryDirectory() as tmpdir:
        base_path = Path(tmpdir) / "base.yaml"
        ext_path = Path(tmpdir) / "ext.yaml"

        # ext.yaml defines a scope and a negative scope
        ext_path.write_text("""
port: 1000
debug:
  port: 2000
not debug:
  port: 3000
""")

        # base.yaml includes ext.yaml and overrides the base value
        base_path.write_text("""
include: ext.yaml
port: 80
""")

        # Test Case A: Active scope 'debug'
        obj = load(base_path, scopes=["debug"])
        # Should have: base.port(80) then ext.debug.port(2000)
        assert obj["port"] == 2000

        # Test Case B: Active scope 'prod'
        obj = load(base_path, scopes=["prod"])
        # Should have: base.port(80) then ext.not debug.port(3000)
        assert obj["port"] == 3000


def test_scope_cleanup() -> None:
    """Verify that no scope-related metadata remains in the final configuration."""
    config = """
val: 1
scope_aliases:
  d: debug
debug:
  val: 2
prod:
  val: 3
not debug:
  val: 4
"""
    obj = load(config, scopes=["debug"])

    # Final dict should only contain 'val'
    assert obj == {"val": 2}
    assert "debug" not in obj
    assert "prod" not in obj
    assert "not debug" not in obj
    assert "scope_aliases" not in obj
