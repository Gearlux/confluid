"""Tests for tag-driven scope resolution (``confluid/scopes.py``).

Scope wrappers live at an arbitrary key whose VALUE carries a ``!scope:`` /
``!notscope:`` tag. The key is YAML scaffolding (top-level mappings can't
hold bare tags); the resolver walks values, finds ``ScopeBlock`` sentinels,
and splices their contents in place at the wrapper's slot when active.

Covers both YAML tag forms (``!scope:KEY=VAL`` and ``!scope:KEY(VAL)``),
boolean and keyed activation, negation with the unset-⇒-active convention,
aliases, hierarchies, nested scopes, recursive includes, and the standard
load → dump → load round-trip.
"""

from pathlib import Path
from typing import Any, Dict, cast

import pytest

import confluid
from confluid import configurable, discover_dimensions, get_registry, load, load_config
from confluid.fluid import ScopeBlock
from confluid.scopes import normalize_active, parse_scope_arg, resolve_scopes


@pytest.fixture(autouse=True)
def setup_registry() -> None:
    get_registry().clear()


# ---------------------------------------------------------------------------
# Tag parsing — both YAML forms produce equivalent ScopeBlock sentinels.
# ---------------------------------------------------------------------------


def test_tag_form_equality(tmp_path: Path) -> None:
    """``!scope:task=classification`` and ``!scope:task(classification)`` parse identically."""
    assign_path = tmp_path / "assign.yaml"
    paren_path = tmp_path / "paren.yaml"

    assign_path.write_text(
        """
val: 1
if_task: !scope:task=classification
  model: ClassifierModel
"""
    )
    paren_path.write_text(
        """
val: 1
if_task: !scope:task(classification)
  model: ClassifierModel
"""
    )

    raw_assign = load_config(assign_path)
    raw_paren = load_config(paren_path)

    sb_a = raw_assign["if_task"]
    sb_p = raw_paren["if_task"]
    assert isinstance(sb_a, ScopeBlock) and isinstance(sb_p, ScopeBlock)
    assert (sb_a.key, sb_a.value, sb_a.negate) == ("task", "classification", False)
    assert (sb_p.key, sb_p.value, sb_p.negate) == ("task", "classification", False)

    out_a = load(raw_assign, flow=False, scopes=["task=classification"])
    out_p = load(raw_paren, flow=False, scopes=["task=classification"])
    assert out_a == out_p == {"val": 1, "model": "ClassifierModel"}


def test_boolean_tag_no_value() -> None:
    yaml_text = """
base: 1
if_debug: !scope:debug
  v: 2
"""
    out = load(yaml_text, flow=False, scopes=["debug"])
    assert out == {"base": 1, "v": 2}


def test_parse_scope_arg() -> None:
    assert parse_scope_arg("debug") == ("debug", None)
    assert parse_scope_arg("task=classification") == ("task", "classification")
    assert parse_scope_arg("  task = classification  ") == ("task", "classification")


# ---------------------------------------------------------------------------
# Basic active / inactive splice behaviour.
# ---------------------------------------------------------------------------


def test_inactive_scope_dropped() -> None:
    """When no `--scope` is passed, every positive scope block is dropped."""
    yaml_text = """
val: 1
if_debug: !scope:debug
  val: 2
"""
    out = load(yaml_text, flow=False)
    assert out == {"val": 1}


def test_inactive_keyed_scope_dropped() -> None:
    yaml_text = """
model: base
if_cls: !scope:task=classification
  model: classification
if_seg: !scope:task=segmentation
  model: segmentation
"""
    # No --task passed → both keyed blocks drop, base value survives.
    assert load(yaml_text, flow=False) == {"model": "base"}


def test_keyed_scope_selects_correct_block() -> None:
    yaml_text = """
model: base
if_cls: !scope:task=classification
  model: classifier
if_seg: !scope:task=segmentation
  model: segmenter
"""
    assert load(yaml_text, flow=False, scopes=["task=classification"])["model"] == "classifier"
    assert load(yaml_text, flow=False, scopes=["task=segmentation"])["model"] == "segmenter"


