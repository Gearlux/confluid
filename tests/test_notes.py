"""What a config file says about its own settings: each top-level key's comment, its ``!ref:`` feeds, its scopes —
and a class parameter's help read from source, nothing imported (``confluid.notes``)."""

import sys
import textwrap
from pathlib import Path

import pytest

from confluid import DocumentNotes, KeyNote, ScopeNote, SourceParam, document_notes, source_param_docs, source_params


def _config(tmp_path: Path, body: str, name: str = "run.yaml") -> Path:
    path = tmp_path / name
    path.write_text(textwrap.dedent(body))
    return path


def _keys(notes: DocumentNotes) -> dict:
    return {key.name: key.comment for key in notes.keys}


def test_a_keys_comment_is_the_run_of_comment_lines_directly_above_it(tmp_path: Path) -> None:
    notes = document_notes(
        _config(
            tmp_path,
            """\
            # A file header the blank line below keeps apart.

            # The folders read,
            # each one as it is.
            folders: []
            # The CSV written.
            catalog: out.csv
            plain: 1
            """,
        )
    )
    assert _keys(notes) == {
        "folders": "The folders read, each one as it is.",
        "catalog": "The CSV written.",
        "plain": "",
    }
    assert [key.line for key in notes.keys] == [5, 7, 8]


def test_without_a_comment_above_the_comment_ending_its_line_is_taken(tmp_path: Path) -> None:
    notes = document_notes(
        _config(
            tmp_path,
            """\
            per_folder: 0    # 0 = every setup
            label: "a # inside quotes"
            hashed: a#b
            quoted: 'it # is' # after the quotes
            above: 1  # the line's own
            """,
        )
    )
    assert _keys(notes) == {
        "per_folder": "0 = every setup",
        "label": "",
        "hashed": "",
        "quoted": "after the quotes",
        "above": "the line's own",
    }


def test_the_comment_ending_a_line_carries_on_through_the_comment_lines_aligned_below_it(tmp_path: Path) -> None:
    notes = document_notes(
        _config(
            tmp_path,
            """\
            boxes: [annotations]       # the box lists drawn: annotations (lime), regions
                                       # (yellow), visible (cyan),
                                       # all
            workers: 1                 # recordings drawn at once
            plain: 2
            """,
        )
    )
    assert _keys(notes) == {
        "boxes": "the box lists drawn: annotations (lime), regions (yellow), visible (cyan), all",
        "workers": "recordings drawn at once",  # the lines carrying boxes' comment on are no comment above it
        "plain": "",
    }


def test_a_comment_line_in_another_column_is_the_next_keys_not_a_carry_on(tmp_path: Path) -> None:
    notes = document_notes(
        _config(
            tmp_path,
            """\
            foo: 1   # foo's help
            # bar's help
            bar: 2
            baz: 3   # baz's help
              # not aligned: the next key's
            qux: 4
            """,
        )
    )
    assert _keys(notes) == {
        "foo": "foo's help",
        "bar": "bar's help",
        "baz": "baz's help",
        "qux": "not aligned: the next key's",
    }


def test_an_aligned_carry_on_stops_at_a_section_rule_and_leaves_the_comment_below_it_to_the_next_key(
    tmp_path: Path,
) -> None:
    notes = document_notes(
        _config(
            tmp_path,
            """\
            fft_size: 30kHz    # the spectrogram's bins:
                               # a count or a bin width
                               # ----------
            # The step between frames.
            hop_size: 1.0
            """,
        )
    )
    assert _keys(notes) == {
        "fft_size": "the spectrogram's bins: a count or a bin width",
        "hop_size": "The step between frames.",
    }


def test_a_scope_blocks_comment_carries_on_like_a_keys(tmp_path: Path) -> None:
    notes = document_notes(
        _config(
            tmp_path,
            """\
            quick: !scope:quick   # a quick look:
                                  # 5 folders
              max_folders: 5
            """,
        )
    )
    assert notes.scopes == (ScopeNote("quick", False, 1, "a quick look: 5 folders"),)


