"""``load()`` is the ONE door — every stop point is ``until=<stage>``.

One function, a closed ``until`` Literal (``raw`` / ``document`` / ``settled`` / ``objects``),
every input shape at every stage, ``return_paths``. Pins: the four stages on one document, the
idempotence (``load(load(x, until=<earlier>)) == load(x)``), the list-root pro/con, ``raw`` from
text/dict/path, ``return_paths`` incl. a scope-spliced include, the exact signature, the
engine→loader direction, and the missing-file rule with its con case.
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
    """``load(load(x, until=<earlier>))`` == ``load(x)`` — passes already applied are idempotent."""
    once = load(_DOC)["model"]
    for stage in ("raw", "document", "settled"):
        twice = load(load(_DOC, until=stage))["model"]  # type: ignore[arg-type]
        assert isinstance(twice, _Box)
        assert (twice.size, twice.tag) == (once.size, once.tag), stage


def test_a_fluid_root_with_an_explicit_document_context_broadcasts() -> None:
    """The DI shape: a marker built against the document it came from — ``load(node, context=document)``."""
    document = load(_DOC, until="document")
    built = load(document["model"], context=document)
    assert isinstance(built, _Box)
    assert built.size == 7  # the document's bare key reached the node


def test_a_LIST_root_builds() -> None:
    """PRO: a list root is data like any other — its markers build."""
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
    """``until='raw'`` reads includes from every input shape — a path, YAML text, a dict."""
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
    """PRO: an include inside an ACTIVATED scope block is read — so it is listed."""
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


def test_the_signature_is_exactly_the_one_door() -> None:
    """One door: ``load`` carries exactly these keywords, and no sibling entry point exists beside it."""
    params = inspect.signature(load).parameters
    assert set(params) == {"data", "until", "context", "scopes", "solidify", "return_paths"}
    loading_names = {n for n in confluid.__all__ if n.startswith("load")}
    assert loading_names == {"load", "load_configurables"}


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
    """A name that ends in a config suffix is a FILE: a missing one raises instead of parsing the
    name as YAML text and loading as nothing."""
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


# --------------------------------------------------------------------------- passes 5–6 run ONCE


def test_a_document_placeholder_is_substituted_at_every_stage_past_raw(monkeypatch: pytest.MonkeyPatch) -> None:
    """PRO: ``${VAR}`` in the DOCUMENT is pass 5 — substituted from ``until="document"`` on, once."""
    monkeypatch.setenv("CONFLUID_STAGE_TEST", "/store")
    doc = "root: ${CONFLUID_STAGE_TEST}/runs\nmodel: !class:_Box\n  tag: ${CONFLUID_STAGE_TEST}\n"
    assert load(doc, until="raw")["root"] == "${CONFLUID_STAGE_TEST}/runs"  # not yet
    for stage in ("document", "settled"):
        out = load(doc, until=stage)  # type: ignore[arg-type]
        assert out["root"] == "/store/runs", stage
        assert out["model"].kwargs["tag"] == "/store", stage  # marker kwargs interpolate too
    built = load(doc)
    assert (built["root"], built["model"].tag) == ("/store/runs", "/store")


def test_an_explicit_context_is_interpolated_once_in_load(monkeypatch: pytest.MonkeyPatch) -> None:
    """A caller-supplied ``context=`` gets pass 5 too — in ``load``, so a placeholder in a context
    key still reaches the node with its substituted value (the engine no longer re-runs the pass)."""
    monkeypatch.setenv("CONFLUID_STAGE_SIZE", "9")
    document = load(_DOC, until="document")
    ctx = {"size": "${CONFLUID_STAGE_SIZE}"}
    built = load({"model": document["model"]}, context=ctx)["model"]
    assert built.size == 9


def test_runtime_kwargs_of_a_bare_type_are_code_not_document(monkeypatch: pytest.MonkeyPatch) -> None:
    """CON: interpolation is a DOCUMENT pass. Runtime kwargs handed to ``flow()`` from code keep
    their text — for a bare type exactly as for a marker built in code (the two agreed only for
    the marker before)."""
    from confluid import flow

    monkeypatch.setenv("CONFLUID_STAGE_TAG", "env")
    assert flow(_Box, tag="${CONFLUID_STAGE_TAG}").tag == "${CONFLUID_STAGE_TAG}"
    assert flow(Target(_Box, tag="${CONFLUID_STAGE_TAG}")).tag == "${CONFLUID_STAGE_TAG}"


def test_the_engine_entries_run_neither_pass_5_nor_pass_6() -> None:
    """Structural: passes 5–6 live in ``loader._load`` alone. The engine still uses the
    resolver for ``!ref:`` LOOKUP, so the pin is on the two entries' own bodies."""
    import confluid.engine as engine

    for entry in (engine.materialize, engine.settle):
        body = inspect.getsource(entry)
        assert "Resolver(" not in body and ".resolve(" not in body, entry.__name__
        assert "expand_dotted_keys(" not in body, entry.__name__


# --------------------------------------------------------------------------- live values in a data document


def test_a_live_value_in_a_data_document_keeps_its_identity() -> None:
    """BUGS-2026-08-19 PA12 — `load(<dict>)` is how a pre-built object enters a
    document; pass 6 deep-copied it (and crashed on an uncopyable one)."""
    import threading

    class Opaque:
        pass

    o = Opaque()
    assert load({"x": o, "y": 1}, until="document")["x"] is o
    lock = threading.Lock()
    assert load({"lock": lock, "y": 1}, until="document")["lock"] is lock


