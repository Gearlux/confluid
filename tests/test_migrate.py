"""The ``confluid-migrate`` codemod — tags to the plain-YAML reserved-key format.

Two properties carry the whole thing and are pinned hardest:

* **only the converted lines change** — these configs are mostly comments, and a
  migration diff that restyles the file cannot be reviewed;
* **the verification actually fails on a bad conversion** — a check that only ever
  passes is worse than no check, so the equivalence tests feed it wrong answers.
"""

from pathlib import Path
from typing import Any, Optional

import pytest
import yaml

from confluid import configurable, load
from confluid.migrate import main, migrate_file, migrate_text, verify_equivalence


@configurable
class Box:
    def __init__(self, size: int = 1, label: str = "b") -> None:
        self.size, self.label = size, label


@configurable
class Opt:
    def __init__(self, params: Any = None, lr: float = 0.01) -> None:
        self.params, self.lr = params, lr


@configurable
class Holder:
    def __init__(self, box: Optional[Box] = None) -> None:
        self.box = box


def convert(text: str) -> str:
    return migrate_text(text)[0]


# --------------------------------------------------------------------------- #
# The tag forms
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "tagged, plain",
    [
        ("m: !class:Box", "m:\n  _target_: Box"),
        ("m: !class:Box()", "m:\n  _target_: Box"),
        ("m: !class:Box(size=3)", "m:\n  _target_: Box\n  size: 3"),
        ("m: !class:Box(size=3,label=x)", "m:\n  _target_: Box\n  size: 3\n  label: x"),
        ("m: !lazy:Opt", "m:\n  _target_: Opt\n  _partial_: true"),
        ("m: !lazy:Opt(lr=0.5)", "m:\n  _target_: Opt\n  _partial_: true\n  lr: 0.5"),
        ("a: !ref:proto", "a: ${ref:proto}"),
        ("a: !clone:proto", "a: ${clone:proto}"),
        ("b: !scope:size=big", "b:\n  _scope_: {size: big}"),
        ("b: !notscope:size", "b:\n  _notscope_: {size: }"),
        ("b: !scope:extra=yes", 'b:\n  _scope_: {extra: "yes"}'),
    ],
    ids=lambda v: v.splitlines()[0][:28],
)
def test_key_forms_convert(tagged: str, plain: str) -> None:
    assert convert(tagged) == plain


@pytest.mark.parametrize(
    "tagged, plain",
    [
        ("ops:\n  - !class:Box", "ops:\n  - _target_: Box"),
        ("ops:\n  - !class:Box(size=3)", "ops:\n  - _target_: Box\n    size: 3"),
        ("ops:\n  - !lazy:Opt(lr=0.5)", "ops:\n  - _target_: Opt\n    _partial_: true\n    lr: 0.5"),
        ("ops:\n  - !ref:proto", "ops:\n  - ${ref:proto}"),
    ],
    ids=["bare", "inline-kwargs", "lazy", "ref"],
)
def test_sequence_items_keep_the_compact_layout(tagged: str, plain: str) -> None:
    """A sequence item carries its first key on the dash line, as configs are written."""
    assert convert(tagged) == plain


def test_a_block_body_is_left_where_it_is() -> None:
    """The body already sits one level in — the marker's keys join it, nothing moves."""
    assert convert("m: !class:Box\n  size: 3\n  label: x") == "m:\n  _target_: Box\n  size: 3\n  label: x"


def test_nesting_depth_is_preserved() -> None:
    got = convert("outer:\n  inner: !class:Box\n    size: 3")
    assert got == "outer:\n  inner:\n    _target_: Box\n    size: 3"
    assert yaml.safe_load(got)["outer"]["inner"] == {"_target_": "Box", "size": 3}


def test_a_clone_with_kwargs_uses_the_mapping_form() -> None:
    """A scalar ``${clone:}`` has nowhere to put the overrides."""
    assert convert("c: !clone:proto\n  size: 9") == "c:\n  _clone_: proto\n  size: 9"


# --------------------------------------------------------------------------- #
# What must NOT change
# --------------------------------------------------------------------------- #


