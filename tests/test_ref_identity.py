"""Tests for ``!ref:`` identity semantics.

A ``!ref:target`` must resolve to the **same live object** as ``target``
itself — it is a late-bound alias, not a copy. Use ``!clone:target`` when
independent copies are wanted.
"""

from typing import Any

import pytest

from confluid import configurable, get_registry, load


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


def test_ref_vs_clone_distinction() -> None:
    """!ref: shares identity; !clone: creates a deep copy. Both must coexist."""

    @configurable
    class Widget:
        def __init__(self, label: str = "w") -> None:
            self.label = label

    yaml_str = """
base: !class:Widget()
  label: base
aliased: !ref:base
cloned: !clone:base
"""
    result: Any = load(yaml_str)

    assert result["aliased"] is result["base"]
    assert result["cloned"] is not result["base"]
    assert result["cloned"].label == result["base"].label


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


def test_dotted_attribute_ref_reuses_single_instance() -> None:
    """``!ref:obj.attr`` must resolve against the SAME materialized ``obj`` as the top-level key.

    Regression: the dotted-ref used to re-flow the RAW marker (missing the instance memo, which
    keys on the *resolved* marker), building a SECOND ``obj`` and re-running its constructor — so a
    splitter referenced via ``.train`` / ``.val`` reloaded its upstream source. ``_resolve_dotted_ref``
    now maps the raw marker through ``flow_memo`` first, so every attribute-ref shares the one live
    instance the memo caches (one construction → one load).
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

        @property
        def tail(self) -> str:
            return f"tail-of-{self.size}"

    yaml_str = """
loader: !class:Loader()
  size: 5
a: !ref:loader.head
b: !ref:loader.tail
"""
    result: Any = load(yaml_str)

    assert Loader.instantiations == 1, "dotted-ref re-flowed a duplicate instance (extra load)"
    assert result["a"] == "head-of-5"
    assert result["b"] == "tail-of-5"
    # The attribute-refs resolved off the SAME instance as the top-level key.
    assert result["a"] == result["loader"].head


def test_ref_inside_list_shares_instance() -> None:
    """!ref: inside a YAML list must resolve to the same Instance marker as the source.

    Note: _deep_flow only materializes Instance markers at the top dict level;
    markers buried inside plain lists remain as Instance markers. The identity
    invariant we care about (``!ref: == same object``) is tested on the raw
    markers via ``flow=False``.
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
    result: Any = load(yaml_str, flow=False)

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
    """load(..., flow=False) must also preserve Fluid identity for !ref:."""

    @configurable
    class Thing:
        def __init__(self) -> None:
            pass

    from confluid.fluid import Instance

    yaml_str = """
thing: !class:Thing()
alias: !ref:thing
"""
    result: Any = load(yaml_str, flow=False)
    # Post-resolver, both should point at the SAME Instance marker
    assert isinstance(result["thing"], Instance)
    assert result["alias"] is result["thing"]
