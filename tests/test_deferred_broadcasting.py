from typing import Any

import pytest

from confluid import Class, Instance, PartialClass, Reference, configurable, flow, get_registry, load, materialize


def _inst(target: str, /, **kwargs: Any) -> Instance:
    """Build an Instance marker with kwargs assigned post-construction.

    ``target`` is positional-only so test kwargs literally named ``name`` or
    ``target`` can't collide with it."""
    marker = Instance(target)
    marker.kwargs.update(kwargs)
    return marker


@pytest.fixture(autouse=True)
def setup_registry() -> None:
    """Re-register module-level classes after any prior test clears the registry."""
    get_registry().register_class(Engine, name="Engine")
    get_registry().register_class(Car, name="Car")
    get_registry().register_class(Garage, name="Garage")


@configurable
class Engine:
    def __init__(self, power: int = 100, type: str = "gas"):
        self.power = power
        self.type = type


@configurable
class Car:
    def __init__(self, engine: Any = Class(Engine), color: str = "red"):
        self.engine = engine
        self.color = color


@configurable
class Garage:
    def __init__(self, car: Car):
        self.car = car


def test_deferred_materialization_basic() -> None:
    """Test that flow() correctly materializes a Class citizen."""
    car = Car(color="blue")

    # engine is a Class citizen
    assert isinstance(car.engine, Class)
    assert car.engine.target == Engine

    # flow() should materialize it
    engine_instance = flow(car.engine)
    assert isinstance(engine_instance, Engine)
    assert engine_instance.power == 100


def test_class_citizen_captures_broadcasting() -> None:
    """Context values apply when engine is explicitly specified in config."""
    config = {
        "car": _inst("Car", color="yellow", engine=_inst("Engine")),
        "power": 777,
    }

    car = materialize(config["car"], context=config)

    assert isinstance(car.engine, Engine)
    assert car.engine.power == 777
    assert car.color == "yellow"


def test_reference_citizen() -> None:
    """Test that a Reference in config resolves during materialization."""
    config = {
        "engine_template": _inst("Engine", power=444),
        "car": _inst("Car", engine=Reference("engine_template")),
    }

    car = materialize(config["car"], context=config)

    # Reference should be resolved → Engine instance
    assert isinstance(car.engine, Engine)
    assert car.engine.power == 444


def test_deferred_materialization_with_overrides() -> None:
    """Test that flow() accepts runtime overrides for deferred objects."""
    car = Car()

    # Materialize with a runtime override
    engine_instance = flow(car.engine, power=500)
    assert isinstance(engine_instance, Engine)
    assert engine_instance.power == 500


def test_ordered_broadcasting_from_root() -> None:
    """Confluid uses flat-view ordered last-write-wins.

    The engine's own ``power: 200`` lives at ``car.engine.power`` — earlier
    in document order than the top-level ``power: 999``. Under flat-view
    ordered semantics, the later-positioned scalar wins, so ``power: 999``
    is the final value. (Old priority was "explicit > broadcast"; that
    rule is gone — every source is just ordered by its YAML position.)
    """
    config = {
        "car": _inst("Car", color="green", engine=_inst("Engine", power=200)),
        "power": 999,  # Root broadcast — appears AFTER car in document order
    }

    car = materialize(config["car"], context=config)

    # Top-level power=999 appears later in doc order than the nested 200 → wins.
    assert car.engine.power == 999
    assert car.color == "green"

    # Without an explicit nested power, the broadcast still fills it in.
    config2 = {
        "car": _inst("Car", color="blue", engine=_inst("Engine")),
        "power": 999,
    }
    car2 = materialize(config2["car"], context=config2)
    assert car2.engine.power == 999


def test_path_based_fallback_resolution() -> None:
    """Test that Confluid can resolve classes by full module path if not in registry."""
    # Use JSONDecoder: standard library, pure python, standard init
    marker = _inst("json.JSONDecoder", strict=False)

    # This should trigger the fallback logic we added
    decoder = flow(marker)
    import json

    assert isinstance(decoder, json.JSONDecoder)
    assert decoder.strict is False


def test_deferred_instance_marker_flow() -> None:
    """Test that a deferred Instance marker stored in an attribute is correctly flowed."""
    config = {"engine": _inst("Engine", power=123)}

    # Simulate an object created with a deferred Instance marker
    car = Car(engine=config["engine"])
    assert isinstance(car.engine, Instance)

    # flow() should recognize the Instance marker and materialize it
    engine_instance = flow(car.engine)
    assert isinstance(engine_instance, Engine)
    assert engine_instance.power == 123


@configurable
class BodyAssigned:
    """Post-construction attr: ``self.nested = Class(Engine)`` — no ctor param for it.

    Mirrors the Matrainer Trainer pattern where nested deferred objects are
    assigned inside __init__ rather than declared in the signature.
    """

    def __init__(self, color: str = "red") -> None:
        self.color = color
        self.nested = Class(Engine)


@configurable
class BodyAssignedLazy:
    """The same shape with a ``!lazy:`` slot — a runtime-injection point.

    The canonical instance of this is a trainer holding
    ``self.optimizer = PartialClass(AdamW)``: it cannot be built during
    materialization because ``params=`` only exists once the model does.
    """

    def __init__(self, color: str = "red") -> None:
        self.color = color
        # `type` is preset in CODE, `power` is not — the pair is what lets the tests
        # below tell "a bare key reaches an unset slot" from "a tuned block keeps
        # what it did not mention".
        self.nested = PartialClass(Engine, type="diesel")


