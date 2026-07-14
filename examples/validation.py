"""Validation policies — companion to ``docs/validation.md``.

Strict constructor validation (the default), relaxing one point to ``"warn"``,
tightening a range with ``Annotated[..., Field(...)]``, and the per-class
``validate=False`` opt-out. Requires the ``confluid[pydantic]`` extra.
"""

from typing import Annotated, Any

import pydantic
from pydantic import Field

from confluid import configurable, reset_policy, set_policy


@configurable
class Optimizer:
    def __init__(self, lr: float = 1e-3, weight_decay: float = 0.0) -> None:
        """A typed optimizer config.

        Args:
            lr: Learning rate.
            weight_decay: L2 penalty.
        """
        self.lr = lr
        self.weight_decay = weight_decay


@configurable
class Classifier:
    def __init__(self, num_classes: Annotated[int, Field(ge=1)] = 1000) -> None:
        """Constraint tightening lives on the annotation, not in the body.

        Args:
            num_classes: Number of classes — must be >= 1.
        """
        self.num_classes = num_classes


@configurable(validate=False)
class ExperimentalThing:
    def __init__(self, **kwargs: Any) -> None:
        """Too dynamic for pydantic — validation opted out per class."""
        self.kwargs = kwargs


def main() -> None:
    # 1. Strict (default): a bad type raises pydantic.ValidationError at the constructor.
    try:
        Optimizer(lr="not a float")  # type: ignore[arg-type]
    except pydantic.ValidationError as exc:
        print(f"strict init rejected bad lr: {exc.errors()[0]['msg']}")
    else:
        raise AssertionError("strict validation should have rejected the string lr")

    # 2. Annotated[..., Field(ge=1)] enforces the range with the same machinery.
    try:
        Classifier(num_classes=0)
    except pydantic.ValidationError:
        print("Field(ge=1) rejected num_classes=0")
    else:
        raise AssertionError("the range constraint should have fired")

    # 3. "warn" lets the call proceed (logged instead of raised).
    set_policy(init="warn")
    try:
        relaxed = Optimizer(lr="still not a float")  # type: ignore[arg-type]
        print(f"warn policy let the call through: lr={relaxed.lr!r}")
    finally:
        reset_policy()

    # 4. validate=False: intentionally untyped constructors skip validation entirely.
    thing = ExperimentalThing(anything="goes", even=object())
    print(f"validate=False accepted arbitrary kwargs: {sorted(thing.kwargs)}")


if __name__ == "__main__":
    main()
