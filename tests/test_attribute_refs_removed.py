"""Attribute references are REMOVED (record 19, phase 2 — user ruling 1b).

A ``!ref:`` whose FIRST segment is a document key walks STRUCTURE only — dict keys and list
indices. Reading an ATTRIBUTE of the object built at that key (``!ref:split.train``) or calling
a METHOD on it (``!ref:obj.build()``) is refused with a located error naming the rewrite: give
the referent's class a selector parameter and reference the whole object, or write the marker
again with the selector set. The refusal fires on BOTH paths — ``load()`` and ``resolve()`` —
which is how ``hydraide`` reports it (exit 2, the same message).

Everything else a dotted ``!ref:`` did keeps working, and every one of those cases is pinned
below as a CON case: a whole-object ref, a walk through a plain mapping / a list index, an
import-path ref (first segment NOT a document key — the spelling ``dump()`` emits for a
function-valued param), a document key literally named ``a.b``. (The CON refs sit inside a
marker's kwargs: a structural dotted ``!ref:`` at a TOP-LEVEL document key stays an unresolved
``Reference`` after ``load()`` today — measured, pre-existing, reported as F5 — and that
asymmetry is not this change's to alter.)
"""

import textwrap
from pathlib import Path
from typing import Any

import pytest
import yaml

from confluid import ConfigurationError, configurable, dump, load, resolve
from confluid.hydraide import emit


@configurable
class ASplit:
    """A referent whose views are PROPERTIES (the shape the workspace used) and a selector."""

    built = 0

    def __init__(self, source: Any = None, split: Any = None, val_fraction: float = 0.0, seed: int = 0) -> None:
        self.source, self.split, self.val_fraction, self.seed = source, split, val_fraction, seed
        ASplit.built += 1

    @property
    def train(self) -> str:
        return f"train-view-of-{id(self)}"

    @property
    def val(self) -> str:
        return f"val-view-of-{id(self)}"

    def build(self) -> str:
        return "built"


@configurable
class AIndexable:
    built = 0

    def __init__(self, root: str = "r") -> None:
        self.root = root
        AIndexable.built += 1


@configurable
class AStream:
    def __init__(self, source: Any = None, ops: Any = None) -> None:
        self.source, self.ops = source, ops


ATTRIBUTE_REF = "split: !class:ASplit\n  val_fraction: 0.2\nuse: !class:AStream\n  source: !ref:split.train\n"


# --------------------------------------------------------------------------- #
# PRO: the attribute ref is refused, located, on both paths — and by hydraide
# --------------------------------------------------------------------------- #


def test_an_attribute_ref_is_refused_by_load_naming_the_line_and_the_rewrite() -> None:
    with pytest.raises(ConfigurationError) as exc:
        load(ATTRIBUTE_REF)
    msg = str(exc.value)
    assert "split.train" in msg
    assert "ATTRIBUTE" in msg and "`train`" in msg and "`split`" in msg
    assert ":4:11" in msg, msg  # the !ref: node's own line:col
    assert "selector" in msg and "!ref:split" in msg  # the rewrite is named


def test_the_same_document_is_refused_by_resolve_with_the_same_message() -> None:
    """``resolve()`` is passes 1–7 — hydraide. Refusing there is how the tool REPORTS the ref."""
    with pytest.raises(ConfigurationError) as via_load:
        load(ATTRIBUTE_REF)
    with pytest.raises(ConfigurationError) as via_resolve:
        resolve(ATTRIBUTE_REF)
    assert str(via_resolve.value) == str(via_load.value)


def test_hydraide_emit_reports_it_as_the_same_located_error(tmp_path: Path) -> None:
    """`emit` IS `resolve()` + the serializer, so the tool refuses exactly as `load()` does — the
    file:line:col of the `!ref:` node in the message (the CLI framework renders it, exit 1)."""
    (tmp_path / "cfg.yaml").write_text(ATTRIBUTE_REF)
    with pytest.raises(ConfigurationError) as exc:
        emit(tmp_path / "cfg.yaml")
    assert "cfg.yaml:4:11" in str(exc.value) and "ATTRIBUTE" in str(exc.value)


def test_the_string_spelling_is_refused_too() -> None:
    """``${ref:split.train}`` parses to the same Reference marker (no location to name — a
    scalar carries none; the message still names the path and the rewrite)."""
    with pytest.raises(ConfigurationError) as exc:
        load("split: !class:ASplit\nuse: !class:AStream\n  source: '${ref:split.train}'\n")
    assert "split.train" in str(exc.value) and "ATTRIBUTE" in str(exc.value)


def test_a_method_call_ref_is_refused() -> None:
    with pytest.raises(ConfigurationError) as exc:
        load("split: !class:ASplit\nuse: !ref:split.build()\n")
    msg = str(exc.value)
    assert "METHOD" in msg and "`build`" in msg and ":2:6" in msg


def test_a_deeper_attribute_step_is_refused_at_the_marker() -> None:
    """The first segment is a document key; the walk leaves structure at the marker `inner`."""
    with pytest.raises(ConfigurationError) as exc:
        load("outer:\n  inner: !class:ASplit\nuse: !ref:outer.inner.train\n")
    assert "outer.inner.train" in str(exc.value) and "`train`" in str(exc.value)


# --------------------------------------------------------------------------- #
# The rewrite for the one shape the workspace used — measured, no engine change
# --------------------------------------------------------------------------- #

