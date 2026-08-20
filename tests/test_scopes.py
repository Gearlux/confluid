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
from confluid import configurable, discover_dimension_values, discover_dimensions, get_registry, load
from confluid.exceptions import ScopeError
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

    raw_assign = load(assign_path, until="raw")
    raw_paren = load(paren_path, until="raw")

    sb_a = raw_assign["if_task"]
    sb_p = raw_paren["if_task"]
    assert isinstance(sb_a, ScopeBlock) and isinstance(sb_p, ScopeBlock)
    assert (sb_a.dims, sb_a.negate) == ({"task": "classification"}, False)
    assert (sb_p.dims, sb_p.negate) == ({"task": "classification"}, False)

    out_a = load(raw_assign, until="document", scopes=["task=classification"])
    out_p = load(raw_paren, until="document", scopes=["task=classification"])
    assert out_a == out_p == {"val": 1, "model": "ClassifierModel"}


def test_boolean_tag_no_value() -> None:
    yaml_text = """
base: 1
if_debug: !scope:debug
  v: 2
"""
    out = load(yaml_text, until="document", scopes=["debug"])
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
    out = load(yaml_text, until="document")
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
    assert load(yaml_text, until="document") == {"model": "base"}


def test_keyed_scope_selects_correct_block() -> None:
    yaml_text = """
model: base
if_cls: !scope:task=classification
  model: classifier
if_seg: !scope:task=segmentation
  model: segmenter
"""
    assert load(yaml_text, until="document", scopes=["task=classification"])["model"] == "classifier"
    assert load(yaml_text, until="document", scopes=["task=segmentation"])["model"] == "segmenter"


def test_splice_preserves_position() -> None:
    """The unwrapped scope's contents replace the wrapper at its slot."""
    yaml_text = """
a: 1
if_debug: !scope:debug
  b: 2
c: 3
"""
    out = load(yaml_text, until="document", scopes=["debug"])
    assert list(out.items()) == [("a", 1), ("b", 2), ("c", 3)]


def test_splice_collision_last_value_wins() -> None:
    """When the unwrapped value's key already exists, the block's value wins."""
    yaml_text = """
val: 1
if_debug: !scope:debug
  val: 10
"""
    out = load(yaml_text, until="document", scopes=["debug"])
    assert out == {"val": 10}
    assert list(out.keys()) == ["val"]


# ---------------------------------------------------------------------------
# The splice follows the include-paste rule (BUGS-2026-08-19 SR1 / SR2 / PA2 /
# PA3): a block's keys land at the WRAPPER's slot through the same per-key merge
# `deep_merge` applies to an included file — a mapping over a marker TUNES it, a
# nested block deep-merges, a restated key is RE-ANCHORED at the later position
# — and so does a plain key written after a block. Plain assignment did none of
# that: an active block deleted a marker, a nested block lost its other keys,
# and a spliced key kept the EARLIER writer's position, so whether a later bare
# key won flipped with the activation.
# ---------------------------------------------------------------------------


@configurable
class _Model:
    def __init__(self, name: str = "a", depth: int = 1) -> None:
        self.name = name
        self.depth = depth


@configurable
class _Holder:
    def __init__(self, model: Any = None) -> None:
        self.model = model


@configurable
class _Stage:
    def __init__(self, lr: float = 0.0, epochs: int = 1) -> None:
        self.lr = lr
        self.epochs = epochs


def _register_splice_fixtures() -> None:
    # the autouse fixture clears the registry before every test
    for cls in (_Model, _Holder, _Stage):
        confluid.register(cls)


def test_a_block_mapping_at_a_marker_slot_TUNES_the_marker() -> None:
    """SR1 — the activated mapping lands on the marker's kwargs; the marker is kept."""
    _register_splice_fixtures()
    text = """
runnable: !class:_Holder
  model: !class:_Model {name: a, depth: 3}
  alt: !scope:big
    model: {name: b}
"""
    inactive = load(text)["runnable"].model
    assert (inactive.name, inactive.depth) == ("a", 3)
    active = load(text, scopes=["big"])["runnable"].model
    assert isinstance(active, _Model), f"the marker was replaced by {active!r}"
    assert (active.name, active.depth) == ("b", 3)


def test_a_block_mapping_at_a_nested_block_deep_merges() -> None:
    """SR1 — the other keys of the nested block survive the activated override."""
    text = "Trainer: {lr: 0.1, epochs: 5}\nif_x: !scope:x\n  Trainer: {lr: 0.9}\n"
    assert load(text, scopes=["x"], until="document") == {"Trainer": {"epochs": 5, "lr": 0.9}}


def test_a_block_re_stating_a_SCALAR_still_replaces_it() -> None:
    """The con case: a scalar has nothing to merge — last value wins, as before."""
    assert load("lr: 0.1\nif_x: !scope:x\n  lr: 0.9\n", scopes=["x"], until="document") == {"lr": 0.9}


