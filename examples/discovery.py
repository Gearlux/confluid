"""Discovery tags — companion to ``docs/discovery.md``.

Tags classes with ``category`` / ``group`` / the behavioral marks (``random`` /
``constant``), queries them back through the registry, and extracts docstring
parameter help with ``parse_param_docs``.
"""

import confluid
from confluid import configurable, get_registry, parse_param_docs


@configurable(category="loss")
class FocalLoss:
    def __init__(self, gamma: float = 2.0) -> None:
        """A discoverable loss.

        Args:
            gamma: Focusing parameter.
        """
        self.gamma = gamma


@configurable(category="op", group="numpy")
class StandardizeOp:
    def __init__(self, mean: float = 0.0, std: float = 1.0) -> None:
        """A palette-grouped op (a visual editor nests it under .../Op/numpy).

        Args:
            mean: Channel mean.
            std: Channel standard deviation.
        """
        self.mean = mean
        self.std = std


@configurable(category="op", random=True)
class NoiseOp:
    def __init__(self, sigma: float = 0.1) -> None:
        """Non-deterministic op — editors re-execute it on every run.

        Args:
            sigma: Noise standard deviation.
        """
        self.sigma = sigma


@configurable(category="op", constant=True)
class StaticConfig:
    def __init__(self, level: int = 3) -> None:
        """A PURE value producer — outputs depend only on the constructor config.

        Args:
            level: The configured level.
        """
        self.level = level


def main() -> None:
    registry = get_registry()

    losses = registry.list_classes(category="loss")
    numpy_ops = registry.list_classes(group="numpy")
    assert "FocalLoss" in losses, losses
    assert "StandardizeOp" in numpy_ops, numpy_ops
    print(f"category='loss' finds: {sorted(set(losses) & {'FocalLoss'})}")
    print(f"group='numpy' finds:   {sorted(set(numpy_ops) & {'StandardizeOp'})}")

    # Behavioral marks are stamp-only class attributes — consumers just getattr them.
    assert getattr(NoiseOp, "__confluid_random__", False) is True
    assert getattr(StaticConfig, "__confluid_constant__", False) is True
    print("NoiseOp is marked random; StaticConfig is marked constant")

    # Declaring both marks together is contradictory and raises at decoration time.
    try:

        @configurable(random=True, constant=True)
        class Broken:
            def __init__(self) -> None: ...

    except confluid.ConfigurableDefinitionError:
        print("random=True + constant=True correctly rejected")
    else:
        raise AssertionError("contradictory marks should raise ConfigurableDefinitionError")

    # One docstring, every GUI: the Args: block is machine-readable.
    docs = parse_param_docs(StandardizeOp)
    assert docs["mean"] == "Channel mean."
    print(f"parse_param_docs(StandardizeOp): {docs}")


if __name__ == "__main__":
    main()
