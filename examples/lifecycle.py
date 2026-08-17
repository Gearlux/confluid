"""The lifecycle: one document walked through every pass (docs/lifecycle.md).

Each stage prints what it changed and asserts the invariant the guide states, so
this file fails loudly if the order of passes ever shifts:

    parse -> import -> include -> scope -> interpolate -> expand
          -> broadcast -> flow -> solidify

Standalone and zero-arg: it writes its include tree to a temp directory and
cleans up after itself.
"""

import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional, cast

import yaml

# `PartialClass` is the MARKER class (what `!partial:` parses to); `confluid.Partial`
# is the annotation alias that declares a deferred SLOT — different things, easy to mix up.
from confluid import Fluid, PartialClass, configurable, dump, flow, load, load_config, resolve
from confluid.loader import ConfluidLoader

BASE_YAML = """
# Shared defaults, pulled in by the experiment file below.
dropout: 0.9
Encoder:
  width: 16
"""

EXPERIMENT_YAML = """
include: base.yaml

run_root: "${env:LIFECYCLE_ROOT}/runs"  # env interpolation — burns in at load
width: 32                             # a bare key: broadcasts to any node taking `width`

profile: !scope:mode=fast
  dropout: 0.0

encoder: !class:Encoder
head: !class:Head
head.units: 128                       # dotted key — nests at the position written here

optimizer: !partial:Adam                            # deferred: configured, never built by load()
  lr: 0.01
"""


@configurable
class Encoder:
    def __init__(self, width: int = 8, dropout: float = 0.5) -> None:
        self.width = width
        self.dropout = dropout

    def __repr__(self) -> str:
        return f"Encoder(width={self.width}, dropout={self.dropout})"


@configurable
class Head:
    def __init__(self, units: int = 10, dropout: float = 0.5) -> None:
        self.units = units
        self.dropout = dropout
        self._built: Optional[str] = None

    def solidify(self) -> None:
        """Finalize derived state — idempotent, as the contract requires."""
        if self._built is None:
            self._built = f"head[{self.units}x{self.dropout}]"

    def __repr__(self) -> str:
        return f"Head(units={self.units}, dropout={self.dropout}, built={self._built!r})"


@configurable
class Adam:
    """Stands in for a real optimizer: it needs a runtime `params` argument."""

    def __init__(self, params: Any, lr: float = 0.001) -> None:
        self.params = params
        self.lr = lr

    def __repr__(self) -> str:
        return f"Adam(params={self.params!r}, lr={self.lr})"


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "base.yaml").write_text(BASE_YAML)
        (root / "experiment.yaml").write_text(EXPERIMENT_YAML)
        os.environ["LIFECYCLE_ROOT"] = "/store"
        cwd = Path.cwd()
        os.chdir(root)  # so `include: base.yaml` resolves next to the including file
        try:
            _walk_the_passes(root / "experiment.yaml")
        finally:
            os.chdir(cwd)
            os.environ.pop("LIFECYCLE_ROOT", None)


def _walk_the_passes(path: Path) -> None:
    print("=== 1. PARSE — reserved keys become typed markers ===")
    raw = yaml.load(path.read_text(), Loader=ConfluidLoader)
    print(f"encoder: {raw['encoder']!r}")
    print(f"optimizer: {raw['optimizer']!r}")
    print(f"profile: {raw['profile']!r}")
    print(f"run_root still literal: {raw['run_root']!r}")
    assert isinstance(raw["optimizer"], PartialClass)
    assert "${env:LIFECYCLE_ROOT}" in raw["run_root"]  # nothing is interpolated yet

    print("\n=== 2-3. IMPORT + INCLUDE — one document, includer read LAST ===")
    merged = load_config(str(path))
    print(f"merged key order: {list(merged)}")
    assert "Encoder" in merged  # came from base.yaml
    assert list(merged).index("dropout") < list(merged).index("width")

    print("\n=== 4-6. SCOPE, INTERPOLATE, EXPAND (load(flow=False)) ===")
    ir = cast(Dict[str, Any], load(str(path), flow=False, scopes=["mode=fast"]))
    print(f"scope spliced its contents:  dropout = {ir['dropout']}")
    print(f"interpolation burned in:     run_root = {ir['run_root']!r}")
    print(f"dotted key nested:           head kwargs = {ir['head'].kwargs}")
    assert ir["dropout"] == 0.0  # the `!scope:mode=fast` block replaced base.yaml's 0.9
    assert ir["run_root"] == "/store/runs"
    assert ir["head"].kwargs["units"] == 128
    assert isinstance(ir["encoder"], Fluid)  # still a marker — nothing is built yet

    print("\n=== 7. BROADCAST — document order, last spec wins (resolve()) ===")
    markers = cast(Dict[str, Any], resolve(str(path), scopes=["mode=fast"]))
    print(f"encoder marker kwargs:   {markers['encoder'].kwargs}")
    print(f"optimizer marker kwargs: {markers['optimizer'].kwargs}")
    # `width: 32` is bare and sits after the `Encoder:` block from base.yaml, so
    # it wins; `dropout: 0.0` reaches BOTH nodes, deferred slot included.
    assert markers["encoder"].kwargs["width"] == 32
    assert markers["optimizer"].kwargs["lr"] == 0.01  # the marker's own kwarg, untouched
    assert isinstance(markers["optimizer"], PartialClass)  # resolve() builds nothing

    print("\n=== 8-9. FLOW + SOLIDIFY — live objects, finalized post-order ===")
    built = cast(Dict[str, Any], load(str(path), scopes=["mode=fast"]))
    print(f"encoder:   {built['encoder']}")
    print(f"head:      {built['head']}")
    print(f"optimizer: {built['optimizer']!r}   <- still deferred")
    assert built["encoder"].width == 32 and built["encoder"].dropout == 0.0
    assert built["head"].units == 128
    assert built["head"]._built == "head[128x0.0]"  # solidify() ran AFTER the values landed
    assert isinstance(built["optimizer"], PartialClass)  # deferral withholds CONSTRUCTION only

    print("\n=== the deferred slot, built when its runtime argument exists ===")
    optimizer = flow(built["optimizer"], params="<model.parameters()>")
    print(f"flow(optimizer, params=...) -> {optimizer}")
    assert optimizer.lr == 0.01

    print("\n=== round trip: dump() emits what the passes decided ===")
    text = dump(built["encoder"])
    print(text.strip())
    reloaded = load(text)
    assert reloaded.width == 32 and reloaded.dropout == 0.0


if __name__ == "__main__":
    main()
