import pytest

import confluid
from confluid import configurable, flow, load, materialize


@pytest.fixture(autouse=True)
def setup_registry() -> None:
    confluid.get_registry().clear()


def test_basic_flow_idempotent() -> None:
    @configurable
    class Model:
        def __init__(self, layers: int = 3) -> None:
            self.layers = layers

    # flow on already-live instance returns it unchanged
    model = Model(layers=10)
    assert flow(model).layers == 10


def test_flow_string_reference() -> None:
    @configurable
    class Model:
        def __init__(self, layers: int = 3) -> None:
            self.layers = layers

    # flow resolves !class: patterns
    instance = flow("!class:Model(layers=20)")
    assert instance.layers == 20
    assert isinstance(instance, Model)


def test_load_hierarchy() -> None:
    @configurable
    class Model:
        def __init__(self, layers: int = 3) -> None:
            self.layers = layers

    # raw load returns dictionary markers in Dictionary-First pattern
    data = {"Model": {"layers": 15}}
    config_data = load(data, flow=False)

    # Explicit materialize to get the instance
    instance = materialize({"_confluid_class_": "Model", **config_data["Model"]})
    assert isinstance(instance, Model)
    assert instance.layers == 15


def test_materialize_shorthand() -> None:
    @configurable
    class Simple:
        def __init__(self, val: int = 0) -> None:
            self.val = val

    # materialize accepts flat markers
    obj = materialize({"_confluid_class_": "Simple", "val": 42})
    assert obj.val == 42


def test_flow_auto_solidify_called() -> None:
    """flow() invokes solidify() on the constructed instance (lazy finalization)."""

    @configurable
    class Backbone:
        def __init__(self, width: int = 8) -> None:
            self.width = width
            self.params: list[int] | None = None  # built lazily by solidify()

        def solidify(self) -> None:
            # Materialize derived state only once construction is complete.
            self.params = list(range(self.width))

    instance = flow("!class:Backbone(width=4)")
    assert isinstance(instance, Backbone)
    # solidify() ran automatically — params are populated post-flow.
    assert instance.params == [0, 1, 2, 3]


def test_flow_auto_solidify_via_materialize() -> None:
    """Auto-solidification also fires on the materialize() marker path."""

    @configurable
    class Backbone:
        def __init__(self, width: int = 2) -> None:
            self.width = width
            self.solidified = False

        def solidify(self) -> None:
            self.solidified = True

    instance = materialize({"_confluid_class_": "Backbone", "width": 5})
    assert instance.solidified is True


def test_flow_no_solidify_method_ok() -> None:
    """A class without solidify() flows cleanly (the hook is optional)."""

    @configurable
    class Plain:
        def __init__(self, val: int = 0) -> None:
            self.val = val

    instance = flow("!class:Plain(val=7)")
    assert instance.val == 7


def test_flow_non_callable_solidify_skipped() -> None:
    """A non-callable ``solidify`` attribute is ignored, not invoked."""

    @configurable
    class HasAttr:
        def __init__(self) -> None:
            # ``solidify`` here is data, not a method — flow() must not call it.
            self.solidify = "not a method"

    instance = flow("!class:HasAttr()")
    assert instance.solidify == "not a method"


def test_flow_idempotent_does_not_resolidify() -> None:
    """flow() on an already-live instance returns it without re-running solidify()."""

    @configurable
    class Counter:
        def __init__(self) -> None:
            self.solidify_count = 0

        def solidify(self) -> None:
            self.solidify_count += 1

    instance = flow("!class:Counter()")
    assert instance.solidify_count == 1

    # Re-flowing a live object is idempotent (passes through, no re-solidify).
    assert flow(instance) is instance
    assert instance.solidify_count == 1