def test_a_spliced_key_lands_at_the_WRAPPERS_position() -> None:
    """SR2 — the block's value is spliced at the wrapper's slot, so it beats a
    bare key written BETWEEN the first writer and the wrapper (the same document
    written flat, or through an include, already answered 0.5)."""
    _register_splice_fixtures()
    text = """
_Stage: {lr: 0.2}
lr: 0.1
if_x: !scope:x
  _Stage: {lr: 0.5}
s: !class:_Stage
"""
    doc = load(text, scopes=["x"], until="document")
    assert list(doc) == ["lr", "_Stage", "s"], f"spliced key kept the earlier position: {list(doc)}"
    assert load(text, scopes=["x"])["s"].lr == 0.5
    assert load("lr: 0.1\n_Stage: {lr: 0.5}\ns: !class:_Stage\n")["s"].lr == 0.5  # the flat spelling agrees


def test_a_plain_key_written_AFTER_a_block_lands_at_its_own_position() -> None:
    """PA3 — activating a block that only supplies a default must not flip which
    of two LATER lines wins: `lr: 2` is written after `_Stage: {lr: 7}` either way."""
    _register_splice_fixtures()
    text = """
defaults: !scope:x
  lr: 1
_Stage: {lr: 7}
lr: 2
s: !class:_Stage
"""
    assert load(text)["s"].lr == 2
    assert load(text, scopes=["x"])["s"].lr == 2


def test_a_plain_key_written_AFTER_a_block_merges_with_what_the_block_spliced() -> None:
    """The later plain key is the overlay: a nested block deep-merges (its own
    keys win), a marker written later REPLACES a block-spliced mapping (a marker
    is a new node — the same rule `deep_merge` applies to an included file)."""
    merged = load("if_x: !scope:x\n  _Stage: {lr: 0.9, epochs: 3}\n_Stage: {lr: 0.1}\n", scopes=["x"], until="document")
    assert merged == {"_Stage": {"epochs": 3, "lr": 0.1}}
    replaced = load("if_x: !scope:x\n  s: {lr: 0.9}\ns: !class:_Stage {epochs: 2}\n", scopes=["x"], until="document")
    assert replaced["s"].kwargs == {"epochs": 2}


def test_two_active_blocks_on_one_key_the_second_tunes_the_firsts_marker() -> None:
    _register_splice_fixtures()
    text = "a: !scope:x\n  s: !class:_Stage {lr: 0.9}\nb: !scope:y\n  s: {epochs: 4}\n"
    s = load(text, scopes=["x", "y"])["s"]
    assert (s.lr, s.epochs) == (0.9, 4)


def test_two_active_blocks_each_carrying_an_include_read_BOTH_files(tmp_path: Path) -> None:
    """PA2 — the second block's `include:` used to overwrite the first's, so one
    file was never read (the P11 loss, through scopes). Colliding includes
    combine into a list; `include:` already takes one."""
    (tmp_path / "a.yaml").write_text("from_a: 1\nlr: 0.1\n")
    (tmp_path / "b.yaml").write_text("from_b: 2\n")
    main = tmp_path / "main.yaml"
    main.write_text("a_blk: !scope:x\n  include: a.yaml\nb_blk: !scope:y\n  include: b.yaml\n")
    result, paths = load(str(main), scopes=["x", "y"], until="document", return_paths=True)
    assert result == {"from_a": 1, "lr": 0.1, "from_b": 2}
    assert [p.name for p in paths] == ["main.yaml", "a.yaml", "b.yaml"]


# ---------------------------------------------------------------------------
# Negation — unset-⇒-active convention.
# ---------------------------------------------------------------------------


def test_notscope_boolean_active_when_unset() -> None:
    yaml_text = """
lr: 0.001
unless_debug: !notscope:debug
  lr: 0.0001
"""
    out = load(yaml_text, until="document")
    assert out == {"lr": 0.0001}


def test_notscope_boolean_dropped_when_active() -> None:
    yaml_text = """
lr: 0.001
unless_debug: !notscope:debug
  lr: 0.0001
if_debug: !scope:debug
  lr: 0.1
"""
    out = load(yaml_text, until="document", scopes=["debug"])
    assert out == {"lr": 0.1}


def test_notscope_keyed_active_when_key_unset() -> None:
    """`!notscope:task=segmentation` is active when no --task is supplied."""
    yaml_text = """
postproc: base
unless_seg: !notscope:task=segmentation
  postproc: default
"""
    assert load(yaml_text, until="document")["postproc"] == "default"


def test_notscope_keyed_active_when_value_differs() -> None:
    yaml_text = """
postproc: base
unless_seg: !notscope:task=segmentation
  postproc: default
"""
    # Different value → notscope fires.
    assert load(yaml_text, until="document", scopes=["task=classification"])["postproc"] == "default"


