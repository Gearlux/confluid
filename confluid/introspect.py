"""Stdlib-only introspection — the ONE slot enumeration every reader projects from.

:func:`slots` answers "which configurable slots does this target have?" ONCE,
returning rich :class:`Slot` records (name, kind, hint-resolved annotation,
default, source, declaring ``owner``) in signature order. Every reader states
its rule as a projection — a KIND SET (:func:`slot_names`), an ``owner`` filter,
a per-field mapping — never as a private re-derived walk: six readers each
walked the signature with their own "minus self/cls" filter and gave five
different answers for one class (``docs/architecture.md`` record 12; pinned by
``tests/test_introspection_agreement.py``). :func:`body_slot_names` is the
sibling projection over the same walk for the different question "what does the
``__init__`` body assign" — ``slots()`` reports a name once and lets the
signature claim it.

Underneath, ONE AST scan of an ``__init__`` body (:func:`scan_init_body`) finds
the body slots, with two narrow name-set projections kept for the broadcast
layer: :func:`init_setattr_names` (every assigned body-slot NAME — the widest
view, ``AugAssign`` and literal ``setattr(self, "x", …)`` included) and
:func:`init_partial_setattr_names` (names whose assigned VALUE is a
``PartialClass(...)`` call — deferred body slots, serialized as
``_partial_: true``).

This module imports ONLY the stdlib, so it is a dependency leaf: safe for the
optional-pydantic consumer, and structurally incapable of import cycles.

Load-bearing subtlety: ``inspect.getsource`` follows
``functools.wraps``' ``__wrapped__``, so scanning ``cls.__dict__["__init__"]``
AFTER ``@configurable`` wrapped it still parses the ORIGINAL constructor
source — never the 6-line validation wrapper. Keep using ``getsource``; never
read ``__code__`` directly. Pinned by
``tests/test_introspect.py::test_scan_sees_through_configurable_wrapper``.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import textwrap
import types
from typing import (
    Annotated,
    Any,
    Dict,
    FrozenSet,
    List,
    Literal,
    NamedTuple,
    Optional,
    Set,
    Tuple,
    Union,
    get_args,
    get_origin,
    get_type_hints,
)

SlotKind = Literal["assign", "annassign", "augassign", "setattr"]

#: Basename of the generated per-package bake table module (``confluid.bake``
#: writes ``<package>/_confluid_baked.py``; :func:`baked_init_attrs` imports it).
BAKED_MODULE_BASENAME = "_confluid_baked"

# Per TOP-LEVEL package: the imported ``BROADCAST_ATTRS`` table, or ``None``
# when the package ships no bake module. Import results are stable for the
# process lifetime, so this cache is never cleared (unlike the engine's
# per-materialize-pass attr caches).
_baked_tables: Dict[str, Optional[Dict[str, Tuple[str, ...]]]] = {}


class BodySlot(NamedTuple):
    """One non-underscore attribute assignment found in an ``__init__`` body."""

    name: str
    kind: SlotKind
    annotation: Optional[ast.AST]  # AnnAssign annotation node, else None
    value: Optional[ast.AST]  # assigned-value node, else None


def init_callable(target: Any) -> Optional[Any]:
    """The callable whose signature governs CALLING ``target``.

    For a class, its ``__init__``; for any other callable (a registered builder
    FUNCTION, per the "A Target May Be ANY Callable" mandate), the callable
    itself. Returns ``None`` for a class whose ``__init__`` is literally
    ``None`` — a legal class attribute that makes the class unconstructable.

    This is the ONE class-vs-callable dispatch every signature reader must go
    through. Reading ``getattr(target, "__init__")`` unconditionally resolves a
    plain function's ``__init__`` to ``object.__init__`` — signature
    ``(*args, **kwargs)`` — which made the accept-list machinery answer
    "accepts everything" for every registered builder function (measured:
    ``accepts_key(builder, "run_name")`` was True for a builder declaring only
    ``weights``/``num_classes``). ``engine._ctor_params`` carried the correct
    branch while ``broadcast`` did not; this helper is where the four copies
    were unified so they cannot diverge again.

    Callers still read the signature / type hints themselves (their filtering
    and error handling differ); this helper only answers WHOSE signature.
    """
    if inspect.isclass(target):
        return getattr(target, "__init__", None)
    return target


def init_source_available(init_func: Any) -> bool:
    """True when ``inspect.getsource`` can read this ``__init__``'s source.

    :func:`scan_init_body` returns ``()`` indistinguishably for "no source"
    (compiled / frozen / zip-imported deployments, where ``getsource`` raises
    ``OSError``/``TypeError``) and "genuinely empty body". This probe separates
    the two so the broadcasting engine can warn loudly when post-init body
    attributes are INVISIBLE (dev-vs-packaged behavioral divergence) instead of
    silently dropping them — the escape hatch is
    ``@configurable(broadcast_attrs=[...])``.

    Like the scanners below, this sees through ``functools.wraps`` wrappers
    (``getsource`` follows ``__wrapped__``), so probing the validation-wrapped
    ``__init__`` reports on the ORIGINAL constructor's source.
    """
    try:
        inspect.getsource(init_func)
    except (OSError, TypeError):
        return False
    return True


def baked_init_attrs(klass: Any) -> Optional[Tuple[str, ...]]:
    """Build-time-scanned ``__init__`` body-slot names for ``klass``, if baked.

    ``confluid.bake`` runs the SAME AST scan as :func:`scan_init_body` at BUILD
    time (while source still exists) and writes the results into a generated
    ``<top_package>/_confluid_baked.py`` module. This looks the class up in
    that table: returns the baked name tuple (possibly empty — an empty entry
    means "scanned at build time, no body slots"), or ``None`` when the class
    is not covered (no bake module, or the class isn't in it).

    The bake module is imported lazily by dotted name. NOTE for frozen-app
    bundlers that trace imports statically (PyInstaller): a dynamic import is
    invisible to the tracer — declare ``<pkg>._confluid_baked`` as a hidden
    import or import it explicitly from the package's ``__init__``.
    """
    module = getattr(klass, "__module__", None)
    qualname = getattr(klass, "__qualname__", None)
    if not module or not qualname:
        return None
    top = module.split(".", 1)[0]
    if top not in _baked_tables:
        try:
            baked_module = importlib.import_module(f"{top}.{BAKED_MODULE_BASENAME}")
        except ImportError:
            _baked_tables[top] = None
        else:
            table = getattr(baked_module, "BROADCAST_ATTRS", None)
            _baked_tables[top] = table if isinstance(table, dict) else None
    table = _baked_tables[top]
    if table is None:
        return None
    entry = table.get(f"{module}.{qualname}")
    return tuple(entry) if entry is not None else None


def scan_init_body(init_func: Any) -> Tuple[BodySlot, ...]:
    """Scan a single ``__init__`` for ``self.<name>`` assignments (pure AST).

    Records, in ``ast.walk`` order (so nested ``if``/``for``/``try`` bodies
    are included — pinned behavior), every:

    * ``self.x = …``                      → kind ``"assign"`` (value captured)
    * ``self.x: T = …``                   → kind ``"annassign"`` (annotation +
      value captured; a bare ``self.x: T`` declaration has ``value=None``)
    * ``self.x += …``                     → kind ``"augassign"``
    * ``setattr(self, "x", …)`` (literal) → kind ``"setattr"``

    Underscore-prefixed names are filtered at scan time. Unreadable source
    (``inspect.getsource`` failure) or unparsable source returns ``()`` —
    callers treat "no source" as "no body slots".
    """
    try:
        source = inspect.getsource(init_func)
    except (OSError, TypeError):
        return ()
    try:
        # ``textwrap.dedent`` preserves relative indentation (strips the
        # common leading whitespace), so the method body still sits under its
        # ``def`` header. ``inspect.cleandoc`` would flatten every line to
        # column 0 and break the parse.
        tree = ast.parse(textwrap.dedent(source))
    except SyntaxError:
        return ()

    slots: list[BodySlot] = []

    def _self_attr(target: Any) -> Optional[str]:
        if (
            isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and target.value.id == "self"
            and not target.attr.startswith("_")
        ):
            return target.attr
        return None

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                name = _self_attr(t)
                if name is not None:
                    slots.append(BodySlot(name, "assign", None, node.value))
        elif isinstance(node, ast.AnnAssign):
            name = _self_attr(node.target)
            if name is not None:
                slots.append(BodySlot(name, "annassign", node.annotation, node.value))
        elif isinstance(node, ast.AugAssign):
            name = _self_attr(node.target)
            if name is not None:
                slots.append(BodySlot(name, "augassign", None, node.value))
        elif (
            # ``setattr(self, "x", ...)`` with a string-literal name. Non-literal
            # names (variables, f-strings) stay invisible — we don't try to be
            # clever.
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "setattr"
            and len(node.args) >= 2
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id == "self"
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
            and not node.args[1].value.startswith("_")
        ):
            slots.append(BodySlot(node.args[1].value, "setattr", None, node.args[2] if len(node.args) > 2 else None))

    return tuple(slots)


def init_setattr_names(init_func: Any) -> Set[str]:
    """Every body-slot name, ALL kinds — the broadcast/accept-list projection."""
    return {slot.name for slot in scan_init_body(init_func)}


def init_partial_setattr_names(init_func: Any) -> Set[str]:
    """Names of assign/annassign slots whose VALUE is a ``PartialClass(...)``/``Partial(...)`` call.

    An annotated declaration without a value (``self.x: T``) and
    ``AugAssign``/``setattr`` slots never qualify.
    """
    return {
        slot.name
        for slot in scan_init_body(init_func)
        if slot.kind in ("assign", "annassign") and _is_lazy_call(slot.value)
    }


#: The call names that mark a body slot deferred. This scan matches on the NAME in
#: the SOURCE, so a name dropped here stops deferring silently — no error, no
#: diagnostic, the slot is simply built and an optimizer is constructed without its
#: params. The pre-rename ``PartialClass`` / ``Partial`` spellings were carried here for
#: exactly that reason and went with the aliases (2026-08-11); adding a new spelling
#: means adding it here in the same change.
_PARTIAL_CALL_NAMES = ("PartialClass", "Partial")


def _is_lazy_call(value: Any) -> bool:
    """True for a call whose callee names a deferred-slot constructor.

    Matches bare names AND attribute-qualified calls (``confluid.PartialClass(...)``)
    by inspecting only the final attribute — same rule as the original scanner.
    """
    if not isinstance(value, ast.Call):
        return False
    func = value.func
    name = func.id if isinstance(func, ast.Name) else (func.attr if isinstance(func, ast.Attribute) else None)
    return name in _PARTIAL_CALL_NAMES


def annotation_has_marker(annotation: Any, marker: str) -> bool:
    """True iff ``marker`` appears in the annotation's ``Annotated`` metadata — at any wrapper depth.

    The ONE detection rule behind ``is_partial_annotation`` / ``is_mandatory_annotation`` /
    ``is_no_broadcast_annotation``. Directly-nested ``Annotated`` layers flatten their
    metadata (``Mandatory[Partial[T]]`` carries both markers at the top), but a ``Union``
    arm does NOT — and since ``Partial[T]`` / ``Mandatory[T]`` expand to
    ``Annotated[Union[T, Fluid], marker]``, composed spellings bury the inner marker
    inside a Union arm. This helper therefore also walks ``Union`` arms (PEP 604
    included) and ``Annotated`` payloads, so every composition order — and the natural
    ``Optional[Partial[T]] = None`` spelling — is detected. It deliberately does NOT
    recurse into other generics (``List[Partial[T]]`` marks the ELEMENT, not the param).
    """
    if marker in getattr(annotation, "__metadata__", ()):
        return True
    origin = get_origin(annotation)
    if origin is Annotated:
        return annotation_has_marker(get_args(annotation)[0], marker)
    if origin is Union or origin is types.UnionType:
        return any(annotation_has_marker(arm, marker) for arm in get_args(annotation))
    return False


def marked_param_names(target: Any, marker: str, cache_attr: Optional[str] = None) -> Set[str]:
    """Signature-parameter names of ``target`` carrying ``marker`` — the ONE scan.

    The scan-plus-cache behind ``partial_param_names`` / ``mandatory_param_names`` /
    ``no_broadcast_param_names``, which used to carry three near-identical copies
    that had already drifted on callable support: two read
    ``getattr(cls, "__init__")`` directly, so an identical ``Partial[...]`` /
    ``Mandatory[...]`` annotation was reported on a class and silently DROPPED on
    a registered builder FUNCTION (whose ``__init__`` is ``object.__init__`` —
    the exact failure :func:`init_callable` exists to prevent). Every reader goes
    through :func:`init_callable` here, so classes and callables answer alike.

    ``cache_attr`` names the per-target stamp to read/write (own ``__dict__``
    only, never ``getattr`` — an MRO walk serves a parent's cached answer to
    every subclass); pass ``None`` when the caller caches a superset itself
    (``partial_param_names`` caches the union with the body-slot scan). A hint
    named ``return`` is excluded — it is a function's return annotation, not a
    parameter.
    """
    if cache_attr is not None:
        cached = target.__dict__.get(cache_attr) if hasattr(target, "__dict__") else None
        if cached is not None:
            return cached  # type: ignore[no-any-return]
    init = init_callable(target)
    names: Set[str] = set()
    if init is not None:
        try:
            hints = get_type_hints(init, include_extras=True)
        except Exception:
            hints = {}
        names = {name for name, ann in hints.items() if name != "return" and annotation_has_marker(ann, marker)}
    if cache_attr is not None:
        try:
            setattr(target, cache_attr, names)
        except (AttributeError, TypeError):
            pass
    return names


# --------------------------------------------------------------------------- #
# The ONE slot enumeration
#
# A shared "ctor params minus self/cls" HELPER was considered here and rightly
# rejected: the apparent duplicates each carry a load-bearing difference a name
# set cannot express — the dumper needs ORDERED params (dump-key order is
# round-trip-pinned), the accept-list needs its ``**kwargs`` → ``None``
# broadcast-everything sentinel, and schema / pydantic_export consume rich
# ``inspect.Parameter`` metadata.
#
# Every one of those objections is about the RETURN TYPE of a name-set helper.
# None is about the ENUMERATION underneath, which was the same walk five times —
# and five hand-rolled "minus self/cls" filters with DIFFERENT kind exclusions
# gave five different answers for one class (measured; pinned in
# ``tests/test_introspection_agreement.py``). So the shared thing is the walk,
# returning rich records, and each consumer keeps its difference as a one-line
# projection over them.
# --------------------------------------------------------------------------- #

SlotKindT = Literal[
    "positional_only",  # can never be passed by keyword — not a keyword slot
    "keyword",  # POSITIONAL_OR_KEYWORD / KEYWORD_ONLY — the ordinary case
    "var_positional",  # ``*args`` — not a slot at all; its NAME addresses nothing
    "var_keyword",  # ``**kwargs`` — the catchall; names nothing, refuses nothing
    "class_attr",  # a public settable class attribute
    "body_slot",  # ``self.x = …`` in ``__init__`` — a slot with no signature entry
]

_PARAM_KIND_MAP: Dict[Any, SlotKindT] = {
    inspect.Parameter.POSITIONAL_ONLY: "positional_only",
    inspect.Parameter.POSITIONAL_OR_KEYWORD: "keyword",
    inspect.Parameter.KEYWORD_ONLY: "keyword",
    inspect.Parameter.VAR_POSITIONAL: "var_positional",
    inspect.Parameter.VAR_KEYWORD: "var_keyword",
}

#: Sentinel for "this slot has no default" — distinct from a default OF ``None``.
NO_DEFAULT: Any = inspect.Parameter.empty


class Slot(NamedTuple):
    """One configurable slot of a target, however it is declared.

    The fields are what the six readers between them need; each takes a subset —
    the accept-list wants ``name`` + ``kind``, ``input_specs`` wants ``annotation``
    and ``default``, ``to_pydantic`` wants all of it.
    """

    name: str
    kind: SlotKindT
    annotation: Any  #: resolved type hint, or ``Any`` when unresolvable
    default: Any  #: :data:`NO_DEFAULT` when the slot has none
    source: Literal["signature", "class_attr", "body_scan", "declared", "baked"]
    #: The MRO class that DECLARED this slot. Load-bearing for ``body_slot`` only,
    #: where readers legitimately disagree about scope: the accept-list wants a
    #: framework base's ``self.training = True`` (broadcasting may set it), while
    #: ``to_pydantic`` must not — those would become fields on every generated
    #: schema. One walk, two scopes, decided by the READER. For every other kind
    #: it is the target itself.
    owner: Any = None


#: Per-target slot cache, keyed by identity. Declared HERE (this module owns the
#: enumeration) and registered for the per-pass clear by ``broadcast``, which owns
#: the ONE clear site and already imports this module — the reverse of the
#: ``engine._parent_blacklist_cache`` arrangement, for the same reason: this
#: module imports only the stdlib and must keep doing so.
#: The VALUE is ``(target, slots)``: an id-keyed entry (an UNHASHABLE callable)
#: must pin the object whose address keys it — a freed callable's recycled id
#: served the previous callable's slots (BUGS-2026-08-13 I7; the memo mandate).
_slots_cache: Dict[Any, Tuple[Any, Tuple["Slot", ...]]] = {}


def slots(target: Any) -> Tuple["Slot", ...]:
    """Every configurable slot of ``target`` — the ONE enumeration.

    Signature parameters first, in SIGNATURE ORDER (which the dumper's round-trip
    pins rest on), then class attributes, then ``__init__``-body slots. A name is
    reported ONCE: a body slot that is also a signature parameter is the parameter.

    ``target`` may be a class OR any callable — the signature comes from
    :func:`init_callable`, so a registered builder FUNCTION answers like a class.
    A callable has no class attributes and no ``__init__`` body, so it yields
    signature slots alone.

    Best-effort by construction, like every reader it replaces: an unreadable
    signature yields no signature slots, an unresolvable annotation degrades to
    ``Any`` (the SLOT survives — losing it would drop a knob from every GUI), and
    an unscannable ``__init__`` (compiled / frozen) falls back to the declared and
    baked names.
    """
    cache_key = target if _hashable(target) else id(target)
    cached = _slots_cache.get(cache_key)
    if cached is not None:
        return cached[1]

    found: List[Slot] = []
    seen: Set[str] = set()

    init = init_callable(target)
    if init is not None:
        try:
            hints = get_type_hints(init, include_extras=True)
        except Exception:  # noqa: BLE001 - an unresolvable hint must not lose the SLOT
            hints = {}
        try:
            parameters = dict(inspect.signature(init).parameters)
        except (TypeError, ValueError):
            parameters = {}
        for name, param in parameters.items():
            if name in ("self", "cls"):
                continue
            seen.add(name)
            annotation = hints.get(name, param.annotation)
            found.append(
                Slot(
                    name=name,
                    kind=_PARAM_KIND_MAP[param.kind],
                    annotation=Any if annotation is inspect.Parameter.empty else annotation,
                    default=param.default,
                    source="signature",
                    owner=target,
                )
            )

    found.extend(_non_signature_slots(target, seen))
    result = tuple(found)
    _slots_cache[cache_key] = (target, result)  # the first element is the PIN
    return result


def _hashable(target: Any) -> bool:
    """Whether ``target`` can key a dict — a callable may define ``__eq__`` without ``__hash__``."""
    try:
        hash(target)
    except TypeError:
        return False
    return True


def _non_signature_slots(target: Any, seen: Set[str]) -> List[Slot]:
    """Class attributes and ``__init__``-body slots — ``@configurable`` CLASSES only.

    A plain callable has neither: its function attributes are not config slots,
    and there is no body to ``setattr`` into post-construction.
    """
    if not (isinstance(target, type) and getattr(target, "__confluid_configurable__", False)):
        return []

    out: List[Slot] = []
    for name in dir(target):
        if name.startswith("_") or name in seen:
            continue
        member = getattr(target, name, None)
        if member is None or callable(member):
            continue
        if isinstance(member, property) and member.fset is None:
            continue  # derived state, per the class-design convention — never a config knob
        seen.add(name)
        out.append(Slot(name, "class_attr", Any, member, "class_attr", target))

    for name, source, annotation, owner in _body_slot_sources(target):
        if name in seen:
            continue
        seen.add(name)
        out.append(Slot(name, "body_slot", annotation, NO_DEFAULT, source, owner))
    return out


def _body_slot_sources(target: type) -> List[Tuple[str, Any, Any, Any]]:
    """Body-slot ``(name, source, annotation)`` triples, MRO-wide.

    The effective NAME set is ``scan ∪ declared ∪ baked`` (the packaged-mode rule):
    fresh source always governs in a dev checkout, and the build-time bake table
    is consulted per MRO class only when that class's live scan finds nothing.

    The ANNOTATION is resolved here — ``self.run_name: Optional[str] = None`` is a
    typed slot, and reporting it as ``Any`` is a lie the class did not tell. It
    was resolved TWICE elsewhere until 2026-08-12 (``pydantic_export`` for its
    typed fields, ``confluid.partial`` for ``Partial[T]`` detection) with nothing
    checking the two agreed, across 163 annotated body slots in this workspace.
    A declared (``broadcast_attrs=``) or baked name carries no annotation and stays
    ``Any``; so does an unannotated ``self.x = …``.

    Measured cost of resolving eagerly: 74 us for a class with ten annotated
    slots, once per distinct class per pass (the result is cached), against a
    278 ms materialize. Lazy resolution was considered and rejected for that
    ratio — it would have made ``Slot`` something other than a plain NamedTuple.
    """
    out: List[Tuple[str, Any, Any, Any]] = []
    emitted: Set[str] = set()

    def _add(name: str, source: str, annotation: Any = Any, owner: Any = None) -> None:
        if name not in emitted:
            emitted.add(name)
            out.append((name, source, annotation, owner if owner is not None else target))

    for declared in getattr(target, "__confluid_broadcast_attrs__", None) or ():
        _add(declared, "declared")
    for klass in getattr(target, "__mro__", ()):
        if klass is object:
            continue
        init = klass.__dict__.get("__init__")
        if init is None:
            continue
        scanned = scan_init_body(init)
        annotations = {
            slot.name: slot.annotation
            for slot in scanned
            if slot.kind in ("assign", "annassign") and slot.annotation is not None
        }
        for name in sorted({slot.name for slot in scanned}):
            node = annotations.get(name)
            _add(name, "body_scan", resolve_ast_annotation(node, init) if node is not None else Any, klass)
        if not scanned:
            for name in baked_init_attrs(klass) or ():
                _add(name, "baked", Any, klass)
    return out


def body_slot_names(target: Any) -> Set[str]:
    """Every name ``__init__`` assigns, MRO-wide — INCLUDING ones that are also params.

    The sibling projection of :func:`slots`, over the same walk, answering a
    different question. ``slots()`` reports a name ONCE and lets the signature
    claim it, because a slot is a slot; this asks what the BODY assigns, which is
    what the broadcast layer's post-init injection needs to know (``self.model =
    model`` is both a parameter and a body assignment, and the accept-list unions
    the two).

    Sharing the walk is the point — the two answers may differ, but they must
    never disagree about what the body contains.
    """
    if not isinstance(target, type):
        return set()
    return {name for name, _, _, _ in _body_slot_sources(target)}


def slot_names(target: Any, kinds: FrozenSet[str]) -> Set[str]:
    """Names of ``target``'s slots whose kind is in ``kinds`` — the common projection.

    A caller states its rule as a KIND SET instead of re-deriving a "minus
    self/cls" filter, which is the drift this enumeration ends.
    """
    return {slot.name for slot in slots(target) if slot.kind in kinds}


def contains_forwardref(anno: Any) -> bool:
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
    return any(contains_forwardref(arg) for arg in get_args(anno))


def resolve_ast_annotation(annotation: Any, init_func: Any) -> Any:
    """Best-effort resolve an AST annotation node to a runtime type, else ``Any``.

    Lives here rather than beside its first caller because it is pure AST + ``typing``
    (the module map's rule: this module is the ONE stdlib-only scanning home) and has
    two consumers with nothing else in common — the pydantic exporter, which needs the
    type, and :func:`confluid.partial.partial_param_names`, which needs only the marker and
    must not reach into an optional-dependency module to get it.

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

    # UNWRAP first: `@configurable` replaces `__init__` with a validation wrapper whose
    # `__globals__` is confluid's own module dict, where the caller's names do not exist.
    # Resolving against it silently degraded EVERY body-slot annotation to `Any` — so a
    # class that dutifully wrote `self.optimizer: Partial[Optimizer] = ...` got an untyped
    # schema field and an undetected lazy slot. The scanners already see through the
    # wrapper for SOURCE; this makes the scope agree with them.
    target = inspect.unwrap(init_func)

    try:
        src = ast.unparse(annotation)
        scope: Dict[str, Any] = {**vars(_typing), **getattr(target, "__globals__", {})}
        resolved = eval(src, scope)  # noqa: S307 - trusted: source is our own __init__ annotation
    except Exception:
        return Any
    # A string forward ref evals to a ForwardRef instead of raising; pydantic
    # would build a model it can't finish (see _contains_forwardref). Degrade.
    return Any if contains_forwardref(resolved) else resolved
