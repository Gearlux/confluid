"""``load(..., texts={path: text})`` — files the caller holds in memory are read from the caller.

An editor holding files it has not saved hands their texts over. A file whose RESOLVED path is a
key of ``texts`` is parsed from that text instead of read from disk — the entry file and every
``include:`` (a nested one, one a scope block exposes) — and it need not exist on disk. Nothing is
written. Everything else is unchanged: the search tiers (a held path counts as existing at its
own tier), circular-include detection, the not-found message for a path that is NOT held,
``return_paths``, and every location, which names the REAL file.

Pins, each with its con case where one exists: the entry file, a relative include absent from
disk (in a read-only folder), a nested include inside a held file, an include a scope block
exposes (in the entry file and in an included file, one call and the two-step door), a held text
shadowing a different file on disk, the tiers, locations (a ConstructionError, a settled marker,
a YAML syntax error), circular detection, ``return_paths``, relative and ``~`` keys, text and
dict input, ``texts=None`` exactly as today, the call-scoped lifetime, a load nested in the call
(and per thread), the two refused key shapes, and the documented spelling limit on a filesystem
that ignores case.
"""

import stat
import threading
from pathlib import Path
from typing import Any, Dict, List, Union

import pytest
import yaml

from confluid import (
    CircularIncludeError,
    ConfigFileNotFoundError,
    ConfigurationError,
    ConstructionError,
    configurable,
    format_yaml_loc,
    load,
)


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Sandbox CWD + every env var the search tiers read (``HOME`` also drives ``~``)."""
    xdg_home = tmp_path / "xdg"
    xdg_home.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_home))
    monkeypatch.setenv("XDG_CONFIG_DIRS", str(tmp_path / "xdg_sys"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    return xdg_home


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


# --------------------------------------------------------------------------- the entry file


def test_the_entry_file_is_read_from_its_held_text_and_need_not_exist(tmp_path: Path) -> None:
    entry = tmp_path / "graph" / "demo.yaml"  # neither the file nor its folder exists
    held = {entry: "x: 1\n"}

    assert load(entry, until="raw", texts=held) == {"x": 1}
    assert load(str(entry), texts=held) == {"x": 1}  # a str naming a file, every stage
    assert not entry.parent.exists()  # nothing was written

    with pytest.raises(ConfigFileNotFoundError):  # con: not held, not on disk
        load(entry, until="raw")


# --------------------------------------------------------------------------- includes


def test_a_relative_include_is_read_from_its_held_text_and_absent_from_disk(tmp_path: Path) -> None:
    graph = tmp_path / "graph"
    entry = _write(graph / "demo.yaml", "include: prep.steps.yaml\nx: 1\n")
    held = {graph / "prep.steps.yaml": "y: 2\n"}

    graph.chmod(stat.S_IRUSR | stat.S_IXUSR | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)  # 555
    try:
        assert load(entry, until="document", texts=held) == {"y": 2, "x": 1}  # a read-only folder: no write
    finally:
        graph.chmod(stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)
    assert sorted(p.name for p in graph.iterdir()) == ["demo.yaml"]

    with pytest.raises(ConfigFileNotFoundError, match=r"demo\.yaml includes prep\.steps\.yaml: Not found"):
        load(entry, until="raw")  # con: the same document without the held text


def test_a_nested_include_inside_a_held_file_resolves_beside_the_held_file(tmp_path: Path) -> None:
    graph = tmp_path / "graph"
    entry = _write(graph / "demo.yaml", "include: sub/prep.steps.yaml\n")
    _write(graph / "sub" / "leaf.yaml", "leaf: disk\n")  # on disk, beside the held file
    _write(tmp_path / "inner.yaml", "inner: cwd\n")  # the CWD tier: a decoy the held sibling beats
    held = {
        graph / "sub" / "prep.steps.yaml": "include: [inner.yaml, leaf.yaml]\nsteps: 1\n",
        graph / "sub" / "inner.yaml": "inner: held\n",
    }

    assert load(entry, until="raw", texts=held) == {"inner": "held", "leaf": "disk", "steps": 1}


def test_an_include_a_scope_block_exposes_is_read_from_its_held_text(tmp_path: Path) -> None:
    graph = tmp_path / "graph"
    entry = _write(graph / "demo.yaml", "lr: 0.1\ntuning: !scope:tune\n  include: tuning.yaml\n")
    tuning = graph / "tuning.yaml"
    held = {tuning: "lr: 0.5\n"}

    doc, paths = load(entry, until="document", scopes=["tune"], texts=held, return_paths=True)
    assert doc["lr"] == 0.5
    assert tuning.resolve() in paths

    doc, paths = load(entry, until="document", texts=held, return_paths=True)  # con: block inactive
    assert doc["lr"] == 0.1
    assert tuning.resolve() not in paths  # never opened


def test_a_scope_block_in_a_held_included_file_finds_its_include_beside_that_file(tmp_path: Path) -> None:
    """The deferred include is anchored to the block's file even when that file exists only as a text."""
    graph = tmp_path / "graph"
    entry = _write(graph / "demo.yaml", "lr: 0.1\ninclude: sub/frag.yaml\n")
    _write(graph / "tuning.yaml", "lr: 0.9\n")  # beside the ENTRY file: must not be read
    held = {
        graph / "sub" / "frag.yaml": "tuning: !scope:tune\n  include: tuning.yaml\n",
        graph / "sub" / "tuning.yaml": "lr: 0.5\n",
    }

    assert load(entry, until="document", scopes=["tune"], texts=held)["lr"] == 0.5

    raw = load(entry, until="raw", texts=held)  # the two-step door, texts on both calls
    assert load(raw, until="document", scopes=["tune"], texts=held)["lr"] == 0.5


