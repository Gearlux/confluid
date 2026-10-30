"""A bare activation no scope block declares is refused (architecture §1, amendment 2026-10-30).

``--scope vla`` (meaning ``val``) ran the document as if no scope were given: a bare name parses as a dimension
with no value, and an undeclared dimension is inert. The refusal is judged after the settle loop, so a name a scope
block's own include declares, an alias, and a dotted name whose ancestor is declared all still activate; a KEYED
activation of an undeclared dimension stays inert (a CLI passes ``framework=<name>`` unconditionally).
"""

from pathlib import Path
from typing import Any, Dict, cast

import pytest

from confluid import load
from confluid.exceptions import ScopeError

_SPLITS = """\
records: 800
val: !scope:val
  records: 160
quickstart: !scope:quickstart
  records: 10
"""


def _write(folder: Path, name: str, text: str) -> Path:
    path = folder / name
    path.write_text(text)
    return path


def test_a_bare_scope_no_block_declares_is_refused_naming_the_file_and_its_scopes(tmp_path: Path) -> None:
    config = _write(tmp_path, "splits.yaml", _SPLITS)
    with pytest.raises(ScopeError) as caught:
        load(config, until="document", scopes=["vla"])
    assert str(caught.value) == (f"No scope block declares 'vla' (the scopes of {config}: quickstart, val)")


def test_a_declared_bare_scope_activates_as_before(tmp_path: Path) -> None:
    config = _write(tmp_path, "splits.yaml", _SPLITS)
    assert cast(Dict[str, Any], load(config, until="document", scopes=["val"]))["records"] == 160


def test_no_scope_at_all_is_not_judged(tmp_path: Path) -> None:
    config = _write(tmp_path, "splits.yaml", _SPLITS)
    assert cast(Dict[str, Any], load(config, until="document"))["records"] == 800


def test_a_document_without_scope_blocks_says_it_declares_none(tmp_path: Path) -> None:
    config = _write(tmp_path, "plain.yaml", "records: 800\n")
    with pytest.raises(ScopeError) as caught:
        load(config, until="document", scopes=["val"])
    assert str(caught.value) == f"No scope block declares 'val' ({config} declares no scope)"


def test_the_listing_spells_a_keyed_scope_as_its_activation(tmp_path: Path) -> None:
    config = _write(tmp_path, "keyed.yaml", "a: 1\nk: !scope:framework=keras\n  a: 2\nd: !scope:debug\n  a: 3\n")
    with pytest.raises(ScopeError, match=r"the scopes of .*keyed\.yaml: debug, framework=keras\)"):
        load(config, until="document", scopes=["dbg"])


def test_a_scope_declared_in_a_scope_blocks_own_include_activates(tmp_path: Path) -> None:
    """The include of an active block is opened by the settle loop; a name it declares is a declared name."""
    _write(tmp_path, "extra.yaml", "y: 2\nextra: !scope:extra\n  y: 3\n")
    config = _write(tmp_path, "main.yaml", "x: 1\nquick: !scope:quick\n  include: extra.yaml\n")
    assert cast(Dict[str, Any], load(config, until="document", scopes=["quick", "extra"])) == {"x": 1, "y": 3}


def test_a_name_only_an_inactive_blocks_include_declares_is_refused(tmp_path: Path) -> None:
    """An inactive block's include is never opened, so its names are never declared: ``extra`` alone activates
    nothing."""
    _write(tmp_path, "extra.yaml", "y: 2\nextra: !scope:extra\n  y: 3\n")
    config = _write(tmp_path, "main.yaml", "x: 1\nquick: !scope:quick\n  include: extra.yaml\n")
    with pytest.raises(ScopeError, match=r"No scope block declares 'extra' \(the scopes of .*main\.yaml: quick\)"):
        load(config, until="document", scopes=["extra"])


def test_an_alias_the_document_declares_activates(tmp_path: Path) -> None:
    config = _write(tmp_path, "aliased.yaml", _SPLITS + "scope_aliases:\n  fast: [quickstart]\n")
    assert cast(Dict[str, Any], load(config, until="document", scopes=["fast"]))["records"] == 10


def test_a_dotted_name_activates_when_an_ancestor_is_declared(tmp_path: Path) -> None:
    config = _write(tmp_path, "tree.yaml", "a: 1\nprod: !scope:prod\n  a: 2\n")
    assert cast(Dict[str, Any], load(config, until="document", scopes=["prod.gpu"]))["a"] == 2


def test_a_parent_of_a_declared_dotted_name_activates_nothing_and_is_refused(tmp_path: Path) -> None:
    config = _write(tmp_path, "tree.yaml", "a: 1\ngpu: !scope:prod.gpu\n  a: 2\n")
    with pytest.raises(ScopeError, match=r"No scope block declares 'prod'"):
        load(config, until="document", scopes=["prod"])


def test_a_negated_blocks_name_is_declared(tmp_path: Path) -> None:
    config = _write(tmp_path, "neg.yaml", "a: 1\nnodebug: !notscope:debug\n  a: 2\n")
    assert cast(Dict[str, Any], load(config, until="document", scopes=["debug"]))["a"] == 1


def test_a_keyed_activation_of_an_undeclared_dimension_stays_inert(tmp_path: Path) -> None:
    config = _write(tmp_path, "splits.yaml", _SPLITS)
    loaded = cast(Dict[str, Any], load(config, until="document", scopes=["framework=torch", "os=linux"]))
    assert loaded["records"] == 800


def test_a_bare_activation_of_a_keyed_dimension_keeps_its_own_refusal(tmp_path: Path) -> None:
    config = _write(tmp_path, "keyed.yaml", "a: 1\nk: !scope:framework=keras\n  a: 2\n")
    with pytest.raises(ScopeError, match="framework is a KEYED dimension"):
        load(config, until="document", scopes=["framework"])


def test_parsed_data_names_the_files_that_declare_its_scopes(tmp_path: Path) -> None:
    """A CLI loads the raw document first and the activations second: the refusal names the file the blocks are
    written in."""
    config = _write(tmp_path, "splits.yaml", _SPLITS)
    raw = load(config, until="raw")
    with pytest.raises(ScopeError) as caught:
        load(raw, until="document", scopes=["vla"])
    assert str(caught.value) == f"No scope block declares 'vla' (the scopes of {config}: quickstart, val)"


def test_parsed_data_without_a_file_or_a_block_names_the_document() -> None:
    with pytest.raises(ScopeError) as caught:
        load({"a": 1}, until="document", scopes=["val"])
    assert str(caught.value) == "No scope block declares 'val' (the document declares no scope)"


def test_yaml_text_is_judged_alike() -> None:
    with pytest.raises(
        ScopeError, match=r"No scope block declares 'vla' \(the scopes of the document: quickstart, val\)"
    ):
        load(_SPLITS, until="document", scopes=["vla"])


def test_a_non_mapping_root_is_judged_alike() -> None:
    with pytest.raises(ScopeError, match="No scope block declares 'vla'"):
        load([1, 2], until="document", scopes=["vla"])
