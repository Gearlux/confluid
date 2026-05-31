"""Registering classes + the lazy / zero-arg constructor convention (minimal form).

Both classes are **zero-arg constructible** (every parameter defaulted) and do no functional work
in ``__init__``; ``MyModel.summary`` shows the canonical "derived state behind a read-only cached
property" pattern. See confluid ``AGENTS.md`` → "Lazy Initialization & Zero-Arg Construction".
"""

from confluid import configurable, get_registry, register


# 1. Using @configurable decorator
@configurable
class MyModel:
    def __init__(self, layers: int = 3) -> None:
        # Lazy constructor: only stores config (defaulted → ``MyModel()`` works).
        self.layers = layers

    @property
    def summary(self) -> str:
        """A human-readable description, derived lazily from the current ``layers``.

        A recomputing read-only property → derived state, never built in ``__init__`` and never stale.
        """
        return f"<MyModel: {self.layers} layers>"


# 2. Registering a third-party class (also zero-arg constructible)
class ExternalOptimizer:
    def __init__(self, lr: float = 0.01) -> None:
        self.lr = lr


register(ExternalOptimizer, name="Optimizer")

if __name__ == "__main__":
    print("Registered Classes:")
    for cls_name in get_registry().list_classes():
        print(f" - {cls_name}")

    # Zero-arg construction + lazily-derived state.
    model = MyModel()
    print(f"Model layers: {model.layers}")
    print(f"Model summary (derived lazily): {model.summary}")