def test_a_held_text_shadows_a_different_file_on_disk(tmp_path: Path) -> None:
    graph = tmp_path / "graph"
    entry = _write(graph / "demo.yaml", "include: [steps.yaml, other.yaml]\nwho: disk-entry\n")
    steps = _write(graph / "steps.yaml", "step: disk\n")
    _write(graph / "other.yaml", "other: disk\n")  # not held: read from disk
    held = {entry: "include: [steps.yaml, other.yaml]\nwho: held-entry\n", steps: "step: held\n"}

    assert load(entry, until="raw", texts=held) == {"step": "held", "other": "disk", "who": "held-entry"}
    # con: the files on disk are untouched, and a load without texts reads them
    assert steps.read_text() == "step: disk\n"
    assert load(entry, until="raw") == {"step": "disk", "other": "disk", "who": "disk-entry"}


def test_a_held_path_resolves_through_the_same_tiers(tmp_path: Path) -> None:
    """A held file counts as existing AT ITS OWN TIER — it neither loses to nor jumps over a disk file."""
    graph = tmp_path / "graph"
    entry = _write(graph / "demo.yaml", "include: common.yaml\n")

    _write(tmp_path / "common.yaml", "who: cwd-disk\n")  # tier 2 (CWD), on disk
    assert load(entry, until="raw", texts={graph / "common.yaml": "who: sibling-held\n"}) == {"who": "sibling-held"}

    _write(graph / "common.yaml", "who: sibling-disk\n")  # con: tier 1 on disk beats a held tier 2
    assert load(entry, until="raw", texts={tmp_path / "common.yaml": "who: cwd-held\n"}) == {"who": "sibling-disk"}


# --------------------------------------------------------------------------- locations


class _Unrebuildable(Exception):
    """An exception ``type(exc)(msg)`` cannot rebuild, so the engine raises ``ConstructionError``."""

    def __init__(self, a: int, b: int) -> None:
        super().__init__(f"refused {a} {b}")


@configurable
class _RefusesToBuild:
    def __init__(self, size: int = 1) -> None:
        raise _Unrebuildable(size, 2)


@configurable
class _HeldNode:
    def __init__(self, size: int = 1) -> None:
        self.size = size


def test_a_construction_error_from_a_held_file_names_its_file_line_col(tmp_path: Path) -> None:
    graph = tmp_path / "graph"
    entry = _write(graph / "demo.yaml", "include: prep.steps.yaml\n")
    steps = graph / "prep.steps.yaml"
    held = {steps: "# held, never saved\nnode: !class:_RefusesToBuild\n  size: 3\n"}

    with pytest.raises(ConstructionError) as ei:
        load(entry, texts=held)
    assert f"{steps.resolve()}:2:7" in str(ei.value), str(ei.value)


def test_a_settled_marker_from_a_held_file_carries_the_real_path(tmp_path: Path) -> None:
    steps = tmp_path / "graph" / "prep.steps.yaml"
    settled = load(steps, until="settled", texts={steps: "a: 1\nnode: !class:_HeldNode\n  size: 3\n"})
    assert format_yaml_loc(settled["node"]) == f"{steps.resolve()}:2:7"