def test_notscope_keyed_dropped_when_value_matches() -> None:
    yaml_text = """
postproc: base
unless_seg: !notscope:task=segmentation
  postproc: default
"""
    assert load(yaml_text, until="document", scopes=["task=segmentation"])["postproc"] == "base"


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
    out = load(yaml_text, until="document", scopes=["dev"])
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
        load(yaml_text, until="document", scopes=["a"])


def test_hierarchical_boolean_scopes() -> None:
    """`prod.gpu` activates both `prod` and `prod.gpu` boolean scopes."""
    yaml_text = """
val: 1
if_prod: !scope:prod
  val: 100
if_prod_gpu: !scope:prod.gpu
  gpu: true
"""
    out = load(yaml_text, until="document", scopes=["prod.gpu"])
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
    out = load(yaml_text, until="document", scopes=["debug"])
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
    raw = load(yaml_path, until="raw")
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
    raw = load(yaml_path, until="raw")
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
    out = load(yaml_text, until="document", scopes=["debug"])
    assert out == {"outer": {"base": 1, "extra": 2}}

    out_off = load(yaml_text, until="document")
    assert out_off == {"outer": {"base": 1}}


def test_nested_scope_inside_list() -> None:
    yaml_text = """
items:
  - a
  - if_debug: !scope:debug
      keep: true
  - b
"""
    out = load(yaml_text, until="document", scopes=["debug"])
    # Active scope → the wrapper key `if_debug` is replaced by its contents
    # at that slot inside the list-item dict.
    assert out == {"items": ["a", {"keep": True}, "b"]}

    out_off = load(yaml_text, until="document")
    # Inactive scope under a list-item dict → the wrapper key is dropped,
    # leaving an empty dict.
    assert out_off == {"items": ["a", {}, "b"]}


# ---------------------------------------------------------------------------
# Recursive includes interact with scopes.
# ---------------------------------------------------------------------------


def test_an_included_scope_block_supplies_a_value_the_includer_did_not_set(tmp_path: Path) -> None:
    """A scope block in an included file splices at ITS position and wins by default."""
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
    base.write_text("include: ext.yaml\nhost: localhost\n")

    assert cast(Dict[str, Any], load(base, until="document", scopes=["debug"]))["port"] == 2000
    assert cast(Dict[str, Any], load(base, until="document"))["port"] == 3000


def test_the_includer_overrides_an_included_scope_block(tmp_path: Path) -> None:
    """The including file is read AFTER the file it includes — so its value wins.

    Until 2026-08-11 it did not: ``deep_merge`` kept the BASE's position for a
    key the includer re-stated, so ``port: 80`` was judged at the included
    file's position 0 — ahead of the ``!scope:`` block that spliced at position
    1 — and the include won, silently, against confluid's own "last spec wins"
    rule. The identical document written flat gave the opposite answer. See
    ``merger.deep_merge`` and ``docs/interpolation.md`` → "Includes and document
    order".

    The ``include:`` sits on line 1 here, so the paste lands first and everything
    written below it is later — see ``tests/test_includes.py`` for the positional
    rule itself (the directive's own position is meaningful since 2026-08-11).
    """
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
    base.write_text("include: ext.yaml\nport: 80\n")

    assert cast(Dict[str, Any], load(base, until="document", scopes=["debug"]))["port"] == 80
    assert cast(Dict[str, Any], load(base, until="document"))["port"] == 80


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
    base = cast(Dict[str, Any], load(yaml_text, until="document"))
    assert getattr(base["Trainer"]["model"], "target", None) == "SimpleModel"

    # `variant=heavy` swaps in ComplexModel via the dotted-key override.
    heavy = cast(Dict[str, Any], load(yaml_text, until="document", scopes=["variant=heavy"]))
    assert getattr(heavy["Trainer"]["model"], "target", None) == "ComplexModel"


def test_keyed_scope_inside_a_markers_own_kwargs_swaps_the_slot() -> None:
    """A `!scope:` sibling INSIDE a `!class:` block splices into that marker's kwargs.

    The natural way to offer a per-slot alternative is to write it next to the
    slot it replaces, inside the parent marker rather than at the document root::

        runnable: !class:Trainer
          model: !class:SimpleModel
          alt: !scope:model=complex
            model: !class:ComplexModel

    A marker's kwargs are a mapping like any other, so the wrapper must resolve
    there exactly as it does in a plain dict — document order, last write wins.
    Regression: `resolve_scopes` walked dicts and lists but NOT `Fluid.kwargs`,
    so the nested wrapper survived untouched (and was passed to the constructor
    as a stray `alt=` kwarg) while `discover_dimensions` — which has always
    walked `Fluid.kwargs` — still advertised `model` as a dimension. A CLI
    therefore accepted `--model complex` and silently dropped it.
    """

    @configurable
    class SimpleModel:
        def __init__(self, layers: int = 3) -> None:
            self.layers = layers

    @configurable
    class ComplexModel:
        def __init__(self, layers: int = 10) -> None:
            self.layers = layers

    @configurable
    class Trainer:
        def __init__(self, model: Any = None) -> None:
            self.model = model

    yaml_text = """
runnable: !class:Trainer
  model: !class:SimpleModel()
  alt: !scope:model=complex
    model: !class:ComplexModel()
"""
    base = cast(Dict[str, Any], load(yaml_text, until="document"))
    assert getattr(base["runnable"].kwargs["model"], "target", None) == "SimpleModel"
    # The inactive wrapper is dropped outright — it never reaches the constructor.
    assert "alt" not in base["runnable"].kwargs

    active = cast(Dict[str, Any], load(yaml_text, until="document", scopes=["model=complex"]))
    assert getattr(active["runnable"].kwargs["model"], "target", None) == "ComplexModel"
    assert "alt" not in active["runnable"].kwargs

    # And it survives materialization: the flowed trainer holds the swapped model.
    # (`!class:Trainer` without parens is a deferred stub — the consumer flows it.)
    built = confluid.flow(active["runnable"])
    assert isinstance(built.model, ComplexModel)
    assert not hasattr(built, "alt")


