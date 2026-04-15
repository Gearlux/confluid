from typing import Any

import pytest

from confluid import Class, Reference, configurable, flow, get_registry, materialize


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
        "car": {"_confluid_class_": "Car", "color": "yellow", "engine": {"_confluid_class_": "Engine"}},
        "power": 777,
    }

    car = materialize(config["car"], context=config)

    assert isinstance(car.engine, Engine)
    assert car.engine.power == 777
    assert car.color == "yellow"


def test_reference_citizen() -> None:
    """Test that a Reference in config resolves during materialization."""
    config = {
        "engine_template": {"_confluid_class_": "Engine", "power": 444},
        "car": {"_confluid_class_": "Car", "engine": Reference("engine_template")},
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


def test_prioritized_broadcasting_from_root() -> None:
    """Test that explicit kwargs beat root-level broadcasting.

    Priority order: explicit > scoped > broadcast.
    """
    config = {
        "car": {"_confluid_class_": "Car", "color": "green", "engine": {"_confluid_class_": "Engine", "power": 200}},
        "power": 999,  # Root broadcast (lowest priority)
    }

    car = materialize(config["car"], context=config)

    # Explicit power=200 in the engine config wins over root broadcast power=999
    assert car.engine.power == 200
    assert car.color == "green"

    # Verify broadcasting fills in MISSING params (not override explicit ones)
    config2 = {
        "car": {"_confluid_class_": "Car", "color": "blue", "engine": {"_confluid_class_": "Engine"}},
        "power": 999,
    }
    car2 = materialize(config2["car"], context=config2)
    # Engine has no explicit power → broadcast fills it in
    assert car2.engine.power == 999


def test_path_based_fallback_resolution() -> None:
    """Test that Confluid can resolve classes by full module path if not in registry."""
    # Use JSONDecoder: standard library, pure python, standard init
    marker = {"_confluid_class_": "json.JSONDecoder", "strict": False}

    # This should trigger the fallback logic we added
    decoder = flow(marker)
    import json

    assert isinstance(decoder, json.JSONDecoder)
    assert decoder.strict is False


def test_deferred_marker_dictionary_flow() -> None:
    """Test that a marker dictionary stored in an attribute is correctly flowed."""
    config = {"engine": {"_confluid_class_": "Engine", "power": 123}}

    # Simulate an object created with a deferred marker dict
    car = Car(engine=config["engine"])
    assert isinstance(car.engine, dict)

    # flow() should recognize the marker dict and materialize it
    engine_instance = flow(car.engine)
    assert isinstance(engine_instance, Engine)
    assert engine_instance.power == 123


@configurable
class BodyAssigned:
    """Post-construction attr: ``self.nested = Class(Engine)`` — no ctor param for it.

    Mirrors the Marainer Trainer pattern where nested deferred objects are
    assigned inside __init__ rather than declared in the signature.
    """

    def __init__(self, color: str = "red") -> None:
        self.color = color
        self.nested = Class(Engine)


def test_broadcast_reaches_body_assigned_class_attribute() -> None:
    """A Class assigned in __init__'s body (not as a ctor param) must still
    receive root-level broadcasting."""
    get_registry().register_class(BodyAssigned, name="BodyAssigned")

    config = {
        "obj": {"_confluid_class_": "BodyAssigned", "color": "blue"},
        "power": 321,  # Should reach BodyAssigned.nested (= Class(Engine))
    }

    obj = materialize(config["obj"], context=config)

    # Class stays deferred but its kwargs are populated with broadcast scalars
    assert isinstance(obj.nested, Class)
    assert obj.nested.kwargs.get("power") == 321

    # Flowing the deferred Class produces an Engine configured from broadcast
    engine = flow(obj.nested)
    assert isinstance(engine, Engine)
    assert engine.power == 321


if __name__ == "__main__":
    from typing import Any

    pytest.main([__file__])
