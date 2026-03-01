from typing import Any

from confluid import configurable, dump, load


@configurable
class Preprocessor:
    def __init__(self, mode: str = "standard", scale: float = 1.0):
        self.mode = mode
        self.scale = scale

    def __repr__(self) -> str:
        return f"Preprocessor(mode='{self.mode}', scale={self.scale})"


@configurable
class Pipeline:
    def __init__(self, name: str, steps: list[Any]):
        self.name = name
        self.steps = steps

    def __repr__(self) -> str:
        return f"Pipeline(name='{self.name}', steps={self.steps})"


def main() -> None:
    # 1. Create a complex live hierarchy
    p1 = Preprocessor(mode="minmax", scale=2.0)
    p2 = Preprocessor(mode="robust", scale=0.5)

    original_pipeline = Pipeline(name="ProductionPipeline", steps=[p1, p2])

    print("--- Original Hierarchy ---")
    print(original_pipeline)

    # 2. Dump the entire state to YAML
    state_yaml = dump(original_pipeline)
    print("\n--- Exported YAML State ---")
    print(state_yaml)

    # 3. Reconstruct exactly in one line
    reconstructed_pipeline = load(state_yaml)

    print("\n--- Reconstructed Hierarchy ---")
    print(reconstructed_pipeline)

    # 4. Verify equality
    assert reconstructed_pipeline.name == original_pipeline.name
    assert len(reconstructed_pipeline.steps) == len(original_pipeline.steps)
    assert reconstructed_pipeline.steps[0].mode == "minmax"
    print("\n[SUCCESS] Hierarchy reconstructed with 100% fidelity.")


if __name__ == "__main__":
    main()
