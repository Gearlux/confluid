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

import collections.abc
import enum
import inspect
import types
from functools import lru_cache
from typing import (
    Annotated,
    Any,
    Callable,
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

from annotated_types import Ge, Gt, Interval, Le, Lt
from pydantic import BaseModel, ConfigDict, Field, create_model

from confluid.exceptions import IntrospectionError
from confluid.introspect import init_lazy_setattr_names, scan_init_body
from confluid.lazy import is_lazy_annotation
from confluid.schema import _parse_docstring

_SKIP_PARAMS = {"self", "cls", "args", "kwargs"}

# Numeric range marks (PEP-593 ``annotated_types``) the workspace convention puts
# on the OUTER annotation of a ``(min, max)`` container param — see
# ``_spread_range_marks_into_container``.
_RANGE_MARK_TYPES: Tuple[type, ...] = (Interval, Ge, Gt, Le, Lt)

# Container origins whose numeric elements a relocated range mark applies to.
_RANGE_CONTAINER_ORIGINS: Set[Any] = {tuple, list, set, frozenset}

# Abstract iterator/sequence types that pydantic insists on validating as
# generators (wrapping inputs in ``ValidatorIterator``) — which strips the
# original Python identity. For the confluid use case (passthrough
# wrappers), we coerce these to ``Any`` so the original object survives
# validation untouched.
_ITER_TYPES_AS_ANY: Set[Any] = {
    collections.abc.Iterable,
    collections.abc.Iterator,
    collections.abc.Generator,
    collections.abc.AsyncIterable,
    collections.abc.AsyncIterator,
    collections.abc.AsyncGenerator,
    collections.abc.Sequence,
    collections.abc.Mapping,
    collections.abc.MutableMapping,
    collections.abc.MutableSequence,
    collections.abc.Collection,
    collections.abc.Container,
}


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


def _qualname(cls: Callable[..., Any]) -> str:
    """Return the dotted importable path of ``cls`` (a class OR a builder function) for tags."""
    module = getattr(cls, "__module__", None)
    name = getattr(cls, "__qualname__", None) or cls.__name__
    if module is None or module in ("builtins", "__main__"):
        return name
    return f"{module}.{name}"


# Top-level module names whose types pydantic can't emit a JSON Schema for
# (e.g. ``torch.Tensor``, ``numpy.ndarray``). Such params are coerced to ``Any``
# so a generated config stays JSON-schema-able for the MCP / form-spec surface —
# this is what lets a third-party class like ``torch.nn.CrossEntropyLoss``
# (``weight: Optional[Tensor]``) be ``register``-ed and surfaced without a wrapper.
_OPAQUE_TOP_MODULES = frozenset({"torch", "numpy"})


def _is_opaque_type(anno: Any) -> bool:
    """True for a concrete type pydantic cannot JSON-schema (Tensor / ndarray / non-primitive Enum / …)."""
    if not isinstance(anno, type):
        return False
    module = getattr(anno, "__module__", "") or ""
    if module.split(".", 1)[0] in _OPAQUE_TOP_MODULES:
        return True
    # An Enum whose member VALUES are not JSON primitives (the canonical case:
    # torchvision's ``*_Weights`` enums, whose values are ``Weights`` dataclasses
    # carrying a ``type``) builds a valid pydantic CORE schema — so the config
    # validates — but blows up in ``model_json_schema()`` ("Unable to serialize
    # unknown type: <class 'type'>"), which the navigaitor form-spec / MCP surface
    # calls. Coerce such enums to ``Any`` (a free-text widget — torchvision accepts
    # the "DEFAULT" string alias anyway); a plain str/int Enum stays enumerable.
    if issubclass(anno, enum.Enum):
        return not all(isinstance(m.value, (str, int, float, bool, type(None))) for m in anno)
    return False


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
            # Accept either the live instance of the source class (the form
            # that flows through actual Python / YAML materialization) OR the
            # generated pydantic mirror (the form an LLM/MCP layer composes).
            # Without the union, the field would only accept the mirror — but
            # at runtime ``ParentConfig(child=SimpleLeaf(...))`` is the legal
            # call we must validate as-is. ``arbitrary_types_allowed=True`` on
            # ``_StrictConfigBase`` makes the source-class branch isinstance-checked.
            nested = to_pydantic(anno)
            return Union[anno, nested]  # type: ignore[return-value]
        if _is_opaque_type(anno):
            # Tensor / ndarray / etc. — keep the generated model JSON-schema-able
            # (see _OPAQUE_TOP_MODULES). The value still validates loosely; the
            # source class enforces the real type at construction.
            return Any
        return anno

    # ``Literal[...]`` arguments are values, not types — don't recurse.
    if origin is Literal:
        return anno

    # A ``Callable[...]`` param (e.g. ssdlite320's ``norm_layer:
    # Optional[Callable[..., nn.Module]]``) has no JSON-Schema representation —
    # pydantic builds a CallableSchema for the core (so the config still
    # validates) but ``model_json_schema()`` raises "Cannot generate a JsonSchema
    # for core_schema.CallableSchema". Coerce to ``Any`` like the opaque leaves.
    if origin is collections.abc.Callable:
        return Any

    # Abstract iterable / sequence / mapping types: coerce to ``Any`` so
    # pydantic doesn't wrap inputs in ``ValidatorIterator`` (which would
    # strip the original Python object identity needed for shared-instance
    # composition in downstream tools like navigaitor's serializer).
    if origin in _ITER_TYPES_AS_ANY:
        return Any

    # A parameterized generic whose ORIGIN is an opaque (torch/numpy) type —
    # e.g. ``Dataset[Any]`` (origin ``torch.utils.data.Dataset``) — has nothing
    # pydantic can JSON-schema, exactly like a bare opaque type. Coerce to ``Any``
    # so it validates loosely (the source class enforces the real type at
    # construction), consistent with the bare-opaque branch in the ``origin is None``
    # case above. Without this a narrowed ``Union[Dataset[Any], Fluid]`` slot would
    # reject the legitimate config / live-instance forms a ``Union[Module, Fluid]``
    # slot accepts (``Module`` is bare → already coerced; ``Dataset[Any]`` is not).
    if _is_opaque_type(origin):
        return Any

    raw_args = get_args(anno)
    new_args = tuple(_convert_annotation(a) for a in raw_args)

    # Union / Optional / PEP 604 (X | Y) — also coerce iterable members.
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


def _spread_range_marks_into_container(inner: Any, metadata: Tuple[Any, ...]) -> Tuple[Any, Tuple[Any, ...]]:
    """Relocate numeric range marks from a container annotation onto its numeric elements.

    The workspace range-mark convention allows marking a ``(min, max)`` container
    param on the OUTER annotation — ``Annotated[Tuple[float, float], Interval(ge=0.0)]``
    (waivefront-torchsig's ``WattRange``/``DbRange``) — because that is where
    FluxStudio's ``_interval_bounds`` reads the ``__lo``/``__hi`` widget bounds.
    Pydantic, however, applies ``annotated_types`` constraints to the field VALUE:
    ``(0.0, 30.0) >= 0.0`` raises ``TypeError: Unable to apply constraint 'ge'`` the
    first time the kwarg is actually validated. Relocating the marks element-wise
    (``Tuple[Annotated[float, Interval(ge=0.0)], ...]``) keeps the one-mark
    convention AND a validating model, and the JSON schema carries the bounds per
    element (``prefixItems[].minimum``) instead of an inapplicable array constraint.

    Marks on a non-container (scalar) annotation, and non-range metadata on a
    container, are returned untouched.
    """
    range_marks = tuple(m for m in metadata if isinstance(m, _RANGE_MARK_TYPES))
    if not range_marks or get_origin(inner) not in _RANGE_CONTAINER_ORIGINS:
        return inner, metadata

    def _mark(arg: Any) -> Any:
        return Annotated[(arg, *range_marks)] if arg in (int, float) else arg

    args = get_args(inner)
    new_args = tuple(a if a is Ellipsis else _mark(a) for a in args)
    if new_args == args:
        return inner, metadata

    generic: Any = {tuple: Tuple, list: List, set: Set, frozenset: FrozenSet}[get_origin(inner)]
    new_inner = generic[new_args[0]] if len(new_args) == 1 else generic[new_args]
    remaining = tuple(m for m in metadata if m not in range_marks)
    return new_inner, remaining


def _field_for_param(param: inspect.Parameter, anno: Any, description: str) -> Tuple[Any, Any]:
    """Build a ``(type, FieldInfo)`` tuple for ``pydantic.create_model``.

    Handles required vs. defaulted fields and converts mutable defaults
    (``list``/``dict``/``set``) into ``default_factory`` to satisfy pydantic.

    Preserves ``Annotated[T, Field(...)]`` metadata (pydantic constraints like
    ``gt`` / ``le`` / ``Literal`` refinements a source class declares on its
    ``__init__`` params) so code-side tightening survives into the generated
    schema — while still converting the INNER type so nested ``@configurable``
    detection works. Confluid's own ``Lazy`` / ``Mandatory`` markers are dropped
    (``Lazy`` is recorded separately via ``_confluid_lazy_params``; ``Mandatory``
    via :func:`confluid.input_specs`) so neither leaks into the JSON Schema.
    """
    from confluid.lazy import _LAZY_MARKER
    from confluid.mandatory import _MANDATORY_MARKER
    from confluid.no_broadcast import _NO_BROADCAST_MARKER

    internal_markers = {_LAZY_MARKER, _MANDATORY_MARKER, _NO_BROADCAST_MARKER}
    metadata: Tuple[Any, ...] = ()
    inner = anno
    while get_origin(inner) is Annotated:
        args = get_args(inner)
        inner = args[0]
        metadata = metadata + tuple(m for m in args[1:] if not (isinstance(m, str) and m in internal_markers))

    converted_inner = _convert_annotation(inner)
    converted_inner, metadata = _spread_range_marks_into_container(converted_inner, metadata)
    converted_type = Annotated[(converted_inner, *metadata)] if metadata else converted_inner
    desc_kw: Dict[str, Any] = {"description": description} if description else {}

    if param.default is inspect.Parameter.empty:
        return converted_type, Field(..., **desc_kw)

    default = param.default
    if isinstance(default, (list, dict, set)):
        # Capture by value to avoid the closing-over-loop-variable bug.
        snapshot = type(default)(default)
        return converted_type, Field(default_factory=lambda snapshot=snapshot: type(snapshot)(snapshot), **desc_kw)
    return converted_type, Field(default=default, **desc_kw)


def _post_init_lazy_slots(cls: type) -> Set[str]:
    """Names of ``@configurable``-chain body slots whose default is a ``LazyClass(...)``.

    ``self.optimizer: Any = LazyClass(torch.optim.Adam, lr=1e-3)`` marks
    ``optimizer`` as a **deferred (lazy) slot** — the same role a ``Lazy[T]``
    constructor-param annotation plays, but expressed as a body attribute under
    the minimal-ctor pattern. Recorded in ``_confluid_lazy_params`` so the
    serializer emits ``!lazy:`` (not ``!class:``) for whatever fills the slot.
    Scanning delegates to the shared :mod:`confluid.introspect`.
    """
    lazy: Set[str] = set()
    for klass in cls.__mro__:
        if klass is object or not getattr(klass, "__confluid_configurable__", False):
            continue
        init = klass.__dict__.get("__init__")
        if init is not None:
            lazy |= init_lazy_setattr_names(init)
    return lazy


def _contains_forwardref(anno: Any) -> bool:
    """True when ``anno`` is — or nests — an unresolved ``typing.ForwardRef``.

    A string forward reference (``self.child: Optional["Node"] = …``) evaluates
    to ``Optional[ForwardRef('Node')]`` rather than raising, because the string
    inside the subscript is captured verbatim, not looked up. If the referent
    isn't a module global (e.g. a class defined inside a function), pydantic
    can't resolve it and ``create_model`` yields a "not fully defined" model
    whose ``model_validate`` raises ``PydanticUserError``. Detecting the marker
    lets us degrade such slots to ``Any`` (the documented fallback).
    """
    import typing

    if isinstance(anno, typing.ForwardRef):
        return True
    return any(_contains_forwardref(arg) for arg in get_args(anno))


def _resolve_ast_annotation(annotation: Any, init_func: Any) -> Any:
    """Best-effort resolve an AST annotation node to a runtime type, else ``Any``.

    Evaluates the unparsed expression against the defining function's module
    globals plus ``typing``. Any failure (unimportable name, exotic expression)
    — or a resulting annotation that still carries an unresolved forward
    reference — falls back to ``Any``: a post-init slot is always surfaced; only
    its precision degrades.
    """
    if annotation is None:
        return Any
    import ast
    import typing as _typing

    try:
        src = ast.unparse(annotation)
        scope: Dict[str, Any] = {**vars(_typing), **getattr(init_func, "__globals__", {})}
        resolved = eval(src, scope)  # noqa: S307 - trusted: source is our own __init__ annotation
    except Exception:
        return Any
    # A string forward ref evals to a ForwardRef instead of raising; pydantic
    # would build a model it can't finish (see _contains_forwardref). Degrade.
    return Any if _contains_forwardref(resolved) else resolved


def _post_init_field_specs(
    cls: type, signature_params: Set[str], param_docs: Dict[str, str]
) -> Dict[str, Tuple[Any, Any]]:
    """Build pydantic field specs for ``@configurable``-chain post-init body slots.

    Walks ``cls.__mro__`` (most-derived first) over the ``@configurable`` classes
    only — so ``nn.Module`` / ``LightningModule`` internal ``self.x`` assignments
    are never pulled in — collecting every ``self.<name>[: T] = …`` attribute that
    is NOT already a constructor parameter. Each becomes an OPTIONAL field
    (``default=None``): these slots carry their own in-class default and are
    reconfigured post-construction (YAML / broadcasting / a subclass), so a config
    may omit them. This keeps body-attribute config slots — a trainer's
    ``optimizer`` / ``train_loader`` / ``lightning`` / ``*_metrics`` — visible to
    ``to_pydantic`` (navigaitor form-spec, MCP schemas, FluxStudio widgets) even
    though they aren't constructor parameters.
    """
    specs: Dict[str, Tuple[Any, Any]] = {}
    seen: Set[str] = set(signature_params) | _SKIP_PARAMS
    for klass in cls.__mro__:
        if klass is object or not getattr(klass, "__confluid_configurable__", False):
            continue
        init = klass.__dict__.get("__init__")
        if init is None:
            continue
        # ONE shared scan per __init__ (confluid.introspect), projected twice:
        # every slot NAME (all kinds), and the assign/annassign annotation map.
        body_slots = scan_init_body(init)
        names = {slot.name for slot in body_slots}
        annotations: Dict[str, Any] = {}
        for slot in body_slots:
            if slot.kind in ("assign", "annassign"):
                annotations.setdefault(slot.name, slot.annotation)
        for name in names:
            if name in seen:
                continue
            seen.add(name)
            member = getattr(cls, name, None)
            if isinstance(member, property) and member.fset is None:
                continue  # read-only derived property — not a config knob
            if getattr(member, "__confluid_ignore__", False):
                continue
            resolved = _resolve_ast_annotation(annotations.get(name), init)
            converted = _convert_annotation(resolved)
            desc_kw: Dict[str, Any] = {"description": param_docs[name]} if param_docs.get(name) else {}
            # Optional (default None): the class supplies its own default and the
            # slot is reconfigured post-construction, so a config may omit it.
            specs[name] = (Union[converted, None], Field(default=None, **desc_kw))
    return specs


@lru_cache(maxsize=None)
def to_pydantic(cls: Callable[..., Any]) -> Type[BaseModel]:
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
        confluid.IntrospectionError: (a ``TypeError``) If ``cls`` is not a
            class or its ``__init__`` is not inspectable (e.g. C extension
            types without Python wrappers).
    """
    if not callable(cls):
        raise IntrospectionError(f"to_pydantic(cls) expected a class or callable, got {type(cls).__name__}")

    # A target may be a class OR a plain builder/factory FUNCTION (e.g. a torchvision
    # detection builder ``fasterrcnn_resnet50_fpn``) — mirroring flow()/resolve_class's
    # callable-target support (confluid AGENTS "A Target May Be ANY Callable"). For a
    # class we introspect ``__init__``; for a function the callable's OWN signature.
    # A function has no ``__init__`` body, so the post-init body-slot scan is skipped.
    is_class = isinstance(cls, type)
    if is_class:
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
                raise IntrospectionError(f"Cannot introspect {cls.__name__}.__init__: {exc}") from exc
            docstring = init.__doc__ or cls.__doc__ or ""
    else:
        try:
            sig = inspect.signature(cls)
            hints = get_type_hints(cls, include_extras=True)
        except (TypeError, ValueError, NameError) as exc:
            raise IntrospectionError(f"Cannot introspect callable {getattr(cls, '__name__', cls)!r}: {exc}") from exc
        docstring = cls.__doc__ or ""

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

    # Also surface post-init body slots (``self.optimizer = LazyClass(...)`` etc.)
    # that aren't constructor parameters — the minimal-ctor / post-construction
    # pattern keeps configurable slots in the ``__init__`` body, and they must
    # still be enumerable by the form-spec / MCP / FluxStudio surfaces. Signature
    # params already in ``fields`` win (never overwritten).
    signature_params = set(fields)
    if isinstance(cls, type):  # post-init body-slot scan walks ``cls.__mro__`` (classes only)
        for name, spec in _post_init_field_specs(cls, signature_params, param_docs).items():
            fields.setdefault(name, spec)

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
    # serializer emits `!lazy:` instead of `!class:` for these params). Two
    # sources: ``Lazy[T]``-annotated constructor params, AND body slots whose
    # default is a ``LazyClass(...)`` (the minimal-ctor pattern — e.g. a
    # trainer's ``optimizer`` / ``*_loader`` / ``lightning`` body slots). The
    # latter keeps a runtime-injected slot from being serialized as ``!class:``
    # (which confluid would eagerly flow on assignment and crash).
    lazy_params = {name for name, anno in hints.items() if name not in _SKIP_PARAMS and is_lazy_annotation(anno)}
    if isinstance(cls, type):  # body-slot lazy scan walks ``cls.__mro__`` (classes only)
        lazy_params |= _post_init_lazy_slots(cls)
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
