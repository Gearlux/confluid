"""`confluid.spelling.to_tags` — the reserved-key spelling → the tag spelling (record 19, phase 1b).

The inverse of the 2026-08-11 codemod, for the same reason that one edited LINES:
these files are ~86% comments and the comments are the documentation. So the tag
goes on the KEY line, the `_target_:` / `_partial_:` / `_scope_:` line is
deleted, and every other byte — comments, order, quoting, spacing — is untouched.

Safety is not the grammar, it is the check: every conversion here is also
verified by `hydraide.emit(before) == hydraide.emit(after)` — the two spellings of
one document must resolve to byte-identical output, and that is the property the
corpus run rests on.
"""

import textwrap
from pathlib import Path
from typing import Any

from confluid import configurable
from confluid.hydraide import emit
from confluid.spelling import Finding, to_tags


@configurable
class SModel:
    def __init__(self, hidden: int = 16, lr: float = 0.0, name: str = "m") -> None:
        self.hidden, self.lr, self.name = hidden, lr, name


@configurable
class SAdam:
    def __init__(self, params: Any = None, lr: float = 0.0) -> None:
        self.params, self.lr = params, lr


@configurable
class SStream:
    def __init__(self, ops: Any = None, source: Any = None) -> None:
        self.ops, self.source = ops, source


def _same(before: str, after: str, **kw: Any) -> None:
    """The safety net every conversion is held to."""
    assert emit(before, **kw) == emit(after, **kw), f"\n--- before\n{before}\n--- after\n{after}"


def convert(text: str) -> str:
    out, findings = to_tags(textwrap.dedent(text))
    assert findings == [], findings
    return out


# --------------------------------------------------------------------------- #
# The shape matrix
# --------------------------------------------------------------------------- #


def test_a_block_marker_tags_the_key_and_drops_the_target_line() -> None:
    before = "model:\n  _target_: SModel\n  hidden: 32\n"
    after = convert(before)
    assert after == "model: !class:SModel\n  hidden: 32\n"
    _same(before, after)


def test_a_block_marker_with_no_other_kwargs_becomes_a_bare_tag() -> None:
    before = "model:\n  _target_: SModel\n"
    after = convert(before)
    assert after == "model: !class:SModel\n"
    _same(before, after)


def test_partial_true_selects_the_partial_tag_and_drops_both_lines() -> None:
    before = "opt:\n  _target_: SAdam\n  _partial_: true\n  lr: 0.5\n"
    after = convert(before)
    assert after == "opt: !partial:SAdam\n  lr: 0.5\n"
    _same(before, after)


def test_partial_false_is_dropped_as_the_default() -> None:
    before = "opt:\n  _target_: SAdam\n  _partial_: false\n  lr: 0.5\n"
    assert convert(before) == "opt: !class:SAdam\n  lr: 0.5\n"


def test_the_target_line_may_sit_anywhere_in_the_body() -> None:
    before = "model:\n  hidden: 32\n  _target_: SModel\n  lr: 0.1\n"
    after = convert(before)
    assert after == "model: !class:SModel\n  hidden: 32\n  lr: 0.1\n"
    _same(before, after)


def test_a_flow_marker_with_scalar_kwargs_becomes_the_inline_call_form() -> None:
    before = "opt: {_target_: SAdam, lr: 0.5}\n"
    after = convert(before)
    assert after == "opt: !class:SAdam(lr=0.5)\n"
    _same(before, after)


def test_a_flow_marker_with_no_kwargs_becomes_a_bare_tag() -> None:
    assert convert("m: {_target_: SModel}\n") == "m: !class:SModel\n"


def test_a_flow_partial_becomes_the_inline_partial_form() -> None:
    before = "opt: {_target_: SAdam, _partial_: true, lr: 0.5}\n"
    after = convert(before)
    assert after == "opt: !partial:SAdam(lr=0.5)\n"
    _same(before, after)


def test_a_flow_marker_with_a_non_scalar_kwarg_keeps_a_flow_body() -> None:
    """The inline `(k=v)` grammar takes scalars only; anything else rides a
    flow-mapping body after the tag, verbatim."""
    before = "s: {_target_: SStream, ops: [a, b]}\n"
    after = convert(before)
    assert after == "s: !class:SStream {ops: [a, b]}\n"
    _same(before, after)


def test_a_flow_marker_with_a_string_the_inline_grammar_cannot_carry_keeps_a_flow_body() -> None:
    """A space, a comma or a paren in a value would be mangled by `(k=v)`."""
    before = 'm: {_target_: SModel, name: "two words"}\n'
    after = convert(before)
    assert after == 'm: !class:SModel {name: "two words"}\n'
    _same(before, after)


def test_a_nested_block_marker_is_converted_recursively() -> None:
    before = "s:\n  _target_: SStream\n  source:\n    _target_: SModel\n    hidden: 8\n"
    after = convert(before)
    assert after == "s: !class:SStream\n  source: !class:SModel\n    hidden: 8\n"
    _same(before, after)


