"""Broadcasting & ordered matching — the runnable companion to ``docs/broadcasting.md``.

Shows a bare top-level key landing on every accepting sibling (document order,
last write wins), addressed keys stopping exactly at their node, the ``*`` /
``**`` glob forms opting back into the cascade, and both opt-outs: the
param-level ``NoBroadcast[str]`` marker and the class-level
``@configurable(broadcast=False)``.
"""

from typing import Any, Optional

from confluid import NoBroadcast, configurable, load


@configurable
class Transform:
    def __init__(self, name: NoBroadcast[str] = "t", strength: float = 1.0) -> None:
        """A transform whose ``name`` opts out of bare-key broadcasting.

        Args:
            name: Identity label — too generic to accept a broadcast ``name:`` key.
            strength: Effect strength — still broadcastable.
        """
        self.name = name
        self.strength = strength


@configurable(broadcast=False)
class Reporter:
    def __init__(self, path: str = "out", strength: float = 0.0) -> None:
        """A class-level opt-out: NO bare key ever lands here.

        Args:
            path: Output path.
            strength: Same name as Transform's knob — must stay untouched.
        """
        self.path = path
        self.strength = strength


def main() -> None:
    graph = load(
        """
Transform:                # class-name block, first in document order
  strength: 0.25
name: global-label        # blocked by NoBroadcast[str] on Transform.name
transform: !class:Transform()
reporter: !class:Reporter()
strength: 0.75            # bare broadcast, LATER in document order -> last write wins
"""
    )
    transform, reporter = graph["transform"], graph["reporter"]

    assert transform.strength == 0.75, "last write wins: the later bare key overrode the earlier block"
    assert transform.name == "t", "NoBroadcast[str] blocked the bare 'name:' key"
    assert reporter.strength == 0.0, "@configurable(broadcast=False) blocked everything"
    print(f"Transform: name={transform.name!r} strength={transform.strength} (broadcast, last write wins)")
    print(f"Reporter:  path={reporter.path!r} strength={reporter.strength} (class-level opt-out)")

    # Addressed blocks always keep working, even for opted-out classes/params.
    addressed = load(
        """
reporter: !class:Reporter()
Reporter:
  strength: 9.0
"""
    )
    assert addressed["reporter"].strength == 9.0, "an addressed ClassName: block is never blocked"
    print(f"Addressed Reporter block still applies: strength={addressed['reporter'].strength}")

    scoped_broadcasting()


@configurable
class Stage:
    def __init__(self, child: Any = None, lr: float = 0.0, name: Optional[str] = None) -> None:
        """A nestable pipeline stage.

        Args:
            child: Optional nested stage.
            lr: Learning rate — the knob the scoping demo addresses.
            name: Instance name, matchable by addressed config paths.
        """
        self.child = child
        self.lr = lr
        self.name = name


_TREE = """
outer: !class:Stage()
  name: trainer
  child: !class:Stage()
    name: inner
    child: !class:Stage()
      name: leaf
"""


def scoped_broadcasting() -> None:
    """Addressed keys are exact; ``*`` / ``**`` globs opt back into the cascade."""

    def lrs(doc: str) -> tuple:
        root = load(_TREE + doc)["outer"]
        return (root.lr, root.child.lr, root.child.child.lr)

    assert lrs("lr: 0.9\n") == (0.9, 0.9, 0.9), "bare key == implicit '**.lr' — whole tree"
    assert lrs("trainer.lr: 0.5\n") == (0.5, 0.0, 0.0), "addressed key is exact — no cascade"
    assert lrs("trainer.*.lr: 0.5\n") == (0.0, 0.5, 0.0), "'*' = exactly one level (direct children)"
    assert lrs("trainer.**.lr: 0.5\n") == (0.5, 0.5, 0.5), "'**' = zero or more levels (declare-once)"
    print("Scoped broadcasting: bare=(tree)  trainer.lr=(exact)  trainer.*.lr=(children)  trainer.**.lr=(subtree)")


if __name__ == "__main__":
    main()
