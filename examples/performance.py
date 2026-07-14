"""Performance baseline — the runnable companion to ``docs/performance.md``.

Flows a synthetic config tree large enough (thousands of markers) to make the
scoped-broadcasting machinery (the ``_View`` context wrappers threading
``_prepare_kwargs`` / ``_splice_kwargs_at_slot`` / ``_flow_recursive`` and the
``configure()`` mirror) show up in a profile, and prints per-phase timings.

Print-only by design: CI executes every example, so this script never asserts
on timings — runner variance would make that flaky. It exists as a baseline to
eyeball across engine changes; set ``CONFLUID_BENCH_PROFILE=1`` to add a
cProfile breakdown of one ``materialize`` pass.
"""

import cProfile
import os
import pstats
import time
from typing import Any, Callable, List

import yaml

from confluid import configurable, configure, materialize, resolve
from confluid.loader import ConfluidLoader

GROUPS = 10
SUBGROUPS = 10
MARKERS = 20  # per subgroup; every 4th carries a nested child Instance
REPEATS = 3


@configurable
class Stage:
    def __init__(self, lr: float = 0.01, momentum: float = 0.9, tag: str = "s", child: Any = None) -> None:
        """A minimal stage — one broadcastable float pair, a label, a nested slot.

        Args:
            lr: Learning rate — the bare-broadcast target.
            momentum: Second broadcast knob.
            tag: Identity label (each marker sets its own).
            child: Optional nested stage.
        """
        self.lr = lr
        self.momentum = momentum
        self.tag = tag
        self.child = child


def build_yaml() -> str:
    """Build the synthetic document: broadcast keys at the root, a deep marker forest below.

    The root exercises every key-scope path: two bare keys (tree-wide BARE
    cascade), a ``'**'`` glob block, an addressed ``Stage:`` class block
    (EXACT), and a dotted glob key (dotted expansion + STRICT routing).
    """
    lines: List[str] = [
        "lr: 0.005",
        "momentum: 0.8",
        "'**':",
        "  momentum: 0.85",
        "Stage:",
        "  tag: addressed",
        "groups.g0.**.lr: 0.001",
        "groups:",
    ]
    for g in range(GROUPS):
        lines.append(f"  g{g}:")
        for s in range(SUBGROUPS):
            lines.append(f"    s{s}:")
            for m in range(MARKERS):
                lines.append(f"      m{m}: !class:Stage()")
                lines.append(f"        tag: g{g}s{s}m{m}")
                if m % 4 == 0:
                    lines.append("        child: !class:Stage()")
    return "\n".join(lines) + "\n"


def timed(label: str, marker_count: int, fn: Callable[[], Any]) -> None:
    """Run ``fn`` REPEATS times and print best/mean wall time + throughput."""
    samples = []
    for _ in range(REPEATS):
        start = time.perf_counter()
        fn()
        samples.append(time.perf_counter() - start)
    best, mean = min(samples), sum(samples) / len(samples)
    print(
        f"{label:<12} {marker_count:>5} markers   best {best * 1e3:>8.1f} ms   "
        f"mean {mean * 1e3:>8.1f} ms   {marker_count / best:>8.0f} markers/s"
    )


def main() -> None:
    text = build_yaml()
    top = GROUPS * SUBGROUPS * MARKERS
    nested = sum(1 for m in range(MARKERS) if m % 4 == 0) * GROUPS * SUBGROUPS
    markers = top + nested
    print(f"tree: {GROUPS}x{SUBGROUPS} groups, {top} top markers + {nested} nested = {markers} markers")

    # Each phase re-parses: flow() memoizes Instance markers, so a re-used parse
    # would measure the memo hit, not the engine.
    timed("parse", markers, lambda: yaml.load(text, Loader=ConfluidLoader))
    timed("materialize", markers, lambda: materialize(yaml.load(text, Loader=ConfluidLoader)))
    timed("resolve", markers, lambda: resolve(yaml.load(text, Loader=ConfluidLoader)))

    tree = materialize(yaml.load(text, Loader=ConfluidLoader))
    reconf = {"lr": 0.002, "Stage": {"momentum": 0.7}, "**": {"tag": "reconf"}}
    timed("configure", markers, lambda: configure(tree, config=reconf))

    if os.environ.get("CONFLUID_BENCH_PROFILE"):
        parsed = yaml.load(text, Loader=ConfluidLoader)
        profiler = cProfile.Profile()
        profiler.enable()
        materialize(parsed)
        profiler.disable()
        pstats.Stats(profiler).sort_stats("cumulative").print_stats(25)


if __name__ == "__main__":
    main()