def test_broadcast_reaches_body_assigned_LAZY_attribute() -> None:
    """A Partial body slot is CONFIGURED by broadcasting, exactly like a Class one.

    Deferral means "do not BUILD it", not "do not configure it" — merging keys into
    a marker's kwargs constructs nothing. Until 2026-08-03 a Partial was returned
    untouched, so a `!lazy:` marker written in the DOCUMENT received bare keys while
    an identical one created in an `__init__` BODY did not, and a consumer's
    code-declared optimizer could not be retuned from config at all.
    """
    get_registry().register_class(BodyAssignedLazy, name="BodyAssignedLazy")
    config = {"obj": _inst("BodyAssignedLazy"), "power": 321}

    obj = materialize(config["obj"], context=config)

    assert isinstance(obj.nested, PartialClass)  # still deferred — NOT built
    assert obj.nested.kwargs.get("power") == 321  # ... and configured
    assert flow(obj.nested).power == 321  # whoever flows it later gets the value


def test_a_lazy_body_slot_is_never_built_by_materialization() -> None:
    """The half of the contract that must survive the change above.

    A Partial target typically cannot be constructed without a runtime argument, so
    auto-building one during materialization is not a nicety — it raises.
    """
    get_registry().register_class(BodyAssignedLazy, name="BodyAssignedLazy")
    config = {"obj": _inst("BodyAssignedLazy"), "power": 321}
    obj = materialize(config["obj"], context=config)
    assert type(obj.nested) is PartialClass and not isinstance(obj.nested, Engine)


def test_a_mapping_addressed_at_a_deferred_slot_tunes_it_rather_than_replacing_it() -> None:
    """`optimizer: {lr: 0.5}` must configure the marker, not overwrite it with a dict.

    The old behaviour assigned the raw mapping, which silently destroyed the slot:
    the target class was gone and only the key the user mentioned survived. Merging
    also keeps what they did NOT mention, which is the whole reason to spell it as a
    block instead of restating the marker.
    """
    get_registry().register_class(BodyAssignedLazy, name="BodyAssignedLazy")
    marker = _inst("BodyAssignedLazy")
    marker.kwargs["nested"] = {"power": 42}

    obj = materialize(marker, context={"obj": marker})

    assert isinstance(obj.nested, PartialClass)  # not a dict
    assert obj.nested.kwargs["power"] == 42  # the addressed key landed
    assert obj.nested.kwargs["type"] == "diesel"  # ... and the untouched one survived


def test_a_bare_key_overrides_a_marker_kwarg_that_was_set_in_CODE() -> None:
    """A code-set marker kwarg is a DEFAULT, and defaults are what broadcasting overrides.

    `BodyAssignedLazy` presets `type="diesel"` the way a consumer presets
    `PartialClass(AdamW, lr=1e-4)`. Before 2026-08-03 that kwarg blocked the bare key, so
    WHERE a default was written decided whether config could reach it: a plain
    `def __init__(self, type="diesel")` loses to a bare `type:`, while the identical
    default on a marker held out. The run then used the hard-coded value silently.
    """
    get_registry().register_class(BodyAssignedLazy, name="BodyAssignedLazy")
    config = {"obj": _inst("BodyAssignedLazy"), "type": "electric"}

    obj = materialize(config["obj"], context=config)

    assert obj.nested.kwargs["type"] == "electric"  # the document beat the code default
    assert flow(obj.nested).type == "electric"


def test_a_bare_key_does_NOT_override_a_marker_kwarg_written_in_the_DOCUMENT() -> None:
    """The other half, and the reason the rule keys off PROVENANCE rather than presence.

    A kwarg the author wrote ON the marker is them addressing this node; a bare key is
    aimed at the whole document. Addressed wins — otherwise a top-level default would
    reach past an explicit per-node choice, which is the opposite failure.

    The bare key is deliberately written FIRST so document order alone would let it win:
    that is what makes this a test of the provenance guard rather than of ordering.
    Removing the guard flips this to ``electric`` (measured), so it is load-bearing.
    """
    get_registry().register_class(BodyAssignedLazy, name="BodyAssignedLazy")
    doc = load("type: electric\nobj: !class:BodyAssignedLazy()\n  nested: !lazy:Engine(type=turbo)\n")

    assert doc["obj"].nested.kwargs["type"] == "turbo"  # the marker's own spelling stands
    assert flow(doc["obj"].nested).type == "turbo"


def test_broadcast_reaches_body_assigned_class_attribute() -> None:
    """A Class assigned in __init__'s body (not as a ctor param) must still
    receive root-level broadcasting."""
    get_registry().register_class(BodyAssigned, name="BodyAssigned")

    config = {
        "obj": _inst("BodyAssigned", color="blue"),
        "power": 321,  # Should reach BodyAssigned.nested (= Class(Engine))
    }

    obj = materialize(config["obj"], context=config)

    # Class stays deferred but its kwargs are populated with broadcast scalars
    # A plain ``Class(...)`` body slot is BUILT (only ``partial`` defers), and the
    # broadcast key reached it before its constructor ran.
    engine = obj.nested
    assert isinstance(engine, Engine)
    assert engine.power == 321


if __name__ == "__main__":
    pytest.main([__file__])