AFTER = textwrap.dedent(
    """\
    indexable: !class:AIndexable
    split_recipe: &split_recipe
      source: !ref:indexable
      val_fraction: 0.2
      seed: 42
    train_set: !class:AStream
      source: !class:ASplit
        <<: *split_recipe
        split: train
    val_set: !class:AStream
      source: !class:ASplit
        <<: *split_recipe
        split: val
    """
)


def test_the_rewrite_two_selector_markers_share_one_source() -> None:
    AIndexable.built = 0
    g = load(AFTER)
    t, v = g["train_set"].source, g["val_set"].source
    assert (t.split, t.val_fraction, t.seed) == ("train", 0.2, 42)
    assert (v.split, v.val_fraction, v.seed) == ("val", 0.2, 42)
    assert t.source is v.source is g["indexable"], "the store is still ONE instance, via the whole-object ref"
    assert AIndexable.built == 1


def test_the_rewrite_emits_an_anchored_plain_document() -> None:
    out = emit(AFTER)
    doc = yaml.safe_load(out)
    assert "&indexable" in out and out.count("*indexable") == 3  # recipe + two splits
    assert doc["train_set"]["source"] == {
        "_target_": "ASplit",
        "source": doc["indexable"],
        "val_fraction": 0.2,
        "seed": 42,
        "split": "train",
    }
    assert doc["val_set"]["source"]["split"] == "val"
    assert "_ref_" not in out


# --------------------------------------------------------------------------- #
# CON: what a dotted !ref: still does — every row pinned
# --------------------------------------------------------------------------- #


def test_a_whole_object_ref_is_unchanged() -> None:
    g = load("s: !class:ASplit\na: !ref:s\nb: !ref:s\n")
    assert g["a"] is g["b"] is g["s"]


def test_a_walk_through_a_plain_mapping_is_unchanged() -> None:
    assert load("cfg:\n  lr: 0.1\nuse: !class:AStream\n  ops: !ref:cfg.lr\n")["use"].ops == 0.1


def test_a_list_index_walk_is_unchanged() -> None:
    g = load("packs:\n  - {name: a}\n  - {name: b}\nuse: !class:AStream\n  ops: !ref:packs[1].name\n")
    assert g["use"].ops == "b"


def test_a_walk_INTO_a_marker_that_is_a_document_key_but_ends_on_a_kwarg_is_still_refused() -> None:
    """A marker is not a mapping: `!ref:model.hidden` never walked kwargs (census: 0 uses) and it
    is not silently invented now — the walk leaves structure at the marker, so it is refused."""
    with pytest.raises(ConfigurationError):
        load("model: !class:ASplit\n  val_fraction: 0.5\nuse: !ref:model.val_fraction\n")


def test_an_import_path_ref_is_unchanged_the_first_segment_is_not_a_document_key() -> None:
    g = load("use: !class:AStream\n  ops: !ref:posixpath.join\n")
    import posixpath

    assert g["use"].ops is posixpath.join


def test_dump_still_emits_a_function_as_an_import_path_ref_that_reloads() -> None:
    import posixpath

    text = dump({"use": AStream(ops=posixpath.join)})
    assert "${ref:posixpath.join}" in text
    assert load(text)["use"].ops is posixpath.join


def test_a_document_key_literally_named_a_dot_b_still_wins() -> None:
    assert load("'a.b': 7\nuse: !class:AStream\n  ops: !ref:a.b\n")["use"].ops == 7


def test_a_purely_structural_path_still_stays_late_bound_in_the_rich_resolver() -> None:
    """A dict/list-only path returns None from ``resolve_reference_path`` — the deferred-Reference
    machinery owns it (late-binding for post-load overrides). Moved here from the deleted
    ``test_method_ref.py``; the property predates phase 2 and survives it."""
    from confluid.resolver import resolve_reference_path

    ctx = {"labels": ["a", "b"], "idx": 1, "nested": {"x": 5}}
    assert resolve_reference_path("labels[idx]", ctx) is None
    assert resolve_reference_path("nested.x", ctx) is None


def test_the_deleted_object_policy_is_gone() -> None:
    """No second policy, no cursor materializer, no method-call grammar — the walker is structural."""
    import inspect

    from confluid import resolver

    assert not hasattr(resolver, "_materialize_cursor")  # the resolver → engine lazy seam is gone with it
    assert not hasattr(resolver, "_resolve_base_path")
    assert "getattr_fallback" not in inspect.signature(resolver._walk_path_segments).parameters
    # The `()` grammar survives ONLY as the pattern the refusal matches to NAME the method.
    assert resolver._CALL_SUFFIX_RE.match("obj.build()") is not None


# --------------------------------------------------------------------------- #
# The property the ruling rests on: hydraide's output parses with OmegaConf/Hydra
# --------------------------------------------------------------------------- #


def test_the_emitted_document_parses_with_omegaconf() -> None:
    """The plain form is what Hydra READS: anchors, `_target_`, `_partial_`, `${ref:...}`.
    (Whether Hydra can INSTANTIATE it is not the contract — parseability is.)"""
    omegaconf = pytest.importorskip("omegaconf")
    text = emit(AFTER + "opt: !partial:ASplit\nfn: !ref:posixpath.join\n")
    cfg = omegaconf.OmegaConf.create(text)
    plain = omegaconf.OmegaConf.to_container(cfg, resolve=False)
    assert plain["train_set"]["source"]["_target_"] == "ASplit"
    assert plain["opt"] == {"_target_": "ASplit", "_partial_": True}
