"""Pins the 2026-07 scoped-broadcasting semantics: exact addressed keys + glob opt-in.

The ONE rule, restated:

* A BARE top-level key (``lr: 0.9``) is an implicit ``**.lr`` — it broadcasts
  to every accepting node in the tree (unchanged).
* An ADDRESSED key — dotted ``mid.lr: 0.5``, the nested block
  ``mid: {lr: 0.5}``, or a marker's own kwargs — applies to the matched node
  ONLY; it no longer cascades to the node's descendants.
* Glob wildcards opt back in: ``*`` matches exactly one nesting level,
  ``**`` matches zero or more (so ``mid.**.lr`` covers mid AND all its
  descendants — the declare-once form).
* Document-order last-write-wins stays the only priority rule — no
  specificity tiers between exact and glob keys.
* ``*``/``**``-delivered keys are cascade keys and honour the NoBroadcast
  opt-outs like bare keys; exact addressed keys bypass them like blocks.

Every scenario is pinned through BOTH paths: ``materialize()`` (the engine)
and ``configure()`` (post-construction, live objects) — the ONE-rule parity
mandate.
"""

from typing import Any, List, Optional

import pytest

from confluid import NoBroadcast, configurable, configure, get_registry, load, materialize, resolve
from confluid.dumper import dump
from confluid.fluid import Instance


def _inst(target: str, /, **kwargs: Any) -> Instance:
    marker = Instance(target)
    marker.kwargs.update(kwargs)
    return marker


@configurable
class _Node:
    """Generic tree node: a ``child`` slot, a ``children`` list, and an ``lr`` knob."""

    def __init__(
        self,
        child: Any = None,
        children: Optional[List[Any]] = None,
        lr: float = 0.0,
        momentum: float = 0.0,
        name: str = "",
    ) -> None:
        self.child = child
        self.children = list(children) if children is not None else []
        self.lr = lr
        self.momentum = momentum
        self.name = name


@configurable
class _Gated:
    """Node with a NoBroadcast-marked ``lr`` — bare/glob keys must not land."""

    def __init__(self, lr: NoBroadcast[float] = 0.0, name: str = "") -> None:
        self.lr = lr
        self.name = name


@pytest.fixture(autouse=True)
def _register_classes() -> None:
    registry = get_registry()
    registry.register_class(_Node, name="_Node")
    registry.register_class(_Gated, name="_Gated")


_TREE_YAML = """
root: !class:_Node()
  name: root
  child: !class:_Node()
    name: mid
    child: !class:_Node()
      name: leaf
"""


def _tree() -> _Node:
    """Live three-level tree for the configure() path."""
    return _Node(name="root", child=_Node(name="mid", child=_Node(name="leaf")))


def _lrs(root: _Node) -> tuple:
    return (root.lr, root.child.lr, root.child.child.lr)


# ---------------------------------------------------------------------------
# Bare keys: unchanged full-tree broadcast (implicit '**').
# ---------------------------------------------------------------------------


def test_bare_key_broadcasts_tree_wide_materialize() -> None:
    result = load(_TREE_YAML + "lr: 0.9\n")
    assert _lrs(result["root"]) == (0.9, 0.9, 0.9)


def test_bare_key_broadcasts_tree_wide_configure() -> None:
    root = _tree()
    configure(root, config="lr: 0.9")
    assert _lrs(root) == (0.9, 0.9, 0.9)


# ---------------------------------------------------------------------------
# Addressed keys are exact: matched node only, no cascade.
# ---------------------------------------------------------------------------


def test_dotted_key_is_exact_materialize() -> None:
    result = load(_TREE_YAML + "mid.lr: 0.5\n")
    assert _lrs(result["root"]) == (0.0, 0.5, 0.0)


def test_dotted_key_is_exact_configure() -> None:
    root = _tree()
    configure(root, config="mid.lr: 0.5")
    assert _lrs(root) == (0.0, 0.5, 0.0)


def test_nested_block_is_exact_materialize() -> None:
    result = load(_TREE_YAML + "mid:\n  lr: 0.5\n")
    assert _lrs(result["root"]) == (0.0, 0.5, 0.0)


def test_nested_block_is_exact_configure() -> None:
    root = _tree()
    configure(root, config="mid:\n  lr: 0.5")
    assert _lrs(root) == (0.0, 0.5, 0.0)


