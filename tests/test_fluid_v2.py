from confluid import configurable
from confluid.fluid import Fluid, flow


def test_fluid_instantiation() -> None:
    @configurable
    class Model:
        def __init__(self, val: int = 1) -> None:
            self.val = val

    f = Fluid(Model, val=10)
    instance = flow(f)
    assert instance.val == 10

    # Idempotency
    assert flow(instance) is instance


def test_flow_string_tag() -> None:
    @configurable
    class Model:
        def __init__(self, val: int = 1) -> None:
            self.val = val

    instance = flow("!class:Model(val=5)")
    assert instance.val == 5


def test_fluid_by_name() -> None:
    @configurable
    class Model:
        def __init__(self, val: int = 1) -> None:
            self.val = val

    f = Fluid("Model", val=20)
    instance = flow(f)
    assert instance.val == 20
