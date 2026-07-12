"""Stdlib-only source introspection shared across confluid.

ONE AST scan of an ``__init__`` body (:func:`scan_init_body`) backs the three
projections that used to be three near-identical scanners in ``loader`` and
``pydantic_export``:

* :func:`init_setattr_names` — every assigned body-slot NAME (the broadcast /
  accept-list view; the widest — includes ``AugAssign`` and literal
  ``setattr(self, "x", …)``).
* :func:`init_setattr_annotations` — ``{name: annotation AST node or None}``
  for plain/annotated assignments (the ``to_pydantic`` body-slot typing view;
  first assignment wins, in ``ast.walk`` order).
* :func:`init_lazy_setattr_names` — names whose assigned VALUE is a
  ``LazyClass(...)`` / ``Lazy(...)`` call (deferred body slots — emitted as
  ``!lazy:`` by serializers).

The projections deliberately differ in which slot KINDS they see — that
preserves the semantics of the three original scanners (``AugAssign`` and
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
from typing import Any, Dict, Literal, NamedTuple, Optional, Set, Tuple

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


def init_setattr_annotations(init_func: Any) -> Dict[str, Any]:
    """``{name: annotation AST node or None}`` for assign/annassign slots.

    First assignment per name wins (``ast.walk`` order) — a plain ``Assign``
    seen first maps the name to ``None`` (→ typed ``Any``) even if a later
    ``AnnAssign`` carries a type, matching the original scanner.
    ``AugAssign``/``setattr`` slots are deliberately EXCLUDED (they never
    become pydantic body-slot fields).
    """
    found: Dict[str, Any] = {}
    for slot in scan_init_body(init_func):
        if slot.kind in ("assign", "annassign"):
            found.setdefault(slot.name, slot.annotation)
    return found


def init_lazy_setattr_names(init_func: Any) -> Set[str]:
    """Names of assign/annassign slots whose VALUE is a ``LazyClass(...)``/``Lazy(...)`` call.

    An annotated declaration without a value (``self.x: T``) and
    ``AugAssign``/``setattr`` slots never qualify.
    """
    return {
        slot.name
        for slot in scan_init_body(init_func)
        if slot.kind in ("assign", "annassign") and _is_lazy_call(slot.value)
    }


def _is_lazy_call(value: Any) -> bool:
    """True for a call whose callee is named ``LazyClass`` or ``Lazy``.

    Matches bare names AND attribute-qualified calls (``confluid.LazyClass(...)``)
    by inspecting only the final attribute — same rule as the original scanner.
    """
    if not isinstance(value, ast.Call):
        return False
    func = value.func
    name = func.id if isinstance(func, ast.Name) else (func.attr if isinstance(func, ast.Attribute) else None)
    return name in ("LazyClass", "Lazy")


# NOTE — a shared "ctor params minus self/cls" helper was CONSIDERED here and
# deliberately NOT shipped: the apparent duplicates each carry a load-bearing
# difference the shared shape can't express — the dumper needs ORDERED params
# (dump-key order is round-trip-pinned), the loader accept-list needs its
# ``**kwargs`` → ``None`` broadcast-everything sentinel, and schema /
# pydantic_export consume rich ``inspect.Parameter`` metadata, not name sets.
# The AST body-slot scan above is the real duplication; the signature walks
# are not.