def test_own_kwargs_do_not_cascade_materialize() -> None:
    """A marker's own kwargs configure that marker only (same rule as blocks)."""
    config = _inst("_Node", name="root", lr=0.5, child=_inst("_Node", name="mid"))
    root = materialize(config)
    assert root.lr == 0.5
    assert root.child.lr == 0.0


# ---------------------------------------------------------------------------
# '**' glob: zero or more levels — the declare-once cascade form.
# ---------------------------------------------------------------------------


def test_glob_star_star_covers_node_and_descendants_materialize() -> None:
    result = load(_TREE_YAML + "mid.**.lr: 0.5\n")
    assert _lrs(result["root"]) == (0.0, 0.5, 0.5)


def test_glob_star_star_covers_node_and_descendants_configure() -> None:
    root = _tree()
    configure(root, config="mid.**.lr: 0.5")
    assert _lrs(root) == (0.0, 0.5, 0.5)


def test_bare_key_equals_top_level_glob_materialize() -> None:
    result = load(_TREE_YAML + "'**.lr': 0.9\n")
    assert _lrs(result["root"]) == (0.9, 0.9, 0.9)


def test_glob_spelling_equivalence_materialize() -> None:
    """``mid.**.lr`` ≡ ``mid: {'**.lr': …}`` ≡ ``mid: {'**': {lr: …}}``."""
    dotted = load(_TREE_YAML + "mid.**.lr: 0.5\n")
    in_block_dotted = load(_TREE_YAML + "mid:\n  '**.lr': 0.5\n")
    in_block_nested = load(_TREE_YAML + "mid:\n  '**':\n    lr: 0.5\n")
    assert _lrs(dotted["root"]) == _lrs(in_block_dotted["root"]) == _lrs(in_block_nested["root"]) == (0.0, 0.5, 0.5)


def test_glob_spelling_equivalence_configure() -> None:
    for doc in ("mid.**.lr: 0.5", "mid:\n  '**.lr': 0.5", "mid:\n  '**':\n    lr: 0.5"):
        root = _tree()
        configure(root, config=doc)
        assert _lrs(root) == (0.0, 0.5, 0.5), doc


def test_glob_deep_named_segment_materialize() -> None:
    """``root.**.leaf.lr`` — a named segment after '**' floats to any depth."""
    result = load(_TREE_YAML + "root.**.leaf.lr: 0.7\n")
    assert _lrs(result["root"]) == (0.0, 0.0, 0.7)


# ---------------------------------------------------------------------------
# '*' glob: exactly one level — direct children only.
# ---------------------------------------------------------------------------


def test_glob_star_hits_direct_children_only_materialize() -> None:
    result = load(_TREE_YAML + "mid.*.lr: 0.5\n")
    assert _lrs(result["root"]) == (0.0, 0.0, 0.5)


def test_glob_star_hits_direct_children_only_configure() -> None:
    root = _tree()
    configure(root, config="mid.*.lr: 0.5")
    assert _lrs(root) == (0.0, 0.0, 0.5)


def test_glob_star_does_not_reach_grandchildren_materialize() -> None:
    result = load(_TREE_YAML + "root.*.lr: 0.5\n")
    assert _lrs(result["root"]) == (0.0, 0.5, 0.0)


def test_glob_star_reaches_list_held_children_materialize() -> None:
    """Containers are transparent — one level = one Fluid hop, not a list hop."""
    doc = """
root: !class:_Node()
  name: root
  children:
    - !class:_Node()
      name: a
    - !class:_Node()
      name: b
root.*.lr: 0.5
"""
    result = load(doc)
    root = result["root"]
    assert root.lr == 0.0
    assert [c.lr for c in root.children] == [0.5, 0.5]


# ---------------------------------------------------------------------------
# Multi-segment paths without globs: strict one-level hops after the first.
# ---------------------------------------------------------------------------


def test_named_segment_is_strict_one_level_materialize() -> None:
    """``root.leaf.lr`` must NOT reach a grandchild named leaf (one hop only)."""
    result = load(_TREE_YAML + "root.leaf.lr: 0.7\n")
    # 'leaf' is a grandchild of root — the strict segment expires unmatched.
    assert _lrs(result["root"]) == (0.0, 0.0, 0.0)


def test_named_segment_matches_direct_child_materialize() -> None:
    result = load(_TREE_YAML + "root.mid.lr: 0.7\n")
    assert _lrs(result["root"]) == (0.0, 0.7, 0.0)


def test_named_segment_matches_direct_child_configure() -> None:
    root = _tree()
    configure(root, config="root.mid.lr: 0.7")
    assert _lrs(root) == (0.0, 0.7, 0.0)