def test_scope_nested_in_a_dict_inside_a_markers_kwargs_resolves() -> None:
    """The Fluid walk is recursive — a scope one level deeper still splices."""

    @configurable
    class Knob:
        def __init__(self, inner: Any = None) -> None:
            self.inner = inner

    yaml_text = """
root: !class:Knob
  inner:
    base: 1
    if_large: !scope:size=large
      base: 99
"""
    small = cast(Dict[str, Any], load(yaml_text, until="document"))
    assert small["root"].kwargs["inner"] == {"base": 1}

    large = cast(Dict[str, Any], load(yaml_text, until="document", scopes=["size=large"]))
    assert large["root"].kwargs["inner"] == {"base": 99}


def test_scope_inside_a_lazy_markers_kwargs_resolves() -> None:
    """`!lazy:` is a Fluid too — deferring construction doesn't defer scope resolution."""

    @configurable
    class Knob:
        def __init__(self, n: int = 1) -> None:
            self.n = n

    yaml_text = """
slot: !lazy:Knob
  n: 1
  if_big: !scope:size=big
    n: 42
"""
    resolved = cast(Dict[str, Any], load(yaml_text, until="document", scopes=["size=big"]))
    assert resolved["slot"].kwargs["n"] == 42
    assert "if_big" not in resolved["slot"].kwargs


def test_scope_in_a_list_inside_a_markers_kwargs_resolves() -> None:
    """List-valued marker kwargs (an ops chain) reach the list walker through the Fluid.

    Both body shapes are exercised: a MAPPING body appends one entry, a SEQUENCE
    body extends with several.
    """

    @configurable
    class Chain:
        def __init__(self, steps: Any = None) -> None:
            self.steps = steps

    yaml_text = """
chain: !class:Chain
  steps:
    - {op: a}
    - !scope:extra=yes
      op: b
    - !scope:extra=yes
      - {op: b2}
      - {op: b3}
    - {op: c}
"""
    plain = cast(Dict[str, Any], load(yaml_text, until="document"))
    assert plain["chain"].kwargs["steps"] == [{"op": "a"}, {"op": "c"}]

    extra = cast(Dict[str, Any], load(yaml_text, until="document", scopes=["extra=yes"]))
    assert extra["chain"].kwargs["steps"] == [
        {"op": "a"},
        {"op": "b"},
        {"op": "b2"},
        {"op": "b3"},
        {"op": "c"},
    ]


def test_notscope_inside_a_markers_kwargs_uses_the_unset_convention() -> None:
    """The negative twin resolves in marker kwargs under the same unset-⇒-active rule."""

    @configurable
    class Knob:
        def __init__(self, n: int = 1) -> None:
            self.n = n

    yaml_text = """
slot: !class:Knob
  unless_big: !notscope:size=big
    n: 7
"""
    unset = cast(Dict[str, Any], load(yaml_text, until="document"))
    assert unset["slot"].kwargs["n"] == 7

    matched = cast(Dict[str, Any], load(yaml_text, until="document", scopes=["size=big"]))
    assert "n" not in matched["slot"].kwargs
    assert "unless_big" not in matched["slot"].kwargs


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
    block = ScopeBlock(dims={"debug": None}, negate=False, contents={"x": 2})
    config = {"x": 1, "_wrap": block}
    out = resolve_scopes(config, {"debug": None})
    assert out == {"x": 2}


def test_resolve_scopes_bare_top_level_block_active() -> None:
    """A ScopeBlock as the root value resolves to its contents when active."""
    block = ScopeBlock(dims={"debug": None}, negate=False, contents={"x": 99})
    out = resolve_scopes(block, {"debug": None})
    assert out == {"x": 99}


def test_resolve_scopes_bare_top_level_block_inactive() -> None:
    """A ScopeBlock as the root value resolves to None when inactive."""
    block = ScopeBlock(dims={"debug": None}, negate=False, contents={"x": 99})
    out = resolve_scopes(block, {})
    assert out is None


