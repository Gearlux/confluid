"""Modular config composition: an experiment file that ``include:``s a base file.

``experiment.yaml`` pulls in ``base.yaml`` and overrides selected keys; the
merged document then configures a ``Model`` instance. Companion of the
include/interpolation guide (docs/interpolation.md).
"""

from pathlib import Path

from confluid import Target, configurable, load_config, materialize


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
    model = materialize(Target("Model"), context=cfg)

    print(f"Loaded Object: {model}")
    print(f"Verified Layers: {model.layers} (from experiment.yaml)")
    print(f"Verified base_lr: {cfg['base_lr']} (overridden by experiment.yaml)")

    assert model.layers == 50, f"expected experiment.yaml override, got {model.layers}"
    assert cfg["base_lr"] == 0.0001, f"expected experiment.yaml override, got {cfg['base_lr']}"

    print("\n--- Includes and document order ---")
    # `dropout` is set three times across the two files:
    #
    #   base.yaml        dropout: 0.9          (bare)
    #   base.yaml        Model: {dropout: 0.1} (addressed at Model)
    #   experiment.yaml  dropout: 0.5          (bare, written after its Model block)
    #
    # The including file is read AFTER the file it includes, so experiment.yaml's
    # bare key is the last spec and wins — even over an ADDRESSED block in the
    # included file. There are no priority tiers: position alone decides.
    print(f"Merged key order: {list(cfg)}")
    print(f"Verified Dropout: {model.dropout} (experiment.yaml's bare key — the last spec)")

    assert model.dropout == 0.5, f"expected the includer's later bare key, got {model.dropout}"
    assert list(cfg).index("dropout") > list(cfg).index("Model")

    print("\n--- Where the `include:` sits ---")
    # `fallbacks.yaml` writes its own values and includes base.yaml LAST, so the
    # paste lands after them: everything it declares is a fallback base overrides.
    fallback_cfg = load_config(str(Path(__file__).with_name("fallbacks.yaml")))
    fallback_model = materialize(Target("Model"), context=fallback_cfg)

    print(f"fallbacks.yaml (include LAST):  {fallback_model}")
    print("  -> base.yaml won, because the paste lands after this file's own lines")

    assert fallback_model.layers == 3, f"expected base.yaml to win, got {fallback_model.layers}"
    assert fallback_model.dropout == 0.1, f"expected base.yaml to win, got {fallback_model.dropout}"


if __name__ == "__main__":
    main()
