"""Post-construction configuration — companion to ``docs/configure.md``.

``configure()`` applies a document to objects that ALREADY exist, in place,
under the same one matching rule as ``load()``: flat view, document order,
last write wins. The walk covers block-vs-bare ordering, deferred-slot tuning
(configured but never built), layered calls, the returned report, and the
values-before-``solidify()`` ordering.
"""

from typing import Any, List, Optional

from confluid import PartialClass, configurable, configure, flow


class Optimizer:
    """A runtime-injection target: ``params`` exists only once a model is built."""

    def __init__(self, params: Optional[List[Any]] = None, lr: float = 1e-4) -> None:
        self.params = params
        self.lr = lr


@configurable
class Model:
    def __init__(self, layers: int = 3, dropout: float = 0.1) -> None:
        self.layers = layers
        self.dropout = dropout
        self.backbone: Optional[str] = None

    def solidify(self) -> None:
        """Idempotent finalize — build once, cache; ``configure`` re-fires it AFTER applying."""
        if self.backbone is None:
            self.backbone = f"backbone({self.layers} layers)"


@configurable
class Trainer:
    def __init__(self, lr: float = 0.001, name: str = "trainer") -> None:
        self.lr = lr
        self.name = name
        self.model = Model()  # a live configurable child — reached by recursion
        self.optimizer: Any = PartialClass(Optimizer, lr=1e-4)  # a deferred slot


def main() -> None:
    trainer = Trainer()

    # 1. One call, every spelling. Document order decides: the bare `lr` sits
    #    LATER than the Trainer block's, so the bare value wins — no tiers.
    report = configure(
        trainer,
        config="Trainer:\n  lr: 0.5\nlr: 0.9\nlayers: 10\n",
    )
    assert trainer.lr == 0.9, "the later bare key beats the earlier block value"
    assert trainer.model.layers == 10, "the bare key reached the nested child too"
    print(f"after call 1: lr={trainer.lr}  model.layers={trainer.model.layers}")

    # 2. The deferred slot was TUNED, never built: `lr` reached its kwargs, and
    #    the object still does not exist — its owner builds it when the runtime
    #    argument is available.
    assert isinstance(trainer.optimizer, type(PartialClass(Optimizer))), "still a marker"
    assert trainer.optimizer.kwargs["lr"] == 0.9
    live = flow(trainer.optimizer, params=["p0", "p1"])
    assert live.lr == 0.9 and live.params == ["p0", "p1"]
    print(f"deferred slot: kwargs={trainer.optimizer.kwargs} -> built lr={live.lr}")

    # 3. Values first, finalization second: solidify() fired AFTER `layers`
    #    became 10, so the derived state reflects the configured value.
    assert trainer.model.backbone == "backbone(10 layers)"
    print(f"solidified with configured values: {trainer.model.backbone}")

    # 4. The report says what happened — applied keys with receiver + origin,
    #    and which document keys matched nothing.
    assert not report.failed
    applied_keys = {a.key for a in report.applied}
    assert {"lr", "layers"} <= applied_keys
    print(f"report: {report.summary()}")

    # 5. Layering: each call is judged on its own document alone. A key the
    #    first document settled is freely overridden by the next call.
    configure(trainer, config={"lr": 0.7})
    assert trainer.lr == 0.7
    print(f"after call 2: lr={trainer.lr}")

    # 6. A typo'd key inside an object's own block warns and lands in `failed`
    #    (value NOT applied); bare keys never warn — they legitimately match
    #    only some nodes.
    report3 = configure(trainer, config={"Trainer": {"typo_knob": 1}})
    assert [f.reason for f in report3.failed] == ["unknown-attribute"]
    assert not hasattr(trainer, "typo_knob")
    print(f"typo'd block key: {report3.summary()}")


if __name__ == "__main__":
    main()
