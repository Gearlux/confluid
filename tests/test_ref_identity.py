"""Tests for ``!ref:`` identity semantics.

A ``!ref:target`` must resolve to the **same live object** as ``target``
itself — it is a late-bound alias, not a copy (Clone is removed; write the
marker again when an independent copy is wanted).
"""

from typing import Any

import pytest

from confluid import ConfigurationError, configurable, get_registry, load


@pytest.fixture(autouse=True)
def setup_registry() -> None:
    get_registry().clear()


def test_ref_returns_same_instance() -> None:
    """Two !ref:foo uses must be ``is`` the same object as ``foo``."""

    @configurable
    class Counter:
        instantiations = 0

        def __init__(self, name: str = "x") -> None:
            Counter.instantiations += 1
            self.name = name

    yaml_str = """
shared: !class:Counter()
  name: one
user1: !ref:shared
user2: !ref:shared
"""
    result: Any = load(yaml_str)

    assert Counter.instantiations == 1, "Expected one construction; got extra copies via !ref:"
    assert result["user1"] is result["shared"]
    assert result["user2"] is result["shared"]


def test_ref_preserves_mutation_across_aliases() -> None:
    """Mutating the object through one alias must be visible through the others."""

    @configurable
    class Box:
        def __init__(self, value: int = 0) -> None:
            self.value = value

    yaml_str = """
box: !class:Box()
  value: 1
alias1: !ref:box
alias2: !ref:box
"""
    result: Any = load(yaml_str)

    result["alias1"].value = 42
    assert result["box"].value == 42
    assert result["alias2"].value == 42


def test_ref_inside_kwargs_shares_instance() -> None:
    """!ref: used inside a class kwarg must point at the same live object."""

    @configurable
    class Service:
        def __init__(self, name: str = "svc") -> None:
            self.name = name

    @configurable
    class Consumer:
        def __init__(self, service: Any = None) -> None:
            self.service = service

    yaml_str = """
service: !class:Service()
  name: shared
first: !class:Consumer()
  service: !ref:service
second: !class:Consumer()
  service: !ref:service
"""
    result: Any = load(yaml_str)

    assert result["first"].service is result["service"]
    assert result["second"].service is result["service"]
    assert result["first"].service is result["second"].service


def test_self_referential_kwarg_raises_informative_error() -> None:
    """A kwarg ``foo: !ref:foo`` with no outer ``foo`` would loop forever
    (the kwarg splices itself into scope as the only ``foo``). Confluid
    must detect this and raise a clear, actionable error instead of
    stack-overflowing.
    """

    @configurable
    class Widget:
        def __init__(self, checkpoint_path: Any = None) -> None:
            self.checkpoint_path = checkpoint_path

    yaml_str = """
widget: !class:Widget()
  checkpoint_path: !ref:checkpoint_path
"""
    with pytest.raises(ValueError, match=r"Self-referential !ref:checkpoint_path"):
        load(yaml_str)


def test_self_referential_kwarg_with_outer_value_resolves_normally() -> None:
    """When the outer scope DOES define the target, the kwarg-with-same-name
    pattern must still work — it's just an alias, not a self-reference."""

    @configurable
    class Widget:
        def __init__(self, checkpoint_path: Any = None) -> None:
            self.checkpoint_path = checkpoint_path

    yaml_str = """
checkpoint_path: /tmp/model.ckpt
widget: !class:Widget()
  checkpoint_path: !ref:checkpoint_path
"""
    result: Any = load(yaml_str)
    assert result["widget"].checkpoint_path == "/tmp/model.ckpt"


def test_ref_does_not_re_instantiate_even_with_many_aliases() -> None:
    """Heavy-handed case: 10 references, 1 instantiation."""

    @configurable
    class HeavyResource:
        count = 0

        def __init__(self) -> None:
            HeavyResource.count += 1

    yaml_str = "root: !class:HeavyResource()\n"
    yaml_str += "\n".join(f"alias{i}: !ref:root" for i in range(10))

    result: Any = load(yaml_str)

    assert HeavyResource.count == 1
    for i in range(10):
        assert result[f"alias{i}"] is result["root"]


