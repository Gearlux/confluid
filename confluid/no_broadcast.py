"""``NoBroadcast[T]`` annotation — exclude a constructor parameter from BARE-KEY broadcasting.

Broadcasting matches by NAME alone: a top-level YAML key like ``name:`` /
``path:`` / ``size:`` flows into ANY class whose accept-list carries the key —
usually the ergonomic win, occasionally a silent-wrong-value hazard for very
generic parameter names. Mark such a parameter ``NoBroadcast[T]`` and BARE
top-level keys no longer reach it, while every ADDRESSED form keeps working:
``ClassName: {param: value}`` blocks, instance-name blocks, and
post-construction ``configure()`` blocks all still set it (the marker is a
broadcast-only exclusion — the accept-list itself is untouched).

The coarse per-class counterpart is ``@configurable(broadcast=False)``, which
blocks ALL bare-key broadcasts into instances of that class.

Example::

    from confluid import NoBroadcast, configurable

    @configurable
    class Transform:
        def __init__(self, name: NoBroadcast[str] = "t", strength: float = 1.0):
            self.name = name        # a top-level ``name:`` key no longer lands here
            self.strength = strength  # still broadcastable

Type-checkers see ``NoBroadcast[T]`` as ``T`` — the marker only affects runtime
inspection (see :func:`is_no_broadcast_annotation`). It mirrors
:data:`confluid.Lazy` / :data:`confluid.Mandatory` in shape and composes with
them; ``to_pydantic`` strips it so it never leaks into JSON schemas.

Unlike ``Lazy[T]`` / ``Mandatory[T]``, this alias deliberately does NOT union a
``Fluid`` arm into ``T``: those two mark *dependency* slots (which legitimately
hold a deferred ``Fluid`` stub pre-flow), whereas ``NoBroadcast`` is a routing
gate for generically-NAMED scalar knobs (``name``, ``path``, ``size``) whose
values are plain scalars — a ``Fluid`` arm would misdescribe them.
"""

from typing import Annotated, Any, FrozenSet, TypeVar

from confluid.introspect import annotation_has_marker, marked_param_names

T = TypeVar("T")

_NO_BROADCAST_MARKER = "__confluid_no_broadcast__"

NoBroadcast = Annotated[T, _NO_BROADCAST_MARKER]
"""Type alias: ``NoBroadcast[T]`` is ``Annotated[T, _NO_BROADCAST_MARKER]``.

Type-checkers see ``NoBroadcast[T]`` as ``T``; the marker only affects runtime
inspection (see :func:`is_no_broadcast_annotation`). No ``Fluid`` union arm —
see the module docstring for why this deliberately differs from ``Lazy`` /
``Mandatory``.
"""


def is_no_broadcast_annotation(annotation: Any) -> bool:
    """True iff ``annotation`` was declared with ``NoBroadcast[...]`` — at any wrapper depth."""
    return annotation_has_marker(annotation, _NO_BROADCAST_MARKER)


def no_broadcast_param_names(cls: Any) -> FrozenSet[str]:
    """Return the signature parameter names of ``cls`` declared ``NoBroadcast[...]``.

    The scan is :func:`confluid.introspect.marked_param_names` — the ONE
    scan-plus-cache behind all three marker helpers (this module used to carry
    the only callable-correct copy of the class-vs-callable dispatch by hand;
    the helper routes through ``init_callable`` for everyone). Cached per-target
    on ``cls.__confluid_no_broadcast_params__``. Returns the empty set for
    targets without a resolvable signature / hints (plain callables included —
    a builder function's params can carry the marker too).
    """
    return frozenset(marked_param_names(cls, _NO_BROADCAST_MARKER, "__confluid_no_broadcast_params__"))
