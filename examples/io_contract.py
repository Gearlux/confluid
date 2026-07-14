"""The I/O contract — companion to ``docs/io-contract.md``.

A Runnable declares its outputs with ``@output`` (under ``@property``) and marks a
defaulted-for-zero-arg-construction input as genuinely required with ``Mandatory[T]``;
``output_specs`` / ``input_specs`` expose the contract to GUIs and agents.
"""

from typing import Any, Optional

from confluid import Mandatory, configurable, input_specs, output, output_specs


@configurable
class Trainer:
    def __init__(self, model: Mandatory[Any] = None, num_classes: Optional[int] = None) -> None:
        """A minimal Runnable.

        Args:
            model: The model to train — defaulted so ``Trainer()`` works, but marked Mandatory.
            num_classes: Optional class count, derived from the dataset when None.
        """
        self.model = model
        self.num_classes = num_classes

    @property
    @output  # NOTE: @output goes UNDER @property so it stamps the getter
    def trained_model(self) -> Any:
        """The trained model produced by run()."""
        return self.model


def main() -> None:
    outputs = output_specs(Trainer)
    inputs = {spec["name"]: spec for spec in input_specs(Trainer)}

    assert [o["name"] for o in outputs] == ["trained_model"]
    assert inputs["model"]["required"] is True, "Mandatory[T] restores required-ness despite the default"
    assert inputs["num_classes"]["required"] is False and inputs["num_classes"]["nullable"] is True

    print("outputs:")
    for out in outputs:
        print(f"  {out['name']}: {out['description']}")
    print("inputs:")
    for name, inp in inputs.items():
        print(f"  {name}: required={inp['required']} nullable={inp['nullable']}")

    # Zero-arg construction still works — Mandatory is a contract mark, not a ctor gate.
    Trainer()
    print("Trainer() zero-arg construction works; the contract lives in the specs.")


if __name__ == "__main__":
    main()
