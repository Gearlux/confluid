from typing import Any

import yaml
from confluid import configurable, configure, register, solidify

# --- 1. Define modular components ---


@configurable
class Model:
    def __init__(self, layers: int = 3, dropout: float = 0.1):
        self.layers = layers
        self.dropout = dropout

    def __repr__(self) -> str:
        return f"Model(layers={self.layers}, dropout={self.dropout})"


# Third-party class simulation (e.g. from torch.optim)
class AdamOptimizer:
    def __init__(self, lr: float = 0.001):
        self.lr = lr

    def __repr__(self) -> str:
        return f"Adam(lr={self.lr})"


# Register the third-party class
register(AdamOptimizer, name="Adam")


@configurable
class Trainer:
    def __init__(self, model: Model, optimizer: Any = None, epochs: int = 5):
        self.model = model
        self.optimizer = optimizer
        self.epochs = epochs

    def setup(self) -> None:
        # Use 'solidify' to ensure we have a live optimizer instance
        # whether it was passed as a real object or a config reference
        self.optimizer = solidify(self.optimizer)

    def __repr__(self) -> str:
        return f"Trainer(epochs={self.epochs}, model={self.model}, optimizer={self.optimizer})"


# --- 2. Define the experiment in YAML ---

experiment_yaml = """
base_lr: 0.0001

Trainer:
  epochs: 10
  # Dependency Injection: Injecting a constructed Adam instance
  optimizer: "!class:Adam(lr=!ref:base_lr)"

Model:
  layers: 50
  dropout: 0.5
"""


def main() -> None:
    # Instantiate with defaults
    model = Model()
    trainer = Trainer(model=model)

    print("--- Before Configuration ---")
    print(trainer)

    # 3. Apply the hierarchical config
    config_data = yaml.safe_load(experiment_yaml)
    configure(trainer, config=config_data)
    configure(model, config=config_data)

    # Resolve the dependency graph
    trainer.setup()

    print("\n--- After Configuration ---")
    print(trainer)
    print(f"Verified Model Layers: {trainer.model.layers}")
    print(f"Verified Optimizer LR: {trainer.optimizer.lr}")


if __name__ == "__main__":
    main()