def test_scope_block_as_direct_list_item() -> None:
    """A ScopeBlock element in a list — splices its contents into the list when active."""
    block = ScopeBlock(dims={"debug": None}, negate=False, contents={"x": 1})
    out = resolve_scopes([1, block, 2], {"debug": None})
    assert out == [1, {"x": 1}, 2]


def test_scope_block_in_list_dropped_when_inactive() -> None:
    block = ScopeBlock(dims={"debug": None}, negate=False, contents={"x": 1})
    out = resolve_scopes([1, block, 2], {})
    assert out == [1, 2]


def test_scope_block_in_list_with_list_contents() -> None:
    block = ScopeBlock(dims={"debug": None}, negate=False, contents=[1, 2])
    out = resolve_scopes(["a", block, "b"], {"debug": None})
    assert out == ["a", 1, 2, "b"]


def test_scope_block_in_list_with_scalar_contents() -> None:
    block = ScopeBlock(dims={"debug": None}, negate=False, contents="literal")
    out = resolve_scopes(["a", block, "b"], {"debug": None})
    assert out == ["a", "literal", "b"]


# ---------------------------------------------------------------------------
# Body shapes — a block's YAML body may be a mapping, a sequence or a scalar.
# ---------------------------------------------------------------------------


def test_sequence_body_extends_the_surrounding_list() -> None:
    """A SEQUENCE body is the only way to write a conditional list ITEM.

    Regression: `loader._build_scope` built contents from a MappingNode only and
    silently gave everything else `{}`, so such a block was a no-op with no
    diagnostic — while `_resolve_list`'s list branch (which extends) had been
    written for it all along and was simply unreachable from YAML.
    """
    yaml_text = """
ops:
  - a
  - !scope:extra=yes
    - b1
    - b2
  - c
"""
    plain = cast(Dict[str, Any], load(yaml_text))
    assert plain["ops"] == ["a", "c"]

    extra = cast(Dict[str, Any], load(yaml_text, scopes=["extra=yes"]))
    assert extra["ops"] == ["a", "b1", "b2", "c"]


def test_scalar_body_substitutes_one_item_and_is_type_coerced() -> None:
    """A SCALAR body is one conditional VALUE, coerced like an inline `!class:` kwarg."""
    yaml_text = """
ops:
  - a
  - !scope:extra=yes c
  - !scope:extra=yes 42
  - d
"""
    assert cast(Dict[str, Any], load(yaml_text))["ops"] == ["a", "d"]

    extra = cast(Dict[str, Any], load(yaml_text, scopes=["extra=yes"]))
    assert extra["ops"] == ["a", "c", 42, "d"]
    assert isinstance(extra["ops"][2], int)  # `parse_value`, not the raw string


def test_empty_body_declares_nothing_and_stays_inert() -> None:
    """`if_x: !scope:debug` with no body is a placeholder, active or not.

    An empty scalar node must keep yielding `{}` rather than splicing `""` —
    "declares nothing" is a real state, and the sequence/scalar branches must not
    turn a pre-existing placeholder into a value.
    """
    yaml_text = """
base: 1
placeholder: !scope:debug
"""
    assert cast(Dict[str, Any], load(yaml_text)) == {"base": 1}
    assert cast(Dict[str, Any], load(yaml_text, scopes=["debug"])) == {"base": 1}


def test_notscope_sequence_body_uses_the_unset_convention() -> None:
    """The negative twin carries a sequence body under the same activation rule."""
    yaml_text = """
ops:
  - a
  - !notscope:extra=yes
    - fallback1
    - fallback2
"""
    assert cast(Dict[str, Any], load(yaml_text))["ops"] == ["a", "fallback1", "fallback2"]
    assert cast(Dict[str, Any], load(yaml_text, scopes=["extra=yes"]))["ops"] == ["a"]


def test_non_mapping_body_at_a_dict_slot_raises_when_active() -> None:
    """A sequence/scalar body has no KEYS, so a mapping slot cannot splice it.

    The wrapper key is inert scaffolding and cannot stand in for the missing keys,
    so there is no coherent result — a located `ScopeError` beats the bare
    `AttributeError: 'list' object has no attribute 'items'` this used to become.
    """
    yaml_text = """
root:
  base: 1
  oops: !scope:extra=yes
    - b
"""
    with pytest.raises(ScopeError) as excinfo:
        load(yaml_text, scopes=["extra=yes"])
    message = str(excinfo.value)
    assert "'oops'" in message and "list body" in message and "mapping body" in message

    scalar_text = "root:\n  oops: !scope:extra=yes literal\n"
    with pytest.raises(ScopeError):
        load(scalar_text, scopes=["extra=yes"])


def test_non_mapping_body_at_a_dict_slot_is_dropped_when_inactive() -> None:
    """An INACTIVE block is dropped without its contents being examined.

    That is the existing rule for every block (an inactive body may name classes
    this install does not have), so the shape check must not fire ahead of it.
    """
    yaml_text = """
root:
  base: 1
  oops: !scope:extra=yes
    - b
"""
    assert cast(Dict[str, Any], load(yaml_text)) == {"root": {"base": 1}}


