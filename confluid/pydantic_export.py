"""Auto-generate pydantic ``BaseModel`` subclasses from ``@configurable`` classes.

The generated models mirror each class's ``__init__`` signature as typed
pydantic fields and recursively wrap nested ``@configurable`` parameter types
in their own generated models. Non-``@configurable`` types are passed through
unchanged (primitives, ``Optional``, ``Literal``, ``Union``, ``List``,
``Dict``, ``Tuple``, library types).

Generated models carry a ``_confluid_class`` class attribute holding the
dotted importable path of the target class. Downstream serializers (e.g.
``navigaitor.serialize``) read this to emit Confluid ``!class:`` tags.

Auto-generated models are intentionally permissive — they expose every
``__init__`` parameter without extra constraints. Hand-written pydantic
models (with ``model_validator`` and tighter ``Field(...)`` bounds) act as
opinionated overlays for LLM-facing tool surfaces.
"""

from __future__ import annotations

import inspect
import types
from functools import lru_cache
from typing import (
    Annotated,
    Any,
    Dict,
    FrozenSet,
    List,
    Literal,
    Set,
    Tuple,
    Type,
    Union,
    get_args,
    get_origin,
    get_type_hints,
)

from pydantic import BaseModel, ConfigDict, Field, create_model

from confluid.lazy import is_lazy_annotation
from confluid.schema import _parse_docstring

_SKIP_PARAMS = {"self", "cls", "args", "kwargs"}


class _StrictConfigBase(BaseModel):
    """Base class for generated models — forbids unknown fields and allows arbitrary types.

    ``extra="forbid"`` rejects unknown kwargs so LLM-emitted configs surface
    typos. ``arbitrary_types_allowed`` lets nested annotations include
    library types we haven't (and don't want to) introspect (e.g. a sentinel
    ``Path`` from pathlib, or any user class without a pydantic mirror).
    """

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)


def _is_configurable(obj: Any) -> bool:
    """True when ``obj`` is a class decorated with ``@configurable``."""
    return isinstance(obj, type) and bool(getattr(obj, "__confluid_configurable__", False))


def _qualname(cls: type) -> str:
    """Return the dotted importable path of ``cls`` suitable for ``!class:`` tags."""
    module = getattr(cls, "__module__", None)
    name = getattr(cls, "__qualname__", None) or cls.__name__
    if module is None or module in ("builtins", "__main__"):
        return name
    return f"{module}.{name}"


def _unwrap_annotated(anno: Any) -> Any:
    """Strip ``Annotated[X, ...]`` wrappers (incl. ``Lazy[X]``) down to ``X``.

    Pydantic 2 handles ``Annotated`` natively, but we want the field type to be
    plain ``X`` so nested ``@configurable`` detection works. The lazy marker is
    preserved on the source class via :func:`confluid.lazy.lazy_param_names`.
    """
    while get_origin(anno) is Annotated:
        anno = get_args(anno)[0]
    return anno


def _convert_annotation(anno: Any) -> Any:
    """Recursively replace ``@configurable`` types inside ``anno`` with generated models.

    Leaves typing constructs intact (``Optional``, ``Union``, ``List``,
    ``Dict``, ``Tuple``, ``Literal``, etc.) but recurses into their type args.
    Unknown / opaque types are returned as-is.
    """
    anno = _unwrap_annotated(anno)

    # Plain Any / no annotation
    if anno is Any or anno is None or anno is type(None):
        return anno

    origin = get_origin(anno)
    if origin is None:
        if _is_configurable(anno):
            return to_pydantic(anno)
        return anno

    # ``Literal[...]`` arguments are values, not types — don't recurse.
    if origin is Literal:
        return anno

    raw_args = get_args(anno)
    new_args = tuple(_convert_annotation(a) for a in raw_args)

    # Union / Optional / PEP 604 (X | Y)
    if origin is Union or origin is types.UnionType:
        return Union[new_args]  # type: ignore[return-value]

    # Generic aliases for the common containers — reconstruct with converted args.
    builtin_map: Dict[Any, Any] = {
        list: List,
        tuple: Tuple,
        dict: Dict,
        set: Set,
        frozenset: FrozenSet,
    }
    generic = builtin_map.get(origin, origin)
    try:
        if len(new_args) == 1:
            return generic[new_args[0]]
        return generic[new_args]
    except TypeError:
        return anno  # punt: leave the original annotation intact


def _field_for_param(param: inspect.Parameter, anno: Any, description: str) -> Tuple[Any, Any]:
    """Build a ``(type, FieldInfo)`` tuple for ``pydantic.create_model``.

    Handles required vs. defaulted fields and converts mutable defaults
    (``list``/``dict``/``set``) into ``default_factory`` to satisfy pydantic.
    """
    converted_type = _convert_annotation(anno)
    desc_kw: Dict[str, Any] = {"description": description} if description else {}

    if param.default is inspect.Parameter.empty:
        return converted_type, Field(..., **desc_kw)

    default = param.default
    if isinstance(default, (list, dict, set)):
        # Capture by value to avoid the closing-over-loop-variable bug.
        snapshot = type(default)(default)
        return converted_type, Field(default_factory=lambda snapshot=snapshot: type(snapshot)(snapshot), **desc_kw)
    return converted_type, Field(default=default, **desc_kw)


