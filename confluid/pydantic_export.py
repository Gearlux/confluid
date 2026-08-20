"""Auto-generate pydantic ``BaseModel`` subclasses from ``@configurable`` classes.

The generated models mirror each class's ``__init__`` signature as typed
pydantic fields and recursively wrap nested ``@configurable`` parameter types
in their own generated models. Non-``@configurable`` types are passed through
unchanged (primitives, ``Optional``, ``Literal``, ``Union``, ``List``,
``Dict``, ``Tuple``, library types).

Generated models carry a ``_confluid_class`` class attribute holding the
dotted importable path of the target class. A downstream serializer reads
this to emit a confluid ``_target_:`` marker for the filled model.

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
    Optional,
    Set,
    Tuple,
    Type,
    Union,
    get_args,
    get_origin,
    get_type_hints,
)

from annotated_types import Ge, Gt, Interval, Le, Lt
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, create_model
from pydantic.json_schema import WithJsonSchema

from confluid.exceptions import IntrospectionError
from confluid.introspect import NO_DEFAULT, Slot, slots
from confluid.mandatory import _MANDATORY_MARKER
from confluid.no_broadcast import _NO_BROADCAST_MARKER
from confluid.partial import _PARTIAL_MARKER
from confluid.schema import parse_param_docs

#: The slot kinds that become MODEL FIELDS — the signature, minus the variadics.
#: A ``*args`` name can never be passed by keyword and a ``**kwargs`` name is the
#: catchall, so neither is a field; a POSITIONAL_ONLY param IS one (the validation
#: wrap binds it by name via ``sig.bind``, so the model must carry it). Kinds, not
#: names: the old ``_SKIP_PARAMS`` name-set also dropped ordinary parameters that
#: happened to be NAMED ``args``/``kwargs`` — and ``extra="forbid"`` then made the
#: strict init policy refuse a legal constructor call.
_FIELD_KINDS = frozenset({"keyword", "positional_only"})

# Confluid's own annotation markers — stripped wherever ``Annotated`` metadata is
# peeled so none of them leaks into a generated model / JSON schema. (``Partial`` is
# answered by the class-side ``confluid.partial.partial_param_names``; ``Mandatory``
# via ``confluid.input_specs``; ``NoBroadcast`` is a broadcast-routing concern.)
_INTERNAL_MARKERS = {_PARTIAL_MARKER, _MANDATORY_MARKER, _NO_BROADCAST_MARKER}

# Numeric range marks (PEP-593 ``annotated_types``) the workspace convention puts
# on the OUTER annotation of a ``(min, max)`` container param — see
# ``_spread_range_marks_into_container``.
_RANGE_MARK_TYPES: Tuple[type, ...] = (Interval, Ge, Gt, Le, Lt)

# Container origins whose numeric elements a relocated range mark applies to.
_RANGE_CONTAINER_ORIGINS: Set[Any] = {tuple, list, set, frozenset}

# Abstract types pydantic validates LAZILY — it wraps the input in a
# ``ValidatorIterator``, which is one-shot: the first iteration yields the items and
# every later one yields NOTHING. A slot read twice (a wrapper that both counts and
# forwards its inputs) would silently see an empty collection the second time, so these
# are coerced to ``Any`` and the caller's object survives validation untouched.
#
# The list is deliberately NARROWER than "every abstract collection", and the boundary
# is measured rather than assumed (2026-08-02): only the types whose CONTRACT permits a
# generator get the lazy treatment. ``Sequence`` / ``MutableSequence`` / ``Collection`` /
# ``Container`` validate to a real ``list`` and ``Mapping`` / ``MutableMapping`` to a real
# ``dict`` — re-iterable, holding the IDENTICAL element objects — so coercing those bought
# no protection and cost the element type: a ``Sequence[Metric]`` slot reached a form spec
# as a bare ``Any``, which is precisely what the annotation was written to prevent.
_ITER_TYPES_AS_ANY: Set[Any] = {
    collections.abc.Iterable,
    collections.abc.Iterator,
    collections.abc.Generator,
    collections.abc.AsyncIterable,
    collections.abc.AsyncIterator,
    collections.abc.AsyncGenerator,
}


class _StrictConfigBase(BaseModel):
    """Base class for generated models — forbids unknown fields and allows arbitrary types.

    ``extra="forbid"`` rejects unknown kwargs so LLM-emitted configs surface
    typos. ``arbitrary_types_allowed`` lets nested annotations include
    library types we haven't (and don't want to) introspect (e.g. a sentinel
    ``Path`` from pathlib, or any user class without a pydantic mirror).
    ``protected_namespaces=()`` lets a constructor parameter be named
    ``model_<anything>`` without pydantic's namespace warning — the names that
    genuinely SHADOW a ``BaseModel`` attribute (``model_config``, ``schema``,
    ``copy`` …) are mangled by :func:`_field_name` instead.
    """

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True, protected_namespaces=())


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


@lru_cache(maxsize=None)
def _json_schemable(anno: type) -> bool:
    """True when pydantic can emit a JSON Schema for the leaf type (``int``, ``Path``, an Enum, a model…).

    A probe, cached per type: building a ``TypeAdapter`` (arbitrary types allowed, as in the
    generated models) and asking for its schema. A type pydantic cannot schema — or cannot
    even build a validator for — answers False and is made opaque by the caller.
    """
    from pydantic.errors import PydanticInvalidForJsonSchema, PydanticSchemaGenerationError

    try:
        TypeAdapter(anno, config=ConfigDict(arbitrary_types_allowed=True)).json_schema()
    except (PydanticInvalidForJsonSchema, PydanticSchemaGenerationError):
        return False
    except Exception:  # a pydantic-core SchemaError (a type it cannot isinstance) — not schemable either
        return False
    return True


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
    # unknown type: <class 'type'>"), which the form-spec / MCP surface
    # calls. Coerce such enums to ``Any`` (a free-text widget — torchvision accepts
    # the "DEFAULT" string alias anyway); a plain str/int Enum stays enumerable.
    if issubclass(anno, enum.Enum):
        return not all(isinstance(m.value, (str, int, float, bool, type(None))) for m in anno)
    return False


def _convert_annotation(anno: Any) -> Any:
    """Recursively replace ``@configurable`` types inside ``anno`` with generated models.

    A NESTED ``Annotated`` (a Union arm, a container element — e.g. the
    ``Annotated[float, Interval]`` arm inside ``Mandatory[DbPower]``, whose alias
    expands to ``Annotated[Union[Annotated[float, Interval], Fluid], marker]``)
    keeps its non-marker metadata: confluid's internal markers are stripped, the
    payload is converted, and the surviving metadata (range marks, ``Field``
    constraints) is re-wrapped — so code-side tightening reaches the schema even
    when a union-carrying marker alias prevents ``Annotated`` flattening.
    Container range marks relocate element-wise exactly like the field-level path.
    """
    metadata: Tuple[Any, ...] = ()
    while get_origin(anno) is Annotated:
        args = get_args(anno)
        anno = args[0]
        metadata = metadata + tuple(m for m in args[1:] if not (isinstance(m, str) and m in _INTERNAL_MARKERS))
    converted = _convert_annotation_unwrapped(anno)
    if not metadata:
        return converted
    converted, metadata = _spread_range_marks_into_container(converted, metadata)
    return Annotated[(converted, *metadata)] if metadata else converted


def _convert_annotation_unwrapped(anno: Any) -> Any:
    """The conversion body behind :func:`_convert_annotation` — ``anno`` is already peeled.

    Leaves typing constructs intact (``Optional``, ``Union``, ``List``,
    ``Dict``, ``Tuple``, ``Literal``, etc.) but recurses into their type args
    (via :func:`_convert_annotation`, so nested metadata is preserved).
    Unknown / opaque types are returned as-is.
    """

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
        if anno is collections.abc.Callable:
            # The bare PEP 585 spelling has no ``get_origin`` and so missed the
            # ``Callable[...]`` coercion below (BUGS-2026-08-19 N11).
            return Any
        if getattr(anno, "_is_protocol", False) and not getattr(anno, "_is_runtime_protocol", False):
            # A Protocol that is not ``@runtime_checkable`` cannot be ``isinstance``-checked,
            # and pydantic's is-instance schema raised a raw ``SchemaError`` from the
            # constructor of EVERY class that typed a param with one (N1). A runtime-
            # checkable Protocol falls through and keeps its check.
            return Any
        if isinstance(anno, type) and not _json_schemable(anno):
            # A plain leaf class (a helper class, ``logging.Logger``, ``TextIO`` …) validates
            # fine as an arbitrary type but has no JSON Schema, so ``model_json_schema()``
            # raised for every consumer of the mirror (N10). Keep the isinstance check,
            # make the SCHEMA opaque — the documented "never crashes" coercion, which was
            # a two-module allow-list, now holds for any leaf pydantic cannot schema.
            return Annotated[anno, WithJsonSchema({})]
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
    # composition in a downstream serializer).
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
    (a signal-domain consumer's ``WattRange``/``DbRange`` aliases) — because that
    is where a GUI reads its ``__lo``/``__hi`` widget bounds.
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
    if not range_marks:
        return inner, metadata
    if get_origin(inner) in (Union, types.UnionType):
        # ``Optional[Tuple[float, float]]`` is the zero-arg spelling of the same container
        # (class-design rule 2); the mark used to stay on the Union and pydantic raised a
        # raw ``TypeError: Unable to apply constraint`` on a LEGAL value (BUGS-2026-08-19 N9).
        # Relocate into each container arm; the other arms (``None``) are left alone.
        arms = tuple(_spread_range_marks_into_container(a, metadata)[0] for a in get_args(inner))
        if arms != get_args(inner):
            return Union[arms], tuple(m for m in metadata if m not in range_marks)  # type: ignore[return-value]
        return inner, metadata
    if get_origin(inner) not in _RANGE_CONTAINER_ORIGINS:
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


def _field_name(name: str) -> str:
    """The pydantic FIELD name for a constructor parameter.

    The parameter's own name, unless pydantic cannot take it: a leading underscore
    makes a private attribute (``NameError: Fields must not use names with leading
    underscores`` — on EVERY constructor call of the class, BUGS-2026-08-19 N2), and a
    ``BaseModel`` attribute name (``model_config``, ``schema``, ``copy`` …) shadows the
    base (``TypeError`` from ``create_model`` — swallowed, so validation went silently
    OFF for the class, N3). Those two shapes get a mangled field name; the real name
    rides as the field's ALIAS, so ``model_validate(kwargs)`` and the JSON schema still
    speak the parameter's name. :func:`field_name_for` is the reverse map.
    """
    if name.startswith("_") or hasattr(BaseModel, name):
        return f"{name.lstrip('_')}_"
    return name


def field_name_for(model: Type[BaseModel], name: str) -> Optional[str]:
    """Map a constructor-parameter name to the generated model's field name (``None`` when absent)."""
    if name in model.model_fields:
        return name
    mangled = _field_name(name)
    return mangled if mangled in model.model_fields else None