def test_splice_preserves_position() -> None:
    """The unwrapped scope's contents replace the wrapper at its slot."""
    yaml_text = """
a: 1
if_debug: !scope:debug
  b: 2
c: 3
"""
    out = load(yaml_text, flow=False, scopes=["debug"])
    assert list(out.items()) == [("a", 1), ("b", 2), ("c", 3)]


def test_splice_collision_keeps_original_position() -> None:
    """When the unwrapped value's key already exists, the value wins but slot stays."""
    yaml_text = """
val: 1
if_debug: !scope:debug
  val: 10
"""
    out = load(yaml_text, flow=False, scopes=["debug"])
    assert out == {"val": 10}
    assert list(out.keys()) == ["val"]


# ---------------------------------------------------------------------------
# Negation — unset-⇒-active convention.
# ---------------------------------------------------------------------------


def test_notscope_boolean_active_when_unset() -> None:
    yaml_text = """
lr: 0.001
unless_debug: !notscope:debug
  lr: 0.0001
"""
    out = load(yaml_text, flow=False)
    assert out == {"lr": 0.0001}


def test_notscope_boolean_dropped_when_active() -> None:
    yaml_text = """
lr: 0.001
unless_debug: !notscope:debug
  lr: 0.0001
if_debug: !scope:debug
  lr: 0.1
"""
    out = load(yaml_text, flow=False, scopes=["debug"])
    assert out == {"lr": 0.1}


def test_notscope_keyed_active_when_key_unset() -> None:
    """`!notscope:task=segmentation` is active when no --task is supplied."""
    yaml_text = """
postproc: base
unless_seg: !notscope:task=segmentation
  postproc: default
"""
    assert load(yaml_text, flow=False)["postproc"] == "default"


def test_notscope_keyed_active_when_value_differs() -> None:
    yaml_text = """
postproc: base
unless_seg: !notscope:task=segmentation
  postproc: default
"""
    # Different value → notscope fires.
    assert load(yaml_text, flow=False, scopes=["task=classification"])["postproc"] == "default"


def test_notscope_keyed_dropped_when_value_matches() -> None:
    yaml_text = """
postproc: base
unless_seg: !notscope:task=segmentation
  postproc: default
"""
    assert load(yaml_text, flow=False, scopes=["task=segmentation"])["postproc"] == "base"


# ---------------------------------------------------------------------------
# Aliases + hierarchies (boolean scopes only).
# ---------------------------------------------------------------------------


def test_alias_chain() -> None:
    yaml_text = """
scope_aliases:
  dev: [debug, local]
if_debug: !scope:debug
  lr: 0.1
if_local: !scope:local
  db: sqlite
"""
    out = load(yaml_text, flow=False, scopes=["dev"])
    assert out == {"lr": 0.1, "db": "sqlite"}


def test_alias_circular_raises() -> None:
    yaml_text = """
scope_aliases:
  a: b
  b: a
if_a: !scope:a
  v: 1
"""
    with pytest.raises(ValueError, match="Circular scope alias"):
        load(yaml_text, flow=False, scopes=["a"])


def test_hierarchical_boolean_scopes() -> None:
    """`prod.gpu` activates both `prod` and `prod.gpu` boolean scopes."""
    yaml_text = """
val: 1
if_prod: !scope:prod
  val: 100
if_prod_gpu: !scope:prod.gpu
  gpu: true
"""
    out = load(yaml_text, flow=False, scopes=["prod.gpu"])
    assert out == {"val": 100, "gpu": True}


def test_metadata_stripped() -> None:
    yaml_text = """
val: 1
scope_aliases:
  d: debug
scopes: [debug]
if_debug: !scope:debug
  val: 2
"""
    out = load(yaml_text, flow=False, scopes=["debug"])
    assert out == {"val": 2}
    assert "scope_aliases" not in out
    assert "scopes" not in out


# ---------------------------------------------------------------------------
# discover_dimensions — used by liquifai to wire --KEY VAL flags.
# ---------------------------------------------------------------------------


def test_discover_dimensions_top_level(tmp_path: Path) -> None:
    yaml_path = tmp_path / "c.yaml"
    yaml_path.write_text(
        """
if_debug: !scope:debug
  v: 1
if_task: !scope:task=classification
  m: a
if_env: !scope:env(prod)
  e: 1
"""
    )
    raw = load_config(yaml_path)
    assert discover_dimensions(raw) == {"task", "env"}


