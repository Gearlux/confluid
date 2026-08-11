"""Stdlib-only source introspection shared across confluid.

ONE AST scan of an ``__init__`` body (:func:`scan_init_body`) backs the
projections that used to be near-identical scanners in ``loader`` and
``pydantic_export``:

* :func:`init_setattr_names` — every assigned body-slot NAME (the broadcast /
  accept-list view; the widest — includes ``AugAssign`` and literal
  ``setattr(self, "x", …)``).
* :func:`init_partial_setattr_names` — names whose assigned VALUE is a
  ``PartialClass(...)`` / ``Partial(...)`` call (deferred body slots — emitted as
  ``!lazy:`` by serializers).

(``pydantic_export`` consumes :func:`scan_init_body` directly for its typed
body-slot fields; an ``init_setattr_annotations`` projection existed for that
role, was orphaned by the switch, and was deleted 2026-08-09.)

The projections deliberately differ in which slot KINDS they see — that
preserves the semantics of the original scanners (``AugAssign`` and
``setattr`` slots broadcast, but never become pydantic fields or lazy slots).

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


#: The call names that mark a body slot deferred. The ``Lazy*`` spellings are the
#: pre-rename ones and MUST stay until the aliases go: this scan matches on the
#: NAME in the source, so dropping them makes every consumer still written as
#: ``self.x = LazyClass(...)`` — which is all of them, since the alias exists so
#: they need not change — silently stop being deferred. No error, no diagnostic:
#: the slot is simply built, and an optimizer gets constructed without its params.
_PARTIAL_CALL_NAMES = ("PartialClass", "Partial", "LazyClass", "Lazy")


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


# NOTE — a shared "ctor params minus self/cls" helper was CONSIDERED here and
# deliberately NOT shipped: the apparent duplicates each carry a load-bearing
# difference the shared shape can't express — the dumper needs ORDERED params
# (dump-key order is round-trip-pinned), the loader accept-list needs its
# ``**kwargs`` → ``None`` broadcast-everything sentinel, and schema /
# pydantic_export consume rich ``inspect.Parameter`` metadata, not name sets.
# The AST body-slot scan above is the real duplication; the signature walks
# are not.


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
