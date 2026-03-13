from confluid import configurable, get_registry
from confluid.fluid import Fluid, flow


def test_fluid_instantiation():
    @configurable
    class Model:
        def __init__(self, val=1):
            self.val = val

    f = Fluid(Model, val=10)
    instance = flow(f)
    assert instance.val == 10

    # Idempotency
    assert flow(instance) is instance


def test_flow_string_tag():
    @configurable
    class Model:
        def __init__(self, val=1):
            self.val = val

    instance = flow("!class:Model(val=5)")
    assert instance.val == 5


def test_fluid_by_name():
    @configurable
    class Model:
        def __init__(self, val=1):
            self.val = val

    f = Fluid("Model", val=20)
    instance = flow(f)
    assert instance.val == 20
