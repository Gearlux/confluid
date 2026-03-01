from confluid import configurable, load


@configurable
class Model:
    def __init__(self, layers: int = 3, dropout: float = 0.1):
        self.layers = layers
        self.dropout = dropout

    def __repr__(self) -> str:
        return f"Model(layers={self.layers}, dropout={self.dropout})"


def main() -> None:
    print("--- Loading Modular Config ---")
    # Load from file that includes another
    model = load("confluid/examples/modular_includes/experiment.yaml")

    print(f"Loaded Object: {model}")
    print(f"Verified Layers: {model.layers} (from experiment.yaml)")
    print(f"Verified Dropout: {model.dropout} (inherited from base.yaml)")


if __name__ == "__main__":
    main()