# ---------------------------------------------------------------------------
# NoBroadcast interaction: globs are gated like bare keys, exact bypasses.
# ---------------------------------------------------------------------------


def test_glob_keys_respect_no_broadcast_marker() -> None:
    doc = """
node: !class:_Gated()
  name: gated
'**':
  lr: 0.9
"""
    result = load(doc)
    assert result["node"].lr == 0.0  # glob-delivered — blocked like a bare key


def test_exact_key_bypasses_no_broadcast_marker() -> None:
    result = load("node: !class:_Gated()\n  name: gated\ngated.lr: 0.5\n")
    assert result["node"].lr == 0.5  # addressed — always lands


def test_glob_keys_respect_class_level_opt_out() -> None:
    @configurable(broadcast=False)
    class _OptedOut:
        def __init__(self, lr: float = 0.0, name: str = "") -> None:
            self.lr = lr
            self.name = name

    get_registry().register_class(_OptedOut, name="_OptedOut")
    result = load("node: !class:_OptedOut()\n  name: n\n'**':\n  lr: 0.9\n")
    assert result["node"].lr == 0.0
    exact = load("node: !class:_OptedOut()\n  name: n\nn.lr: 0.5\n")
    assert exact["node"].lr == 0.5


# ---------------------------------------------------------------------------
# Ordering: document-order last-write-wins, no specificity tiers.
# ---------------------------------------------------------------------------


def test_exact_then_later_glob_glob_wins_materialize() -> None:
    doc = _TREE_YAML + "mid:\n  lr: 0.3\n'**':\n  lr: 0.9\n"
    result = load(doc)
    assert result["root"].child.lr == 0.9  # the later glob takes the slot


def test_glob_then_later_exact_exact_wins_materialize() -> None:
    doc = _TREE_YAML + "'**':\n  lr: 0.9\nmid:\n  lr: 0.3\n"
    result = load(doc)
    assert _lrs(result["root"]) == (0.9, 0.3, 0.9)


def test_glob_then_later_exact_exact_wins_configure() -> None:
    root = _tree()
    configure(root, config="'**':\n  lr: 0.9\nmid:\n  lr: 0.3")
    assert _lrs(root) == (0.9, 0.3, 0.9)


# ---------------------------------------------------------------------------
# Hygiene: glob keys are routing metadata, never values.
# ---------------------------------------------------------------------------


def test_glob_keys_never_reach_instances_or_dump() -> None:
    config = _inst("_Node", name="root", child=_inst("_Node", name="mid"), **{"**": {"lr": 0.5}})
    root = materialize(config)
    assert root.lr == 0.5
    assert root.child.lr == 0.5
    assert not hasattr(root, "**")
    assert "**" not in (getattr(root, "__confluid_kwargs__", {}) or {})
    assert "**" not in (getattr(root, "__confluid_extra__", []) or [])
    dumped = dump(root)
    assert "**" not in dumped


def test_resolve_strips_glob_routing_from_marker_kwargs() -> None:
    config = {"root": _inst("_Node", name="root", child=_inst("_Node", name="mid"), **{"**": {"lr": 0.5}})}
    markers = resolve(config)
    root_marker = markers["root"]
    assert "**" not in root_marker.kwargs
    assert root_marker.kwargs["lr"] == 0.5  # applied to the introducing node
    assert root_marker.kwargs["child"].kwargs["lr"] == 0.5  # merged into the descendant


# ---------------------------------------------------------------------------
# Guards: refs and grouping dicts are unaffected.
# ---------------------------------------------------------------------------


def test_ref_resolution_unaffected_by_glob_keys() -> None:
    doc = """
base_lr: 0.25
root: !class:_Node()
  name: root
  lr: !ref:base_lr
  child: !class:_Node()
    name: mid
mid.**.lr: 0.5
"""
    result = load(doc)
    assert result["root"].lr == 0.25
    assert result["root"].child.lr == 0.5


def test_two_globs_on_same_node_coexist_materialize() -> None:
    """``mid.**.lr`` + ``mid.**.momentum`` deep-merge into one rider."""
    result = load(_TREE_YAML + "mid.**.lr: 0.5\nmid.**.momentum: 0.1\n")
    mid, leaf = result["root"].child, result["root"].child.child
    assert (mid.lr, mid.momentum) == (0.5, 0.1)
    assert (leaf.lr, leaf.momentum) == (0.5, 0.1)


