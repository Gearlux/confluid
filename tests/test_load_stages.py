"""``load()`` is the ONE door — every stop point is ``until=<stage>`` (2026-08-17).

Before this change the pipeline had five entry names for four stop points:
``load_config`` (passes 1–3, path only), ``load(flow=False)`` (1–6), ``resolve``
(1–7), ``load`` / ``materialize`` (1–9), plus ``load_config_with_paths``. Measured,
``materialize(parsed) == load(parsed)`` on every input but a LIST root (which
``load`` handed back unbuilt), and ``load_config`` had no text form. These tests
pin the unified surface: one function, a closed ``until`` Literal, ``return_paths``.
"""

import inspect
from pathlib import Path
from typing import Any, List

import pytest

import confluid
from confluid import ConfigurationError, Target, configurable, load


@configurable
class _Box:
    def __init__(self, size: int = 1, tag: str = "a") -> None:
        self.size = size
        self.tag = tag


_DOC = """
size: 7
model: !class:_Box
  tag: b
model.tag: c
"""


# --------------------------------------------------------------------------- the four stages


def test_the_four_stages_on_one_document() -> None:
    """One document, four ``until`` values, four distinguishable results."""
    raw = load(_DOC, until="raw")
    assert isinstance(raw["model"], Target)
    assert raw["model"].kwargs == {"tag": "b"}  # nothing merged
    assert raw["model.tag"] == "c"  # pass 6 (expand) has not run: the dotted key is literal

    document = load(_DOC, until="document")
    assert isinstance(document["model"], Target)
    assert "model.tag" not in document  # expanded …
    assert document["model"].kwargs == {"tag": "c"}  # … and the dotted spelling tuned the marker
    assert document["size"] == 7  # pass 7 (broadcast) has not run: bare key not merged in

    settled = load(_DOC, until="settled")
    assert isinstance(settled["model"], Target)  # still a marker …
    assert settled["model"].kwargs == {"tag": "c", "size": 7}  # … with the bare key merged in

    objects = load(_DOC, until="objects")
    assert isinstance(objects["model"], _Box)
    assert (objects["model"].size, objects["model"].tag) == (7, "c")

    default = load(_DOC)
    assert isinstance(default["model"], _Box)  # "objects" is the default


def test_an_unknown_stage_is_refused_not_silently_defaulted() -> None:
    """A typo'd stage must not fall through to the default and hand back objects."""
    with pytest.raises(ConfigurationError, match="until"):
        load(_DOC, until="settle")  # type: ignore[call-overload]


# --------------------------------------------------------------------------- the folds


def test_load_on_already_loaded_data_is_the_same_as_loading_once() -> None:
    """The ``materialize`` fold: ``load(load(x, until=<earlier>))`` == ``load(x)``."""
    once = load(_DOC)["model"]
    for stage in ("raw", "document", "settled"):
        twice = load(load(_DOC, until=stage))["model"]  # type: ignore[arg-type]
        assert isinstance(twice, _Box)
        assert (twice.size, twice.tag) == (once.size, once.tag), stage


def test_a_fluid_root_with_an_explicit_document_context_broadcasts() -> None:
    """The DI shape ``materialize(node, context=document)`` is now ``load(node, context=document)``."""
    document = load(_DOC, until="document")
    built = load(document["model"], context=document)
    assert isinstance(built, _Box)
    assert built.size == 7  # the document's bare key reached the node


def test_a_LIST_root_builds() -> None:
    """PRO: ``load`` used to hand a list root back UNBUILT (``if not isinstance(data, dict): return data``)."""
    built = load("- !class:_Box {size: 3}\n- !class:_Box {size: 4}")
    assert [type(x) for x in built] == [_Box, _Box]
    assert [x.size for x in built] == [3, 4]


def test_a_LIST_root_stays_markers_when_asked_to() -> None:
    """CON: the stage decides — ``until='settled'`` on the same list keeps the markers."""
    settled = load("- !class:_Box {size: 3}\n- !class:_Box {size: 4}", until="settled")
    assert [type(x) for x in settled] == [Target, Target]


def test_a_scalar_root_passes_through_at_every_stage() -> None:
    for stage in ("raw", "document", "settled", "objects"):
        assert load("42", until=stage) == 42  # type: ignore[arg-type]


# --------------------------------------------------------------------------- "raw" from TEXT and dicts