def test_comments_are_never_edited() -> None:
    """A comment is documentation, not a node.

    This is the bug the line grammar exists to prevent: a context-free replace
    uncommented half a commented-out example and produced invalid YAML.
    """
    text = "# model: !class:Box(size=3)\n#   - !lazy:Opt\nm: !class:Box\n"
    got = convert(text)
    assert got.splitlines()[0] == "# model: !class:Box(size=3)"
    assert got.splitlines()[1] == "#   - !lazy:Opt"
    assert got.splitlines()[2] == "m:"


def test_an_inline_comment_survives_on_the_converted_line() -> None:
    assert convert("m: !class:Box   # the model") == "m:   # the model\n  _target_: Box"


def test_untouched_lines_are_byte_identical() -> None:
    text = (
        "# a header\n\n"
        "seed: 7\n"
        'quoted: "a # not-a-comment string"\n'
        "m: !class:Box\n"
        "  size:   3      # odd spacing kept\n"
        "list: [1, 2, 3]\n"
    )
    got = convert(text).splitlines()
    for line in ("# a header", "seed: 7", 'quoted: "a # not-a-comment string"', "  size:   3      # odd spacing kept"):
        assert line in got, line
    assert "list: [1, 2, 3]" in got


def test_a_document_with_no_tags_is_returned_unchanged() -> None:
    text = "a: 1\nb:\n  - x\n  - y\n# comment\n"
    assert convert(text) == text


def test_migration_is_idempotent() -> None:
    once = convert("m: !class:Box(size=3)\np: ${DATA_ROOT}/x\n")
    assert convert(once) == once


# --------------------------------------------------------------------------- #
# Interpolation
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "before, after",
    [
        ("p: ${DATA_ROOT}/x", "p: ${env:DATA_ROOT}/x"),
        ("p: $DATA_ROOT/x", "p: ${env:DATA_ROOT}/x"),
        ("p: ${PORT:8080}", "p: ${env:PORT,8080}"),
        ("p: ${train.dataset}", "p: ${train.dataset}"),  # dotted = a config key, untouched
        ("p: ${env:ALREADY}", "p: ${env:ALREADY}"),  # already explicit
    ],
    ids=["braced", "bare", "default", "config-key-untouched", "idempotent"],
)
def test_environment_reads_become_explicit(before: str, after: str) -> None:
    assert convert(before) == after


def test_interpolation_inside_a_comment_is_left_alone() -> None:
    assert convert("# see ${DATA_ROOT} for the root\na: 1") == "# see ${DATA_ROOT} for the root\na: 1"


# --------------------------------------------------------------------------- #
# Findings — reported, never guessed at
# --------------------------------------------------------------------------- #


def test_an_axis_selector_rides_along_in_the_target_string() -> None:
    """The selector needs no migration: it is part of the target NAME, not tag
    syntax, so it stays ordinary YAML and the registry reads it unchanged."""
    migrated, _conversions, findings = migrate_text("m: !class:Loss@framework=keras\n")
    assert migrated == "m:\n  _target_: Loss@framework=keras\n"
    assert findings == []
    assert yaml.safe_load(migrated)["m"]["_target_"] == "Loss@framework=keras"


def test_a_document_key_selector_survives_too() -> None:
    migrated, _conversions, findings = migrate_text("m: !lazy:Loss@framework=$engine\n")
    assert migrated == "m:\n  _target_: Loss@framework=$engine\n  _partial_: true\n"
    assert findings == []


def test_a_sequence_bodied_scope_becomes_a_nested_list() -> None:
    """The block becomes a LIST whose first item is the marker, so the body items
    stay exactly where they are and the conversion is still line-local."""
    text = "ops:\n  - first\n  - !scope:extra=yes\n    - a\n    - b\n  - last\n"
    migrated, _conversions, findings = migrate_text(text)
    assert migrated == 'ops:\n  - first\n  - - _scope_: {extra: "yes"}\n    - a\n    - b\n  - last\n'
    assert findings == []


