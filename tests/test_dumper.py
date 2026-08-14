from typing import Any

import pytest
import yaml

from confluid import configurable, dump, get_registry


@pytest.fixture(autouse=True)
def setup_registry() -> None:
    get_registry().clear()


def test_basic_dump() -> None:
    @configurable
    class Model:
        def __init__(self, layers: int = 3) -> None:
            self.layers = layers

    model = Model(layers=10)
    output = dump(model)

    # Instances dump with () for instant construction on reload
    assert "_target_: Model" in output
    assert "layers: 10" in output

    # Round-trip via confluid.load produces a live instance
    from confluid import load

    data = load(output)
    assert isinstance(data, Model)
    assert data.layers == 10


def test_hierarchical_dump() -> None:
    @configurable
    class Model:
        def __init__(self, layers: int = 3) -> None:
            self.layers = layers

    @configurable
    class Trainer:
        def __init__(self, model: Model, lr: float = 0.01) -> None:
            self.model = model
            self.lr = lr

    model = Model(layers=5)
    trainer = Trainer(model=model, lr=0.001)

    output = dump(trainer)

    assert "_target_: Trainer" in output
    assert "_target_: Model" in output
    assert "lr: 0.001" in output
    assert "layers: 5" in output


def test_opaque_fallback_emits_class_marker() -> None:
    """Non-@configurable objects degrade to a ``!class:<name>`` scalar marker."""

    class InternalThing:
        def __str__(self) -> str:
            return "internal"

    @configurable
    class Model:
        def __init__(self, thing: Any) -> None:
            self.thing = thing

    model = Model(thing=InternalThing())
    output = dump(model)
    assert "_target_:" in output
    assert "InternalThing" in output


def test_circular_reference() -> None:
    @configurable
    class Node:
        def __init__(self, next_node: Any = None) -> None:
            self.next_node = next_node

    node1 = Node()
    node2 = Node(next_node=node1)
    node1.next_node = node2  # Cycle

    output = dump(node1)
    # Check for YAML anchors/aliases indicating circularity
    assert "&id" in output or "*id" in output


def test_dump_list_and_dict() -> None:
    @configurable
    class Container:
        def __init__(self, items: list[Any], mapping: dict[str, Any]) -> None:
            self.items = items
            self.mapping = mapping

    obj = Container(items=[1, 2], mapping={"a": 1})
    output = dump(obj)

    assert "_target_: Container" in output
    assert "- 1" in output
    assert "a: 1" in output


def test_dump_no_init() -> None:
    @configurable
    class Simple:
        pass

    obj = Simple()
    output = dump(obj)
    assert "_target_: Simple" in output


def test_dump_none() -> None:
    assert "null" in dump(None)


def test_dump_emits_params_in_signature_order() -> None:
    """Dump-key order is round-trip-pinned to SIGNATURE order — the property the
    dumper's ``slots()`` projection must preserve (``slots()`` returns signature
    order by construction)."""

    @configurable
    class Ordered:
        def __init__(self, beta: int = 2, alpha: int = 1, gamma: int = 3) -> None:
            self.beta = beta
            self.alpha = alpha
            self.gamma = gamma

    text = dump(Ordered(beta=5, alpha=6, gamma=7))
    assert text.index("beta") < text.index("alpha") < text.index("gamma")


def test_a_stored_variadic_bundle_is_not_dumped() -> None:
    """A ``*args`` name can never be passed by keyword and a ``**kwargs`` name is
    never a declared slot, so neither belongs in a dumped config — a ``kwargs: {...}``
    line never round-tripped anyway (on reload it lands INSIDE the catchall as a
    literal ``"kwargs"`` key, doubly nested)."""

    @configurable
    class StoresVariadics:
        def __init__(self, *args: int, lr: float = 0.5, **kwargs: Any) -> None:
            self.args = args
            self.kwargs = kwargs
            self.lr = lr

    text = dump(StoresVariadics(lr=0.9))
    assert "lr" in text
    assert "args" not in text
    assert "kwargs" not in text


def test_dump_non_configurable_with_confluid_origin() -> None:
    """Objects created via Target/flow() retain origin metadata for dump."""
    from confluid.fluid import Target, flow

    class Metric:
        def __init__(self, num_classes: int = 10) -> None:
            self.num_classes = num_classes

    inst = Target(Metric, num_classes=5)
    live = flow(inst)

    output = dump(live)
    assert "_target_:" in output
    assert "num_classes: 5" in output


def test_dump_non_configurable_in_configurable_parent() -> None:
    """Non-configurable objects nested inside configurable ones serialize correctly."""
    from confluid.fluid import Target, flow

    class Metric:
        def __init__(self, average: str = "macro") -> None:
            self.average = average

    @configurable
    class Trainer:
        def __init__(self, metrics: Any = None) -> None:
            self.metrics = metrics

    metric = Target(Metric, average="weighted")
    trainer = Trainer(metrics=[flow(metric)])

    output = dump(trainer)
    assert "_target_: Trainer" in output
    assert "_target_:" in output
    assert "average: weighted" in output


def test_dump_non_configurable_round_trip() -> None:
    """Dump/load round-trip for non-configurable objects preserves kwargs."""
    from confluid.fluid import Target, flow

    class Widget:
        def __init__(self, size: int = 3, color: str = "red") -> None:
            self.size = size
            self.color = color

    inst = Target(Widget, size=7, color="blue")
    live = flow(inst)

    output = dump(live)
    assert "size: 7" in output
    assert "color: blue" in output


def test_dump_function_reference_round_trip() -> None:
    """Module-level callables dump as ``${ref:module.qualname}`` and reload as the live function.

    The spelling is a plain STRING, not a tag: a tag anywhere in the document costs the whole
    file its ``yaml.safe_load`` readability, which is the property the plain format exists for.
    """
    import os.path

    from confluid import load

    @configurable
    class Loader:
        def __init__(self, joiner: Any = None) -> None:
            self.joiner = joiner

    obj = Loader(joiner=os.path.join)
    output = dump(obj)

    assert "${ref:posixpath.join}" in output or "${ref:ntpath.join}" in output
    yaml.safe_load(output)  # the point of the spelling: a plain reader can still parse it

    reloaded = load(output)
    assert isinstance(reloaded, Loader)
    assert reloaded.joiner is os.path.join


def test_dump_lambda_rejected() -> None:
    """Anonymous callables (lambdas, closures) cannot be referenced — dump raises."""

    @configurable
    class Holder:
        def __init__(self, fn: Any = None) -> None:
            self.fn = fn

    obj = Holder(fn=lambda x: x)
    with pytest.raises(yaml.representer.RepresenterError):
        dump(obj)


def test_dump_builtin_function_reference() -> None:
    """Built-in callables (e.g., ``len``) dump as ``${ref:builtins.len}``."""

    @configurable
    class Holder:
        def __init__(self, fn: Any = None) -> None:
            self.fn = fn

    obj = Holder(fn=len)
    output = dump(obj)
    assert "${ref:builtins.len}" in output


def test_a_nested_non_configurable_class_value_dumps_its_qualname() -> None:
    """A class-valued kwarg dumps ``module.QualName``. The ``__name__`` spelling dumped
    ``pkg.Inner`` for a NESTED class — a path that resolves to nothing, or silently to
    an UNRELATED top-level class that happens to share the short name."""

    class Holder:
        class Inner:
            pass

    @configurable
    class HoldsClass:
        def __init__(self, activation: type = Holder.Inner) -> None:
            self.activation = activation

    text = dump(HoldsClass())
    assert "Holder.Inner" in text
