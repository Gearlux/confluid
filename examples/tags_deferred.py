"""Targets & deferred initialization — the runnable companion to ``docs/targets.md``.

Covers the marker family end-to-end: ``!class:Name`` (built at load) vs ``!partial:Name``
(built only by an explicit ``flow()``), ``flow()`` runtime injection (keyword AND
positional), the Python-side ``Partial[T]`` annotation (typed with the interface the slot
flows into), post-flow ``solidify()``, and ``!ref:`` (shared instance) vs a second marker
(a second instance).
"""

from typing import Any, List, Optional

from confluid import Partial, PartialClass, Target, configurable, flow, load


@configurable
class Engine:
    def __init__(self, cylinders: int = 4, fuel: str = "petrol") -> None:
        """A trivially cheap engine.

        Args:
            cylinders: Number of cylinders.
            fuel: Fuel type.
        """
        self.cylinders = cylinders
        self.fuel = fuel


@configurable
class Car:
    def __init__(self, engine: Optional[Engine] = None, color: str = "red") -> None:
        """A car that builds its own engine on demand (deferred-stub receiver).

        Args:
            engine: The engine — may arrive as a deferred ``Target`` stub.
            color: Paint color.
        """
        self.engine = engine
        self.color = color

    def start(self) -> Engine:
        """Build the (possibly deferred) engine exactly when it is needed."""
        self.engine = flow(self.engine)
        return self.engine


def main() -> None:
    # --- construction: `!partial:` is the whole difference ------------------------------
    # `!class:Name` and `!class:Name(...)` both build, wherever they sit; only
    # `!partial:` (`_partial_: true` in the reserved-key form) withholds construction.
    doc = """
car: !class:Car
  color: blue
  engine: !class:Engine     # built, like every non-partial target
ready_engine: !class:Engine(cylinders=8)
"""
    graph = load(doc)
    car, ready = graph["car"], graph["ready_engine"]
    assert isinstance(car, Car) and car.color == "blue"
    assert isinstance(car.engine, Engine), "a non-partial target is built"
    assert isinstance(ready, Engine) and ready.cylinders == 8

    # `flow()` is idempotent, so a receiver that flows its own slot keeps working
    # whether it was handed a marker or a live object.
    built = car.start()
    assert isinstance(built, Engine) and built.cylinders == 4
    print(f"built target, re-flowed idempotently: {built.cylinders} cylinders")

    # --- !partial: runtime injection: kwargs merge, runtime wins ------------------------
    partial_graph = load("factory: !partial:Engine(cylinders=6)")
    partial_engine = partial_graph["factory"]
    injected = flow(partial_engine, fuel="diesel")  # fuel only exists at runtime
    assert isinstance(injected, Engine)
    assert (injected.cylinders, injected.fuel) == (6, "diesel")
    print(f"!partial: built with runtime kwarg: {injected.cylinders} cylinders on {injected.fuel}")

    # --- Partial[T] annotation: the slot is typed with the INTERFACE it flows into ---------
    # Partial[Engine] == Annotated[Union[Engine, Fluid], marker]: the Target(...) default
    # type-checks (a Target IS a Fluid), auto-flow walkers leave the slot deferred,
    # and the subscript documents what an explicit flow() eventually builds.
    @configurable
    class Garage:
        def __init__(self, spare: Partial[Engine] = Target(Engine, cylinders=3)) -> None:
            """A garage holding a deferred spare-engine template.

            Args:
                spare: Deferred engine template, flowed on demand.
            """
            self.spare = spare

    garage = Garage()
    assert isinstance(garage.spare, Target), "Partial slot stays a deferred stub"
    spare = flow(garage.spare, fuel="e85")  # runtime kwarg injected at flow time
    assert isinstance(spare, Engine) and (spare.cylinders, spare.fuel) == (3, "e85")
    print(f"Partial[Engine] slot flowed on demand: {spare.cylinders} cylinders on {spare.fuel}")

    # --- positional runtime injection: inputs a keyword cannot carry --------------------
    # `Fleet(*cars, depot=...)` takes its cars POSITIONALLY, so no marker kwarg could
    # deliver them — flow()'s positional args are the channel. The config still owns
    # every knob (`depot`), which is the whole point of deferring the slot.
    class Fleet:
        def __init__(self, *cars: Any, depot: str = "central") -> None:
            self.cars, self.depot = list(cars), depot

    fleet_slot: Partial[Fleet] = PartialClass(Fleet, depot="north")
    fleet = flow(fleet_slot, Engine(cylinders=2), Engine(cylinders=3))
    assert [engine.cylinders for engine in fleet.cars] == [2, 3]
    assert fleet.depot == "north", "the stored knob survives positional injection"
    print(f"positional injection: {len(fleet.cars)} engines into the {fleet.depot} depot")

    # --- post-flow solidify(): build-once-and-cache, live objects included ---------------
    @configurable
    class Turbo:
        def __init__(self, stages: int = 2) -> None:
            """A turbo whose blades are built lazily, never in the constructor.

            Args:
                stages: Number of compressor stages.
            """
            self.stages = stages
            self.blades: Optional[List[int]] = None

        def solidify(self) -> List[int]:
            """Build the blades once; later calls are a free cache hit."""
            if self.blades is None:
                self.blades = list(range(self.stages))
            return self.blades

    live_turbo = Turbo(stages=3)  # constructed directly: nothing built yet
    assert live_turbo.blades is None
    assert flow(live_turbo) is live_turbo, "a live object flows to itself"
    assert live_turbo.blades == [0, 1, 2], "...and is solidified on the way through"
    assert flow(live_turbo, solidify=False) is live_turbo
    blades = live_turbo.blades
    assert blades is not None
    print(f"solidify() built {len(blades)} blades on a live object")

    # --- !ref: shared identity; a second marker is a second instance --------------------
    identity = load(
        """
proto: !class:Engine(cylinders=12)
a: !ref:proto
b: !ref:proto
c: !class:Engine(cylinders=12)
"""
    )
    assert identity["a"] is identity["proto"] and identity["b"] is identity["proto"]
    assert identity["c"] is not identity["proto"] and identity["c"].cylinders == 12
    print("!ref: shares one instance; a marker written twice is two instances")


if __name__ == "__main__":
    main()