def test_two_globs_on_same_node_coexist_configure() -> None:
    root = _tree()
    configure(root, config="mid.**.lr: 0.5\nmid.**.momentum: 0.1")
    mid, leaf = root.child, root.child.child
    assert (mid.lr, mid.momentum) == (0.5, 0.1)
    assert (leaf.lr, leaf.momentum) == (0.5, 0.1)


def test_inner_rider_merges_with_outer_rider_materialize() -> None:
    """A node's own '**' rider merges with the ancestors' — neither is lost."""
    result = load(_TREE_YAML + "'**.lr': 0.9\nmid.**.momentum: 0.1\n")
    root, mid, leaf = result["root"], result["root"].child, result["root"].child.child
    assert (root.lr, root.momentum) == (0.9, 0.0)
    assert (mid.lr, mid.momentum) == (0.9, 0.1)
    assert (leaf.lr, leaf.momentum) == (0.9, 0.1)


def test_star_with_deeper_named_segment_materialize() -> None:
    """``root.*.leaf.lr`` — '*' consumes one level, then a strict named hop."""
    result = load(_TREE_YAML + "root.*.leaf.lr: 0.7\n")
    assert _lrs(result["root"]) == (0.0, 0.0, 0.7)


def test_in_marker_dotted_key_reaches_fluid_kwarg_materialize() -> None:
    """A dotted key inside a marker body traverses into the child Fluid's kwargs."""
    doc = """
root: !class:_Node()
  name: root
  child: !class:_Node()
    name: mid
  child.lr: 0.7
"""
    result = load(doc)
    assert result["root"].lr == 0.0
    assert result["root"].child.lr == 0.7


def test_in_block_dotted_key_merges_with_sibling_block_materialize() -> None:
    """Dotted and nested spellings of the same sub-block deep-merge (copy-on-write)."""
    doc = _TREE_YAML + "mid:\n  leaf: {lr: 0.5}\n  leaf.momentum: 0.1\n"
    result = load(doc)
    leaf = result["root"].child.child
    assert (leaf.lr, leaf.momentum) == (0.5, 0.1)


def test_direct_flow_honours_glob_kwargs() -> None:
    """A hand-built marker flowed OUTSIDE materialize still honours '**' kwargs."""
    from confluid import Class, flow

    child_stub = Class("_Node")
    marker = _inst("_Node", name="root", child=child_stub, **{"**": {"lr": 0.5}})
    root = flow(marker)
    assert root.lr == 0.5
    assert not hasattr(root, "**")
    child = flow(root.child)  # deferred stub — glob was merged into its kwargs
    assert child.lr == 0.5


def test_top_level_star_addresses_configured_roots_configure() -> None:
    """A root-level '*' block addresses the objects configure() was called on."""
    root = _tree()
    configure(root, config="'*':\n  lr: 0.5")
    assert _lrs(root) == (0.5, 0.0, 0.0)


def test_parent_rider_before_self_slot_merges_materialize() -> None:
    """A literal '**' block preceding the marker merges with the marker's own rider."""
    doc = """
'**':
  lr: 0.9
root: !class:_Node()
  name: root
  child: !class:_Node()
    name: mid
root.**.momentum: 0.1
"""
    result = load(doc)
    root, mid = result["root"], result["root"].child
    assert (root.lr, root.momentum) == (0.9, 0.1)
    assert (mid.lr, mid.momentum) == (0.9, 0.1)


def test_top_level_star_block_materialize() -> None:
    """A root-level '*' block addresses the top-level nodes only."""
    result = load("'*':\n  lr: 0.5\n" + _TREE_YAML.lstrip())
    assert _lrs(result["root"]) == (0.5, 0.0, 0.0)


def test_block_rider_merges_with_own_rider_materialize() -> None:
    """A '**' delivered via a named block merges with the marker's own '**'."""
    doc = """
root: !class:_Node()
  name: root
  child: !class:_Node()
    name: mid
    '**':
      momentum: 0.1
    child: !class:_Node()
      name: leaf
mid:
  '**':
    lr: 0.5
"""
    result = load(doc)
    leaf = result["root"].child.child
    assert (leaf.lr, leaf.momentum) == (0.5, 0.1)


def test_direct_flow_star_kwargs_feed_nested_stubs() -> None:
    from confluid import Class, flow

    marker = _inst("_Node", name="root", child=Class("_Node"), **{"*": {"lr": 0.5}})
    root = flow(marker)
    assert root.lr == 0.0  # '*' addresses the children, not the receiver
    assert flow(root.child).lr == 0.5


