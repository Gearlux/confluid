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

Example::

    from confluid import configurable
    from confluid.mandatory import Mandatory

    @configurable
    class Trainer:
        def __init__(self, model: Mandatory[Any], lr: float = 1e-3):
            self.model = model  # the contract says: must be wired before run()
            self.lr = lr

Type-checkers see ``Mandatory[T]`` as ``T`` — the marker only affects runtime
inspection (see :func:`is_mandatory_annotation`). It mirrors :data:`confluid.Lazy`
in shape; the two compose (``Mandatory[Lazy[T]]``) since both are string markers
in ``Annotated.__metadata__``.
"""

from typing import Annotated, Any, Set, TypeVar, get_type_hints

T = TypeVar("T")

_MANDATORY_MARKER = "__confluid_mandatory__"

Mandatory = Annotated[T, _MANDATORY_MARKER]
"""Type alias: ``Mandatory[T]`` is ``Annotated[T, _MANDATORY_MARKER]``.

Type-checkers see ``Mandatory[T]`` as ``T``; the marker only affects runtime
inspection (see :func:`is_mandatory_annotation`).
"""


def is_mandatory_annotation(annotation: Any) -> bool:
    """True iff ``annotation`` was declared with ``Mandatory[...]``."""
    return _MANDATORY_MARKER in getattr(annotation, "__metadata__", ())


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