def test_a_dotted_attribute_ref_is_refused_not_resolved() -> None:
    """``!ref:obj.attr`` — the attribute reference — is REMOVED (record 19, phase 2).

    This test used to pin that ``!ref:loader.head`` / ``.tail`` resolved off ONE shared
    ``loader`` (a regression where the dotted ref re-flowed the raw marker). The sharing
    guarantee is now carried by the whole-object ref alone: reference ``!ref:loader`` and read
    the attribute on the consumer side. The old spelling is refused, located, naming that.
    """

    @configurable
    class Loader:
        instantiations = 0

        def __init__(self, size: int = 3) -> None:
            Loader.instantiations += 1
            self.size = size

        @property
        def head(self) -> str:
            return f"head-of-{self.size}"

    Loader.instantiations = 0
    with pytest.raises(ConfigurationError) as exc:
        load("loader: !class:Loader()\n  size: 5\na: !ref:loader.head\n")
    assert "ATTRIBUTE `head`" in str(exc.value) and "!ref:loader" in str(exc.value)
    assert Loader.instantiations == 0, "refused before anything is built"

    # The whole-object ref still shares ONE instance across every site.
    graph = load("loader: !class:Loader()\n  size: 5\na: !ref:loader\nb: !ref:loader\n")
    assert graph["a"] is graph["b"] is graph["loader"] and Loader.instantiations == 1


def test_ref_inside_list_shares_instance() -> None:
    """!ref: inside a YAML list must resolve to the same Target marker as the source.

    Note: _deep_flow only materializes Target markers at the top dict level;
    markers buried inside plain lists remain as Target markers. The identity
    invariant we care about (``!ref: == same object``) is tested on the raw
    markers via ``until="document"``.
    """

    @configurable
    class Node:
        def __init__(self, name: str = "n") -> None:
            self.name = name

    yaml_str = """
node: !class:Node()
  name: only
roster:
  - !ref:node
  - !ref:node
"""
    result: Any = load(yaml_str, until="document")

    assert result["roster"][0] is result["node"]
    assert result["roster"][1] is result["node"]


def test_ref_on_non_configurable_value_returns_same_value() -> None:
    """!ref: on a scalar or plain list must produce the same Python object."""
    yaml_str = """
numbers: [1, 2, 3]
aliased: !ref:numbers
"""
    result: Any = load(yaml_str)
    # Plain containers may be deepcopied during expansion, but value equality holds
    assert result["aliased"] == result["numbers"]


def test_ref_preserves_identity_without_flow() -> None:
    """load(..., until="document") must also preserve Fluid identity for !ref:."""

    @configurable
    class Thing:
        def __init__(self) -> None:
            pass

    from confluid.fluid import Target

    yaml_str = """
thing: !class:Thing()
alias: !ref:thing
"""
    result: Any = load(yaml_str, until="document")
    # Post-resolver, both should point at the SAME Target marker
    assert isinstance(result["thing"], Target)
    assert result["alias"] is result["thing"]


def test_sibling_list_items_do_not_share_an_instance_via_recycled_ids() -> None:
    """Distinct markers in one list must build distinct objects.

    Both engine memos key on ``id(marker)``, which is unique only while the marker
    is alive. The engine builds short-lived broadcast COPIES, and CPython reuses a
    freed object's address — so without a keepalive the second list item's copy can
    land on the first one's address and read as a memo HIT, handing back the wrong
    instance. Measured before the fix: every stage of a pipeline came back as the
    first stage.
    """
    from typing import List, Optional

    from confluid import configurable, load

    @configurable
    class _Leaf:
        def __init__(self, tag: str = "") -> None:
            self.tag = tag

    @configurable
    class _Node:
        def __init__(self, name: str = "", leaf: Optional[_Leaf] = None) -> None:
            self.name, self.leaf = name, leaf

    @configurable
    class _Root:
        def __init__(self, nodes: Optional[List[_Node]] = None) -> None:
            self.nodes = nodes or []

    root = load(
        """
root: !class:_Root()
  nodes:
    - !class:_Node()
      name: a
      leaf: !class:_Leaf(tag=a)
    - !class:_Node()
      name: b
      leaf: !class:_Leaf(tag=b)
    - !class:_Node()
      name: c
      leaf: !class:_Leaf(tag=c)
"""
    )["root"]

    assert [n.name for n in root.nodes] == ["a", "b", "c"]
    assert [n.leaf.tag for n in root.nodes] == ["a", "b", "c"]
    assert len({id(n) for n in root.nodes}) == 3


