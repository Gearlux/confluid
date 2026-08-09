"""``Lazy[T]`` annotation — opt out of eager deep-flow.

Mark a constructor parameter with ``Lazy[T]`` to declare that the attribute is
**intentionally** kept as a deferred ``Fluid`` even when an external walker
(e.g. liquifai's ``flow_mode="auto"``) would otherwise eagerly flow it. Use
this when the attribute will be flowed at runtime with extra kwargs that
aren't available at construction time — the canonical case is an optimizer
that wants ``params=self.parameters()``.

Subscript with the **interface the slot eventually flows into** (the abstract
base, not the concrete default) — ``Lazy[Optimizer]``, not ``Lazy[Adam]``::

    from torch.optim import Adam, Optimizer

    from confluid import Class, configurable, flow
    from confluid.lazy import Lazy

    @configurable
    class Trainer:
        def __init__(self, optimizer: Lazy[Optimizer] = Class(Adam, lr=1e-3)):
            self.optimizer = optimizer  # stays a Class stub

        def configure_optimizers(self):
            return flow(self.optimizer, params=self.parameters())

Without ``Lazy``, ``flow_mode="auto"`` would eagerly call ``flow(optimizer)``
at script init — which fails because ``Adam`` requires ``params``.
"""

from typing import Annotated, Any, Set, TypeVar, Union

from confluid.fluid import Fluid
from confluid.introspect import (
    annotation_has_marker,
    init_lazy_setattr_names,
    marked_param_names,
    resolve_ast_annotation,
    scan_init_body,
)

T = TypeVar("T")

_LAZY_MARKER = "__confluid_lazy__"

Lazy = Annotated[Union[T, Fluid], _LAZY_MARKER]
"""Type alias: ``Lazy[T]`` is ``Annotated[Union[T, Fluid], _LAZY_MARKER]``.

``T`` is the type the slot flows into once ``flow()``'d; the ``Fluid`` arm is
what makes the alias honest to static checkers — pre-flow, the slot holds a
deferred ``Class``/``LazyClass`` stub, so ``optimizer: Lazy[Optimizer] =
Class(Adam, lr=1e-3)`` type-checks (a ``Class`` *is* a ``Fluid``). Use the
interface type for ``T`` (``Lazy[Optimizer]``); ``Lazy[Any]`` stays valid when
the target type is genuinely open. Post-flow narrowing is served by
``confluid.cast(node, Optimizer)``. The marker only affects runtime inspection
(see :func:`is_lazy_annotation`).
"""


def is_lazy_annotation(annotation: Any) -> bool:
    """True iff ``annotation`` was declared with ``Lazy[...]`` — at any wrapper depth.

    Walks nested ``Annotated`` / ``Union`` layers so composed spellings
    (``Mandatory[Lazy[T]]``, ``Optional[Lazy[T]] = None``) are detected even
    though the union-carrying aliases bury the inner marker in a Union arm.
    """
    return annotation_has_marker(annotation, _LAZY_MARKER)


def body_slot_lazy_names(cls: Any) -> Set[str]:
    """Deferred ``__init__``-BODY slots across ``cls``'s ``@configurable`` MRO.

    ``cls`` may be any callable: a plain function has no ``__mro__``, so the
    walk below is empty and it reports no body slots — which is correct, a
    function has no ``__init__`` body.

    A class with many deferred dependencies may declare them as body attributes rather
    than constructor parameters (AGENTS rule 4) — a trainer's ``optimizer`` /
    ``train_loader`` / ``lightning``. Such a slot is deferred if EITHER signal says so,
    and both are reported here so there is one answer rather than one per caller:

    * its **value** is a ``LazyClass(...)`` — what the serializer keys off, so the slot
      round-trips as ``!lazy:`` instead of a ``!class:`` the engine would eagerly flow;
    * its **annotation** is ``Lazy[T]`` — the declaration a reader and a type-checker see.

    Best-effort by construction. The scan reads ``__init__`` SOURCE, so a compiled /
    frozen / zip-imported deployment yields nothing (the documented packaged-mode caveat —
    run ``confluid-bake``); an annotation that will not evaluate degrades to ``Any``, which
    simply is not lazy.
    """
    names: Set[str] = set()
    for klass in getattr(cls, "__mro__", ()):
        if klass is object or not getattr(klass, "__confluid_configurable__", False):
            continue
        init = klass.__dict__.get("__init__")
        if init is None:
            continue
        names |= init_lazy_setattr_names(init)  # lazy by VALUE
        for slot in scan_init_body(init):  # lazy by ANNOTATION
            if slot.kind in ("assign", "annassign") and is_lazy_annotation(
                resolve_ast_annotation(slot.annotation, init)
            ):
                names.add(slot.name)
    return names


def lazy_param_names(cls: Any) -> Set[str]:
    """Every slot of ``cls`` declared ``Lazy[...]`` — constructor params AND body slots.

    Both are configurable slots (AGENTS rule 4), so both are reported. Scanning only the
    constructor made the marker load-bearing in one place and decorative in the other: a
    trainer whose deferred slots all live in the body (``self.optimizer`` /
    ``self.train_loader`` / ``self.lightning``) reported an EMPTY set while carrying a
    ``Lazy`` annotation on every one of them, so a deep-flow walker had nothing to honour
    and stayed correct only because those slots happened to hold ``LazyClass`` VALUES.

    ``cls`` may be a class OR any callable (a registered builder FUNCTION) — the
    signature scan is :func:`confluid.introspect.marked_param_names`, which
    dispatches through ``init_callable``; a function has no ``__init__`` body,
    so the body-slot union is empty for it.

    Cached per-class on ``cls.__confluid_lazy_params__`` (the UNION with the
    body-slot scan — which is why the shared helper is called uncached here) so
    deep-flow walkers don't re-introspect on every visit. Returns an empty set
    if ``cls`` has no resolvable signature or no Lazy slots.
    """
    # Read the cache from the class's OWN __dict__, never getattr — getattr
    # walks the MRO, so a subclass queried after its parent returned the
    # PARENT's cached answer (measured: a Sub declaring ``opt: Lazy[Any]``
    # reported set() after Base was queried first, and the reverse direction
    # inherited markers the subclass never declared). Same guard the registry
    # uses for ``__confluid_name__``.
    cached = cls.__dict__.get("__confluid_lazy_params__") if hasattr(cls, "__dict__") else None
    if cached is not None:
        return cached  # type: ignore[no-any-return]
    names = marked_param_names(cls, _LAZY_MARKER)
    names |= body_slot_lazy_names(cls)
    try:
        cls.__confluid_lazy_params__ = names
    except (AttributeError, TypeError):
        pass
    return names
