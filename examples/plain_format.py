"""The plain-YAML format: `_target_` / `_partial_` / `_ref_` / `_scope_`.

Companion to `docs/plain-format.md`. Demonstrates — and ASSERTS — that:

1. a confluid document written with reserved keys is ordinary YAML that
   `yaml.safe_load` reads, while the tag spelling of the same document is not;
2. the two spellings produce the same object graph;
3. `_partial_: true` withholds construction while still being configured;
4. `${ref:}` shares one instance and `${clone:}` makes an independent copy;
5. YAML anchors and merge keys (`<<:`) compose with markers;
6. `_scope_` blocks select between variants per run.

Run it:  python examples/plain_format.py
"""

import yaml

from confluid import configurable, flow, load


@configurable
class Dataset:
    def __init__(self, name: str = "synthetic", n: int = 8, seed: int = 0) -> None:
        self.name, self.n, self.seed = name, n, seed


@configurable
class Model:
    def __init__(self, hidden: int = 16, seed: int = 0) -> None:
        self.hidden, self.seed = hidden, seed


@configurable
class SGD:
    def __init__(self, params: object = None, lr: float = 0.01) -> None:
        self.params, self.lr = params, lr


@configurable
class Trainer:
    def __init__(self, model: object = None, dataset: object = None, seed: int = 0) -> None:
        self.model, self.dataset, self.seed = model, dataset, seed


PLAIN = """
seed: 7
dataset: {_target_: Dataset, n: 256}
model:
  _target_: Model
  hidden: 32
optimizer:
  _target_: SGD
  _partial_: true
  lr: 0.5
trainer:
  _target_: Trainer
  model: ${ref:model}
  dataset: ${ref:dataset}
"""

TAGGED = """
seed: 7
dataset: !class:Dataset(n=256)
model: !class:Model()
  hidden: 32
optimizer: !lazy:SGD(lr=0.5)
trainer: !class:Trainer()
  model: !ref:model
  dataset: !ref:dataset
"""


def main() -> None:
    print("=" * 70)
    print("1. The plain format is ordinary YAML; the tag format is not")
    print("=" * 70)
    raw = yaml.safe_load(PLAIN)
    print(f"   yaml.safe_load(PLAIN)['model'] -> {raw['model']}")
    assert raw["model"] == {"_target_": "Model", "hidden": 32}

    try:
        yaml.safe_load(TAGGED)
        raise AssertionError("expected the tagged document to be unreadable by plain YAML")
    except yaml.YAMLError as exc:
        print(f"   yaml.safe_load(TAGGED)         -> {type(exc).__name__}: could not determine a constructor")

    print()
    print("=" * 70)
    print("2. Both spellings build the same graph")
    print("=" * 70)
    plain, tagged = load(PLAIN), load(TAGGED)
    for name in ("model", "dataset", "trainer"):
        left, right = plain[name], tagged[name]
        assert type(left) is type(right), f"{name}: {type(left)} vs {type(right)}"
    assert plain["model"].hidden == tagged["model"].hidden == 32
    assert plain["dataset"].n == tagged["dataset"].n == 256
    print(f"   model.hidden  {plain['model'].hidden} == {tagged['model'].hidden}")
    print(f"   dataset.n     {plain['dataset'].n} == {tagged['dataset'].n}")

    print()
    print("=" * 70)
    print("3. A bare key broadcasts into every accepting node, built or not")
    print("=" * 70)
    # `seed: 7` is merged in pass 7; constructors run in pass 8 — so an eagerly
    # built node sees it too. This is why deferring construction is never needed
    # merely to let configuration reach a node.
    assert plain["trainer"].seed == plain["model"].seed == plain["dataset"].seed == 7
    print(
        f"   trainer.seed={plain['trainer'].seed}  model.seed={plain['model'].seed}  "
        f"dataset.seed={plain['dataset'].seed}   (one key, zero threading)"
    )

    print()
    print("=" * 70)
    print("4. `_partial_: true` withholds construction — but is still configured")
    print("=" * 70)
    marker = plain["optimizer"]
    assert not isinstance(marker, SGD), "a partial slot must not be built at load"
    print(f"   after load(): {type(marker).__name__} (not an SGD), carrying lr={marker.kwargs['lr']}")
    built = flow(marker, params="MODEL_PARAMS")
    assert isinstance(built, SGD) and built.params == "MODEL_PARAMS" and built.lr == 0.5
    print(f"   flow(marker, params=…) -> SGD(params={built.params!r}, lr={built.lr})")

    print()
    print("=" * 70)
    print("5. `${ref:}` shares one instance; `${clone:}` copies")
    print("=" * 70)
    assert plain["trainer"].model is plain["model"], "a reference must share the instance"
    print(f"   trainer.model is model -> {plain['trainer'].model is plain['model']}")

    copies = load("proto: {_target_: Model, hidden: 4}\na: ${ref:proto}\nc: ${clone:proto}")
    assert copies["a"] is copies["proto"]
    assert copies["c"] is not copies["proto"] and copies["c"].hidden == 4
    print(
        f"   a is proto -> {copies['a'] is copies['proto']}   "
        f"c is proto -> {copies['c'] is copies['proto']} (an independent copy)"
    )

    print()
    print("=" * 70)
    print("6. YAML's own anchors and `<<:` compose with markers")
    print("=" * 70)
    # Because the format is ordinary YAML, the standard many-variants-of-one-node
    # idiom needs no confluid-specific spelling. The node's own key beats the
    # merged one, exactly as YAML defines.
    variants = load(
        "base: &base\n"
        "  _target_: Model\n"
        "  hidden: 16\n"
        "small:\n"
        "  <<: *base\n"
        "large:\n"
        "  <<: *base\n"
        "  hidden: 512\n"
    )
    assert isinstance(variants["small"], Model), "a merged marker must convert like a literal one"
    assert variants["small"].hidden == 16
    assert variants["large"].hidden == 512
    print(f"   small.hidden={variants['small'].hidden}   large.hidden={variants['large'].hidden} (own key wins)")

    print()
    print("=" * 70)
    print("7. `_scope_` blocks select a variant per run")
    print("=" * 70)
    scoped = """
    default_model:
      _notscope_: {size: }
      model: {_target_: Model, hidden: 16}
    big_model:
      _scope_: {size: big}
      model: {_target_: Model, hidden: 512}
    """
    assert load(scoped)["model"].hidden == 16
    assert load(scoped, scopes=["size=big"])["model"].hidden == 512
    print(f"   no scope        -> hidden={load(scoped)['model'].hidden}")
    print(f"   scopes=[size=big] -> hidden={load(scoped, scopes=['size=big'])['model'].hidden}")

    print()
    print("All assertions passed.")


if __name__ == "__main__":
    main()