# ---------------------------------------------------------------------------
# Kwargs on a reference TUNE the shared referent (user ruling 2026-08-19, option A —
# BUGS-2026-08-19 PA10 / BC8 / SR9). They are folded into the referent marker's own
# kwargs before pass 7 (the same thing `proto.k: 5` does), so they compete at the
# referent's position like any own kwarg; every spelling lands — the mapping form,
# the tag with a body, a reference nested in a marker's kwargs, and a dotted path
# walking through a reference. Before: every one of them vanished, report `unused=[]`.
# ---------------------------------------------------------------------------


@configurable
class _S:
    def __init__(self, v: int = 1, k: int = 0) -> None:
        self.v = v
        self.k = k


@configurable
class _Opt:
    def __init__(self, lr: float = 0.0) -> None:
        self.lr = lr


@configurable
class _Trainer:
    def __init__(self, optimizer: Any = None) -> None:
        self.optimizer = optimizer


def _register_ref_fixtures() -> None:
    from confluid import register

    for cls in (_S, _Opt, _Trainer):
        register(cls)


def test_the_tag_spelling_keeps_a_reference_body_at_parse() -> None:
    """`!ref:proto` with a mapping body used to discard the body entirely."""
    raw = load("proto: !class:_S {v: 1}\nuse: !ref:proto\n  k: 5\n", until="raw")
    assert raw["use"].kwargs == {"k": 5}


@pytest.mark.parametrize(
    "label, doc",
    [
        ("mapping form, top level", "proto: !class:_S {v: 1}\nuse: {_ref_: proto, k: 5}\n"),
        ("tag + body, top level", "proto: !class:_S {v: 1}\nuse: !ref:proto\n  k: 5\n"),
        ("${ref:} plus dotted kwarg", "proto: !class:_S {v: 1}\nuse: ${ref:proto}\nuse.k: 5\n"),
    ],
)
def test_reference_kwargs_tune_the_shared_referent(label: str, doc: str) -> None:
    _register_ref_fixtures()
    r = load(doc)
    assert r["use"] is r["proto"], label
    assert (r["proto"].v, r["proto"].k) == (1, 5), label


def test_reference_kwargs_inside_a_markers_kwargs_tune_the_referent() -> None:
    _register_ref_fixtures()
    r = load("proto: !class:_S {v: 1}\nhost: !class:_Trainer\n  optimizer: {_ref_: proto, k: 5}\n")
    assert r["host"].optimizer is r["proto"]
    assert r["proto"].k == 5


def test_a_dotted_path_THROUGH_a_reference_tunes_the_referent() -> None:
    """`a.optimizer.lr: 9.0` where `a.optimizer` is `!ref:shared` — the dotted walk
    lands on the Reference's kwargs (pass 6); the fold carries them to `shared`."""
    _register_ref_fixtures()
    doc = "shared: !class:_Opt {lr: 1.0}\na: !class:_Trainer\n  optimizer: !ref:shared\na.optimizer.lr: 9.0\n"
    r = load(doc)
    assert r["a"].optimizer is r["shared"]
    assert r["shared"].lr == 9.0


def test_two_references_tuning_one_referent_last_writer_wins_and_both_land() -> None:
    _register_ref_fixtures()
    r = load("proto: !class:_S\nx: {_ref_: proto, v: 3}\ny: {_ref_: proto, v: 4, k: 7}\n")
    assert r["x"] is r["y"] is r["proto"]
    assert (r["proto"].v, r["proto"].k) == (4, 7)


def test_folded_reference_kwargs_compete_at_the_REFERENTS_position() -> None:
    """Not a second precedence rule: the kwargs sit in `proto`'s own kwargs, so a
    bare key written AFTER `proto` still wins and one written BEFORE still loses —
    exactly as for `proto.v: 5`."""
    _register_ref_fixtures()
    later_bare = load("proto: !class:_S {v: 1}\nuse: {_ref_: proto, v: 5}\nv: 9\n")
    assert later_bare["proto"].v == 9
    earlier_bare = load("v: 9\nproto: !class:_S {v: 1}\nuse: {_ref_: proto, v: 5}\n")
    assert earlier_bare["proto"].v == 5


def test_a_reference_without_kwargs_is_unchanged() -> None:
    _register_ref_fixtures()
    r = load("proto: !class:_S {v: 1}\nuse: {_ref_: proto}\nb: !ref:proto\n")
    assert r["use"] is r["proto"] is r["b"]
    assert (r["proto"].v, r["proto"].k) == (1, 0)


