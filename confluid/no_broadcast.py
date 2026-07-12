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
"""

from typing import Annotated, Any, FrozenSet, TypeVar, get_type_hints

T = TypeVar("T")

_NO_BROADCAST_MARKER = "__confluid_no_broadcast__"

NoBroadcast = Annotated[T, _NO_BROADCAST_MARKER]
"""Type alias: ``NoBroadcast[T]`` is ``Annotated[T, _NO_BROADCAST_MARKER]``.

Type-checkers see ``NoBroadcast[T]`` as ``T``; the marker only affects runtime
inspection (see :func:`is_no_broadcast_annotation`).
"""


def is_no_broadcast_annotation(annotation: Any) -> bool:
    """True iff ``annotation`` was declared with ``NoBroadcast[...]``."""
    return _NO_BROADCAST_MARKER in getattr(annotation, "__metadata__", ())


def no_broadcast_param_names(cls: Any) -> FrozenSet[str]:
    """Return the ``__init__`` parameter names of ``cls`` declared ``NoBroadcast[...]``.

    Cached per-class on ``cls.__confluid_no_broadcast_params__``. Returns the
    empty set for targets without a resolvable ``__init__`` / hints (plain
    callables included — a builder function's params can carry the marker too,
    resolved via the callable's own hints).
    """
    cached = getattr(cls, "__confluid_no_broadcast_params__", None)
    if cached is not None:
        return cached  # type: ignore[no-any-return]
    target = getattr(cls, "__init__", None) if isinstance(cls, type) else cls
    if target is None:
        return frozenset()
    try:
        hints = get_type_hints(target, include_extras=True)
    except Exception:
        return frozenset()
    names = frozenset(name for name, ann in hints.items() if is_no_broadcast_annotation(ann))
    try:
        cls.__confluid_no_broadcast_params__ = names
    except (AttributeError, TypeError):
        pass
    return names