def test_raw_processes_includes_from_a_path_a_text_and_a_dict(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``load_config`` took a PATH only; ``until='raw'`` reads includes from every input shape."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "base.yaml").write_text("lr: 0.1\n")
    entry = tmp_path / "entry.yaml"
    entry.write_text("include: base.yaml\nx: 1\n")

    from_path = load(entry, until="raw")
    from_text = load("include: base.yaml\nx: 1\n", until="raw")
    from_dict = load({"include": "base.yaml", "x": 1}, until="raw")
    for raw in (from_path, from_text, from_dict):
        assert raw == {"lr": 0.1, "x": 1}


def test_raw_keeps_scope_blocks_unresolved(tmp_path: Path) -> None:
    """The reason "raw" exists: scope DISCOVERY must see the blocks before pass 4 splices them away."""
    from confluid.fluid import ScopeBlock

    doc = "size: 1\nfast: !scope:mode=fast\n  size: 9\n"
    raw = load(doc, until="raw", scopes=["mode=fast"])
    assert isinstance(raw["fast"], ScopeBlock)  # not spliced yet
    assert confluid.discover_dimensions(raw) == {"mode"}

    document = load(doc, until="document", scopes=["mode=fast"])
    assert "fast" not in document and document["size"] == 9  # spliced


# --------------------------------------------------------------------------- return_paths


def _write_tree(tmp_path: Path) -> Path:
    (tmp_path / "base.yaml").write_text("lr: 0.1\n")
    (tmp_path / "extra.yaml").write_text("wd: 0.01\n")
    entry = tmp_path / "entry.yaml"
    entry.write_text("include: base.yaml\noverlay: !scope:extra\n  include: extra.yaml\n")
    return entry


def test_return_paths_lists_every_file_read_in_order(tmp_path: Path) -> None:
    entry = _write_tree(tmp_path)
    raw, paths = load(entry, until="raw", return_paths=True)
    assert raw["lr"] == 0.1
    assert paths == [entry.resolve(), (tmp_path / "base.yaml").resolve()]


def test_return_paths_includes_a_scope_spliced_include(tmp_path: Path) -> None:
    """PRO: ``load_config_with_paths`` wrapped only the pre-scope read, so an include
    inside an activated scope block was READ but never listed."""
    entry = _write_tree(tmp_path)
    document, paths = load(entry, until="document", scopes=["extra"], return_paths=True)
    assert document["wd"] == 0.01  # the overlay was spliced …
    assert paths == [entry.resolve(), (tmp_path / "base.yaml").resolve(), (tmp_path / "extra.yaml").resolve()]


def test_return_paths_does_not_list_a_file_the_stage_never_opened(tmp_path: Path) -> None:
    """CON: at ``until='raw'`` the scope block is still closed, so its include is not read — and not listed."""
    entry = _write_tree(tmp_path)
    _, paths = load(entry, until="raw", scopes=["extra"], return_paths=True)
    assert paths == [entry.resolve(), (tmp_path / "base.yaml").resolve()]

    _, paths = load(entry, until="document", return_paths=True)  # block never activated
    assert paths == [entry.resolve(), (tmp_path / "base.yaml").resolve()]


def test_return_paths_is_a_tuple_at_every_stage_and_empty_for_text(tmp_path: Path) -> None:
    entry = _write_tree(tmp_path)
    for stage in ("raw", "document", "settled", "objects"):
        result, paths = load(entry, until=stage, return_paths=True)  # type: ignore[arg-type]
        assert isinstance(result, dict) and isinstance(paths, list), stage
    result, paths = load("x: 1", return_paths=True)
    assert (result, paths) == ({"x": 1}, [])


def test_return_paths_is_per_call(tmp_path: Path) -> None:
    """Two loads do not share an accumulator — the second list starts empty."""
    entry = _write_tree(tmp_path)
    _, first = load(entry, until="raw", return_paths=True)
    _, second = load(entry, until="raw", return_paths=True)
    assert first == second and first is not second


# --------------------------------------------------------------------------- the surface


def test_the_folded_names_are_gone() -> None:
    """One door: the four folded entry points and the ``flow=`` kwarg no longer exist."""
    for name in ("materialize", "resolve", "load_config", "load_config_with_paths"):
        assert name not in confluid.__all__, name
        assert not hasattr(confluid, name), name
    params = inspect.signature(load).parameters
    assert "flow" not in params
    assert set(params) == {"data", "until", "context", "scopes", "solidify", "return_paths"}


def test_cast_stays() -> None:
    assert "cast" in confluid.__all__


def test_the_engine_no_longer_imports_the_loader() -> None:
    """The one sanctioned engine→loader seam was ``load(until="settled")``'s str/Path convenience.
    With the stop point on ``load``, the engine works on PREPARED data only."""
    import ast

    import confluid.engine as engine

    imported = {
        node.module
        for node in ast.walk(ast.parse(inspect.getsource(engine)))
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "confluid.loader" not in imported


def test_settle_is_the_engines_pass_7_entry() -> None:
    """``engine.settle(prepared, context)`` — pass 7 on already-prepared data, nothing built."""
    from confluid.engine import settle

    document = load(_DOC, until="document")
    settled = settle(document, context=document)
    assert isinstance(settled["model"], Target)
    assert settled["model"].kwargs == {"tag": "c", "size": 7}


def test_the_stage_literal_is_the_one_source_of_truth() -> None:
    """The runtime check derives from the Literal (``get_args``), never a restated tuple."""
    from typing import get_args

    from confluid.loader import _STAGES, Stage

    assert _STAGES == get_args(Stage) == ("raw", "document", "settled", "objects")


def test_return_paths_typing_shape() -> None:
    """A smoke that the tuple form is what the annotation promises (values, not just types)."""
    result: Any
    paths: List[Path]
    result, paths = load("x: 1", return_paths=True)
    assert result == {"x": 1} and paths == []


# --------------------------------------------------------------------------- a missing file raises


def test_a_missing_file_raises_for_a_Path_and_for_a_yaml_named_str(tmp_path: Path) -> None:
    """``load_config`` raised on a missing file; the fold keeps that. Before, ``load("missing.yaml")``
    parsed the NAME as YAML text and returned the string ``"missing.yaml"`` — a config that loads as nothing."""
    from confluid import ConfigFileNotFoundError

    with pytest.raises(ConfigFileNotFoundError):
        load(tmp_path / "missing.yaml", until="raw")
    with pytest.raises(ConfigFileNotFoundError):
        load(str(tmp_path / "missing.yml"))
    with pytest.raises(ConfigFileNotFoundError):
        load("missing_xyz.yaml")  # relative, no such file under any search tier


def test_a_bare_word_without_a_config_suffix_is_still_yaml_text() -> None:
    """CON: the suffix rule is narrow — a plain scalar document still parses as text."""
    assert load("hello") == "hello"
    assert load("42") == 42
