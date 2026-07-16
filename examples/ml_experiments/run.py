"""ML experiment suite — a real-world confluid scenario.

A research team keeps ONE base config (object wiring + defaults), selects
model/optimizer "config groups" per run without editing files, layers named
experiment overlays on top, and archives an exact reproducible snapshot of
what actually ran. See README.md in this directory for the full walkthrough
(including the Hydra-concept -> confluid-feature mapping).

Four acts:
  1. defaults        -- load experiment_quick.yaml, train MLP + SGD
  2. group selection -- same file, scopes=["model=cnn", "optimizer=adam"]
  3. overlay         -- experiment_full.yaml overrides win over base.yaml
  4. reproducibility -- dump() the trained wiring, load() it back, compare

The components are pure-Python stand-ins (no torch/numpy): the point is the
configuration story, not the math.
"""

import random
from pathlib import Path
from typing import Any, List, Optional, Tuple

from confluid import NoBroadcast, configurable, dump, flow, load

CFG_DIR = Path(__file__).parent


# --------------------------------------------------------------------------
# Components — all lazy-init/zero-arg: constructors only store values,
# derived state is recomputed from current inputs by properties/methods.
# --------------------------------------------------------------------------


@configurable
class SyntheticDataset:
    """Noisy samples of the line y = 2x - 1.

    Args:
        n_samples: Number of (x, y) points to generate.
        noise: Standard deviation of the Gaussian noise on y.
        seed: RNG seed (reached by the broadcast top-level ``seed`` key).
    """

    def __init__(self, n_samples: int = 64, noise: float = 0.05, seed: int = 0):
        self.n_samples = n_samples
        self.noise = noise
        self.seed = seed

    @property
    def points(self) -> List[Tuple[float, float]]:
        rng = random.Random(self.seed)
        xs = [i / max(self.n_samples - 1, 1) for i in range(self.n_samples)]
        return [(x, 2.0 * x - 1.0 + rng.gauss(0.0, self.noise)) for x in xs]


class LinearStub:
    """Shared stub behaviour: predict y = w*x + b, expose loss + gradients."""

    def __init__(self, seed: int, device: str, verbose: bool):
        self.seed = seed
        self.device = device
        self.verbose = verbose
        self._params: Optional[List[float]] = None

    @property
    def params(self) -> List[float]:
        if self._params is None:
            rng = random.Random(self.seed)
            self._params = [rng.uniform(-0.1, 0.1), rng.uniform(-0.1, 0.1)]
        return self._params

    def loss_and_grads(self, points: List[Tuple[float, float]]) -> Tuple[float, List[float]]:
        w, b = self.params
        n = len(points)
        loss = gw = gb = 0.0
        for x, y in points:
            err = (w * x + b) - y
            loss += err * err / n
            gw += 2.0 * err * x / n
            gb += 2.0 * err / n
        return loss, [gw, gb]


@configurable
class MLP(LinearStub):
    """A (stub) multi-layer perceptron.

    Args:
        hidden: Hidden width — the knob experiment overlays tune.
        seed: Weight-init seed (reached by the broadcast ``seed`` key).
        device: Compute device (reached by the broadcast ``device`` key).
        verbose: Chatty training (reached by the broadcast ``verbose`` key).
    """

    def __init__(self, hidden: int = 32, seed: int = 0, device: str = "cpu", verbose: bool = False):
        super().__init__(seed, device, verbose)
        self.hidden = hidden


@configurable
class CNN(LinearStub):
    """A (stub) convolutional network — the ``model=cnn`` config group.

    Args:
        channels: Channel count.
        seed: Weight-init seed.
        device: Compute device.
        verbose: Chatty training.
    """

    def __init__(self, channels: int = 8, seed: int = 0, device: str = "cpu", verbose: bool = False):
        super().__init__(seed, device, verbose)
        self.channels = channels


@configurable
class SGD:
    """Plain gradient descent.

    Args:
        lr: Learning rate (wired to the shared ``base_lr`` via ``!ref:``).
        model: The LIVE model whose parameters to update — injected at RUN
            time by the trainer (``flow(self.optimizer, model=...)``), which
            is why the YAML declares the optimizer with ``!lazy:``. An object
            survives the flow by identity (a plain list kwarg would be copied).
            ``NoBroadcast`` keeps the bare top-level ``model:`` key from
            pre-wiring it at load — this slot belongs to the run.
    """

    def __init__(self, lr: float = 0.01, model: NoBroadcast[Any] = None):
        self.lr = lr
        self.model = model

    def step(self, grads: List[float]) -> None:
        assert self.model is not None, "SGD needs a model — flow it with model=..."
        params = self.model.params
        for i, g in enumerate(grads):
            params[i] -= self.lr * g


