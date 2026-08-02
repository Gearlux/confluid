from typing import Any

import pytest

import confluid
from confluid import Instance, configurable, flow, load, materialize


def _inst(target: str, /, **kwargs: Any) -> Instance:
    """Build an Instance marker with kwargs assigned post-construction.

    ``target`` is positional-only so test kwargs literally named ``name`` or
    ``target`` can't collide with it."""
    marker = Instance(target)
    marker.kwargs.update(kwargs)
    return marker


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

    # raw load keeps the plain hierarchy untouched
    data = {"Model": {"layers": 15}}
    config_data = load(data, flow=False)

    # Explicit materialize of an Instance marker built from the block
    instance = materialize(_inst("Model", **config_data["Model"]))
    assert isinstance(instance, Model)
    assert instance.layers == 15


def test_materialize_shorthand() -> None:
    @configurable
    class Simple:
        def __init__(self, val: int = 0) -> None:
            self.val = val

    # materialize accepts Instance markers
    obj = materialize(_inst("Simple", val=42))
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

    instance = materialize(_inst("Backbone", width=5))
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


def test_flow_idempotent_returns_the_same_object_and_refires_the_hook() -> None:
    """flow() on a live instance returns THAT object, and the (cached) hook fires again.

    Identity is the idempotency that matters — never a second object. The hook
    re-firing is deliberate: ``flow()`` cannot tell an object it solidified from
    one that arrived live and unbuilt, so it calls ``solidify()`` either way and
    the method is expected to be build-once-and-cache. See
    ``test_flow_solidifies_a_live_object`` for the case that forces it.
    """

    @configurable
    class Counter:
        def __init__(self) -> None:
            self.solidify_count = 0

        def solidify(self) -> None:
            self.solidify_count += 1

    instance = flow("!class:Counter()")
    assert instance.solidify_count == 1

    assert flow(instance) is instance
    assert instance.solidify_count == 2


def test_flow_solidifies_a_live_object() -> None:
    """A live object handed to flow() is BUILT — the case the pass-through exists for.

    A model wired ``!class:Model()`` (or constructed in Python) never went through
    the marker path, so its lazy state was never finalized. Without this the
    failure lands far away: an optimizer flowed with ``params=`` gets an empty
    parameter list.
    """

    @configurable
    class Backbone:
        def __init__(self, width: int = 4) -> None:
            self.width = width
            self.params: list[int] | None = None

        def solidify(self) -> list[int]:
            if self.params is None:
                self.params = list(range(self.width))
            return self.params

    live = Backbone(width=3)
    assert live.params is None  # a bare constructor does no functional work

    assert flow(live) is live
    assert live.params == [0, 1, 2]


def test_flow_solidify_false_leaves_a_live_object_unbuilt() -> None:
    """The suppression flag governs the live pass-through too (cheap introspection)."""

    @configurable
    class Backbone:
        def __init__(self) -> None:
            self.built = False

        def solidify(self) -> None:
            self.built = True

    live = Backbone()
    assert flow(live, solidify=False) is live
    assert live.built is False


# ---- positional runtime args ------------------------------------------------ #
# A marker carries kwargs only (a YAML tag can express nothing else), but a target
# may take its inputs POSITIONALLY. The forcing case is a VARIADIC signature —
# `DataLoaders(*loaders, path=…, device=…)` — where the inputs have no keyword to
# arrive under at all, so the slot could not be deferred without this.


class _Bundle:
    """A variadic target: the positional half has no keyword to arrive under."""

    def __init__(self, *items: Any, label: str = "", tag: str = "") -> None:
        self.items = list(items)
        self.label = label
        self.tag = tag


def test_flow_passes_positional_runtime_args_to_the_target() -> None:
    marker = confluid.LazyClass(_Bundle, label="stored")
    built = flow(marker, "a", "b")
    assert built.items == ["a", "b"]
    assert built.label == "stored"


def test_positional_args_compose_with_stored_and_runtime_kwargs() -> None:
    marker = confluid.LazyClass(_Bundle, label="stored", tag="stored")
    built = flow(marker, 1, 2, tag="runtime")
    assert (built.items, built.label, built.tag) == ([1, 2], "stored", "runtime")


def test_a_var_positional_name_is_never_passed_as_a_keyword() -> None:
    """``items`` matches the ``*items`` parameter NAME but can only arrive positionally.

    Without the VAR_POSITIONAL filter in ``_ctor_params`` this key survives the
    ctor filter and the call dies with ``got some positional-only arguments
    passed as keyword arguments``.
    """
    built = flow(confluid.LazyClass(_Bundle, items=["ignored"]), "real")
    assert built.items == ["real"]


def test_positional_args_are_dropped_for_an_already_live_object() -> None:
    """Same convention as runtime kwargs: the object is built, so the extras cannot apply."""
    live = _Bundle("original")
    assert flow(live, "ignored") is live
    assert live.items == ["original"]


def test_positional_args_reach_a_bare_unregistered_type() -> None:
    assert flow(_Bundle, "x", label="L").items == ["x"]


def test_positional_args_on_a_configurable_bare_type_raise() -> None:
    """A registry-configurable type materializes THROUGH a marker, which is kwargs-only."""

    @configurable
    class Registered:
        def __init__(self, *items: Any) -> None:
            self.items = list(items)

    with pytest.raises(confluid.ConstructionError, match="keyword arguments only"):
        flow(Registered, "a")


def test_positional_args_survive_the_solidify_suppression_re_entry() -> None:
    marker = confluid.LazyClass(_Bundle, label="L")
    assert flow(marker, "a", "b", solidify=False).items == ["a", "b"]
