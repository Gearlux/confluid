"""hydraide — resolve a config to ONE plain-YAML document.

Companion to `docs/hydraide.md`. Demonstrates — and ASSERTS — that:

1. the tag spelling (what you write) and the reserved-key spelling (what hydraide
   emits) resolve to BYTE-IDENTICAL output;
2. the emitted document carries every settled value — the include, the dotted
   override, the scoped bare key — visibly, and reloads to the same graph;
3. `emit` is idempotent, which is what `check` rests on;
4. a shared marker is a NAMED anchor.

Run it:  python examples/hydraide.py
"""

import tempfile
from pathlib import Path

import yaml

from confluid import configurable, load
from confluid.hydraide import check, emit


@configurable
class Model:
    def __init__(self, hidden: int = 16, lr: float = 0.0) -> None:
        self.hidden, self.lr = hidden, lr


@configurable
class Stream:
    def __init__(self, ops: object = None, lr: float = 0.0) -> None:
        self.ops, self.lr = ops, lr


@configurable
class Resize:
    def __init__(self, size: int = 1) -> None:
        self.size = size


@configurable
class Adam:
    def __init__(self, params: object = None, lr: float = 0.0) -> None:
        self.params, self.lr = params, lr


TAG_BASE = """\
model: !class:Model(hidden=32)
lr: 0.1
train_set: !class:Stream
  ops: !ref:preprocess
preprocess:
  - !class:Resize(size=224)
optimizer: !partial:Adam
"""

PLAIN_BASE = """\
model: {_target_: Model, hidden: 32}
lr: 0.1
train_set:
  _target_: Stream
  ops: ${ref:preprocess}
preprocess:
  - {_target_: Resize, size: 224}
optimizer: {_target_: Adam, _partial_: true}
"""

EXPERIMENT = """\
include: {base}
model.hidden: 64
torch_only:
  _scope_: {{framework: torch}}
  lr: 0.3
"""


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "base_tag.yaml").write_text(TAG_BASE)
        (root / "base_plain.yaml").write_text(PLAIN_BASE)
        (root / "exp_tag.yaml").write_text(EXPERIMENT.format(base="base_tag.yaml"))
        (root / "exp_plain.yaml").write_text(EXPERIMENT.format(base="base_plain.yaml"))

        print("=" * 70)
        print("1. Both spellings emit byte-identical output")
        print("=" * 70)
        from_tags = emit(root / "exp_tag.yaml", scopes=["framework=torch"])
        from_plain = emit(root / "exp_plain.yaml", scopes=["framework=torch"])
        assert from_tags == from_plain, "the two spellings must resolve to the SAME document"
        print("   emit(tag input) == emit(plain input) -> True")

        print()
        print("=" * 70)
        print("2. The emitted document — every settled value, visible")
        print("=" * 70)
        for line in from_tags.rstrip().split("\n"):
            print(f"   {line}")
        doc = yaml.safe_load(from_tags)  # plain YAML: stock safe_load reads it
        assert doc["model"] == {"_target_": "Model", "hidden": 64, "lr": 0.3}
        assert doc["optimizer"] == {"_target_": "Adam", "_partial_": True, "lr": 0.3}
        assert "include" not in doc and "torch_only" not in doc

        graph = load(from_tags)
        assert (graph["model"].hidden, graph["model"].lr) == (64, 0.3)
        assert graph["train_set"].ops[0].size == 224
        print("   reload -> model.hidden=64, model.lr=0.3, train_set.ops[0].size=224")

        print()
        print("=" * 70)
        print("3. Idempotent — the property `check` rests on")
        print("=" * 70)
        (root / "resolved.yaml").write_text(from_tags)
        assert emit(root / "resolved.yaml") == from_tags
        assert check(root / "resolved.yaml") is None, "a resolved file is its own resolution"
        diff = check(root / "exp_tag.yaml")
        assert diff is not None and "+++" in diff, "an unresolved file reports a diff"
        print("   check(resolved.yaml) -> None      check(exp_tag.yaml) -> a unified diff")

        print()
        print("=" * 70)
        print("4. A shared marker is a NAMED anchor")
        print("=" * 70)
        assert "&preprocess_0" in from_tags and "*preprocess_0" in from_tags
        print("   the Resize marker reached twice is emitted as &preprocess_0 / *preprocess_0")

    print()
    print("All assertions passed.")


if __name__ == "__main__":
    main()
