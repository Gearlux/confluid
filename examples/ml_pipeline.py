"""Lazy-init / zero-arg ``@configurable`` classes + post-construction configuration.

Demonstrates the workspace class-design convention (see confluid ``AGENTS.md`` →
"Lazy Initialization & Zero-Arg Construction"):

  * the constructor does **no functional work** — it only stores values;
  * **every parameter is defaulted**, so ``Cls()`` (zero-arg) always works and the
    object can be configured *after* construction (the Post-Construction Paradigm);
  * **derived / resettable state lives behind a read-only, lazily-computed, cached**
    ``@property`` (here ``Model.weights`` and ``Trainer.optimizer``) — never built in
    ``__init__``.
"""

from typing import Any, List, Optional

import yaml

from confluid import Class, configurable, configure, flow, register

# --- 1. Define modular components ---


@configurable
class Model:
    def __init__(self, layers: int = 3, dropout: float = 0.1) -> None:
        # Lazy constructor: only stores config (both knobs defaulted → ``Model()`` works).
        self.layers = layers
        self.dropout = dropout

    @property
    def weights(self) -> List[float]:
        """Per-layer weights, derived lazily from the CURRENT ``layers`` (never built in __init__).

        A *recomputing* property — not a stored attribute — so it can't go stale when ``layers`` is
        changed post-construction via ``configure``. (``configure`` introspects an instance by
        ``getattr``, which would trigger and freeze a *cached* property before the new config lands;
        recomputing reflects current state. Cache only an expensive external materialization whose
        inputs are stable by first use — see dataflux ``HuggingFaceSource.dataset``.)
        """
        return [0.0] * self.layers

    def __repr__(self) -> str:
        return f"Model(layers={self.layers}, dropout={self.dropout})"


# Third-party class simulation (e.g. from torch.optim)
class AdamOptimizer:
    def __init__(self, lr: float = 0.001) -> None:
        self.lr = lr

    def __repr__(self) -> str:
        return f"Adam(lr={self.lr})"


# Register the third-party class
register(AdamOptimizer, name="Adam")


@configurable
class Trainer:
    def __init__(self, model: Optional[Model] = None, epochs: int = 5) -> None:
        # Lazy constructor, zero-arg constructible: ``model`` defaults to None so ``Trainer()``
        # is valid; the dependency graph is wired afterwards via ``configure`` (build → configure → use).
        self.model = model
        self.epochs = epochs
        # The optimizer arrives as a deferred recipe (a ``Class`` stub by default, or a
        # ``!class:Adam(...)`` from YAML). The *live* optimizer is materialized lazily by the property.
        self.optimizer: Any = Class(AdamOptimizer)

    @property
    def built_optimizer(self) -> Any:
        """The live optimizer, materialized from the CURRENT ``optimizer`` recipe via idempotent ``flow``.

        A recomputing property so it reflects the recipe set by ``configure`` (a cached one could
        freeze the pre-config default — see ``Model.weights``). ``flow`` is idempotent: a recipe
        (``Class`` / ``!class:`` Fluid) or an already-live object both resolve correctly.
        """
        return flow(self.optimizer)

    def __repr__(self) -> str:
        return f"Trainer(epochs={self.epochs}, model={self.model}, optimizer={self.optimizer})"


# --- 2. Define the experiment in YAML ---

experiment_yaml = """
base_lr: 0.0001

Trainer:
  epochs: 10
  # Dependency Injection: a deferred Adam recipe, built lazily by Trainer.built_optimizer
  optimizer: "!class:Adam(lr=!ref:base_lr)"

Model:
  layers: 50
  dropout: 0.5
"""


def main() -> None:
    # Zero-arg construction works — no functional work happens in any constructor.
    print("--- Zero-Arg Construction ---")
    print(Trainer())  # Trainer(epochs=5, model=None, optimizer=<Class AdamOptimizer>)

    # Build the components, then apply the hierarchical config post-construction.
    model = Model()
    trainer = Trainer(model=model)

    print("\n--- Before Configuration ---")
    print(trainer)

    config_data = yaml.safe_load(experiment_yaml)
    configure(trainer, config=config_data)
    configure(model, config=config_data)

    print("\n--- After Configuration ---")
    print(trainer)

    # `model` is optional config (defaulted to None for zero-arg construction), so guard before use.
    assert trainer.model is not None
    print(f"Verified Model Layers: {trainer.model.layers}")

    # Lazy derived state — materialized only now, on first access.
    print(f"Verified Optimizer LR: {trainer.built_optimizer.lr}")
    print(f"Lazy Model Weights (len): {len(trainer.model.weights)}")


if __name__ == "__main__":
    main()