def test_sequence_body_survives_a_round_trip_through_includes(tmp_path: Path) -> None:
    """`loader._process_includes_recursive` walks a non-mapping body too.

    It assumed `ScopeBlock.contents` was a dict and did `.items()` on it, so the
    moment the loader started building sequence bodies, merely LOADING such a file
    raised. All three walkers over a block's contents must agree on the shapes.
    """
    inc = tmp_path / "inc.yaml"
    inc.write_text("shared: 1\n")
    main = tmp_path / "main.yaml"
    main.write_text(
        """
include: inc.yaml
ops:
  - a
  - !scope:extra=yes
    - b
"""
    )
    loaded = cast(Dict[str, Any], load(main, scopes=["extra=yes"]))
    assert loaded["shared"] == 1
    assert loaded["ops"] == ["a", "b"]


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
    raw = load(yaml_path, until="raw")
    assert "size" in discover_dimensions(raw)


# ---------------------------------------------------------------------------
# An active value must be one the document declares
# ---------------------------------------------------------------------------
# Asking for a variant that does not exist used to resolve to the DEFAULT and say
# nothing — a typo'd `--framework kears` ran the default backend and looked like
# success until the artifacts were read.

_TWO_VARIANTS = """
runnable: default
torch: !scope:framework=torch
  runnable: torch
keras: !scope:framework=keras
  runnable: keras
"""


def _unresolved(text: str) -> Any:
    """Parse WITHOUT resolving scopes — `load()` has already spliced them away.

    Discovery reads the raw document, which is also what `resolve_scopes` is handed.
    """
    import yaml

    from confluid.loader import ConfluidLoader

    return yaml.load(text, Loader=ConfluidLoader)


def test_dimension_values_are_discovered_per_dimension() -> None:
    raw = _unresolved(_TWO_VARIANTS + "big: !scope:size=large\n  n: 1\n")
    assert discover_dimension_values(raw) == {"framework": {"torch", "keras"}, "size": {"large"}}


def test_discover_dimensions_reads_the_same_walkers_keys() -> None:
    """One walker: the key-only view is derived, never a second traversal."""
    raw = _unresolved(_TWO_VARIANTS)
    assert discover_dimensions(raw) == set(discover_dimension_values(raw))


def test_a_boolean_scope_declares_no_value() -> None:
    raw = _unresolved("dbg: !scope:debug\n  verbose: true\n")
    assert discover_dimension_values(raw) == {}


def test_a_negated_block_declares_the_dimension_but_no_selectable_value() -> None:
    """`!notscope:framework=torch` is activated by every value EXCEPT `torch`.

    So its value is the one thing that does not SELECT it — the dimension is real
    (a CLI must still bind `--framework`) but there is nothing to offer as a choice.
    """
    raw = _unresolved("alt: !notscope:framework=torch\n  runnable: other\n")
    assert discover_dimension_values(raw) == {"framework": set()}
    assert discover_dimensions(raw) == {"framework"}


def test_a_negation_makes_every_value_meaningful_so_nothing_is_rejected() -> None:
    """The check must not fire where a negation is what the value is read against."""
    text = "postproc: base\nunless_seg: !notscope:task=segmentation\n  postproc: default\n"
    assert cast(Dict[str, Any], load(text, until="document", scopes=["task=classification"]))["postproc"] == "default"
    assert cast(Dict[str, Any], load(text, until="document", scopes=["task=segmentation"]))["postproc"] == "base"


def test_an_undeclared_value_on_a_declared_dimension_raises() -> None:
    with pytest.raises(ScopeError) as ei:
        load(_TWO_VARIANTS, until="document", scopes=["framework=kears"])

    message = str(ei.value)
    assert "framework='kears'" in message
    assert "keras, torch" in message, "the error must list the values that DO exist"


def test_an_undeclared_DIMENSION_stays_an_inert_no_op() -> None:
    """The narrow rule: a CLI may pass a dimension a config has not grown into yet."""
    loaded = cast(Dict[str, Any], load(_TWO_VARIANTS, until="document", scopes=["hardware=gpu"]))
    assert loaded["runnable"] == "default"


def test_a_declared_value_resolves_as_before() -> None:
    loaded = cast(Dict[str, Any], load(_TWO_VARIANTS, until="document", scopes=["framework=keras"]))
    assert loaded["runnable"] == "keras"


def test_no_scopes_at_all_resolves_to_the_documents_own_keys() -> None:
    assert cast(Dict[str, Any], load(_TWO_VARIANTS, until="document"))["runnable"] == "default"


def test_a_boolean_activation_is_never_value_checked() -> None:
    """`--scope debug` carries no value, so there is nothing to check it against."""
    loaded = cast(Dict[str, Any], load(_TWO_VARIANTS + "dbg: !scope:debug\n  verbose: true\n", scopes=["debug"]))
    assert loaded["verbose"] is True