def test_direct_flow_glob_self_application_guards() -> None:
    from confluid import flow

    # An explicit own kwarg wins over the rider's self-application; unknown
    # keys are skipped; a NoBroadcast class blocks the glob entirely.
    root = flow(_inst("_Node", lr=0.3, **{"**": {"lr": 0.9, "bogus": 1}}))
    assert root.lr == 0.3
    assert not hasattr(root, "bogus")
    gated = flow(_inst("_Gated", **{"**": {"lr": 0.9}}))
    assert gated.lr == 0.0


def test_expand_block_keys_unit() -> None:
    """Unit pins for the in-block dotted-key expansion helper."""
    from confluid.engine import _expand_block_keys

    # No dotted keys — same object back (no-op identity).
    block = {"lr": 1}
    assert _expand_block_keys(block) is block

    # Dotted keys nest; values shared by reference (no copies).
    sentinel = object()
    out = _expand_block_keys({"a.b": sentinel})
    assert out["a"]["b"] is sentinel

    # Descending into an existing dict is copy-on-write — input untouched.
    inner = {"x": 1}
    src = {"a": inner, "a.y": 2}
    out = _expand_block_keys(src)
    assert out["a"] == {"x": 1, "y": 2}
    assert inner == {"x": 1}

    # A dict value merges with an existing dict at the final segment.
    out = _expand_block_keys({"a": {"b": {"x": 1}}, "a.b": {"y": 2}})
    assert out["a"]["b"] == {"x": 1, "y": 2}

    # Dotted keys traverse INTO a Fluid's kwargs (mirroring the merger).
    fluid = _inst("_Node", name="mid")
    out = _expand_block_keys({"child": fluid, "child.lr": 0.7})
    assert out["child"] is fluid
    assert fluid.kwargs["lr"] == 0.7


def test_named_segment_is_strict_one_level_configure() -> None:
    root = _tree()
    configure(root, config="root.leaf.lr: 0.7")
    assert _lrs(root) == (0.0, 0.0, 0.0)  # leaf is a grandchild — segment expired


def test_star_inside_rider_routes_children_configure() -> None:
    """A '*' nested in a '**' rider addresses every matched node's children."""
    root = _tree()
    configure(root, config="mid:\n  '**':\n    '*':\n      lr: 0.5")
    assert _lrs(root) == (0.0, 0.0, 0.5)


def test_inner_rider_merges_with_outer_rider_configure() -> None:
    root = _tree()
    configure(root, config="'**':\n  lr: 0.9\nmid.**.momentum: 0.1")
    assert (root.lr, root.momentum) == (0.9, 0.0)
    assert (root.child.lr, root.child.momentum) == (0.9, 0.1)
    assert (root.child.child.lr, root.child.child.momentum) == (0.9, 0.1)


def test_two_matched_blocks_merge_strict_routing_configure() -> None:
    """Class-name AND instance-name blocks hoisting the same sub-block merge."""
    root = _tree()
    configure(root, config="_Node:\n  mid:\n    momentum: 0.1\nroot.mid.lr: 0.5")
    mid = root.child
    assert (mid.lr, mid.momentum) == (0.5, 0.1)


def test_class_block_attr_recursion_is_per_match_configure() -> None:
    """``_Node: {child: {lr}}`` — every matched node routes to ITS child attr."""
    root = _tree()
    configure(root, config="_Node:\n  child:\n    lr: 0.5")
    # The floating class block matches every _Node; each recursion configures
    # that node's own child — so mid and leaf receive it, root does not.
    assert _lrs(root) == (0.0, 0.5, 0.5)


def test_attr_recursion_routes_deeper_segments_configure() -> None:
    """``root: {child: {child: {lr}}}`` — attr recursions chain one level at a time."""
    root = _tree()
    configure(root, config="root:\n  child:\n    child:\n      lr: 0.5")
    assert _lrs(root) == (0.0, 0.0, 0.5)


def test_top_level_dotted_glob_pair_merges_in_merger() -> None:
    """``a.**.x`` + ``a.**.y`` deep-merge into one nested block at expansion."""
    from confluid.merger import expand_dotted_keys

    out = expand_dotted_keys({"a.**.x": 1, "a": {"**": {"y": 2}}})
    assert out["a"]["**"] == {"y": 2, "x": 1}


