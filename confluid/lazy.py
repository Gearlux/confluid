"""``Lazy[T]`` annotation — opt out of eager deep-flow.

Mark a constructor parameter with ``Lazy[T]`` to declare that the attribute is
**intentionally** kept as a deferred ``Fluid`` even when an external walker
(e.g. liquifai's ``flow_mode="auto"``) would otherwise eagerly flow it. Use
this when the attribute will be flowed at runtime with extra kwargs that
aren't available at construction time — the canonical case is an optimizer
that wants ``params=self.parameters()``.

Example::

    from confluid import Class, configurable, flow
    from confluid.lazy import Lazy

    @configurable
    class Trainer:
        def __init__(self, optimizer: Lazy[Any] = Class(Adam, lr=1e-3)):
            self.optimizer = optimizer  # stays a Class stub

        def configure_optimizers(self):
            return flow(self.optimizer, params=self.parameters())

Without ``Lazy``, ``flow_mode="auto"`` would eagerly call ``flow(optimizer)``
at script init — which fails because ``Adam`` requires ``params``.
"""

from typing import Annotated, Any, Set, TypeVar, get_type_hints

T = TypeVar("T")

_LAZY_MARKER = "__confluid_lazy__"

Lazy = Annotated[T, _LAZY_MARKER]
"""Type alias: ``Lazy[T]`` is ``Annotated[T, _LAZY_MARKER]``.

Type-checkers see ``Lazy[T]`` as ``T``; the marker only affects runtime
inspection (see :func:`is_lazy_annotation`).
"""


def is_lazy_annotation(annotation: Any) -> bool:
    """True iff ``annotation`` was declared with ``Lazy[...]``."""
    return _LAZY_MARKER in getattr(annotation, "__metadata__", ())


def lazy_param_names(cls: type) -> Set[str]:
    """Return the set of ``__init__`` parameter names of ``cls`` declared ``Lazy[...]``.

    Cached per-class on ``cls.__confluid_lazy_params__`` so deep-flow walkers
    don't re-introspect on every visit. Returns an empty set if ``cls`` has
    no resolvable ``__init__`` or no Lazy params.
    """
    cached = getattr(cls, "__confluid_lazy_params__", None)
    if cached is not None:
        return cached  # type: ignore[no-any-return]
    init = getattr(cls, "__init__", None)
    if init is None:
        return set()
    try:
        hints = get_type_hints(init, include_extras=True)
    except Exception:
        return set()
    names = {name for name, ann in hints.items() if is_lazy_annotation(ann)}
    try:
        cls.__confluid_lazy_params__ = names  # type: ignore[attr-defined]
    except (AttributeError, TypeError):
        pass
    return names