def test_the_check_reaches_a_dimension_declared_inside_a_markers_kwargs() -> None:
    """The value walker and the resolver agree on node kinds — including Fluid kwargs."""
    text = "root: !class:Knob\n  model: a\n  alt: !scope:model=convnet\n    model: b\n"
    with pytest.raises(ScopeError, match="convnet"):
        load(text, until="document", scopes=["model=resnet"])


def test_repr_format() -> None:
    """ScopeBlock.__repr__ surfaces the reserved-key spelling for diagnostics."""
    bk = ScopeBlock(dims={"debug": None}, negate=False, contents={"x": 1})
    assert "_scope_: {debug}" in repr(bk)
    kk = ScopeBlock(dims={"task": "cls"}, negate=False, contents={"x": 1})
    assert "_scope_: {task: cls}" in repr(kk)
    nk = ScopeBlock(dims={"debug": None}, negate=True, contents={"x": 1})
    assert "_notscope_: {debug}" in repr(nk)
    mk = ScopeBlock(dims={"framework": "keras", "model": "convnet"}, negate=False, contents={})
    assert "_scope_: {framework: keras, model: convnet}" in repr(mk)


# ---------------------------------------------------------------------------
# default_scopes — a document declares the value a dimension takes when the
# caller names none. Keyed only; read beside `scope_aliases`, before pass 4.
# ---------------------------------------------------------------------------

_DEFAULTED = """
default_scopes: [framework=lightning]
model: shared
lightning: !scope:framework=lightning
  runnable: LightningX
keras: !scope:framework=keras
  runnable: KerasX
"""


def test_a_default_scope_fires_when_the_caller_names_no_value() -> None:
    out = cast(Dict[str, Any], load(_DEFAULTED, until="document"))
    assert out == {"model": "shared", "runnable": "LightningX"}
    assert "default_scopes" not in out  # load metadata, stripped like scope_aliases


def test_a_callers_value_wins_over_the_default() -> None:
    out = cast(Dict[str, Any], load(_DEFAULTED, until="document", scopes=["framework=keras"]))
    assert out["runnable"] == "KerasX"


def test_a_default_only_fills_the_dimensions_the_caller_left_unset() -> None:
    """Two dimensions, one defaulted, one named by the caller: each resolves independently."""
    text = _DEFAULTED + "default_scopes: [framework=lightning, model=convnet]\n"
    text = text.replace("default_scopes: [framework=lightning]\n", "")  # keep ONE key
    text += "cnn: !scope:model=convnet\n  model: convnet\nres: !scope:model=resnet\n  model: resnet\n"
    out = cast(Dict[str, Any], load(text, until="document", scopes=["framework=keras"]))
    assert out == {"model": "convnet", "runnable": "KerasX"}


def test_an_undeclared_default_value_is_a_scope_error() -> None:
    """A typo'd default is loud — the same check an activation gets, not a silent nothing."""
    with pytest.raises(ScopeError, match="framework='kears'.*keras, lightning"):
        load(_DEFAULTED.replace("framework=lightning]", "framework=kears]"), until="document")


def test_a_boolean_default_is_refused() -> None:
    """The caller cannot switch a boolean default OFF, so it would be always-on, not a default."""
    text = "default_scopes: [smoke]\nsmoke_on: !scope:smoke\n  epochs: 1\n"
    with pytest.raises(ScopeError, match=r"default_scopes.*'smoke'.*!notscope:smoke"):
        load(text, until="document")


def test_an_alias_as_a_default_is_refused() -> None:
    """Aliases expand boolean names only, and a default is keyed — an alias entry has no `=`."""
    text = "scope_aliases:\n  ci: [quick]\ndefault_scopes: [ci]\nq: !scope:quick\n  n: 1\n"
    with pytest.raises(ScopeError, match=r"default_scopes.*'ci'"):
        load(text, until="document")


def test_a_default_is_not_interpolated() -> None:
    """Scopes settle before interpolation, so `${...}` in a default is a literal that matches nothing."""
    text = _DEFAULTED.replace("[framework=lightning]", '["framework=${env:CONFLUID_FW}"]')
    with pytest.raises(ScopeError, match=r"framework='\$\{env:CONFLUID_FW\}'"):
        load(text, until="document")


def test_a_default_deactivates_a_notscope_block_exactly_as_a_caller_value_would() -> None:
    text = "default_scopes: [model=cnn]\nno_model: !notscope:model\n  model: mlp\ncnn: !scope:model=cnn\n  model: cnn\n"
    assert cast(Dict[str, Any], load(text, until="document")) == {"model": "cnn"}


def test_a_default_is_not_a_declared_value_by_itself() -> None:
    """`discover_dimension_values` reads BLOCKS; a default that names no block is an error, not a declaration."""
    raw = load(_DEFAULTED, until="raw")
    assert discover_dimension_values(raw) == {"framework": {"lightning", "keras"}}
    assert cast(Dict[str, Any], raw)["default_scopes"] == ["framework=lightning"]  # intact at "raw"


