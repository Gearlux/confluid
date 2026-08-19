from pathlib import Path

import pytest

from confluid import load


def test_file_includes(tmp_path: Path) -> None:
    common = tmp_path / "common.yaml"
    common.write_text("base_val: 1\nshared: True")

    main = tmp_path / "main.yaml"
    main.write_text("include: common.yaml\nmain_val: 2\nshared: False")

    data = load(main, until="raw")
    assert data["base_val"] == 1
    assert data["main_val"] == 2
    # main should override common
    assert data["shared"] is False


def test_circular_include_error(tmp_path: Path) -> None:
    a = tmp_path / "a.yaml"
    b = tmp_path / "b.yaml"

    a.write_text("include: b.yaml")
    b.write_text("include: a.yaml")

    with pytest.raises(ValueError, match="Circular include"):
        load(a, until="raw")


def test_return_paths_single_file(tmp_path: Path) -> None:
    """Single-file load returns the entrypoint as the only path."""
    main = tmp_path / "main.yaml"
    main.write_text("foo: 1\nbar: 2")

    data, paths = load(main, until="raw", return_paths=True)
    assert data == {"foo": 1, "bar": 2}
    assert paths == [main.resolve()]


def test_return_paths_returns_full_tree(tmp_path: Path) -> None:
    """Nested includes appear in load order, deduplicated, with the entrypoint first."""
    common = tmp_path / "common.yaml"
    common.write_text("base_val: 1")

    extra = tmp_path / "extra.yaml"
    extra.write_text("extra_val: 9")

    main = tmp_path / "main.yaml"
    main.write_text("include:\n  - common.yaml\n  - extra.yaml\nmain_val: 2")

    data, paths = load(main, until="raw", return_paths=True)
    assert data["base_val"] == 1
    assert data["extra_val"] == 9
    assert data["main_val"] == 2

    resolved = [p.resolve() for p in paths]
    assert resolved[0] == main.resolve()
    assert common.resolve() in resolved
    assert extra.resolve() in resolved
    # Deduplicated
    assert len(resolved) == len(set(resolved))


def test_return_paths_threadlocal_isolation(tmp_path: Path) -> None:
    """A bare ``load(until="raw")`` inside a ``return_paths=True`` call does not
    pollute the outer accumulator, and successive calls each get a fresh list."""
    main_a = tmp_path / "a.yaml"
    main_a.write_text("a: 1")
    main_b = tmp_path / "b.yaml"
    main_b.write_text("b: 2")

    _, paths_a = load(main_a, until="raw", return_paths=True)
    _, paths_b = load(main_b, until="raw", return_paths=True)

    assert paths_a == [main_a.resolve()]
    assert paths_b == [main_b.resolve()]
    # No bleed-through.
    assert main_b.resolve() not in paths_a
    assert main_a.resolve() not in paths_b


def test_return_paths_circular_error(tmp_path: Path) -> None:
    """Circular includes still raise under ``return_paths=True``."""
    a = tmp_path / "a.yaml"
    b = tmp_path / "b.yaml"
    a.write_text("include: b.yaml")
    b.write_text("include: a.yaml")

    with pytest.raises(ValueError, match="Circular include"):
        load(a, until="raw", return_paths=True)


# ---------------------------------------------------------------------------
# Includes and document order — the including file is read LAST (2026-08-11).
#
# Confluid has ONE precedence rule (document order, last spec wins), so the
# merged KEY ORDER is the arbitration. `deep_merge` used to keep the base's
# position for a key the includer re-stated, which made the include beat an
# override written after it — the rule inverted by the most common composition
# in the system. See `merger.deep_merge`.
# ---------------------------------------------------------------------------


def test_an_overridden_key_takes_the_including_files_position(tmp_path: Path) -> None:
    """Key order after the merge is base-only keys, then the includer's keys."""
    base = tmp_path / "base.yaml"
    base.write_text("lr: 0.1\nStage:\n  lr: 0.2\nkept: 1\n")
    main = tmp_path / "main.yaml"
    main.write_text("include: base.yaml\nlr: 0.3\nextra: 9\n")

    assert list(load(main, until="raw")) == ["Stage", "kept", "lr", "extra"]


