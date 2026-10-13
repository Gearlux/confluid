"""The reserved-key vocabulary has one home, a leaf module, and the modules below the loader never import it.

``resolver`` (a quoted marker's refusal) and ``dumper`` (the plain-YAML spelling a dump emits) need the four reserved
key names. While those lived in ``loader`` — which imports ``resolver`` at the top — each reached for them with a
function-local import, the only way round the cycle. ``confluid.keys`` holds the names and imports nothing from
confluid, so every module takes them at the top (``docs/architecture.md`` record 30).
"""

import ast
from pathlib import Path

import confluid
from confluid import keys, loader

_PACKAGE = Path(confluid.__file__).parent


def _imports_of(module_path: Path) -> list[str]:
    """Every module a file imports, at any depth (a function-local import counts)."""
    names: list[str] = []
    for node in ast.walk(ast.parse(module_path.read_text())):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


def test_the_reserved_key_names_have_one_home() -> None:
    assert keys.RESERVED_KEYS == {"_target_", "_partial_", "_ref_", "_clone_", "_scope_", "_notscope_"}
    assert (keys.TARGET_KEY, keys.PARTIAL_KEY, keys.REF_KEY) == ("_target_", "_partial_", "_ref_")
    assert (keys.SCOPE_KEY, keys.NOTSCOPE_KEY) == ("_scope_", "_notscope_")
    # the loader builds its marker gate from the same names, never a copy
    assert loader.RESERVED_KEYS is keys.RESERVED_KEYS and loader.TARGET_KEY is keys.TARGET_KEY


def test_keys_is_a_leaf_module() -> None:
    assert not [name for name in _imports_of(_PACKAGE / "keys.py") if name.startswith("confluid")]


def test_resolver_and_dumper_do_not_import_the_loader() -> None:
    for name in ("resolver", "dumper"):
        assert "confluid.loader" not in _imports_of(_PACKAGE / f"{name}.py"), name


def test_the_key_names_are_spelled_by_their_constants_outside_keys() -> None:
    """No module but ``keys`` writes a reserved key as a string literal: a renamed key changes in one place."""
    import ast
    import io
    import tokenize

    literals = []
    for path in sorted(_PACKAGE.glob("*.py")):
        if path.name == "keys.py":
            continue
        for tok in tokenize.generate_tokens(io.StringIO(path.read_text()).readline):
            if tok.type != tokenize.STRING:
                continue
            try:
                value = ast.literal_eval(tok.string)
            except (ValueError, SyntaxError):
                continue
            if value in keys.RESERVED_KEYS:
                literals.append(f"{path.name}:{tok.start[0]} {tok.string}")
    assert not literals, literals
