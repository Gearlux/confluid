"""``Mandatory[T]`` annotation — declare a constructor input as a REQUIRED contract slot.

Mark a constructor parameter with ``Mandatory[T]`` to declare that the input
**must** be provided (wired / non-null) for the object to run — *independently*
of whether the parameter carries a default. This is the explicit complement to
the structural convention confluid already uses (a parameter with no default, or
a non-``Optional`` type, reads as required): under the **Zero-Arg Construction**
mandate every parameter tends to be defaulted, which would make a genuinely
mandatory class / ``Fluid`` slot *look* optional. ``Mandatory[T]`` restores the
contract so consumers (FluxStudio sockets, navigaitor's form-spec, MCP schemas)
can render the slot as required even when it is defaulted for zero-arg build.

Named ``Mandatory`` (NOT ``Required``) to avoid confusion with
``typing.Required`` (a TypedDict-field marker with unrelated semantics).

Subscript with the **interface the slot flows into** — a mandatory dependency
slot typically receives either a live instance or a deferred ``Fluid`` stub, so
the alias bakes the ``Fluid`` arm in (like :data:`confluid.Lazy`). The canonical
spellings::

    from torch import nn
    from torch.optim import Adam, Optimizer

    from confluid import Class, Lazy, configurable, flow
    from confluid.mandatory import Mandatory

    @configurable
    class Trainer:
        def __init__(
            self,
            model: Mandatory[nn.Module] = Class(TimmModel),
            optimizer: Mandatory[Lazy[Optimizer]] = Class(Adam, lr=1e-3),
        ):
            self.model = model          # required contract slot
            self.optimizer = optimizer  # required AND kept deferred until run time

        def configure_optimizers(self):
            return flow(self.optimizer, params=self.parameters())

``Mandatory[Lazy[T]]`` is the composed form for a required slot that must also
stay deferred (runtime injection). The marker only affects runtime inspection
(see :func:`is_mandatory_annotation`); detection walks nested ``Annotated`` /
``Union`` layers (:func:`confluid.introspect.annotation_has_marker`), so the
marker buried inside the composed alias's ``Union`` arm is still found.
"""

from typing import Annotated, Any, Set, TypeVar, Union, get_type_hints

from confluid.fluid import Fluid
from confluid.introspect import annotation_has_marker

T = TypeVar("T")

_MANDATORY_MARKER = "__confluid_mandatory__"

Mandatory = Annotated[Union[T, Fluid], _MANDATORY_MARKER]
"""Type alias: ``Mandatory[T]`` is ``Annotated[Union[T, Fluid], _MANDATORY_MARKER]``.

``T`` is the interface the slot flows into; the ``Fluid`` arm admits the deferred
``Class``/``LazyClass`` stub a dependency slot holds pre-flow, so
``model: Mandatory[nn.Module] = Class(TimmModel)`` type-checks under strict mypy
(previously this required spelling ``Mandatory[Union[nn.Module, Fluid]]`` by
hand). The marker only affects runtime inspection (see
:func:`is_mandatory_annotation`).
"""


def is_mandatory_annotation(annotation: Any) -> bool:
    """True iff ``annotation`` was declared with ``Mandatory[...]`` — at any wrapper depth."""
    return annotation_has_marker(annotation, _MANDATORY_MARKER)


def mandatory_param_names(cls: type) -> Set[str]:
    """Return the ``__init__`` parameter names of ``cls`` declared ``Mandatory[...]``.

    Cached per-class on ``cls.__confluid_mandatory_params__`` so introspecting
    consumers (FluxStudio's runnable-node builder) don't re-resolve hints on every
    call. Returns an empty set if ``cls`` has no resolvable ``__init__`` or no
    Mandatory params.
    """
    cached = getattr(cls, "__confluid_mandatory_params__", None)
    if cached is not None:
        return cached  # type: ignore[no-any-return]
    init = getattr(cls, "__init__", None)
    if init is None:
        return set()
    try:
        hints = get_type_hints(init, include_extras=True)
    except Exception:
        return set()
    names = {name for name, ann in hints.items() if is_mandatory_annotation(ann)}
    try:
        cls.__confluid_mandatory_params__ = names  # type: ignore[attr-defined]
    except (AttributeError, TypeError):
        pass
    return names
