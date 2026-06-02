from typing import Union

from confluid import Class, cast, configurable


@configurable
class Model:
    def __init__(self, layers: int = 3):
        self.layers = layers


def test_cast_fluid() -> None:
    # 1. Cast a Fluid recipe
    fluid_model = Class(Model, layers=10)

    # Static analysis hint: 'model' is seen as a Model instance
    model: Model = cast(fluid_model, Model)

    assert isinstance(model, Model)
    assert model.layers == 10


def test_cast_solid() -> None:
    # 2. Cast a live object (idempotency)
    live_model = Model(layers=5)

    model: Model = cast(live_model, Model)

    assert model is live_model
    assert model.layers == 5


def test_cast_with_runtime_kwargs() -> None:
    # 3. Cast with runtime overrides
    fluid_model = Class(Model, layers=10)

    model: Model = cast(fluid_model, Model, layers=20)

    assert isinstance(model, Model)
    assert model.layers == 20


def test_cast_union_type() -> None:
    # 4. Typical use case: Union[T, Fluid]
    def get_model(eager: bool) -> Union[Model, Class]:
        if eager:
            return Model(layers=5)
        return Class(Model, layers=10)

    # Case A: Eager
    m1 = get_model(eager=True)
    res1: Model = cast(m1, Model)
    assert res1.layers == 5

    # Case B: Deferred
    m2 = get_model(eager=False)
    res2: Model = cast(m2, Model)
    assert res2.layers == 10
