"""Tags & deferred initialization — the runnable companion to ``docs/tags.md``.

Covers the tag family end-to-end: ``!class:Name`` (deferred ``Class`` stub) vs
``!class:Name(...)`` (eager ``Instance``), ``!lazy:`` + ``flow()`` runtime injection,
the Python-side ``Lazy[T]`` annotation (typed with the interface the slot flows
into), and ``!ref:`` (shared instance) vs ``!clone:`` (deep copy).
"""

from typing import Optional

from confluid import Class, Lazy, configurable, flow, load


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
            engine: The engine — may arrive as a deferred ``Class`` stub.
            color: Paint color.
        """
        self.engine = engine
        self.color = color

    def start(self) -> Engine:
        """Build the (possibly deferred) engine exactly when it is needed."""
        self.engine = flow(self.engine)
        return self.engine


def main() -> None:
    # --- !class: eager vs deferred: the trailing () is the whole difference -------------
    doc = """
car: !class:Car()
  color: blue
  engine: !class:Engine          # NO parens -> stays a deferred Class stub
ready_engine: !class:Engine(cylinders=8)   # parens -> built during load()
"""
    graph = load(doc)
    car, ready = graph["car"], graph["ready_engine"]
    assert isinstance(car, Car) and car.color == "blue"
    assert isinstance(car.engine, Class), "no parens -> the receiver got a deferred stub"
    assert isinstance(ready, Engine) and ready.cylinders == 8, "parens -> eagerly built"

    built = car.start()  # the receiver flows the stub on its own terms
    assert isinstance(built, Engine) and built.cylinders == 4
    print(f"deferred stub built on demand: {built.cylinders} cylinders")

    # --- !lazy: runtime injection: kwargs merge, runtime wins ---------------------------
    lazy_graph = load("factory: !lazy:Engine(cylinders=6)")
    lazy_engine = lazy_graph["factory"]
    injected = flow(lazy_engine, fuel="diesel")  # fuel only exists at runtime
    assert isinstance(injected, Engine)
    assert (injected.cylinders, injected.fuel) == (6, "diesel")
    print(f"!lazy: built with runtime kwarg: {injected.cylinders} cylinders on {injected.fuel}")

    # --- Lazy[T] annotation: the slot is typed with the INTERFACE it flows into ---------
    # Lazy[Engine] == Annotated[Union[Engine, Fluid], marker]: the Class(...) default
    # type-checks (a Class IS a Fluid), auto-flow walkers leave the slot deferred,
    # and the subscript documents what an explicit flow() eventually builds.
    @configurable
    class Garage:
        def __init__(self, spare: Lazy[Engine] = Class(Engine, cylinders=3)) -> None:
            """A garage holding a deferred spare-engine template.

            Args:
                spare: Deferred engine template, flowed on demand.
            """
            self.spare = spare

    garage = Garage()
    assert isinstance(garage.spare, Class), "Lazy slot stays a deferred stub"
    spare = flow(garage.spare, fuel="e85")  # runtime kwarg injected at flow time
    assert isinstance(spare, Engine) and (spare.cylinders, spare.fuel) == (3, "e85")
    print(f"Lazy[Engine] slot flowed on demand: {spare.cylinders} cylinders on {spare.fuel}")

    # --- !ref: vs !clone: shared identity vs deep copy ----------------------------------
    identity = load(
        """
proto: !class:Engine(cylinders=12)
a: !ref:proto
b: !ref:proto
c: !clone:proto
"""
    )
    assert identity["a"] is identity["proto"] and identity["b"] is identity["proto"]
    assert identity["c"] is not identity["proto"] and identity["c"].cylinders == 12
    print("!ref: shares one instance; !clone: is an independent deep copy")


if __name__ == "__main__":
    main()