def test_discover_dimensions_nested(tmp_path: Path) -> None:
    """Scopes living inside sub-dicts and lists are still discovered."""
    yaml_path = tmp_path / "c.yaml"
    yaml_path.write_text(
        """
outer:
  if_size: !scope:size=large
    x: 1
list_section:
  - if_flavor: !scope:flavor=spicy
      y: 2
"""
    )
    raw = load_config(yaml_path)
    assert discover_dimensions(raw) == {"size", "flavor"}


# ---------------------------------------------------------------------------
# Nested scopes — inside sub-dicts and lists.
# ---------------------------------------------------------------------------


def test_nested_scope_inside_subdict() -> None:
    yaml_text = """
outer:
  base: 1
  if_debug: !scope:debug
    extra: 2
"""
    out = load(yaml_text, flow=False, scopes=["debug"])
    assert out == {"outer": {"base": 1, "extra": 2}}

    out_off = load(yaml_text, flow=False)
    assert out_off == {"outer": {"base": 1}}


def test_nested_scope_inside_list() -> None:
    yaml_text = """
items:
  - a
  - if_debug: !scope:debug
      keep: true
  - b
"""
    out = load(yaml_text, flow=False, scopes=["debug"])
    # Active scope → the wrapper key `if_debug` is replaced by its contents
    # at that slot inside the list-item dict.
    assert out == {"items": ["a", {"keep": True}, "b"]}

    out_off = load(yaml_text, flow=False)
    # Inactive scope under a list-item dict → the wrapper key is dropped,
    # leaving an empty dict.
    assert out_off == {"items": ["a", {}, "b"]}


# ---------------------------------------------------------------------------
# Recursive includes interact with scopes.
# ---------------------------------------------------------------------------


def test_recursive_includes_with_scopes(tmp_path: Path) -> None:
    ext = tmp_path / "ext.yaml"
    base = tmp_path / "base.yaml"
    ext.write_text(
        """
port: 1000
if_debug: !scope:debug
  port: 2000
unless_debug: !notscope:debug
  port: 3000
"""
    )
    base.write_text(
        """
include: ext.yaml
port: 80
"""
    )
    # debug active → ext's if_debug wins on port
    out_dbg = cast(Dict[str, Any], load(base, flow=False, scopes=["debug"]))
    assert out_dbg["port"] == 2000

    # debug inactive → ext's unless_debug fires
    out_off = cast(Dict[str, Any], load(base, flow=False))
    assert out_off["port"] == 3000


# ---------------------------------------------------------------------------
# Materialization — scopes interact with !class:/!ref: machinery.
# ---------------------------------------------------------------------------


def test_keyed_scope_replaces_an_entire_class() -> None:
    @configurable
    class SimpleModel:
        def __init__(self, layers: int = 3) -> None:
            self.layers = layers

    @configurable
    class ComplexModel:
        def __init__(self, layers: int = 10) -> None:
            self.layers = layers

    yaml_text = """
Trainer:
  model: !class:SimpleModel
if_heavy: !scope:variant=heavy
  Trainer.model: !class:ComplexModel
"""
    # Default (no scope active) keeps SimpleModel.
    base = cast(Dict[str, Any], load(yaml_text, flow=False))
    assert getattr(base["Trainer"]["model"], "target", None) == "SimpleModel"

    # `variant=heavy` swaps in ComplexModel via the dotted-key override.
    heavy = cast(Dict[str, Any], load(yaml_text, flow=False, scopes=["variant=heavy"]))
    assert getattr(heavy["Trainer"]["model"], "target", None) == "ComplexModel"


def test_round_trip_with_scopes(tmp_path: Path) -> None:
    """load → dump → load preserves the materialized graph after scope resolution."""

    @configurable
    class Knob:
        def __init__(self, n: int = 1) -> None:
            self.n = n

    yaml_path = tmp_path / "c.yaml"
    yaml_path.write_text(
        """
root: !class:Knob()
  n: 1
if_high: !scope:tier=high
  root: !class:Knob()
    n: 99
"""
    )

    inst = load(yaml_path, scopes=["tier=high"])
    knob = inst["root"] if isinstance(inst, dict) else inst
    # `!class:Foo()` with a mapping body parses to Instance — materialized eagerly.
    assert isinstance(knob, Knob)
    assert knob.n == 99

    dumped = confluid.dump(knob)
    reloaded = load(dumped)
    assert isinstance(reloaded, Knob)
    assert reloaded.n == 99