# --------------------------------------------------------------------------- load() never mutates parsed input


def test_a_raw_document_survives_loads_under_different_activations() -> None:
    """SR3 (BUGS-2026-08-19) — pass 4 rewrote the caller's marker kwargs in place,
    so the SECOND load answered with the FIRST call's activation, and discovery
    went empty. The raw→discover→load-with-scopes sequence is the documented CLI
    pattern; the raw tree must survive it."""
    from confluid import discover_dimension_values

    raw = load(
        "m: !class:collections.Counter\n  v: 1\n  alt: !scope:model=convnet\n    v: 10\n",
        until="raw",
    )
    assert discover_dimension_values(raw) == {"model": {"convnet"}}
    first = load(raw, scopes=["model=convnet"], until="document")
    assert first["m"].kwargs == {"v": 10}
    second = load(raw, until="document")
    assert second["m"].kwargs == {"v": 1}, "the first activation must not burn into the raw tree"
    assert discover_dimension_values(raw) == {"model": {"convnet"}}, "discovery survives the loads"


def test_load_does_not_pop_import_from_the_callers_dict() -> None:
    """PA26's caller half, closed by the same copy."""
    caller = {"import": "os", "a": 1}
    load(caller, until="raw")
    assert caller == {"import": "os", "a": 1}


def test_the_copy_spans_data_AND_context_through_one_memo() -> None:
    """The con: `load(doc["car"], context=doc)` locates the marker's slot in the
    context by IDENTITY — separate copies would sever it (the ordered-broadcast
    pin next door relies on this shape end to end)."""
    document: dict = {"box": Target(_Box, size=1), "size": 9}
    built = load(document["box"], context=document)
    assert built.size == 9, "the later bare key still reaches the marker through the copied context"
    assert document["box"].kwargs == {"size": 1}, "the caller's marker is untouched"


# --------------------------------------------------------------------------- expansion runs before interpolation (PA8)


def test_interpolation_reads_the_expanded_tree() -> None:
    """PA8 (BUGS-2026-08-19) — `${train.lr}` answered with a literal dotted key the
    final document does not even contain; expansion runs first now, so interpolation
    and the returned tree give the same answer."""
    doc = load("train.lr: 0.2\ntrain:\n  lr: 0.1\nx: ${train.lr}\n", until="document")
    assert doc["train"]["lr"] == 0.1
    assert doc["x"] == 0.1


def test_a_dotted_only_key_still_interpolates() -> None:
    """PA8 con — with no nested twin, the expanded tree holds the same value."""
    doc = load("a.b: 7\nx: ${a.b}\n", until="document")
    assert doc["x"] == 7 and doc["a"] == {"b": 7}


def test_a_dotted_writes_value_interpolates_where_it_lands() -> None:
    """PA8 consequence — the value sits inside the landing block when interpolation
    runs, so a competing local path wins there: the same answer a literal
    `lr: ${x.y}` written inside the block gets."""
    doc = load("x: {y: 1}\ntrain.lr: ${x.y}\ntrain:\n  x: {y: 2}\n", until="document")
    assert doc["train"]["lr"] == 2


def test_container_placeholder_resolves_at_the_load_door(monkeypatch: pytest.MonkeyPatch) -> None:
    """PA6 end to end — the report's repro through load()."""
    monkeypatch.setenv("CONFLUID_TEST_ROOT", "/store")
    doc = load("a:\n  b:\n    path: ${env:CONFLUID_TEST_ROOT}/x\nc: ${a.b}\n", until="document")
    assert doc["c"] == {"path": "/store/x"}


def test_ref_placeholder_spelling_survives_expansion() -> None:
    """The hoist — `${ref:proto}` becomes a marker BEFORE expansion, so a dotted
    write through it tunes the shared referent exactly as the `!ref:` tag does."""
    doc = load("proto: !class:collections.Counter {v: 1}\nuse: ${ref:proto}\nuse.k: 5\n", until="document")
    assert doc["use"] is doc["proto"]
    assert doc["proto"].kwargs == {"v": 1, "k": 5}


def test_a_dotted_write_through_a_plain_placeholder_is_refused() -> None:
    """`use: ${a.b}` is VALUE substitution, not sharing — a dotted write through it
    would silently clobber the not-yet-resolved string, so it refuses and names the
    sharing spelling instead."""
    with pytest.raises(ConfigurationError, match=r"\$\{a\.b\}"):
        load("a:\n  b: {v: 1}\nuse: ${a.b}\nuse.k: 5\n", until="document")


def test_the_dollar_escape_survives_the_document_stage_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """PA14 (BUGS-2026-08-22) — collapsing `$$` at pass 6 made `load(load(x, until="document"))`
    re-expand the now-bare `$NAME`; the escape is opaque through the document stage now."""
    monkeypatch.setenv("RUN_USER", "gert")
    text = "command: echo $$RUN_USER\n"
    once = load(text, until="document")
    assert once == {"command": "echo $$RUN_USER"}
    assert load(once, until="document") == once
    assert load(once) == load(text) == {"command": "echo $RUN_USER"}