def test_a_yaml_syntax_error_in_a_held_text_names_the_real_file(tmp_path: Path) -> None:
    steps = tmp_path / "graph" / "prep.steps.yaml"
    with pytest.raises(yaml.MarkedYAMLError) as ei:
        load(steps, until="raw", texts={steps: "a: [1, 2\nb: 3\n"})
    assert ei.value.problem_mark is not None
    assert ei.value.problem_mark.name == str(steps.resolve())


# --------------------------------------------------------------------------- cycles, paths, keys


def test_a_circular_include_through_held_files_is_detected(tmp_path: Path) -> None:
    a = (tmp_path / "graph" / "a.yaml").resolve()
    b = (tmp_path / "graph" / "b.yaml").resolve()
    with pytest.raises(CircularIncludeError) as ei:
        load(a, until="raw", texts={a: "include: b.yaml\n", b: "include: a.yaml\n"})
    assert f"{a} -> {b} -> {a}" in str(ei.value)


def test_return_paths_lists_a_held_path_like_a_read_one(tmp_path: Path) -> None:
    graph = tmp_path / "graph"
    entry = _write(graph / "demo.yaml", "include: [prep.steps.yaml, leaf.yaml]\n")
    leaf = _write(graph / "leaf.yaml", "leaf: 1\n")
    steps = graph / "prep.steps.yaml"

    _, paths = load(entry, until="raw", texts={steps: "steps: 1\n"}, return_paths=True)
    assert paths == [entry.resolve(), steps.resolve(), leaf.resolve()]


def test_a_key_given_relative_or_with_a_tilde_matches(tmp_path: Path) -> None:
    graph = tmp_path / "graph"
    entry = _write(graph / "demo.yaml", "include: prep.steps.yaml\n")
    assert load(entry, until="raw", texts={"graph/prep.steps.yaml": "y: 2\n"}) == {"y": 2}  # relative to CWD
    assert load(entry, until="raw", texts={Path("graph/prep.steps.yaml"): "y: 3\n"}) == {"y": 3}

    home_file = tmp_path / "home" / "cfg" / "main.yaml"  # HOME is sandboxed to tmp_path/home
    assert load(home_file, until="raw", texts={"~/cfg/main.yaml": "x: 1\n"}) == {"x": 1}
    tilde_entry = _write(graph / "tilde.yaml", "include: ~/cfg/main.yaml\n")  # `~` in the document
    assert load(tilde_entry, until="raw", texts={home_file: "x: 2\n"}) == {"x": 2}

    # a suffix-less name: held, it names that file; not held, it is YAML text (the scalar) as today
    assert load("settings", until="raw", texts={"settings": "x: 1\n"}) == {"x": 1}
    assert load("settings", until="raw") == "settings"


def test_texts_apply_to_yaml_text_and_parsed_data_input(tmp_path: Path) -> None:
    """Every input shape takes ``texts`` — a relative include of text/dict input resolves against CWD."""
    held = {tmp_path / "steps.yaml": "y: 2\n"}
    assert load("include: steps.yaml\nx: 1\n", until="raw", texts=held) == {"y": 2, "x": 1}
    assert load({"include": "steps.yaml", "x": 1}, until="raw", texts=held) == {"y": 2, "x": 1}


# --------------------------------------------------------------------------- texts=None is today


def test_texts_None_behaves_exactly_as_today(tmp_path: Path) -> None:
    graph = tmp_path / "graph"
    entry = _write(graph / "demo.yaml", "include: common.yaml\nx: 1\n")
    _write(graph / "common.yaml", "y: 2\n")

    assert load(entry, until="raw", texts=None, return_paths=True) == load(entry, until="raw", return_paths=True)

    broken = _write(graph / "broken.yaml", "include: missing.yaml\n")
    messages: List[str] = []
    for texts in (None, {}, {tmp_path / "unrelated.yaml": "z: 3\n"}):
        with pytest.raises(ConfigFileNotFoundError) as ei:
            load(broken, until="raw", texts=texts)
        messages.append(str(ei.value))
    assert messages[0] == messages[1] == messages[2]  # a path NOT held: the not-found message is unchanged
    assert "broken.yaml includes missing.yaml: Not found: missing.yaml (searched: " in messages[0]


def test_the_held_texts_last_for_the_call_only(tmp_path: Path) -> None:
    entry = tmp_path / "demo.yaml"
    assert load(entry, until="raw", texts={entry: "x: 1\n"}) == {"x": 1}
    with pytest.raises(ConfigFileNotFoundError):
        load(entry, until="raw")  # after a call that returned

    with pytest.raises(ConfigurationError):
        load(entry, until="raw", texts={entry: "a: 1\na: 2\n"})  # a duplicate key raises inside the call
    with pytest.raises(ConfigFileNotFoundError):
        load(entry, until="raw")  # after a call that raised