def test_a_nested_flow_marker_inside_a_flow_marker_is_converted() -> None:
    before = "s: {_target_: SStream, source: {_target_: SModel, hidden: 8}}\n"
    after = convert(before)
    # A tag INSIDE a flow container must be followed by whitespace (PyYAML scans
    # `!class:SModel(hidden=8)}` as one tag), so a nested marker takes the flow-body form.
    assert after == "s: !class:SStream {source: !class:SModel {hidden: 8}}\n"
    _same(before, after)


def test_a_nested_flow_marker_without_kwargs_takes_an_empty_body() -> None:
    before = "s: {_target_: SStream, source: {_target_: SModel}}\n"
    after = convert(before)
    assert after == "s: !class:SStream {source: !class:SModel {}}\n"
    _same(before, after)


def test_a_list_item_marker_on_the_dash_line() -> None:
    before = "ops:\n  - _target_: SModel\n    hidden: 4\n  - _target_: SModel\n"
    after = convert(before)
    assert after == "ops:\n  - !class:SModel\n    hidden: 4\n  - !class:SModel\n"
    _same(before, after)


def test_a_flow_list_item_marker() -> None:
    before = "ops:\n  - {_target_: SModel, hidden: 4}\n"
    after = convert(before)
    assert after == "ops:\n  - !class:SModel(hidden=4)\n"
    _same(before, after)


# --------------------------------------------------------------------------- #
# References and scopes
# --------------------------------------------------------------------------- #


def test_a_whole_value_ref_placeholder_becomes_the_ref_tag() -> None:
    before = "model:\n  _target_: SModel\ns:\n  _target_: SStream\n  source: ${ref:model}\n"
    after = convert(before)
    assert after == "model: !class:SModel\ns: !class:SStream\n  source: !ref:model\n"
    _same(before, after)


def test_an_attribute_ref_and_an_import_ref_convert_the_same_way() -> None:
    """Phase 2's business, not this tool's: `${ref:a.b}` is `!ref:a.b`, the same
    late-bound marker, and passes through."""
    assert convert("x: ${ref:split.train}\n") == "x: !ref:split.train\n"
    assert convert("f: ${ref:pkg.mod.func}\n") == "f: !ref:pkg.mod.func\n"


def test_a_ref_inside_a_flow_sequence_is_LEFT_as_the_placeholder() -> None:
    """Inside a flow container the placeholder must be QUOTED (an unquoted `{`
    opens a flow mapping), and `[!ref:model, !ref:model]` does not scan either —
    PyYAML reads `ref:model,` as the tag. `${ref:}` is legal in both spellings,
    so a quoted one stays."""
    before = 'model:\n  _target_: SModel\ns:\n  _target_: SStream\n  ops: ["${ref:model}", "${ref:model}"]\n'
    after = convert(before)
    assert after == 'model: !class:SModel\ns: !class:SStream\n  ops: ["${ref:model}", "${ref:model}"]\n'
    _same(before, after)


def test_the_ref_mapping_form_converts() -> None:
    before = "model:\n  _target_: SModel\nalias: {_ref_: model}\n"
    after = convert(before)
    assert after == "model: !class:SModel\nalias: !ref:model\n"
    _same(before, after)


def test_a_single_dimension_scope_block_tags_the_wrapper_key() -> None:
    before = "lr: 0.1\ntorch_only:\n  _scope_: {framework: torch}\n  lr: 0.3\n"
    after = convert(before)
    assert after == "lr: 0.1\ntorch_only: !scope:framework=torch\n  lr: 0.3\n"
    _same(before, after)
    _same(before, after, scopes=["framework=torch"])


def test_a_boolean_dimension_scope_block() -> None:
    before = "dbg:\n  _scope_: {debug: }\n  level: DEBUG\n"
    after = convert(before)
    assert after == "dbg: !scope:debug\n  level: DEBUG\n"
    _same(before, after)
    _same(before, after, scopes=["debug"])


def test_a_notscope_block() -> None:
    before = "default:\n  _notscope_: {model: }\n  m: 1\n"
    after = convert(before)
    assert after == "default: !notscope:model\n  m: 1\n"
    _same(before, after)


def test_a_flow_scope_block() -> None:
    before = "t: {_scope_: {framework: torch}, lr: 0.3}\n"
    after = convert(before)
    assert after == "t: !scope:framework=torch {lr: 0.3}\n"
    _same(before, after, scopes=["framework=torch"])


# --------------------------------------------------------------------------- #
# What the tool preserves and what it reports
# --------------------------------------------------------------------------- #


def test_comments_and_blank_lines_are_untouched() -> None:
    before = textwrap.dedent(
        """\
        # the model
        model:            # trailing on the key
          _target_: SModel   # trailing on the target line
          # a comment inside the body

          hidden: 32      # trailing on a kwarg
        """
    )
    after, findings = to_tags(before)
    assert findings == []
    assert after == textwrap.dedent(
        """\
        # the model
        model: !class:SModel            # trailing on the key   # trailing on the target line
          # a comment inside the body

          hidden: 32      # trailing on a kwarg
        """
    )
    _same(before, after)