def test_a_malformed_default_scopes_key_is_refused() -> None:
    with pytest.raises(ScopeError, match="default_scopes"):
        load("default_scopes: framework=lightning\nx: 1\n", until="document")  # a string, not a list


def test_hydraide_emits_the_defaulted_variant_when_no_scope_is_given(tmp_path: Path) -> None:
    from confluid import hydraide

    path = tmp_path / "d.yaml"
    path.write_text(_DEFAULTED)
    assert "LightningX" in hydraide.emit(path)
    assert "KerasX" in hydraide.emit(path, scopes=["framework=keras"])


def test_default_scopes_reads_the_raw_documents_defaults() -> None:
    """`confluid.default_scopes(raw)` — the public reader a CLI uses to SHOW the default beside a
    dimension's values. It takes the RAW document like `discover_dimension_values`, answers `{}` for
    a document without the key (or a non-dict root), and refuses a malformed key the same way `load()` does."""
    from confluid import default_scopes

    raw = load(_DEFAULTED, until="raw")
    assert default_scopes(raw) == {"framework": "lightning"}
    assert discover_dimension_values(raw) == {"framework": {"lightning", "keras"}}
    assert default_scopes(load("x: 1\n", until="raw")) == {}
    assert default_scopes([1, 2]) == {}
    with pytest.raises(ScopeError, match="default_scopes"):
        default_scopes({"default_scopes": [42]})


def test_an_include_line_does_not_break_an_anchor_aliased_inside_a_scope_block(tmp_path: Path) -> None:
    """BUGS-2026-08-19 PA5 — any `include:` runs the document through `deep_merge`,
    which deep-copied every ScopeBlock and with it the anchored marker inside;
    one instance became two. Same document, with and without the include."""
    _register_splice_fixtures()
    (tmp_path / "other.yaml").write_text("unrelated: 1\n")
    doc = "proto: &p !class:_Model\n  depth: 3\nblk: !scope:x\n  shared: *p\n"
    (tmp_path / "plain.yaml").write_text(doc)
    (tmp_path / "with_include.yaml").write_text("include: other.yaml\n" + doc)
    plain = load(str(tmp_path / "plain.yaml"), scopes=["x"])
    assert plain["proto"] is plain["shared"]
    included = load(str(tmp_path / "with_include.yaml"), scopes=["x"])
    assert included["proto"] is included["shared"]


def test_an_empty_scope_suffix_is_refused_like_the_reserved_key_spelling() -> None:
    """PA19 (BUGS-2026-08-19) — `!scope:` with no dimension was silently inert (a
    positive block on '' can never fire, so the body vanished) while
    `_scope_: {'': }` refused. Two spellings, one behaviour."""
    from confluid import ConfigurationError

    with pytest.raises(ConfigurationError, match="names no dimension"):
        load("blk: !scope:\n  a: 1\nb: 2\n", until="raw")


def test_a_non_string_scope_value_is_refused_like_a_boolean() -> None:
    """PA20 — YAML coerces `0.10` to 0.1 and `010` to 8, so the str() mint produced
    values the tag spelling (which keeps its text) and a CLI activation string never
    match; the refusal booleans already got now covers every non-string."""
    from confluid import ConfigurationError

    with pytest.raises(ConfigurationError, match=r"float 0\.1.*quote it"):
        load("b:\n  _scope_: {f: 0.10}\n  a: 1\n", until="raw")
    with pytest.raises(ConfigurationError, match=r"int 8.*quote it"):
        load("b:\n  _scope_: {f: 010}\n  a: 1\n", until="raw")
    raw = load("b:\n  _scope_: {f: '0.10'}\n  a: 1\n", until="raw")
    assert raw["b"].dims == {"f": "0.10"}, "the quoted spelling keeps its text (the con)"


def test_a_bare_activation_of_a_KEYED_dimension_is_refused_naming_the_values() -> None:
    """SR13 (BUGS-2026-08-19) — `scopes=["framework"]` against keyed blocks selected
    nothing AND suppressed the document's default_scopes value, silently: the run
    used neither. The refusal names the declared values and their lines."""
    doc = (
        "default_scopes: [framework=lightning]\n"
        "l: !scope:framework=lightning\n  runnable: L\n"
        "k: !scope:framework=keras\n  runnable: K\n"
    )
    with pytest.raises(ScopeError, match=r"KEYED dimension.*keras, lightning.*framework=<value>"):
        load(doc, scopes=["framework"], until="document")
    assert load(doc, until="document") == {"runnable": "L"}, "no activation still takes the default (con)"
    boolean = load("d: !scope:debug\n  verbose: true\n", scopes=["debug"], until="document")
    assert boolean == {"verbose": True}, "a bare BOOLEAN activation is untouched (con)"
