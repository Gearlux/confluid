from typing import Any

import pytest

import confluid
from confluid import Instance, configurable, flow, get_registry, load, materialize


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
    marker = confluid.PartialClass(_Bundle, label="stored")
    built = flow(marker, "a", "b")
    assert built.items == ["a", "b"]
    assert built.label == "stored"


def test_positional_args_compose_with_stored_and_runtime_kwargs() -> None:
    marker = confluid.PartialClass(_Bundle, label="stored", tag="stored")
    built = flow(marker, 1, 2, tag="runtime")
    assert (built.items, built.label, built.tag) == ([1, 2], "stored", "runtime")


def test_a_var_positional_name_is_never_passed_as_a_keyword() -> None:
    """``items`` matches the ``*items`` parameter NAME but can only arrive positionally.

    Without the VAR_POSITIONAL filter in ``_ctor_params`` this key survives the
    ctor filter and the call dies with ``got some positional-only arguments
    passed as keyword arguments``.
    """
    built = flow(confluid.PartialClass(_Bundle, items=["ignored"]), "real")
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
    marker = confluid.PartialClass(_Bundle, label="L")
    assert flow(marker, "a", "b", solidify=False).items == ["a", "b"]


# ---- `**kwargs` targets: a runtime kwarg is a call ARGUMENT ------------------ #
# A `**kwargs` signature names no parameter for a runtime kwarg, so the constructor
# filter dropped every one of them and built the target with NOTHING. The config half
# is deliberately untouched: a bare broadcast key still lands as a post-init attribute
# on a `**kwargs` @configurable class (docs/broadcasting.md, pinned in
# test_broadcast_scoping.py and examples/broadcasting.py).


class _Forwarder:
    """The real-world shape: a subclass declaring ``**kwargs`` and forwarding them on."""

    def __init__(self, **kwargs: Any) -> None:
        self.received = dict(kwargs)


def test_a_runtime_kwarg_reaches_a_var_keyword_constructor() -> None:
    built = flow(confluid.PartialClass(_Forwarder), model="resnet", batch_size=4)

    assert built.received == {"model": "resnet", "batch_size": 4}


def test_a_var_keyword_constructor_used_to_be_built_with_nothing() -> None:
    """The regression in the terms it was found in: the object must not come back empty.

    A ``transformers.Trainer`` subclass declaring ``def __init__(self, **kwargs)`` was
    constructed with no arguments and died as *"`Trainer` requires either a `model` or
    `model_init` argument"* — a message pointing nowhere near confluid.
    """

    class _NeedsAModel:
        def __init__(self, **kwargs: Any) -> None:
            if "model" not in kwargs:
                raise RuntimeError("requires either a `model` or `model_init` argument")
            self.model = kwargs["model"]

    assert flow(confluid.PartialClass(_NeedsAModel), model="a-model").model == "a-model"


def test_a_declared_param_and_an_extra_both_arrive() -> None:
    """The mixed signature: the named parameter binds, the rest ride ``**kwargs``."""

    class _Mixed:
        def __init__(self, name: str = "", **kwargs: Any) -> None:
            self.name, self.extra = name, dict(kwargs)

    built = flow(confluid.PartialClass(_Mixed), name="run", depth=3)

    assert built.name == "run" and built.extra == {"depth": 3}


def test_a_runtime_kwarg_is_not_ALSO_set_as_an_attribute() -> None:
    """Taken by the constructor means taken — never applied twice.

    ``_apply_post_init_attrs`` gates on what the ctor actually received rather than on
    the declared parameter names, which is what keeps the two from diverging here.
    """

    @configurable(validate=False)
    class _Recording:
        def __init__(self, **kwargs: Any) -> None:
            self.options = dict(kwargs)
            self.assigned_after: list = []

        def __setattr__(self, name: str, value: Any) -> None:
            # `__confluid_*` is the engine's own round-trip bookkeeping, not a config key.
            if "assigned_after" in self.__dict__ and not name.startswith("__confluid_"):
                self.assigned_after.append(name)
            object.__setattr__(self, name, value)

    built = flow(confluid.PartialClass(_Recording), model="resnet")

    assert built.options == {"model": "resnet"}
    assert built.assigned_after == [], "the ctor took it; a post-init setattr would double-apply"


def test_a_kwarg_written_ON_the_marker_reaches_the_constructor() -> None:
    """``!lazy:Forwarder(tag=…)`` is the author saying what to BUILD this node with.

    A forwarding subclass would otherwise never see it — the value would land as an
    attribute on the built object and quietly do nothing.
    """
    built = flow(confluid.PartialClass(_Forwarder, tag="from-config"))

    assert built.received == {"tag": "from-config"}