def test_a_yaml_boolean_scope_value_is_quoted() -> None:
    """The tag stores `yes` as TEXT; unquoted it would become True and never match
    the activation string a CLI passes."""
    assert convert("b: !scope:extra=yes") == 'b:\n  _scope_: {extra: "yes"}'
    assert convert("b: !scope:extra=no") == 'b:\n  _scope_: {extra: "no"}'
    assert convert("b: !scope:model=cnn") == "b:\n  _scope_: {model: cnn}"


def test_a_surviving_tag_is_always_reported() -> None:
    """The backstop: nothing is left behind without a finding naming its line."""
    text = 'm: "!class:Box(lr=!ref:base)"\n'  # the quoted-string spelling
    _migrated, _conversions, findings = migrate_text(text)
    assert findings and findings[0].line == 1


def test_findings_carry_the_line_number() -> None:
    text = 'a: 1\nb: 2\nm: "!class:Box(lr=!ref:base)"\n'
    _migrated, _conversions, findings = migrate_text(text)
    assert [f.line for f in findings] == [3]


# --------------------------------------------------------------------------- #
# Verification — it must FAIL on a bad conversion
# --------------------------------------------------------------------------- #


def test_equivalence_passes_on_a_correct_conversion() -> None:
    before = "m: !class:Box(size=3)\no: !lazy:Opt(lr=0.5)\n"
    assert verify_equivalence(before, convert(before)) == []


def test_equivalence_FAILS_when_partial_is_dropped() -> None:
    """The verdict that matters most: a lost ``_partial_`` builds an optimizer with
    no params, hours later and nowhere near the config."""
    before = "o: !lazy:Opt(lr=0.5)\n"
    wrong = "o:\n  _target_: Opt\n  lr: 0.5\n"  # _partial_: true dropped
    assert verify_equivalence(before, wrong)


def test_equivalence_FAILS_when_a_kwarg_is_dropped() -> None:
    before = "m: !class:Box(size=3)\n"
    assert verify_equivalence(before, "m:\n  _target_: Box\n")


def test_equivalence_FAILS_on_invalid_yaml() -> None:
    problems = verify_equivalence("m: !class:Box\n", "m:\n  _target_: Box\n :bad\n")
    assert problems and "not valid plain YAML" in problems[0]


def test_equivalence_checks_INSIDE_scope_blocks() -> None:
    """A wrong conversion in an inactive block is invisible to a no-scope resolve.

    The block is dropped before any marker is built, so the default comparison
    passes; only iterating the declared activations catches it.
    """
    before = "v: !scope:size=big\n  m: !lazy:Opt(lr=0.5)\n"
    wrong = "v:\n  _scope_: size=big\n  m:\n    _target_: Opt\n    lr: 0.5\n"  # _partial_ dropped
    assert verify_equivalence(before, convert(before)) == []
    assert verify_equivalence(before, wrong), "a difference inside a scope block must be caught"


# --------------------------------------------------------------------------- #
# End to end — the converted document builds the same objects
# --------------------------------------------------------------------------- #


def test_the_converted_document_loads_to_the_same_objects() -> None:
    before = "seed: 7\nh: !class:Holder()\n  box: !class:Box(size=3)\n"
    after = convert(before)
    assert yaml.safe_load(after)  # the point of the exercise
    old, new = load(before)["h"], load(after)["h"]
    assert type(old) is type(new)
    assert old.box.size == new.box.size == 3


# --------------------------------------------------------------------------- #
# File + CLI
# --------------------------------------------------------------------------- #


def test_migrate_file_rewrites_in_place(tmp_path: Path) -> None:
    path = tmp_path / "c.yaml"
    path.write_text("m: !class:Box(size=3)\n")
    result, problems = migrate_file(path)
    assert problems == [] and result.changed
    assert path.read_text() == "m:\n  _target_: Box\n  size: 3\n"