@configurable
class Adam(SGD):
    """SGD with momentum — the ``optimizer=adam`` config group.

    Args:
        lr: Learning rate.
        model: Injected at run time, like SGD.
        beta: Momentum coefficient.
    """

    def __init__(self, lr: float = 0.01, model: NoBroadcast[Any] = None, beta: float = 0.9):
        super().__init__(lr, model)
        self.beta = beta
        self._velocity: Optional[List[float]] = None

    def step(self, grads: List[float]) -> None:
        assert self.model is not None, "Adam needs a model — flow it with model=..."
        if self._velocity is None:
            self._velocity = [0.0] * len(grads)
        params = self.model.params
        for i, g in enumerate(grads):
            self._velocity[i] = self.beta * self._velocity[i] + (1.0 - self.beta) * g
            params[i] -= self.lr * self._velocity[i]


@configurable
class Trainer:
    """Wires model + dataset + optimizer and runs the loop.

    Args:
        model: The model to train (``!ref:model`` in YAML).
        dataset: The training data (``!ref:dataset``).
        optimizer: A DEFERRED optimizer (``!lazy:`` in YAML) — built inside
            ``fit()`` once the model's parameters exist.
        max_epochs: Training length — the knob overlays override.
        device: Reached by the broadcast ``device`` key.
        verbose: Reached by the broadcast ``verbose`` key.
    """

    def __init__(
        self,
        model: Any = None,
        dataset: Any = None,
        optimizer: Any = None,
        max_epochs: int = 10,
        device: str = "cpu",
        verbose: bool = False,
    ):
        self.model = model
        self.dataset = dataset
        self.optimizer = optimizer
        self.max_epochs = max_epochs
        self.device = device
        self.verbose = verbose

    def fit(self) -> float:
        # The canonical runtime injection: the !lazy: optimizer finally gets
        # the argument only the run can supply — the live model.
        opt = flow(self.optimizer, model=self.model)
        points = self.dataset.points
        loss = float("inf")
        for epoch in range(self.max_epochs):
            loss, grads = self.model.loss_and_grads(points)
            opt.step(grads)
            if self.verbose:
                print(f"    epoch {epoch}: loss={loss:.4f}")
        return loss


# --------------------------------------------------------------------------
# The four acts
# --------------------------------------------------------------------------


def load_trainer(config: str, scopes: Optional[List[str]] = None) -> Trainer:
    cfg = load(str(CFG_DIR / config), scopes=scopes or [])
    trainer = flow(cfg["trainer"])  # idempotent: builds a deferred stub, passes a live one through
    assert isinstance(trainer, Trainer)
    return trainer


def main() -> None:
    print("=== Act 1: defaults (experiment_quick.yaml) ===")
    trainer = load_trainer("experiment_quick.yaml")
    assert isinstance(trainer.model, MLP), f"default group should pick MLP, got {type(trainer.model)}"
    # The bare `seed: 7` broadcast reached BOTH the dataset and the model — zero plumbing.
    assert trainer.dataset.seed == 7 and trainer.model.seed == 7
    assert trainer.max_epochs == 3, "trainer.max_epochs addressed override from the overlay"
    assert trainer.dataset.n_samples == 32, "SyntheticDataset class-block override from the overlay"
    loss = trainer.fit()
    print(f"model=MLP(hidden={trainer.model.hidden}) epochs={trainer.max_epochs} loss={loss:.4f}")

    print("\n=== Act 2: group selection — scopes=['model=cnn', 'optimizer=adam'] ===")
    trainer = load_trainer("experiment_quick.yaml", scopes=["model=cnn", "optimizer=adam"])
    assert isinstance(trainer.model, CNN), "model=cnn selects the CNN group"
    loss = trainer.fit()
    assert isinstance(flow(trainer.optimizer), Adam), "optimizer=adam selects the Adam group"
    print(f"model=CNN(channels={trainer.model.channels}) loss={loss:.4f}")

    print("\n=== Act 3: experiment overlay (experiment_full.yaml) ===")
    trainer = load_trainer("experiment_full.yaml")
    assert trainer.max_epochs == 400, "overlay overrides base (document order, last write wins)"
    assert trainer.model.hidden == 128, "MLP class-block override from the overlay"
    loss = trainer.fit()
    opt = flow(trainer.optimizer)
    assert opt.lr == 0.1, "!ref:base_lr picked up the overlay's base_lr"
    w, b = trainer.model.params
    assert abs(w - 2.0) < 0.1 and abs(b + 1.0) < 0.1, "the long run genuinely recovers y = 2x - 1"
    print(f"hidden={trainer.model.hidden} epochs={trainer.max_epochs} lr={opt.lr} loss={loss:.4f}")
    print(f"recovered w={w:.3f} b={b:.3f} (target: w=2, b=-1)")

    print("\n=== Act 4: reproducibility — dump the wiring, load it back ===")
    snapshot = dump(trainer)
    print(snapshot)
    clone = flow(load(snapshot))
    assert isinstance(clone, Trainer) and isinstance(clone.model, MLP)
    assert clone.max_epochs == trainer.max_epochs
    assert clone.model.hidden == trainer.model.hidden
    assert clone.dataset.n_samples == trainer.dataset.n_samples
    print("snapshot round-trip OK — the archived YAML rebuilds the identical experiment")


if __name__ == "__main__":
    main()