def test_a_BARE_broadcast_key_does_not_reach_the_constructor() -> None:
    """The other half of the addressing rule, and the reason it is a rule.

    A ``**kwargs`` class has an unknowable accept-list, so confluid errs permissive and
    EVERY bare top-level key reaches it. Feeding those to the constructor would turn
    permissive broadcasting into "called with whatever the document happens to
    contain" — so they keep landing as post-init attributes.
    """

    @configurable(validate=False)
    class _Passthrough:
        def __init__(self, **kwargs: Any) -> None:
            self.options = dict(kwargs)

    graph = load(
        """
sink: !class:_Passthrough(tag=addressed)
name: run-42
"""
    )
    sink = graph["sink"]

    assert sink.options == {"tag": "addressed"}, "the marker's own kwarg is an argument"
    assert sink.name == "run-42", "the bare cascading key is an attribute"
    assert "name" not in sink.options


def test_a_plain_class_is_unaffected_by_the_var_keyword_path() -> None:
    """No `**kwargs`, so a runtime kwarg outside the signature still becomes an attribute."""

    @configurable(validate=False)
    class _Declared:
        def __init__(self, lr: float = 0.1) -> None:
            self.lr = lr

    built = flow(confluid.PartialClass(_Declared), lr=0.5, note="extra")

    assert built.lr == 0.5
    assert built.note == "extra"


# --- a zero-parameter constructor is configurable ---------------------------- #


def test_a_zero_parameter_constructor_still_takes_config() -> None:
    """`def __init__(self)` + body slots must be configurable, not a TypeError.

    The class-design convention encourages a MINIMAL constructor with the
    dependencies as `__init__`-body slots. Taken to its limit that is no parameters
    at all — and such a class died on any config key with
    `TypeError: got an unexpected keyword argument`, from inside its own
    constructor, which points nowhere near the config that caused it.

    Cause: `_ctor_params` returned a plain `set()` both when the signature could not
    be READ and when it was read and found EMPTY. Those need opposite handling —
    pass everything vs pass nothing — and the caller's truthiness fallback picked
    the wrong one for the second.
    """

    @configurable
    class ZeroArgHost:
        def __init__(self) -> None:
            self.slot: Any = None

    get_registry().register_class(ZeroArgHost, name="ZeroArgHost")

    obj = load("o: !class:ZeroArgHost()\n  slot: 42\n")["o"]

    assert obj.slot == 42


def test_an_unreadable_signature_still_receives_every_kwarg() -> None:
    """The other side of the same distinction — do not fix one by breaking it.

    When `inspect.signature` raises there is no accept-list to filter with, so the
    best effort is to pass everything and let the call decide. `_UNKNOWN_PARAMS`
    carries that state; a plain empty set cannot, which is the whole point.

    Asserted against the sentinel rather than a class with an unreadable signature:
    every builtin checked (dict/list/int/object/memoryview/range) introspects fine
    under CPython 3.12, so a "realistic" fixture would silently stop exercising the
    branch. Pinning the two properties the caller depends on — identity is
    distinguishable, and it still behaves as a set — is what actually holds.
    """
    from confluid.engine import _UNKNOWN_PARAMS, _ctor_params

    # A genuinely readable zero-arg signature is NOT the unknown sentinel.
    assert _ctor_params(ZeroArgReadable) is not _UNKNOWN_PARAMS
    assert _ctor_params(ZeroArgReadable) == set()

    # ...and the sentinel is still an ordinary set for every reader of it.
    assert isinstance(_UNKNOWN_PARAMS, set)
    assert "anything" not in _UNKNOWN_PARAMS


@configurable
class ZeroArgReadable:
    """Module-level so its signature is introspectable the normal way."""

    def __init__(self) -> None:
        self.slot: Any = None


def test_a_class_with_no_callable_init_is_left_unbuilt() -> None:
    """`_ctor_params` returning None is NOT dead code — pinned so it survives a cleanup.

    Every class inherits `object.__init__`, so the branch reads as unreachable. It is
    not: `__init__ = None` is a legal class attribute and `getattr` then returns
    `None`. Such a class cannot be constructed by anyone (`Nulled()` raises
    `TypeError: 'NoneType' object is not callable`), so confluid hands the marker back
    unbuilt rather than crashing.

    Deleting the branch would fall through to `inspect.signature(None)`, which raises,
    yielding `_UNKNOWN_PARAMS` and a call to `None(**kwargs)` — the same failure with a
    worse message and no marker to inspect.
    """
    from confluid.engine import _ctor_params

    class Nulled:
        __init__ = None  # type: ignore[assignment]

    get_registry().register_class(Nulled, name="NulledInit")

    assert _ctor_params(Nulled) is None

    result = load("o: !class:NulledInit()\n  k: 1\n")["o"]

    assert isinstance(result, Instance)  # handed back as a marker, not constructed
    assert result.kwargs["k"] == 1  # ...with its configuration intact
