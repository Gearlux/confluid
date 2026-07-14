"""Configuration reports — the runnable companion to ``docs/report.md``.

Shows the three buckets of a ``ConfigurationReport``: an applied key (with
its receiver and origin), a failed key (a typo inside a matched block), and
an unused key (matched nothing anywhere) — first from ``configure()``'s
return value, then aggregated across a load-then-configure pass via the
``collect_report()`` context manager.
"""

from typing import Any, Optional

from confluid import collect_report, configurable, configure, load


@configurable
class Model:
    def __init__(self, layers: int = 3, lr: float = 0.01, name: Optional[str] = None) -> None:
        """A configurable model stub.

        Args:
            layers: Depth of the network.
            lr: Learning rate.
            name: Optional instance name (enables instance-name block matching).
        """
        self.layers = layers
        self.lr = lr
        self.name = name


@configurable
class Trainer:
    def __init__(self, model: Any = None, epochs: int = 1) -> None:
        """A trainer holding a model.

        Args:
            model: The model to train.
            epochs: Number of training epochs.
        """
        self.model = model
        self.epochs = epochs


def main() -> None:
    # --- configure() returns the report --------------------------------------
    trainer = Trainer(model=Model(name="encoder"))
    report = configure(
        trainer,
        config={
            "lr": 0.001,  # bare broadcast — lands on the model
            "Trainer": {"epochs": 10, "epochz": 99},  # applied + a typo'd key
            "ghost": 1,  # matches nothing anywhere
        },
    )

    print(f"summary: {report.summary()}")
    for a in report.applied:
        print(f"  applied: {a.key!r} -> {a.target} ({a.origin})")
    for f in report.failed:
        print(f"  failed:  {f.key!r} on {f.target} ({f.reason})")
    print(f"  unused:  {report.unused}")

    assert trainer.epochs == 10 and trainer.model.lr == 0.001
    assert [f.key for f in report.failed] == ["epochz"]
    assert report.unused == ["ghost"]

    # --- collect_report() spans a load-then-configure pass -------------------
    yaml_text = """
trainer: !class:Trainer
  model: !class:Model
lr: 0.005
ghost: 2
"""
    with collect_report() as pass_report:
        tree = load(yaml_text)  # engine path: 'lr' broadcasts, 'ghost' doesn't
        inner = configure(tree["trainer"], config={"epochs": 3})  # same report
    assert inner is pass_report

    print(f"pass summary: {pass_report.summary()}")
    assert {(a.key, a.origin) for a in pass_report.applied} >= {("lr", "bare"), ("epochs", "bare")}
    assert pass_report.unused == ["ghost"]

    print("OK")


if __name__ == "__main__":
    main()
