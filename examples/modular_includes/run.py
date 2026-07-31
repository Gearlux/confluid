"""Modular config composition: an experiment file that ``include:``s a base file.

``experiment.yaml`` pulls in ``base.yaml`` and overrides selected keys; the
merged document then configures a ``Model`` instance. Companion of the
include/interpolation guide (docs/interpolation.md).
"""

from pathlib import Path

from confluid import Instance, configurable, load_config, materialize


@configurable
class Model:
    def __init__(self, layers: int = 3, dropout: float = 0.1):
        self.layers = layers
        self.dropout = dropout

    def __repr__(self) -> str:
        return f"Model(layers={self.layers}, dropout={self.dropout})"


def main() -> None:
    print("--- Loading Modular Config ---")
    # Load the include tree (experiment.yaml -> base.yaml), then build the
    # Model from its class-name config block.
    cfg = load_config(str(Path(__file__).with_name("experiment.yaml")))
    model = materialize(Instance("Model"), context=cfg)

    print(f"Loaded Object: {model}")
    print(f"Verified Layers: {model.layers} (from experiment.yaml)")
    print(f"Verified Dropout: {model.dropout} (inherited from base.yaml)")
    print(f"Verified base_lr: {cfg['base_lr']} (overridden by experiment.yaml)")

    assert model.layers == 50, f"expected experiment.yaml override, got {model.layers}"
    assert model.dropout == 0.1, f"expected base.yaml default, got {model.dropout}"
    assert cfg["base_lr"] == 0.0001, f"expected experiment.yaml override, got {cfg['base_lr']}"


if __name__ == "__main__":
    main()