def test_a_document_already_in_tag_form_is_a_no_op() -> None:
    text = "model: !class:SModel(hidden=32)\nopt: !partial:SAdam\ns: !class:SStream\n  source: !ref:model\n"
    assert to_tags(text) == (text, [])


def test_to_tags_is_idempotent() -> None:
    before = "model:\n  _target_: SModel\n  hidden: 32\nopt: {_target_: SAdam, _partial_: true}\n"
    once, _ = to_tags(before)
    assert to_tags(once) == (once, [])


def test_non_marker_content_is_untouched() -> None:
    text = "include: base.yaml\nlr: 0.1\nnames: {a: 1, b: 2}\nitems: [1, 2]\ntarget: not-reserved\n"
    assert to_tags(text) == (text, [])


def test_a_multi_dimension_scope_is_REPORTED_not_converted() -> None:
    """A tag suffix is a string and carries ONE dimension; the mapping form is the
    only spelling for several. Zero of these exist in the workspace today — the
    report exists so a future one is loud."""
    text = "b:\n  _scope_: {framework: torch, size: big}\n  lr: 1\n"
    out, findings = to_tags(text)
    assert out == text, "the line must be left alone"
    assert len(findings) == 1 and findings[0].line == 2 and "dimension" in findings[0].reason


def test_a_merge_key_on_a_marker_is_REPORTED_not_converted() -> None:
    """`<<:` merges KEYS into a mapping; a tagged node cannot be a merge target
    the same way, so the P2 idiom has no line-for-line tag form."""
    text = "base: &b\n  _target_: SModel\n  hidden: 1\nfast:\n  <<: *b\n  hidden: 2\n"
    out, findings = to_tags(text)
    assert any("merge" in f.reason for f in findings)


def test_a_list_form_scope_block_tags_the_dash_and_keeps_the_body_items() -> None:
    """`- - _scope_: {k: v}` — the ONLY plain spelling of a conditional list ITEM —
    becomes `- !scope:k=v` with the body items (the remaining `- ` lines) untouched;
    a sequence body EXTENDS the surrounding list in both spellings."""
    before = 'ops:\n  - always\n  - - _scope_: {extra: "yes"}\n    - extra_a\n    - 42\n  - last\n'
    after = convert(before)
    assert after == "ops:\n  - always\n  - !scope:extra=yes\n    - extra_a\n    - 42\n  - last\n"
    _same(before, after)
    _same(before, after, scopes=["extra=yes"])


def test_a_list_form_scope_block_with_two_dimensions_is_REPORTED() -> None:
    text = "ops:\n  - - _scope_: {a: x, b: y}\n    - extra_a\n"
    out, findings = to_tags(text)
    assert out == text
    assert any("dimension" in f.reason for f in findings)


def test_a_finding_carries_path_line_and_text() -> None:
    _, findings = to_tags("b:\n  _scope_: {a: x, b: y}\n  k: 1\n", path="cfg.yaml")
    assert isinstance(findings[0], Finding)
    assert (findings[0].path, findings[0].line) == ("cfg.yaml", 2)
    assert "_scope_" in findings[0].text


# --------------------------------------------------------------------------- #
# The corpus-run helper: convert a FILE and prove it equivalent under every activation
# --------------------------------------------------------------------------- #


def test_convert_file_rewrites_only_when_equivalent_under_every_activation(tmp_path: Path) -> None:
    from confluid.spelling import convert_file

    (tmp_path / "base.yaml").write_text("model:\n  _target_: SModel\n  hidden: 32\n")
    cfg = tmp_path / "exp.yaml"
    cfg.write_text(
        "include: base.yaml\nlr: 0.1\ntorch_only:\n  _scope_: {framework: torch}\n  lr: 0.3\n"
        "opt: {_target_: SAdam, _partial_: true}\n"
    )

    result = convert_file(cfg)

    assert result.written is True and result.findings == []
    assert cfg.read_text() == (
        "include: base.yaml\nlr: 0.1\ntorch_only: !scope:framework=torch\n  lr: 0.3\n" "opt: !partial:SAdam\n"
    )
    assert result.activations_checked >= 2, "default AND framework=torch"


def test_convert_file_leaves_a_file_with_findings_untouched(tmp_path: Path) -> None:
    from confluid.spelling import convert_file

    cfg = tmp_path / "multi.yaml"
    text = "b:\n  _scope_: {a: x, b: y}\n  k: 1\n"
    cfg.write_text(text)

    result = convert_file(cfg)

    assert result.written is False and len(result.findings) == 1
    assert cfg.read_text() == text


def test_convert_file_is_a_no_op_on_a_tag_form_file(tmp_path: Path) -> None:
    from confluid.spelling import convert_file

    cfg = tmp_path / "tags.yaml"
    cfg.write_text("model: !class:SModel(hidden=1)\n")
    assert convert_file(cfg).written is False