def test_kwargs_on_a_reference_to_a_PLAIN_VALUE_are_refused_with_a_location() -> None:
    """A plain value has no kwargs to tune — the con case; it must not silently drop."""
    with pytest.raises(ConfigurationError, match=r"lr.*plain value|plain value.*lr") as info:
        load("lr: 0.1\nuse: {_ref_: lr, k: 5}\n")
    assert "<unicode string>:2:6" in str(info.value)


def test_reference_kwargs_survive_the_document_stage_idempotently() -> None:
    """`load(load(x, until="document")) == load(x)` — the fold happens once."""
    _register_ref_fixtures()
    text = "proto: !class:_S {v: 1}\nuse: {_ref_: proto, k: 5}\n"
    once = load(text)
    twice = load(load(text, until="document"))
    assert (once["proto"].v, once["proto"].k) == (twice["proto"].v, twice["proto"].k) == (1, 5)


# ---------------------------------------------------------------------------
# ENG-3 (BUGS-2026-08-19) — a referenced node that FAILS to construct fails the
# load; only a genuinely unresolvable reference stays deferred.
# ---------------------------------------------------------------------------


def test_a_referenced_nodes_constructor_failure_propagates() -> None:
    """`except ValueError` swallowed the referent's own crash (ConfigurationError
    IS a ValueError) and silently left the Reference in the slot."""
    from confluid import Target, active_context, flow
    from confluid.fluid import Reference

    class _BadCtor:
        def __init__(self, x: int = 0) -> None:
            raise ValueError("disk is on fire")

    @configurable
    class _Holder:
        def __init__(self, child: Any = None) -> None:
            self.child = child

    with active_context({"a": Target(_BadCtor)}):
        with pytest.raises(ValueError, match="disk is on fire"):
            flow(Target(_Holder, child=Reference("a")))


def test_a_reference_to_an_UNKNOWN_class_propagates() -> None:
    from confluid import Target, active_context, flow
    from confluid.exceptions import UnknownClassError
    from confluid.fluid import Reference

    @configurable
    class _Holder2:
        def __init__(self, child: Any = None) -> None:
            self.child = child

    with active_context({"a": Target("no.such.module.Cls")}):
        with pytest.raises(UnknownClassError):
            flow(Target(_Holder2, child=Reference("a")))


def test_a_genuinely_unresolvable_reference_is_still_kept_deferred() -> None:
    """The con: the narrow catch — a miss stays a Reference for a later flow."""
    from confluid import Target, active_context, flow
    from confluid.fluid import Reference

    @configurable
    class _Holder3:
        def __init__(self, child: Any = None) -> None:
            self.child = child

    with active_context({"unrelated": 1}):
        host = flow(Target(_Holder3, child=Reference("missing_key")))
    assert isinstance(host.child, Reference)


def test_a_dotted_ref_to_a_NULL_value_is_the_value_not_an_attribute_refusal() -> None:
    """SR6 — `!ref:cfg.x` with `cfg: {x: null}` was refused as "reads the ATTRIBUTE
    `x` of the object built at `cfg`" — an object that does not exist. A structural
    walk that FOUND null is a hit."""

    @configurable
    class _NullHost:
        def __init__(self, v: Any = 1) -> None:
            self.v = v

    built = load("cfg: {x: null}\nuse: {_target_: _NullHost, v: {_ref_: cfg.x}}\n")
    assert built["use"].v is None
    assert load("cfg: {x: 0}\nuse: {_ref_: cfg.x}\n")["use"] == 0, "falsy-but-present still resolves (con)"


def test_a_genuine_attribute_reference_is_still_refused() -> None:
    """The con for SR6: the refusal fires for a walk that LEAVES structure, not for
    a structural hit on a null."""

    @configurable
    class _SplitLike:
        def __init__(self, v: int = 2) -> None:
            self.v = v

    with pytest.raises(ConfigurationError, match="reads the ATTRIBUTE"):
        load("split: {_target_: _SplitLike, v: 2}\nt: {_ref_: split.train}\n")


def test_a_dotted_ref_into_an_int_keyed_table_resolves() -> None:
    """SR7 — `!ref:class_names[1]` on `{1: DJI}` was refused as an attribute ref."""
    built = load("class_names: {1: DJI, 2: MAVIC}\nuse: {_ref_: 'class_names[1]'}\n")
    assert built["use"] == "DJI"