def test_grouping_dict_keeps_bare_scope_materialize() -> None:
    """A dict key matching no node is a transparent grouping — its scalar
    contents stay bare within that structural subtree."""
    from confluid import flow

    doc = """
group:
  lr: 0.4
  node: !class:_Node()
    name: n
    child: !class:_Node()
      name: inner
"""
    result = load(doc)
    # _deep_flow only builds top-level markers; the group-nested marker keeps
    # its broadcast-merged kwargs and builds on an explicit flow().
    node = flow(result["group"]["node"])
    assert node.lr == 0.4
    assert node.child.lr == 0.4


# ---------------------------------------------------------------------------
# _View copy/update preserve the scope side-table (the silent-flattening trap)
# ---------------------------------------------------------------------------


def test_view_copy_returns_view_with_scopes() -> None:
    """``view.copy()`` must return a ``_View`` carrying the tags — ``dict.copy()``
    on a subclass returns a plain ``dict``, which would silently flatten every
    addressed/glob key to BARE."""
    from confluid.engine import _KeyScope, _View

    v = _View({"a": 1})
    v.set("b", 2, _KeyScope.EXACT)
    v.set("c", 3, _KeyScope.ADDRESSED)

    c = v.copy()
    assert isinstance(c, _View)
    assert c == {"a": 1, "b": 2, "c": 3}
    assert c.scope_of("a") is _KeyScope.BARE
    assert c.scope_of("b") is _KeyScope.EXACT
    assert c.scope_of("c") is _KeyScope.ADDRESSED
    # Independent side-table — mutating the copy never touches the original.
    c.set("b", 9, _KeyScope.STRICT)
    assert v.scope_of("b") is _KeyScope.EXACT


def test_view_update_last_write_wins_on_scopes() -> None:
    """``update()`` takes each key's scope FROM THE SOURCE: a ``_View`` source
    carries its tag over, and an untagged source key CLEARS an existing tag
    (the value was overwritten, so the stale tag must not survive)."""
    from confluid.engine import _KeyScope, _View

    v = _View()
    v.set("kept", 1, _KeyScope.EXACT)
    v.set("retagged", 2, _KeyScope.EXACT)
    v.set("cleared", 3, _KeyScope.EXACT)

    src = _View()
    src.set("retagged", 20, _KeyScope.STRICT)
    src["cleared"] = 30  # untagged (BARE) in the source

    v.update(src)
    assert v.scope_of("kept") is _KeyScope.EXACT  # untouched key keeps its tag
    assert v.scope_of("retagged") is _KeyScope.STRICT
    assert v.scope_of("cleared") is _KeyScope.BARE
    assert v == {"kept": 1, "retagged": 20, "cleared": 30}


def test_view_update_from_plain_sources_clears_tags() -> None:
    """A plain-dict / iterable-of-pairs / keyword source is untagged, so the
    updated keys become BARE."""
    from confluid.engine import _KeyScope, _View

    v = _View()
    v.set("a", 1, _KeyScope.EXACT)
    v.set("b", 2, _KeyScope.EXACT)
    v.set("c", 3, _KeyScope.EXACT)

    v.update({"a": 10})
    v.update(iter([("b", 20)]))  # one-shot iterator source
    v.update(c=30)
    assert v == {"a": 10, "b": 20, "c": 30}
    assert all(v.scope_of(k) is _KeyScope.BARE for k in ("a", "b", "c"))


# ---------------------------------------------------------------------------
# **kwargs constructors: unknowable accept-list -> permissive broadcasting
# ---------------------------------------------------------------------------


def test_var_keyword_class_receives_every_bare_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """A ``**kwargs`` constructor makes the accept-list ``None`` (unknowable),
    which the gates treat as accept-EVERYTHING — every bare top-level key
    broadcasts in. The permissive path announces itself once at TRACE."""
    from types import SimpleNamespace

    import confluid.engine as engine_module

    traces: List[str] = []
    real_logger = engine_module.logger
    monkeypatch.setattr(
        engine_module,
        "logger",
        SimpleNamespace(
            trace=lambda msg: traces.append(msg),
            debug=real_logger.debug,
            warning=real_logger.warning,
        ),
    )
    engine_module._acceptable_keys_cache.clear()

    @configurable(validate=False)
    class _CatchAll:
        def __init__(self, **kwargs: Any) -> None:
            self.options = dict(kwargs)

    graph = load(
        """
sink: !class:_CatchAll()
name: run-42
strength: 0.75
"""
    )
    sink = graph["sink"]
    assert sink.name == "run-42" and sink.strength == 0.75
    assert any("accept-list unknown" in msg and "_CatchAll" in msg for msg in traces)