def test_an_override_written_after_an_include_beats_the_includes_addressed_block(tmp_path: Path) -> None:
    """The measured defect: `lr: 0.3` lost to the include's `Stage: {lr: 0.2}`.

    Both spellings of one document must agree — the flat form has always given
    0.3, and the include form gave 0.2 with no diagnostic anywhere.
    """
    from confluid import configurable, load

    @configurable(name="IncludeOrderStage")
    class Stage:
        def __init__(self, lr: float = 0.0) -> None:
            self.lr = lr

    base = tmp_path / "base.yaml"
    base.write_text("lr: 0.1\nIncludeOrderStage:\n  lr: 0.2\n")
    main = tmp_path / "main.yaml"
    main.write_text("include: base.yaml\nlr: 0.3\ns: !class:IncludeOrderStage()\n")

    via_include = load(main)["s"].lr
    flat = load("IncludeOrderStage:\n  lr: 0.2\nlr: 0.3\ns: !class:IncludeOrderStage()\n")["s"].lr

    assert via_include == flat == 0.3


def test_an_include_still_wins_over_an_earlier_include(tmp_path: Path) -> None:
    """Between two includes the LATER one wins, and keeps the later position."""
    first = tmp_path / "first.yaml"
    first.write_text("shared: 1\nonly_first: a\n")
    second = tmp_path / "second.yaml"
    second.write_text("shared: 2\nonly_second: b\n")
    main = tmp_path / "main.yaml"
    main.write_text("include:\n  - first.yaml\n  - second.yaml\nmine: z\n")

    data = load(main, until="raw")
    assert data["shared"] == 2
    assert list(data) == ["only_first", "shared", "only_second", "mine"]


# ---------------------------------------------------------------------------
# `include:` splices AT ITS POSITION (2026-08-11).
#
# The directive behaves as if the included document were pasted into the source
# document at that line — the only reading consistent with the one precedence
# rule, and the same rule `!scope:` blocks already follow. Until this change the
# directive was popped and the WHOLE including file merged over the result, so
# where you wrote it made no difference at all.
# ---------------------------------------------------------------------------


def test_an_include_splices_at_the_position_it_was_written(tmp_path: Path) -> None:
    """Include first → my later lines win. Include last → the paste wins."""
    (tmp_path / "base.yaml").write_text("a: from_base\nb: from_base\n")
    first = tmp_path / "first.yaml"
    first.write_text("include: base.yaml\na: from_main\n")
    last = tmp_path / "last.yaml"
    last.write_text("a: from_main\ninclude: base.yaml\n")

    assert load(first, until="raw")["a"] == "from_main"  # my line is later → mine wins
    assert load(last, until="raw")["a"] == "from_base"  # the paste is later → base wins
    # A key written on ONE side only keeps the position of the side that has it.
    assert list(load(first, until="raw")) == ["b", "a"]
    assert list(load(last, until="raw")) == ["a", "b"]


def test_an_include_in_the_middle_splits_the_document(tmp_path: Path) -> None:
    """Keys above the include lose to it; keys below it win. One rule, one reading."""
    (tmp_path / "base.yaml").write_text("x: from_base\ny: from_base\n")
    main = tmp_path / "main.yaml"
    main.write_text("x: above\ninclude: base.yaml\ny: below\n")

    data = load(main, until="raw")
    assert data["x"] == "from_base"  # written above the paste → the paste wins
    assert data["y"] == "below"  # written below the paste → mine wins


def test_an_include_at_the_bottom_makes_the_document_a_set_of_fallbacks(tmp_path: Path) -> None:
    """The capability the old behaviour could not express at all.

    With the include last, everything above it is a fallback the shared file
    overrides — previously impossible without splitting into another file, since
    the including file always won regardless of where the directive sat.
    """
    from confluid import configurable, load

    @configurable(name="FallbackStage")
    class Stage:
        def __init__(self, lr: float = 0.0) -> None:
            self.lr = lr

    (tmp_path / "base.yaml").write_text("lr: 0.1\nFallbackStage:\n  lr: 0.2\n")
    main = tmp_path / "main.yaml"
    main.write_text("lr: 0.3\ns: !class:FallbackStage()\ninclude: base.yaml\n")

    cfg = load(main, until="raw")
    assert list(cfg) == ["s", "lr", "FallbackStage"]
    assert cfg["lr"] == 0.1  # one `lr`, at the paste's later position, base's value
    assert load(cfg)["s"].lr == 0.2  # the addressed block is the last spec


