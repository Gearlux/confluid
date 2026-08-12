from typing import Union

from confluid import Target, cast, configurable


@configurable
class CastModel:
    def __init__(self, layers: int = 3):
        self.layers = layers


def test_cast_fluid() -> None:
    # 1. Cast a Fluid recipe
    fluid_model = Target(CastModel, layers=10)

    # Static analysis hint: 'model' is seen as a CastModel instance
    model: CastModel = cast(fluid_model, CastModel)

    assert isinstance(model, CastModel)
    assert model.layers == 10


def test_cast_solid() -> None:
    # 2. Cast a live object (idempotency)
    live_model = CastModel(layers=5)

    model: CastModel = cast(live_model, CastModel)

    assert model is live_model
    assert model.layers == 5


def test_cast_with_runtime_kwargs() -> None:
    # 3. Cast with runtime overrides
    fluid_model = Target(CastModel, layers=10)

    model: CastModel = cast(fluid_model, CastModel, layers=20)

    assert isinstance(model, CastModel)
    assert model.layers == 20


def test_cast_union_type() -> None:
    # 4. Typical use case: Union[T, Fluid]
    def get_model(eager: bool) -> Union[CastModel, Target]:
        if eager:
            return CastModel(layers=5)
        return Target(CastModel, layers=10)

    # Case A: Eager
    m1 = get_model(eager=True)
    res1: CastModel = cast(m1, CastModel)
    assert res1.layers == 5

    # Case B: Deferred
    m2 = get_model(eager=False)
    res2: CastModel = cast(m2, CastModel)
    assert res2.layers == 10