@lru_cache(maxsize=None)
def to_pydantic(cls: type) -> Type[BaseModel]:
    """Return a pydantic ``BaseModel`` subclass mirroring ``cls.__init__``.

    Each call with the same ``cls`` returns the same model (cached). Nested
    ``@configurable`` parameter types are recursively wrapped via the same
    function, which gives correct identity for shared sub-types and breaks
    most reference cycles (the cache returns the in-flight class on second
    visit before recursion fully unwinds; explicit cycles would still need
    forward refs — not common in ML configs).

    The returned model has:

    * One field per non-excluded ``__init__`` parameter, with the original
      type (with nested ``@configurable`` types replaced by their generated
      models) and the original default value.
    * A ``_confluid_class`` class attribute holding the dotted importable
      path of ``cls`` — used by the pydantic→Confluid YAML serializer.
    * ``model_config = ConfigDict(extra="forbid")`` so unknown fields raise.
    * The same docstring as ``cls`` (or its ``__init__``) for ergonomics in
      tooling that reads ``__doc__``.

    Excluded parameters: ``self``, ``cls``, ``*args``, ``**kwargs``, and any
    parameter whose class attribute is decorated with ``@ignore_config``.

    Args:
        cls: A class. Typically ``@configurable``-decorated, but any class
            with an inspectable ``__init__`` is accepted — the function does
            not enforce the marker so hand-built pydantic mirrors of
            third-party classes can be produced.

    Returns:
        A new pydantic ``BaseModel`` subclass.

    Raises:
        TypeError: If ``cls`` is not a class or its ``__init__`` is not
            inspectable (e.g. C extension types without Python wrappers).
    """
    if not isinstance(cls, type):
        raise TypeError(f"to_pydantic(cls) expected a class, got {type(cls).__name__}")

    init = cls.__dict__.get("__init__") or cls.__init__  # type: ignore[misc]
    if init is object.__init__:
        # Classes that don't override __init__ have no configurable params.
        sig = inspect.Signature(parameters=[])
        hints: Dict[str, Any] = {}
        docstring = cls.__doc__ or ""
    else:
        try:
            sig = inspect.signature(init)
            hints = get_type_hints(init, include_extras=True)
        except (TypeError, ValueError, NameError) as exc:
            raise TypeError(f"Cannot introspect {cls.__name__}.__init__: {exc}") from exc
        docstring = init.__doc__ or cls.__doc__ or ""

    param_docs = _parse_docstring(docstring)
    fields: Dict[str, Tuple[Any, Any]] = {}

    for param_name, param in sig.parameters.items():
        if param_name in _SKIP_PARAMS:
            continue
        if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue

        member = getattr(cls, param_name, None)
        if member is not None and getattr(member, "__confluid_ignore__", False):
            continue

        anno = hints.get(param_name, Any)
        fields[param_name] = _field_for_param(param, anno, param_docs.get(param_name, ""))

    # ``create_model`` accepts arbitrary kwargs as field definitions, so we
    # unpack ``fields`` alongside the dunder kwargs. The cast keeps mypy from
    # treating the unpack as a single dict positional.
    model: Type[BaseModel] = create_model(  # type: ignore[call-overload]
        f"{cls.__name__}Config",
        __base__=_StrictConfigBase,
        __module__=__name__,
        **fields,
    )
    # Attach the target class identifier as a plain class attribute. We
    # deliberately do NOT declare it as a model field — pydantic ignores
    # non-Field class attributes set after model construction.
    model._confluid_class = _qualname(cls)  # type: ignore[attr-defined]
    if cls.__doc__:
        model.__doc__ = cls.__doc__

    # Preserve the lazy-param marker set for downstream consumers (the
    # serializer can emit `!lazy:` instead of `!class:` for these params).
    lazy_params = {name for name, anno in hints.items() if name not in _SKIP_PARAMS and is_lazy_annotation(anno)}
    if lazy_params:
        model._confluid_lazy_params = frozenset(lazy_params)  # type: ignore[attr-defined]

    return model


def confluid_class_of(model_or_instance: Any) -> str | None:
    """Return the ``!class:`` target stored on a generated model, or ``None``."""
    if isinstance(model_or_instance, BaseModel):
        cls: type = type(model_or_instance)
    elif isinstance(model_or_instance, type):
        cls = model_or_instance
    else:
        return None
    val = getattr(cls, "_confluid_class", None)
    return val if isinstance(val, str) else None


def lazy_param_names_of(model_or_instance: Any) -> FrozenSet[str]:
    """Return the set of lazy-marked param names on a generated model, or empty."""
    if isinstance(model_or_instance, BaseModel):
        cls: type = type(model_or_instance)
    elif isinstance(model_or_instance, type):
        cls = model_or_instance
    else:
        return frozenset()
    val = getattr(cls, "_confluid_lazy_params", None)
    return val if isinstance(val, frozenset) else frozenset()