def test_a_positional_include_still_deep_merges_a_nested_block(tmp_path: Path) -> None:
    """Splicing changes WHERE a block lands, not that blocks combine."""
    (tmp_path / "base.yaml").write_text("Trainer:\n  lr: 0.1\n  epochs: 5\n")
    main = tmp_path / "main.yaml"
    main.write_text("include: base.yaml\nTrainer:\n  lr: 0.9\n")

    assert load(main, until="raw")["Trainer"] == {"lr": 0.9, "epochs": 5}


def test_two_includes_paste_in_the_order_they_are_listed(tmp_path: Path) -> None:
    (tmp_path / "first.yaml").write_text("shared: 1\nonly_first: a\n")
    (tmp_path / "second.yaml").write_text("shared: 2\nonly_second: b\n")
    main = tmp_path / "main.yaml"
    main.write_text("shared: 0\ninclude:\n  - first.yaml\n  - second.yaml\n")

    data = load(main, until="raw")
    assert data["shared"] == 2  # both pastes sit after my line; the later paste wins
    assert list(data) == ["only_first", "shared", "only_second"]


# ---------------------------------------------------------------------------
# `include:` works in EVERY position (BUGS-2026-08-13 P15)
#
# The directive was honoured only where the recursive walk reached the dict
# branch. A marker's kwargs and a scope block's contents were walked VALUE-wise,
# so their own `include:` key never reached the splice: inside a marker it became
# a constructor kwarg named `include`, and inside a scope block it leaked into the
# config as literal data.
#
# Scope blocks additionally settle ITERATIVELY — scopes resolve, then includes
# splice, repeating — so an unactivated block's file is never opened. A
# framework-specific overlay must not have to exist in a checkout that never
# activates that framework.
# ---------------------------------------------------------------------------

from confluid import ConfigurationError, configurable  # noqa: E402
from confluid.fluid import Target  # noqa: E402


@configurable
class Widget:
    def __init__(self, size: int = 1, label: str = "w") -> None:
        self.size, self.label = size, label


def test_include_inside_a_markers_kwargs_splices(tmp_path: Path) -> None:
    (tmp_path / "frag.yaml").write_text("size: 7\nlabel: from-frag\n")
    main = tmp_path / "main.yaml"
    main.write_text("m:\n  _target_: Widget\n  include: frag.yaml\n")

    marker = load(str(main), until="document")["m"]

    assert isinstance(marker, Target)
    assert marker.kwargs == {"size": 7, "label": "from-frag"}
    assert "include" not in marker.kwargs


def test_include_inside_an_ACTIVE_scope_block_splices(tmp_path: Path) -> None:
    """The `post_include` pattern: a conditional overlay applied at the block's slot."""
    (tmp_path / "b.yaml").write_text("from_b: 2\nlr: 0.9\n")
    main = tmp_path / "main.yaml"
    main.write_text("lr: 0.1\npost_include:\n  _notscope_: { default: }\n  include: b.yaml\n")

    assert load(str(main), until="document") == {"lr": 0.9, "from_b": 2}


def test_include_inside_an_INACTIVE_scope_block_is_never_opened(tmp_path: Path) -> None:
    """The property the iterative settle exists to protect.

    The file does not exist at all. A framework overlay must not be mandatory in
    a checkout that never activates that framework.
    """
    main = tmp_path / "main.yaml"
    main.write_text("lr: 0.1\npost_include:\n  _scope_: { framework: torch }\n  include: does_not_exist.yaml\n")

    assert load(str(main), until="document") == {"lr": 0.1}


def test_an_included_file_may_itself_carry_a_scoped_include(tmp_path: Path) -> None:
    """The iteration has to REPEAT: splicing can expose a new scope block, whose
    activation can expose a further include."""
    (tmp_path / "inner.yaml").write_text("depth: 2\n")
    (tmp_path / "outer.yaml").write_text(
        "depth: 1\nnested:\n  _notscope_: { default: }\n  include: inner.yaml\n",
    )
    main = tmp_path / "main.yaml"
    main.write_text("top:\n  _notscope_: { default: }\n  include: outer.yaml\n")

    assert load(str(main), until="document") == {"depth": 2}


def test_the_same_wrapper_key_in_an_included_and_the_including_file_merges(tmp_path: Path) -> None:
    """BUGS-2026-08-19 PA4, end to end: both blocks condition on the same
    dimension, so the activated result carries both files' keys."""
    (tmp_path / "base.yaml").write_text("torch: !scope:framework=torch\n  lr: 0.1\n")
    main = tmp_path / "main.yaml"
    main.write_text("include: base.yaml\ntorch: !scope:framework=torch\n  epochs: 5\n")

    assert load(str(main), scopes=["framework=torch"], until="document") == {"lr": 0.1, "epochs": 5}
    assert load(str(main), until="document") == {}