def _field_for_slot(slot: Slot, description: str) -> Tuple[Any, Any]:
    """Build a ``(type, FieldInfo)`` tuple for ``pydantic.create_model``.

    Takes the enumeration's :class:`~confluid.introspect.Slot` record — the
    annotation is already hint-resolved (``include_extras=True``) and the default
    carries :data:`~confluid.introspect.NO_DEFAULT` when there is none (the same
    ``inspect.Parameter.empty`` sentinel the signature walk used). Handles
    required vs. defaulted fields and converts mutable defaults
    (``list``/``dict``/``set``) into ``default_factory`` to satisfy pydantic.

    Preserves ``Annotated[T, Field(...)]`` metadata (pydantic constraints like
    ``gt`` / ``le`` / ``Literal`` refinements a source class declares on its
    ``__init__`` params) so code-side tightening survives into the generated
    schema — while still converting the INNER type so nested ``@configurable``
    detection works. Confluid's own ``Partial`` / ``Mandatory`` / ``NoBroadcast``
    markers are dropped (``Partial`` is answered by the class-side
    :func:`confluid.partial.partial_param_names`; ``Mandatory`` via :func:`confluid.input_specs`)
    so none leaks into the JSON Schema. The peel / marker-strip / range-mark
    relocation all live in :func:`_convert_annotation`, which handles nested
    ``Annotated`` layers identically.
    """
    converted_type = _convert_annotation(slot.annotation)
    desc_kw: Dict[str, Any] = {"description": description} if description else {}
    if _field_name(slot.name) != slot.name:
        desc_kw["alias"] = slot.name  # the kwarg / document / schema spelling stays the parameter's own name

    if slot.default is NO_DEFAULT:
        return converted_type, Field(..., **desc_kw)

    default = slot.default
    if isinstance(default, (list, dict, set)):
        # Capture by value to avoid the closing-over-loop-variable bug.
        snapshot = type(default)(default)
        return converted_type, Field(default_factory=lambda snapshot=snapshot: type(snapshot)(snapshot), **desc_kw)
    return converted_type, Field(default=default, **desc_kw)


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
    ``to_pydantic`` (form specs, MCP schemas, GUI widgets) even
    though they aren't constructor parameters.
    """
    specs: Dict[str, Tuple[Any, Any]] = {}
    seen: Set[str] = set(signature_params)
    for slot in slots(cls):
        if slot.kind != "body_slot" or slot.name in seen:
            continue
        # OWNER filter — the one thing this projection needs that a name set cannot
        # express. ``slots()`` walks the WHOLE MRO because the accept-list wants a
        # framework base's ``self.training = True`` (broadcasting may legitimately
        # set it); a generated SCHEMA must not carry it, or every model grows
        # ``training`` / ``prepare_data_per_node`` fields from ``nn.Module``.
        if not getattr(slot.owner, "__confluid_configurable__", False):
            continue
        seen.add(slot.name)
        member = getattr(cls, slot.name, None)
        if isinstance(member, property) and member.fset is None:
            continue  # read-only derived property — not a config knob
        converted = _convert_annotation(slot.annotation)
        desc_kw: Dict[str, Any] = {"description": param_docs[slot.name]} if param_docs.get(slot.name) else {}
        # Optional (default None): the class supplies its own default and the
        # slot is reconfigured post-construction, so a config may omit it.
        specs[slot.name] = (Union[converted, None], Field(default=None, **desc_kw))
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

    Exclusions are by KIND, never by name: variadic parameters (``*args`` /
    ``**kwargs``, whatever they are called) are not fields, and an ordinary
    parameter that merely happens to be NAMED ``args`` or ``kwargs`` IS one
    (see :data:`_FIELD_KINDS`). A declared constructor parameter is always a
    field — this models the CONSTRUCTOR, and a read-only ``@property`` of the
    same name shadows the instance attribute after construction, not the
    argument. Body slots are filtered separately: a setter-less property there
    is derived state and never a knob (:func:`_post_init_field_specs`).

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
    #
    # The probe below exists ONLY for the error contract: ``slots()`` (which the field
    # loop projects from) is best-effort BY DESIGN — an unreadable signature or an
    # unresolvable hint silently yields no/``Any`` slots, because losing a slot loses a
    # GUI knob. ``to_pydantic`` documents the OPPOSITE contract — a raise naming the
    # class — since a silently empty model would validate everything against nothing.
    is_class = isinstance(cls, type)
    if is_class:
        init = cls.__dict__.get("__init__") or cls.__init__  # type: ignore[misc]
        if init is not object.__init__:  # a class without its own __init__ has no params to probe
            try:
                inspect.signature(init)
                get_type_hints(init, include_extras=True)
            except (TypeError, ValueError, NameError) as exc:
                raise IntrospectionError(f"Cannot introspect {cls.__name__}.__init__: {exc}") from exc
    else:
        try:
            inspect.signature(cls)
            get_type_hints(cls, include_extras=True)
        except (TypeError, ValueError, NameError) as exc:
            raise IntrospectionError(f"Cannot introspect callable {getattr(cls, '__name__', cls)!r}: {exc}") from exc

    # The ONE docstring resolver (init doc → class doc for a class; own __doc__
    # for a callable) — this function used to re-implement it inline, and the
    # copies had already drifted (a walker reading init.__doc__ alone lost the
    # docs of every class keeping its Args: block at class level).
    param_docs = parse_param_docs(cls)
    fields: Dict[str, Tuple[Any, Any]] = {}

    # ONE enumeration, projected by KIND (see _FIELD_KINDS). ``slots()`` reports a
    # name once, in signature order, with the hint already resolved — a class and a
    # registered builder function answer alike through ``init_callable``.
    for slot in slots(cls):
        if slot.kind not in _FIELD_KINDS:
            continue
        fields[_field_name(slot.name)] = _field_for_slot(slot, param_docs.get(slot.name, ""))

    # Also surface post-init body slots (``self.optimizer = PartialClass(...)`` etc.)
    # that aren't constructor parameters — the minimal-ctor / post-construction
    # pattern keeps configurable slots in the ``__init__`` body, and they must
    # still be enumerable by the form-spec / MCP / GUI surfaces. Signature
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

    return model


def confluid_class_of(model_or_instance: Any) -> str | None:
    """Return the ``_target_`` path stored on a generated model, or ``None``."""
    if isinstance(model_or_instance, BaseModel):
        cls: type = type(model_or_instance)
    elif isinstance(model_or_instance, type):
        cls = model_or_instance
    else:
        return None
    val = getattr(cls, "_confluid_class", None)
    return val if isinstance(val, str) else None
