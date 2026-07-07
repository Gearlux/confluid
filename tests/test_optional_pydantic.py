"""Pydantic as an optional dependency (the ``confluid[pydantic]`` extra).

Confluid must import and run without pydantic installed: validation degrades
to ``"off"`` (logged once) and the schema-export API raises an ``ImportError``
naming the extra. Pydantic IS installed in the dev environment, so each test
runs a small script in a subprocess whose meta-path blocks the ``pydantic``
and ``annotated_types`` imports — the closest faithful simulation of an
environment where the extra is absent.
"""

import subprocess
import sys

# Prepended to every subprocess script: make ``import pydantic`` (and its
# companion ``annotated_types``) raise ModuleNotFoundError before the real
# package is found.
_BLOCK_PYDANTIC = """\
import importlib.abc
import sys


class _BlockPydantic(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in ("pydantic", "annotated_types"):
            raise ModuleNotFoundError(f"No module named {fullname!r}", name=fullname)
        return None


sys.meta_path.insert(0, _BlockPydantic())
"""


def _run_without_pydantic(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", _BLOCK_PYDANTIC + script],
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_import_and_init_validation_degrades_to_off():
    """confluid imports without pydantic; @configurable init validation is skipped.

    ``lr="not-a-float"`` would raise a pydantic ValidationError under the
    default strict policy — without pydantic the constructor must proceed
    untouched, and the downgrade must be logged exactly once.
    """
    result = _run_without_pydantic(
        """
import confluid


@confluid.configurable
class Optimizer:
    def __init__(self, lr: float = 1e-3) -> None:
        self.lr = lr


first = Optimizer(lr="not-a-float")  # strict validation would raise here
assert first.lr == "not-a-float", first.lr
second = Optimizer(lr="also-bad")  # downgrade already logged — no second line
assert second.lr == "also-bad", second.lr
print("OK")
"""
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout
    # The downgrade notice (routed through whatever logging sink is active)
    # names the extra exactly once — logged on first check, cached after.
    assert result.stderr.count("confluid[pydantic]") == 1, result.stderr


def test_configure_setattr_validation_degrades_to_off():
    """Post-construction configure() applies values without pydantic checks."""
    result = _run_without_pydantic(
        """
import confluid


@confluid.configurable
class Model:
    def __init__(self, layers: int = 3) -> None:
        self.layers = layers


m = Model()
confluid.configure(m, config={"Model": {"layers": 50}})
assert m.layers == 50, m.layers
print("OK")
"""
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_schema_export_api_raises_with_install_hint():
    """confluid.to_pydantic (and friends) raise ImportError naming the extra."""
    result = _run_without_pydantic(
        """
import confluid

for name in ("to_pydantic", "confluid_class_of", "lazy_param_names_of"):
    try:
        getattr(confluid, name)
    except ImportError as exc:
        assert "confluid[pydantic]" in str(exc), (name, str(exc))
    else:
        raise SystemExit(f"expected ImportError for confluid.{name}")
print("OK")
"""
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_lazy_exports_resolve_when_pydantic_present():
    """With pydantic installed, the PEP 562 exports resolve to the real API."""
    import confluid

    model_cls = confluid.to_pydantic(_Sample)
    assert confluid.confluid_class_of(model_cls) is not None
    assert confluid.lazy_param_names_of(model_cls) == frozenset()


class _Sample:
    def __init__(self, x: int = 1) -> None:
        self.x = x
