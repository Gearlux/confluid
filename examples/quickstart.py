"""The README's Quick Start, runnable — every number the README prints is asserted here.

Two ways to get configured objects:

1. **Build everything from the document** — ``load("experiment.yaml")`` constructs the whole
   graph (a Trainer holding a Model, wired and interpolated); no manual construction.
2. **Configure objects you already have** — ``configure(obj, config=…)`` /
   ``configure_from_file`` apply a document IN PLACE (the existing Model stays the same object).

Standalone, zero-arg, exit 0 (writes its two YAML files to a temp directory).
"""

import os
import tempfile
from pathlib import Path
from typing import Optional

from confluid import configurable, configure, configure_from_file, dump, load

# ---- 1. define configurable classes -------------------------------------------------------


@configurable
class Model:
    def __init__(self, layers: int = 3, dropout: float = 0.1):
        self.layers = layers
        self.dropout = dropout


@configurable
class Trainer:
    # Every parameter defaulted, so `Trainer()` works and the model can be wired afterwards
    # (docs/class-design.md) — optional: required params and real work in __init__ are fine too.
    def __init__(self, model: Optional[Model] = None, lr: float = 0.001):
        self.model = model
        self.lr = lr


EXPERIMENT = """\
# experiment.yaml — the tag form; `hydraide emit` turns it into the plain form `yq` reads
defaults:
  n_layers: 10

trainer: !class:Trainer
  lr: 0.0001
  model: !class:Model
    layers: ${defaults.n_layers}      # config-key interpolation — one source of truth
"""

OVERRIDES = """\
# overrides.yaml — applied to objects that already exist; a mapping at a slot tunes IN PLACE
Trainer:
  lr: 0.0001
  model:
    layers: 10
"""


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        os.chdir(tmp)
        Path("experiment.yaml").write_text(EXPERIMENT)
        Path("overrides.yaml").write_text(OVERRIDES)

        # ---- 2. build everything from the document ------------------------------------------
        trainer = load("experiment.yaml")["trainer"]  # the whole graph, constructed and wired
        print(type(trainer).__name__, trainer.lr)  # Trainer 0.0001
        print(type(trainer.model).__name__, trainer.model.layers)  # Model 10
        assert isinstance(trainer, Trainer) and isinstance(trainer.model, Model)
        assert (trainer.lr, trainer.model.layers, trainer.model.dropout) == (0.0001, 10, 0.1)

        # ---- 3. …or configure objects you already have -----------------------------------------
        model = Model()
        trainer = Trainer(model=model)
        report = configure(trainer, config=load("overrides.yaml", until="raw"))
        print(trainer.lr, model.layers)  # 0.0001 10
        print(report.summary())  # 2 applied, 0 failed, 0 unused
        assert (trainer.lr, model.layers) == (0.0001, 10)
        assert trainer.model is model  # tuned in place — the same Model object
        assert report.summary() == "2 applied, 0 failed, 0 unused"

        model2 = Model()
        trainer2 = Trainer(model=model2)
        configure_from_file(trainer2, path="overrides.yaml")  # load + configure in one call
        assert (trainer2.lr, model2.layers) == (0.0001, 10)

        # ---- 4. dump and reconstruct ------------------------------------------------------------
        state_yaml = dump(trainer)  # the live object graph as a reloadable document
        print(state_yaml)
        new_trainer = load(state_yaml)  # the identical hierarchy, e.g. in another process
        assert (new_trainer.lr, new_trainer.model.layers, new_trainer.model.dropout) == (0.0001, 10, 0.1)


if __name__ == "__main__":
    main()
