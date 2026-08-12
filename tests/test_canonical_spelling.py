"""The examples and guides teach the CANONICAL spelling, not the deprecated one.

Every guide has a runnable companion in ``examples/``, so those two surfaces are
the first confluid a reader writes by hand. Until 2026-08-12 twelve of the
twenty-four examples still used the YAML TAG spelling — including
``lifecycle.py``, which the README designates "start here", and
``deep_injection.py``, the flagship pitch — so following the documentation path
emitted a ``FutureWarning`` telling the reader their config was obsolete. The
examples ran and CI was green: nothing checked *which spelling* they taught.

Two scans, deliberately different in strictness, because the two surfaces rot
differently:

* **examples — no tag ANYWHERE**, config or prose. These files are copy-paste
  sources, and a docstring saying "the YAML declares it ``!lazy:``" beside a
  ``_partial_: true`` config warns nobody and misleads every reader. That exact
  pair is what this scan found in ``examples/ml_experiments/``, whose YAML had
  been migrated while its comments, its ``run.py`` docstrings and its README
  still described the tag form.
* **guides — no tag in a YAML CODE BLOCK.** Prose in ``docs/`` legitimately
  discusses the deprecated spelling (naming it is how a reader with old configs
  knows what to convert), so a blanket ban would need so many exemptions it
  would be noise. What must not drift is a guide showing a config its companion
  example no longer matches — and that lives in the fenced ``yaml`` blocks.

The scans are STATIC (source text) rather than a run of each example: a
subprocess sweep costs seconds per file, sees no prose at all, and would have
passed ``ml_pipeline.py``, which warned about nothing because it used the
QUOTED-STRING spelling (``"!class:Adam(lr=!ref:base_lr)"``).

Every allow-list entry carries its reason, and all of them disappear in 0.4.0
with the constructors themselves (see ``TASKS.md`` phase 5c); when they do,
delete the allow-lists rather than the tests.
"""

import re
from pathlib import Path
from typing import List

import pytest

_REPO = Path(__file__).resolve().parent.parent
_EXAMPLES = _REPO / "examples"
_DOCS = _REPO / "docs"

#: Every tag the loader registers a constructor for. Matched as a whole word so
#: ``!class:`` inside a longer token is still found and a bare ``!`` (a YAML
#: negation, a shell bang) is not.
_TAG = re.compile(r"!(?:class|lazy|ref|clone|scope|notscope)\b")

#: example -> why it may keep the tag spelling. A DOCUMENTED exception, never a
#: grandfathered one: each of these teaches the tag form itself, so removing the
#: tags would remove the subject.
_ALLOWED_EXAMPLES = {
    "plain_format.py": "demonstrates that the two spellings produce identical markers",
    "tags_deferred.py": "documents the legacy tag spelling itself (docs/targets.md)",
}

#: guide -> why its YAML blocks may carry tags.
_ALLOWED_DOCS = {
    "targets.md": "the marker-family reference — documents the legacy tag spelling it converts from",
    "architecture.md": "decision records quoting the configs that motivated them, as they were written",
}


def _example_sources() -> List[Path]:
    """Every example file a reader can copy from — scripts, configs, and READMEs."""
    return sorted(
        p
        for pattern in ("*.py", "*/*.py", "*/*.yaml", "*/*.md")
        for p in _EXAMPLES.glob(pattern)
        if p.name != "__init__.py"
    )


def _yaml_block_lines(path: Path) -> List[str]:
    """Lines inside fenced ``yaml`` code blocks, as ``"<line-no>: <text>"``.

    Only ```` ```yaml ```` fences count. A ```` ```python ```` block quoting a
    marker string, or prose naming a tag, is the reader being TOLD about the old
    spelling rather than being taught to write it.
    """
    out: List[str] = []
    inside, lang = False, ""
    for n, line in enumerate(path.read_text().splitlines(), 1):
        stripped = line.lstrip()
        if stripped.startswith("```"):
            inside, lang = (True, stripped[3:].strip()) if not inside else (False, "")
            continue
        if inside and lang in ("yaml", "yml") and _TAG.search(line):
            out.append(f"{path.name}:{n}: {line.strip()}")
    return out


@pytest.mark.parametrize("path", _example_sources(), ids=lambda p: str(p.relative_to(_EXAMPLES)))
def test_examples_use_the_reserved_key_spelling(path: Path) -> None:
    """No example names a tag — in config OR in prose."""
    if path.name in _ALLOWED_EXAMPLES:
        pytest.skip(f"{path.name}: {_ALLOWED_EXAMPLES[path.name]}")

    hits = [
        f"{path.name}:{n}: {line.strip()}"
        for n, line in enumerate(path.read_text().splitlines(), 1)
        if _TAG.search(line)
    ]

    assert not hits, (
        "the tag spelling is deprecated (removed in confluid 0.4.0) and must not appear in an example — "
        "convert configs with `confluid-migrate` and rewrite prose to name the reserved key "
        "(_target_ / _partial_ / ${ref:} / _scope_):\n  " + "\n  ".join(hits)
    )


@pytest.mark.parametrize("path", sorted(_DOCS.glob("*.md")), ids=lambda p: p.name)
def test_guide_yaml_blocks_use_the_reserved_key_spelling(path: Path) -> None:
    """A guide's YAML samples must match the spelling its companion example teaches.

    Converting the examples on 2026-08-12 left 69 tag lines across nine guides
    showing configs their own companion no longer matched — `docs/broadcasting.md`
    displayed `sink: !class:Passthrough(tag=addressed)` while
    `examples/broadcasting.py` had moved to the reserved keys. The pairing is the
    documentation's proof; when the two disagree, one of them is lying.
    """
    if path.name in _ALLOWED_DOCS:
        pytest.skip(f"{path.name}: {_ALLOWED_DOCS[path.name]}")

    hits = _yaml_block_lines(path)

    assert not hits, (
        "a fenced yaml block in a guide must use the reserved-key spelling, so it agrees with "
        "the guide's runnable companion in examples/:\n  " + "\n  ".join(hits)
    )


def test_the_allow_lists_name_files_that_exist() -> None:
    """An entry for a deleted file silently exempts nothing and hides rot."""
    missing = [n for n in _ALLOWED_EXAMPLES if not (_EXAMPLES / n).exists()]
    missing += [n for n in _ALLOWED_DOCS if not (_DOCS / n).exists()]

    assert not missing, f"the allow-lists name files that no longer exist: {missing}"


def test_the_allowed_files_really_do_carry_tags() -> None:
    """The inverse pin: an exemption that is no longer needed must be DELETED.

    Without this the allow-lists only ever grow — a file converted later keeps its
    entry, and the next file to acquire a stray tag can be waved through by adding
    a name to a list that already looks like it has ceremonial entries.
    """
    unnecessary = [n for n in _ALLOWED_EXAMPLES if not _TAG.search((_EXAMPLES / n).read_text())]
    unnecessary += [n for n in _ALLOWED_DOCS if not _yaml_block_lines(_DOCS / n)]

    assert not unnecessary, f"these files no longer need their exemption — remove them: {unnecessary}"