def test_a_comment_above_wins_over_the_one_ending_the_line(tmp_path: Path) -> None:
    notes = document_notes(_config(tmp_path, "# above\nkey: 1  # inline\n"))
    assert _keys(notes) == {"key": "above"}


def test_a_section_rule_ends_the_comment_run_like_a_blank_line(tmp_path: Path) -> None:
    notes = document_notes(
        _config(
            tmp_path,
            """\
            # -- Knobs ----------------------------------------
            fft_size: 30kHz    # the spectrogram's bins
            # == Output ==========
            # The folder written.
            folder: out
            # A heading the rule below keeps apart.
            # ~~~~
            plain: 1
            # Not a rule: a -- dash, a --- triple, a___b.
            prose: 2
            # ----------
            # A quick run.
            quick: !scope:quick
              plain: 2
            """,
        )
    )
    assert _keys(notes) == {
        "fft_size": "the spectrogram's bins",  # the rule is no comment of its: the line's own one is taken
        "folder": "The folder written.",
        "plain": "",
        "prose": "Not a rule: a -- dash, a --- triple, a___b.",
    }
    assert notes.scopes == (ScopeNote("quick", False, 13, "A quick run."),)


def test_directives_and_scope_blocks_are_not_settings(tmp_path: Path) -> None:
    _config(tmp_path, "other: 1\n", name="other.yaml")
    notes = document_notes(
        _config(
            tmp_path,
            """\
            include: other.yaml
            default_scopes: [task=a]
            setting: 1
            # A quick run.
            quick: !scope:quick
              setting: 2
            """,
        )
    )
    assert _keys(notes) == {"setting": ""}
    assert notes.scopes == (ScopeNote("quick", False, 5, "A quick run."),)


def test_every_scope_block_of_the_file_is_noted_in_either_spelling(tmp_path: Path) -> None:
    notes = document_notes(
        _config(
            tmp_path,
            """\
            classify: !scope:task=classification   # the classification task
              head: 1
            nodebug: !notscope:debug
              level: info
            runnable:
              # deep inside
              lean: !scope:lean
                size: 1
            both:
              _scope_: {task: detection, framework: keras}
              head: 2
            plain:
              _scope_: onnx
              export: true
            """,
        )
    )
    assert notes.scopes == (
        ScopeNote("task=classification", False, 1, "the classification task"),
        ScopeNote("debug", True, 3, ""),
        ScopeNote("lean", False, 7, "deep inside"),
        ScopeNote("task=detection,framework=keras", False, 9, ""),
        ScopeNote("onnx", False, 12, ""),
    )
    assert _keys(notes) == {"runnable": ""}


def test_a_key_notes_the_class_parameters_it_feeds_through_ref(tmp_path: Path) -> None:
    notes = document_notes(
        _config(
            tmp_path,
            """\
            folders: []
            size: 3
            runnable: !class:pkg.mod.Builder
              source_dirs: !ref:folders
              inner: !class:pkg.mod.Inner
                count: !ref:size
            plain:
              _target_: pkg.mod.Plain
              amount: {_ref_: size}
            """,
        )
    )
    feeds = {key.name: key.feeds for key in notes.keys}
    assert feeds["folders"] == (("pkg.mod.Builder", "source_dirs"),)
    assert feeds["size"] == (("pkg.mod.Inner", "count"), ("pkg.mod.Plain", "amount"))
    assert feeds["runnable"] == ()


def test_a_file_whose_top_is_a_marker_has_no_settings(tmp_path: Path) -> None:
    assert document_notes(_config(tmp_path, "!class:pkg.mod.Builder\nsize: 1\n")) == DocumentNotes()


def test_a_relative_path_resolves_through_the_search_tiers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _config(tmp_path, "key: 1  # found\n")
    monkeypatch.chdir(tmp_path)
    assert document_notes("run.yaml") == DocumentNotes(keys=(KeyNote("key", 1, "found"),))