# ---------------------------------------------------------------------------
# normalize_active — small unit check on the helper liquifai calls indirectly.
# ---------------------------------------------------------------------------


def test_normalize_active_keyed_and_boolean() -> None:
    active = normalize_active(["debug", "task=classification"], aliases=None)
    assert active == {"debug": None, "task": "classification"}


def test_normalize_active_last_write_wins() -> None:
    active = normalize_active(["task=classification", "task=segmentation"], aliases=None)
    assert active == {"task": "segmentation"}


def test_normalize_active_alias_expansion() -> None:
    aliases = {"dev": ["debug", "local"]}
    active = normalize_active(["dev"], aliases=aliases)
    assert active == {"debug": None, "local": None}


def test_resolve_scopes_directly_on_dict() -> None:
    """resolve_scopes is callable with hand-built dicts that contain ScopeBlocks."""
    block = ScopeBlock(key="debug", value=None, negate=False, contents={"x": 2})
    config = {"x": 1, "_wrap": block}
    out = resolve_scopes(config, {"debug": None})
    assert out == {"x": 2}


def test_resolve_scopes_bare_top_level_block_active() -> None:
    """A ScopeBlock as the root value resolves to its contents when active."""
    block = ScopeBlock(key="debug", value=None, negate=False, contents={"x": 99})
    out = resolve_scopes(block, {"debug": None})
    assert out == {"x": 99}


def test_resolve_scopes_bare_top_level_block_inactive() -> None:
    """A ScopeBlock as the root value resolves to None when inactive."""
    block = ScopeBlock(key="debug", value=None, negate=False, contents={"x": 99})
    out = resolve_scopes(block, {})
    assert out is None


def test_scope_block_as_direct_list_item() -> None:
    """A ScopeBlock element in a list — splices its contents into the list when active."""
    block = ScopeBlock(key="debug", value=None, negate=False, contents={"x": 1})
    out = resolve_scopes([1, block, 2], {"debug": None})
    assert out == [1, {"x": 1}, 2]


def test_scope_block_in_list_dropped_when_inactive() -> None:
    block = ScopeBlock(key="debug", value=None, negate=False, contents={"x": 1})
    out = resolve_scopes([1, block, 2], {})
    assert out == [1, 2]


def test_scope_block_in_list_with_list_contents() -> None:
    block = ScopeBlock(key="debug", value=None, negate=False, contents=[1, 2])
    out = resolve_scopes(["a", block, "b"], {"debug": None})
    assert out == ["a", 1, 2, "b"]


def test_scope_block_in_list_with_scalar_contents() -> None:
    block = ScopeBlock(key="debug", value=None, negate=False, contents="literal")
    out = resolve_scopes(["a", block, "b"], {"debug": None})
    assert out == ["a", "literal", "b"]


def test_discover_dimensions_inside_fluid_kwargs(tmp_path: Path) -> None:
    """Scopes that nest inside a !class:Foo's kwargs are still discovered."""
    yaml_path = tmp_path / "c.yaml"
    yaml_path.write_text(
        """
root: !class:Knob
  inner:
    if_size: !scope:size=large
      x: 1
"""
    )
    raw = load_config(yaml_path)
    assert "size" in discover_dimensions(raw)


def test_repr_format() -> None:
    """ScopeBlock.__repr__ surfaces the tag form for diagnostics."""
    bk = ScopeBlock(key="debug", value=None, negate=False, contents={"x": 1})
    assert "!scope:debug" in repr(bk)
    kk = ScopeBlock(key="task", value="cls", negate=False, contents={"x": 1})
    assert "!scope:task=cls" in repr(kk)
    nk = ScopeBlock(key="debug", value=None, negate=True, contents={"x": 1})
    assert "!notscope:debug" in repr(nk)