def test_migrate_file_writes_nothing_when_verification_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A file whose conversion is not provably equivalent is left ALONE."""
    import confluid.migrate as mig

    path = tmp_path / "c.yaml"
    path.write_text("m: !class:Box(size=3)\n")
    monkeypatch.setattr(mig, "verify_equivalence", lambda *a, **k: ["synthetic problem"])
    _result, problems = migrate_file(path, verify=True)
    assert problems == ["synthetic problem"]
    assert path.read_text() == "m: !class:Box(size=3)\n", "the file must be untouched"


def test_check_mode_writes_nothing_and_signals(tmp_path: Path) -> None:
    path = tmp_path / "c.yaml"
    path.write_text("m: !class:Box(size=3)\n")
    assert main([str(path), "--check"]) == 1
    assert path.read_text() == "m: !class:Box(size=3)\n"


def test_check_mode_is_clean_on_an_already_migrated_file(tmp_path: Path) -> None:
    path = tmp_path / "c.yaml"
    path.write_text("m:\n  _target_: Box\n  size: 3\n")
    assert main([str(path), "--check"]) == 0


def test_a_directory_is_searched_recursively(tmp_path: Path) -> None:
    (tmp_path / "nested").mkdir()
    (tmp_path / "a.yaml").write_text("m: !class:Box\n")
    (tmp_path / "nested" / "b.yml").write_text("m: !class:Box\n")
    assert main([str(tmp_path)]) == 0
    assert "_target_" in (tmp_path / "a.yaml").read_text()
    assert "_target_" in (tmp_path / "nested" / "b.yml").read_text()


def test_the_report_records_every_site(tmp_path: Path) -> None:
    path = tmp_path / "c.yaml"
    path.write_text("m: !class:Box(size=3)\no: !lazy:Opt\n")
    report = tmp_path / "r.csv"
    main([str(path), "--report", str(report)])
    rows = report.read_text().splitlines()
    assert rows[0] == "file,line,before,after"
    assert len(rows) == 3
    assert "!class:Box(size=3)" in rows[1]


def test_findings_make_the_run_signal(tmp_path: Path) -> None:
    """An unconvertible site must not exit 0 — CI has to notice."""
    path = tmp_path / "c.yaml"
    path.write_text('m: "!class:Box(lr=!ref:base)"\n')
    assert main([str(path)]) == 1


# --------------------------------------------------------------------------- #
# Shapes real configs use — each found by running the codemod over the workspace
# --------------------------------------------------------------------------- #


def test_a_tag_alone_on_its_line_converts_in_place() -> None:
    """The form waivefront writes throughout: the tag applies to the block BELOW
    it, at the SAME indent, so its keys join that block rather than opening a
    new level."""
    text = "stream:\n  !class:Box\n  size: 3\n"
    assert convert(text) == "stream:\n  _target_: Box\n  size: 3\n"


def test_a_lone_lazy_tag_carries_partial() -> None:
    assert convert("m:\n  !lazy:Opt\n  lr: 0.5\n") == "m:\n  _target_: Opt\n  _partial_: true\n  lr: 0.5\n"


def test_an_empty_flow_mapping_keeps_the_compact_style() -> None:
    """``- !class:X {}`` is a no-kwargs node written inline; exploding it over
    two lines would restyle a line the author chose to keep short."""
    assert convert("ops:\n  - !class:Box {}\n") == "ops:\n  - {_target_: Box}\n"


def test_a_flow_mapping_merges_its_keys() -> None:
    assert convert("ops:\n  - !class:Box {size: 3}\n") == "ops:\n  - {_target_: Box, size: 3}\n"


def test_a_flow_value_may_contain_braces() -> None:
    """A per-record template inside a quoted value — the brace scan must not stop
    at the inner ``}``. A ``\\{[^}]*\\}`` pattern does, and left two real waivefront
    configs unconverted.
    """
    text = 'ops:\n  - !class:Threshold { low_level: "-{reference_snr_level}" }\n'
    got = convert(text)
    assert got == 'ops:\n  - {_target_: Threshold, low_level: "-{reference_snr_level}"}\n'
    assert yaml.safe_load(got)["ops"][0]["low_level"] == "-{reference_snr_level}"


def test_a_scalar_bodied_scope_becomes_a_one_item_list() -> None:
    """The old scalar body needs no spelling of its own — it is a list of one, and
    `_resolve_list` already extends, so the result is identical."""
    migrated, _conversions, findings = migrate_text("ops:\n  - first\n  - !scope:verbose 42\n")
    assert migrated == "ops:\n  - first\n  - - _scope_: {verbose: }\n    - 42\n"
    assert findings == []