@pytest.fixture
def source_package(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """A package on ``sys.path`` whose modules raise when imported — reading their source must not import them.

    Only the packages ABOVE a target's module are imported (``notes_pkg`` itself, empty here)."""
    root = tmp_path / "notes_pkg"
    (root / "impl").mkdir(parents=True)
    (root / "__init__.py").write_text("")
    (root / "impl" / "__init__.py").write_text(
        'raise RuntimeError("imported")\n'
        "from .builders import Builder\n"
        "from notes_pkg.impl.builders import make as build\n"
        "from os.path import join\n"
    )
    module = textwrap.dedent(
        '''\
        raise RuntimeError("imported")


        class Builder:
            """Build things.

            Args:
                size: How many things.
                label: Their label.
            """

            def __init__(self, size=1, label=""):
                pass


        class Initialised:
            """Its init holds the help."""

            def __init__(self, depth=0):
                """Set up.

                Args:
                    depth: How deep.
                """


        class Plain:
            """No constructor of its own."""


        class Typed:
            def __init__(self, p, /, a: int = 500, *args, flag: bool = False, name: Optional[str] = None,
                         on=True, made=compute(), switch: Optional[bool] = None, **kwargs):
                pass


        def make(width=1):
            """Make one.

            Args:
                width: How wide.
            """
        '''
    )
    (root / "impl" / "builders.py").write_text(module)
    (root / "direct.py").write_text(module)
    monkeypatch.syspath_prepend(str(tmp_path))
    return "notes_pkg"


def test_a_class_parameters_help_is_read_from_its_source_without_importing_it(source_package: str) -> None:
    assert source_param_docs("notes_pkg.direct.Builder") == {"size": "How many things.", "label": "Their label."}
    assert source_param_docs("notes_pkg.direct.Builder()") == source_param_docs("notes_pkg.direct.Builder")
    assert source_param_docs("notes_pkg.direct.Initialised") == {"depth": "How deep."}
    assert source_param_docs("notes_pkg.direct.make") == {"width": "How wide."}
    assert "notes_pkg.direct" not in sys.modules


def test_a_name_a_package_re_exports_is_followed_to_its_definition_without_importing_it(source_package: str) -> None:
    assert source_param_docs("notes_pkg.impl.Builder") == {"size": "How many things.", "label": "Their label."}
    assert source_param_docs("notes_pkg.impl.build") == {"width": "How wide."}
    assert "notes_pkg.impl" not in sys.modules and "notes_pkg.impl.builders" not in sys.modules


@pytest.mark.parametrize(
    "target",
    [
        "Builder",  # a registry name: no module to read
        "notes_pkg.direct.Missing",
        "notes_pkg.missing.Builder",
        "notes_pkg.impl.Missing",
        "notes_pkg.impl.builders.Builder",  # its package raises when imported
        "notes_pkg.impl.join",  # re-exported from outside the package: os.path is not a .py file of its own here
    ],
)
def test_a_target_whose_source_cannot_be_found_has_no_help(source_package: str, target: str) -> None:
    assert source_param_docs(target) == {}


def test_a_classs_parameters_are_read_from_its_source_with_their_literal_defaults(source_package: str) -> None:
    assert source_params("notes_pkg.direct.Typed") == (
        SourceParam("p"),
        SourceParam("a", "int", "500"),
        SourceParam("flag", "bool", "False", switch=True),
        SourceParam("name", "Optional[str]"),  # a None default shows none
        SourceParam("on", "", "True", switch=True),
        SourceParam("made"),  # not a literal: no default to show
        SourceParam("switch", "Optional[bool]", switch=True),
    )
    assert source_params("notes_pkg.impl.Builder") == (SourceParam("size", "", "1"), SourceParam("label", "", ""))
    assert source_params("notes_pkg.direct.make") == (SourceParam("width", "", "1"),)
    assert "notes_pkg.direct" not in sys.modules


@pytest.mark.parametrize("target", ["Builder", "notes_pkg.direct.Missing", "notes_pkg.impl.builders.Builder"])
def test_a_target_whose_source_cannot_be_found_has_no_parameters(source_package: str, target: str) -> None:
    assert source_params(target) == ()


def test_a_class_without_an_init_of_its_own_has_no_parameters(source_package: str) -> None:
    assert source_params("notes_pkg.direct.Plain") == ()
