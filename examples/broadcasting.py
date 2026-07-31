"""Broadcasting & ordered matching — the runnable companion to ``docs/broadcasting.md``.

Shows a bare top-level key landing on every accepting sibling (document order,
last write wins), addressed keys stopping exactly at their node, the ``*`` /
``**`` glob forms opting back into the cascade, both opt-outs (the param-level
``NoBroadcast[str]`` marker and the class-level
``@configurable(broadcast=False)``), the ``**kwargs``-constructor caveat
(an unknowable accept-list broadcasts permissively), and the two public
predicates (``accepts_key`` / ``accepts_broadcast``) that let code outside a
YAML document ask the same question the engine asks.

For the same rules applied at scenario scale — a four-level service tree
configured with zero parameter-threading code — see
``examples/deep_injection.py``.
"""

from typing import Any, Optional

from confluid import NoBroadcast, accepts_broadcast, accepts_key, configurable, load


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
    kwargs_catch_all()
    settability_predicates()


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


@configurable(validate=False)
class Passthrough:
    def __init__(self, **kwargs: Any) -> None:
        """A ``**kwargs`` catch-all constructor — the accept-list is unknowable.

        Args:
            kwargs: Arbitrary options, stored verbatim.
        """
        self.options = dict(kwargs)


def kwargs_catch_all() -> None:
    """A ``**kwargs`` constructor broadcasts PERMISSIVELY — every bare key lands.

    Confluid cannot enumerate such a class's parameters, so it errs permissive
    (accept-everything) and announces it once per class at TRACE level. Use
    ``@configurable(broadcast=False)`` or explicit parameters when that soaks
    up keys you did not intend (docs/broadcasting.md → "Classes with
    ``**kwargs`` constructors").
    """
    graph = load(
        """
sink: !class:Passthrough()
name: run-42
strength: 0.75
"""
    )
    sink = graph["sink"]
    assert sink.name == "run-42" and sink.strength == 0.75, "every bare key broadcast in"
    print(f"Passthrough (**kwargs): received name={sink.name!r} strength={sink.strength} (unfiltered)")


def settability_predicates() -> None:
    """Ask confluid whether a key may land — instead of re-deriving the rules.

    A front-end that delivers configuration from outside a YAML document (a CLI
    turning ``--strength 2.0`` into a config change, a form editor, an RPC
    surface) needs the SAME answer the engine uses. ``accepts_key`` is the
    ADDRESSED question (gated by the accept-list alone); ``accepts_broadcast``
    is the BARE question (accept-list AND both opt-outs). Re-deriving them by
    hand typically misses ``**kwargs`` targets, ``__init__``-body slots, and the
    opt-outs — so an opted-out class quietly accepts a bare key anyway
    (docs/broadcasting.md → "Asking whether a key may land").
    """
    # Class-level opt-out: addressed writes still land, bare keys never do.
    assert accepts_key(Reporter, "strength"), "`Reporter: {strength: ...}` is legal"
    assert not accepts_broadcast(Reporter, "strength"), "a bare `strength:` must not cascade in"

    # Param-level opt-out: only that one slot is shielded.
    assert accepts_key(Transform, "name") and not accepts_broadcast(Transform, "name")
    assert accepts_broadcast(Transform, "strength"), "its sibling is unaffected"

    # Unknowable accept-list -> accepts everything; unknown names -> nothing.
    assert accepts_broadcast(Passthrough, "anything_at_all")
    assert not accepts_key(Transform, "typo")

    print("predicates: Reporter.strength addressed=True bare=False; Transform.name bare=False")


if __name__ == "__main__":
    main()