def test_keys_after_a_scoped_include_still_override_it(tmp_path: Path) -> None:
    """Position semantics survive the extra pass: the block's own later key wins."""
    (tmp_path / "b.yaml").write_text("lr: 0.9\n")
    main = tmp_path / "main.yaml"
    main.write_text("w:\n  _notscope_: { default: }\n  include: b.yaml\n  lr: 0.5\n")

    assert load(str(main), until="document") == {"lr": 0.5}


def test_a_scoped_include_cycle_is_bounded(tmp_path: Path) -> None:
    """Two files each including the other from inside a scope block.

    Each pass exposes the other's directive, so a naive loop never terminates.
    The settle is capped and says so rather than hanging.
    """
    (tmp_path / "a.yaml").write_text("a_seen: 1\nnest:\n  _notscope_: { default: }\n  include: b.yaml\n")
    (tmp_path / "b.yaml").write_text("b_seen: 1\nnest:\n  _notscope_: { default: }\n  include: a.yaml\n")
    main = tmp_path / "main.yaml"
    main.write_text("start:\n  _notscope_: { default: }\n  include: a.yaml\n")

    with pytest.raises(ConfigurationError, match="include|circular|passes"):
        load(str(main), until="document")


def test_include_with_a_mapping_value_is_refused(tmp_path: Path) -> None:
    """Today it consumed the key and spliced NOTHING — a whole file lost in silence."""
    (tmp_path / "frag.yaml").write_text("size: 7\n")
    main = tmp_path / "main.yaml"
    main.write_text("a: 1\ninclude: {path: frag.yaml}\n")

    with pytest.raises(ConfigurationError, match="include"):
        load(str(main), until="document")


def test_a_non_string_include_entry_is_refused(tmp_path: Path) -> None:
    """Today the entry was skipped and the rest spliced, so the loss was invisible."""
    (tmp_path / "frag.yaml").write_text("size: 7\n")
    main = tmp_path / "main.yaml"
    main.write_text("a: 1\ninclude: [frag.yaml, 42]\n")

    with pytest.raises(ConfigurationError, match="include"):
        load(str(main), until="document")


# --- the con cases: every position that already worked must be untouched -----


def test_a_plain_dict_include_is_unchanged(tmp_path: Path) -> None:
    (tmp_path / "frag.yaml").write_text("size: 7\nlabel: from-frag\n")
    main = tmp_path / "main.yaml"
    main.write_text("a: 1\ninclude: frag.yaml\n")

    assert load(str(main), until="document") == {"a": 1, "size": 7, "label": "from-frag"}


def test_a_nested_dict_include_is_unchanged(tmp_path: Path) -> None:
    (tmp_path / "frag.yaml").write_text("size: 7\n")
    main = tmp_path / "main.yaml"
    main.write_text("outer:\n  inner:\n    include: frag.yaml\n    keep: me\n")

    assert load(str(main), until="document") == {"outer": {"inner": {"size": 7, "keep": "me"}}}


def test_a_list_item_include_is_unchanged(tmp_path: Path) -> None:
    (tmp_path / "frag.yaml").write_text("size: 7\n")
    main = tmp_path / "main.yaml"
    main.write_text("items:\n  - include: frag.yaml\n    x: 1\n")

    assert load(str(main), until="document") == {"items": [{"size": 7, "x": 1}]}


def test_a_list_of_include_paths_is_unchanged(tmp_path: Path) -> None:
    (tmp_path / "a.yaml").write_text("from_a: 1\n")
    (tmp_path / "b.yaml").write_text("from_b: 2\n")
    main = tmp_path / "main.yaml"
    main.write_text("x: 1\ninclude: [a.yaml, b.yaml]\n")

    assert load(str(main), until="document") == {"x": 1, "from_a": 1, "from_b": 2}


def test_a_scope_block_without_an_include_is_unchanged(tmp_path: Path) -> None:
    main = tmp_path / "main.yaml"
    main.write_text("lr: 0.1\npost_block:\n  _notscope_: { default: }\n  lr: 0.9\n  from_b: 2\n")

    assert load(str(main), until="document") == {"lr": 0.9, "from_b": 2}