def test_a_load_inside_the_call_sees_the_same_texts_and_each_thread_its_own(tmp_path: Path) -> None:
    """A load run by the document's own objects (a ``solidify()``) reads the call's texts — per thread."""
    barrier = threading.Barrier(2, timeout=30)
    inner = tmp_path / "inner.yaml"  # never on disk

    @configurable
    class _LoadsInSolidify:
        def __init__(self, path: str = "") -> None:
            self.path = path
            self.seen: Any = None

        def solidify(self) -> None:
            barrier.wait()  # both threads are inside their own call before either nested load runs
            self.seen = load(self.path, until="raw")

    entry = tmp_path / "demo.yaml"
    results: Dict[str, Any] = {}

    def run(who: str) -> None:
        document = f"node: !class:_LoadsInSolidify\n  path: {inner}\n"
        results[who] = load(entry, texts={entry: document, inner: f"who: {who}\n"})["node"].seen

    threads = [threading.Thread(target=run, args=(who,)) for who in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results == {"a": {"who": "a"}, "b": {"who": "b"}}


# --------------------------------------------------------------------------- refused shapes


def test_a_text_that_is_not_a_str_is_refused(tmp_path: Path) -> None:
    entry = tmp_path / "demo.yaml"
    with pytest.raises(ConfigurationError, match=r"the text held for .*demo\.yaml must be a str, got bytes"):
        load(entry, until="raw", texts={entry: b"x: 1\n"})  # type: ignore[call-overload]


def test_two_keys_naming_one_file_with_different_texts_are_refused(tmp_path: Path) -> None:
    entry = tmp_path / "demo.yaml"
    ambiguous: Dict[Union[str, Path], str] = {entry: "x: 1\n", "demo.yaml": "x: 2\n"}
    with pytest.raises(ConfigurationError, match=r"two different texts for .*demo\.yaml"):
        load(entry, until="raw", texts=ambiguous)
    same: Dict[Union[str, Path], str] = {entry: "x: 1\n", "demo.yaml": "x: 1\n"}
    assert load(entry, until="raw", texts=same) == {"x": 1}  # con: one text under two spellings of one path


# --------------------------------------------------------------------------- a disk that folds case


def _folds_case(folder: Path) -> bool:
    """Does the filesystem under ``folder`` ignore case (macOS APFS and Windows NTFS do, by default)?"""
    probe = _write(folder / "case_probe.txt", "")
    try:
        return (folder / "CASE_PROBE.TXT").exists()
    finally:
        probe.unlink()


def test_on_a_disk_that_folds_case_a_key_matches_the_spelling_the_include_resolves_to(tmp_path: Path) -> None:
    """The documented limit (``docs/interpolation.md`` → "Spelling"): a key is the RESOLVED path, and resolving keeps
    the include's own spelling — so on a disk that ignores case, ``Prep.steps.yaml`` and ``prep.steps.yaml`` are one
    file to the disk but two keys to ``texts``. Measured 2026-09-30 (macOS APFS): the key spelled like the DISK file
    while the include spells it in lower case was silently not used — the disk text was read."""
    if not _folds_case(tmp_path):
        pytest.skip("needs a filesystem that ignores case (macOS APFS, Windows NTFS)")
    graph = tmp_path / "graph"
    entry = _write(graph / "demo.yaml", "include: prep.steps.yaml\n")
    _write(graph / "Prep.steps.yaml", "a: disk\n")  # the disk spells it with a capital P

    # pro: keyed the way the include spells it — the held text wins; the disk's capital changes nothing
    assert load(entry, until="raw", texts={graph / "prep.steps.yaml": "a: held\n"}) == {"a": "held"}
    # con: keyed the way the DISK spells it — another key: the include reads the disk
    assert load(entry, until="raw", texts={graph / "Prep.steps.yaml": "a: held\n"}) == {"a": "disk"}
    # con: the same key with nothing on disk — the include is not found
    (graph / "Prep.steps.yaml").unlink()
    with pytest.raises(ConfigFileNotFoundError, match=r"demo\.yaml includes prep\.steps\.yaml: Not found"):
        load(entry, until="raw", texts={graph / "Prep.steps.yaml": "a: held\n"})
