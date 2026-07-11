import os
import re
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

import yaml
from loggair import get_logger

logger = get_logger("confluid.resolver")


_PathSegment = Tuple[str, Union[str, int]]
"""``(kind, value)`` — kind is one of ``"key"`` / ``"idx"`` / ``"idxref"``."""

# Token = either a bareword (``\.?(\w[\w-]*)``, optionally preceded by a dot)
# or a bracketed ``[...]`` group with non-empty inner content.
_PATH_TOKEN_RE = re.compile(r"\.?(\w[\w-]*)|\[([^\[\]]+)\]")
_INT_LITERAL_RE = re.compile(r"-?\d+")

# ``${...}`` interpolation. The name group is deliberately wider than an env
# identifier so a placeholder can ALSO carry a dotted / bracketed CONFIG-KEY
# path (``${train.dataset}`` / ``${items[0]}``); the presence of a ``.`` or
# ``[`` is what routes a placeholder to config-key lookup instead of
# ``os.getenv``. A plain identifier stays an environment variable, so every
# pre-existing ``${VAR}`` / ``${VAR:default}`` keeps its meaning.
_INTERP_RE = re.compile(r"\$\{([\w.\[\]-]+)(?::([^}]+))?\}")


def _is_config_path(name: str) -> bool:
    """A ``${...}`` name is a config-key path (not an env var) iff it carries a
    dotted key or a ``[...]`` index — an env var name never does."""
    return "." in name or "[" in name


def _is_scalar(value: Any) -> bool:
    """Values safe to embed into a larger string during interpolation."""
    return isinstance(value, (str, int, float, bool))


def _parse_path_segments(path: str) -> Optional[List[_PathSegment]]:
    """Tokenize a reference path into a flat list of segments.

    ``a.b[0].c[idx]`` →
    ``[("key","a"), ("key","b"), ("idx",0), ("key","c"), ("idxref","idx")]``.

    A bareword that's all-digits (e.g. the ``0`` in ``items.0``) is treated
    as an integer index — same disambiguation as the bracket form.
    Bracketed contents that aren't an integer literal become an
    ``"idxref"`` segment whose value is resolved against the walking
    context at lookup time.

    Returns ``None`` for paths that don't fully tokenize, so the caller
    can treat malformed input as "unresolved" without raising.
    """
    segments: List[_PathSegment] = []
    pos = 0
    n = len(path)
    while pos < n:
        m = _PATH_TOKEN_RE.match(path, pos)
        if not m or m.end() == pos:
            return None
        bare, bracket = m.group(1), m.group(2)
        if bracket is not None:
            inner = bracket.strip()
            if _INT_LITERAL_RE.fullmatch(inner):
                segments.append(("idx", int(inner)))
            else:
                segments.append(("idxref", inner))
        else:
            assert bare is not None
            if _INT_LITERAL_RE.fullmatch(bare):
                segments.append(("idx", int(bare)))
            else:
                segments.append(("key", bare))
        pos = m.end()
    return segments


def _materialize_cursor(value: Any) -> Any:
    """Flow a Fluid cursor into its live object before attribute access.

    Maps the raw marker through the active ``flow_memo`` first (the
    thread-local shared-identity memo ``_flow_recursive`` populates) so a
    dotted ref reuses the SINGLE materialized instance — ``!ref:split.train``
    + ``!ref:split.val`` share one live ``split`` instead of each rebuilding
    the whole subtree. Non-Fluid values pass through untouched.
    """
    from confluid.fluid import Fluid

    if not isinstance(value, Fluid):
        return value
    # The sanctioned lazy seam: resolver is imported by engine at top level,
    # so the reverse dependency (flow + the thread-local memo) is body-local.
    from confluid.engine import _state, flow

    flow_memo = getattr(_state, "flow_memo", None)
    if flow_memo is not None:
        value = flow_memo.get(id(value), value)
    return flow(value)


def _walk_path_segments(
    segments: List[_PathSegment],
    context: Any,
    lookup_fn: Callable[[str, Dict[str, Any]], Any],
    *,
    getattr_fallback: bool = False,
) -> Any:
    """Walk pre-tokenized segments through nested dicts and lists.

    For ``"idxref"`` segments, ``lookup_fn(name, context)`` is called with
    the *original* context (not the current cursor) so the inner name
    resolves in the same scope as the outer reference. The resolved value
    decides the step semantics: ``int`` → list index, ``str``/``int``
    → dict key.

    Two policies share this walker:

    * **structural** (``getattr_fallback=False``, the default) — dicts and
      lists only; the policy behind ``_lookup_path`` (string ``!ref:`` /
      ``${...}`` interpolation / ``configure()``). A ``${train.split}`` can
      never accidentally grab a ``str.split`` method.
    * **object** (``getattr_fallback=True``) — a ``key`` segment on a
      NON-container cursor falls back to ``getattr`` (Fluids are flowed via
      :func:`_materialize_cursor` first). Dict-key/index lookup still wins
      while the cursor IS a container — attribute access only starts once
      the walk leaves structured data. This is the Reference-resolution
      policy (:func:`resolve_reference_path`).

    Returns ``None`` when the walk can't proceed (key missing, index
    out of range, type mismatch), preserving the caller's
    "missing → None" contract.
    """
    current: Any = context
    for kind, val in segments:
        if kind == "key":
            if isinstance(current, dict):
                if val in current:
                    current = current[val]
                    continue
                return None  # dict-key wins on dicts — never getattr into a dict
            if getattr_fallback and not isinstance(current, (list, tuple)):
                current = _materialize_cursor(current)
                nxt = getattr(current, str(val), None)
                if nxt is None:
                    return None
                current = nxt
                continue
            return None
        if kind == "idx":
            assert isinstance(val, int)
            if isinstance(current, (list, tuple)) and -len(current) <= val < len(current):
                current = current[val]
                continue
            return None
        if kind == "idxref":
            assert isinstance(val, str)
            if not isinstance(context, dict):
                return None
            ref_val = lookup_fn(val, context)
            if ref_val is None:
                return None
            if isinstance(current, (list, tuple)):
                if not isinstance(ref_val, int):
                    return None
                if -len(current) <= ref_val < len(current):
                    current = current[ref_val]
                    continue
                return None
            if isinstance(current, dict):
                if isinstance(ref_val, (str, int)) and ref_val in current:
                    current = current[ref_val]
                    continue
                return None
            return None
    return current


_CALL_SUFFIX_RE = re.compile(r"^(.+)\.([\w-]+)\(\)$")


def _import_base(obj_path: str) -> Any:
    """Resolve an out-of-context base path: importable module first, else registry/class path."""
    import importlib

    try:
        return importlib.import_module(obj_path)
    except ImportError:
        from confluid.registry import resolve_class

        return resolve_class(obj_path)


def resolve_reference_path(target: str, context: Optional[Dict[str, Any]]) -> Any:
    """Resolve a dotted / bracketed ``!ref:`` path with OBJECT-access semantics.

    The single rich resolver behind ``Reference`` resolution (used by
    ``flow()`` and ``_flow_recursive`` after their exact-key probe). One
    grammar covers everything the old per-module resolvers split between
    them:

    * ``obj.attr`` — attribute access on a (flowed) context object; the base
      is materialized via :func:`_materialize_cursor`, so dotted refs share
      the single live instance (``!ref:split.train`` / ``!ref:split.val``).
    * ``a.b.c`` / ``packs[0].name`` / ``items[idx]`` — full multi-level
      walks mixing dict keys, list indices, bracketed name-refs, and
      attribute steps (:func:`_walk_path_segments` with the object policy).
    * ``obj.method()`` — a trailing ``()`` CALLS the resolved final
      attribute (zero-arg, re-invoked on every resolution — never memoized).
    * ``package.module.attr`` — when the base is not in ``context``, it is
      imported (``importlib``) or resolved via the class registry, e.g.
      ``!ref:raidar.detection.detection_collate_fn``.

    A literal context key containing dots (``"a.b"``) still wins over the
    segment walk for its prefix, mirroring ``_lookup_path``'s
    literal-key-first rule. Returns ``None`` when unresolvable — the caller
    decides whether that leaves the ``Reference`` deferred or raises.
    """
    ctx = context or {}

    call_match = _CALL_SUFFIX_RE.match(target)
    if call_match:
        base = _resolve_base_path(call_match.group(1), ctx)
        if base is None:
            return None
        method = getattr(base, call_match.group(2), None)
        if method is not None and callable(method):
            return method()
        return None

    # Attribute form: literal-prefix probe first (grammar parity for context
    # keys literally named "a.b"), then the rich segment walk, then import.
    prefix, _, last = target.rpartition(".")
    if prefix and prefix in ctx:
        base = _materialize_cursor(ctx[prefix])
        # Containers keep dict-key/index semantics (the walker's job) — never
        # getattr into a dict/list, or ``cfg.items`` would silently resolve to
        # the builtin ``dict.items`` method instead of missing.
        if not isinstance(base, (dict, list, tuple)):
            return getattr(base, last, None)

    segments = _parse_path_segments(target)
    if segments is not None:
        lookup = Resolver(context=ctx)._lookup_path
        # A PURELY structural path (dict keys / list indices only) is NOT this
        # resolver's to take: the deferred-Reference machinery deliberately
        # keeps it late-bound so post-load overrides (e.g. liquifai's
        # ``--drone_index 8``) still flow through at final materialize time.
        # Only when the structural walk misses do we retry with the OBJECT
        # policy — i.e. the resolution genuinely required an attribute step.
        if _walk_path_segments(segments, ctx, lookup) is None:
            found = _walk_path_segments(segments, ctx, lookup, getattr_fallback=True)
            if found is not None:
                return found

    if prefix:
        base = _import_base(prefix)
        if base is not None:
            return getattr(base, last, None)
    return None


def _resolve_base_path(obj_path: str, ctx: Dict[str, Any]) -> Any:
    """Resolve the base object of a ``.method()`` reference (context → walk → import)."""
    if obj_path in ctx:
        return _materialize_cursor(ctx[obj_path])
    segments = _parse_path_segments(obj_path)
    if segments is not None:
        found = _walk_path_segments(segments, ctx, Resolver(context=ctx)._lookup_path, getattr_fallback=True)
        if found is not None:
            return _materialize_cursor(found)
    return _import_base(obj_path)


class Resolver:
    """Resolves references (!ref), environment variables (${ENV}), and deep keys."""

    def __init__(self, context: Optional[Dict[str, Any]] = None) -> None:
        self.context = context or {}

    def resolve(self, value: Any, local_context: Optional[Dict[str, Any]] = None) -> Any:
        """
        Recursively resolves markers with support for local scoping.
        """
        # 1. Handle Strings (Interpolation and Tags)
        if isinstance(value, str):
            value = self._interpolate(value, local_context)
            if not isinstance(value, str):
                return value

            if value.startswith("!ref:"):
                ref_path = value[5:]
                res = self._resolve_ref(ref_path, local_context)
                # Recurse only if the resolved value is DIFFERENT from the input
                if res != value and isinstance(res, (str, dict)):
                    return self.resolve(res, local_context)
                return res

            if value.startswith("!class:"):
                content = value[7:]
                return self._parse_class_string(content, local_context)

            return value

        # 2. Handle Fluid citizens
        from confluid.fluid import Class, Fluid, Reference

        if isinstance(value, Reference):
            res = self._resolve_ref(value.target, local_context)
            if res == f"!ref:{value.target}":
                return value  # unresolvable — leave the Reference for flow() to retry
            # When the Reference points at another Fluid (Class / Instance /
            # nested Reference), substitute it eagerly so identity-based
            # aliasing works (``result["alias"] is result["thing"]``). When
            # it resolves to a scalar / list / dict, keep the Reference Fluid
            # so later overrides of the source key (e.g. liquifai's
            # ``--drone_index 8`` merged into ``config_data`` after load)
            # can flow through to the rendered value at materialize time.
            if isinstance(res, Fluid):
                return self.resolve(res, local_context)
            return value

        if isinstance(value, (Class, Fluid)):
            return value

        # 3. Handle Dictionaries — recurse, passing the current dict as local_context
        if isinstance(value, dict):
            return {k: self.resolve(v, local_context=value) for k, v in value.items()}

        # 4. Handle Lists
        if isinstance(value, list):
            return [self.resolve(item, local_context) for item in value]

        return value

    def _parse_class_string(self, content: str, local_context: Optional[Dict[str, Any]] = None) -> Any:
        """Parse a string ``'ClassName(args)'`` / ``'ClassName'`` into a Fluid marker.

        ``Name(...)`` (with parens) is eager → :class:`Instance`; a bare
        ``Name`` is deferred → :class:`Class` — the same eager-vs-deferred
        rule as the ``!class:`` YAML tag. Kwargs are assigned
        post-construction so a kwarg literally named ``target`` can't collide
        with the Fluid ctor's own parameter.
        """
        from confluid.fluid import Class, Instance

        if "(" in content and content.endswith(")"):
            cls_name, args_str = content[:-1].split("(", 1)
            fluid = Instance(cls_name)
            if args_str.strip():
                for pair in args_str.split(","):
                    if "=" in pair:
                        k, v = pair.split("=", 1)
                        # Resolve and Parse the value!
                        resolved_v = self.resolve(v.strip(), local_context)
                        if isinstance(resolved_v, str):
                            resolved_v = self._parse_primitive(resolved_v)
                        fluid.kwargs[k.strip()] = resolved_v
            return fluid
        return Class(content)

    def _resolve_ref(self, ref_path: str, local_context: Optional[Dict[str, Any]] = None) -> Any:
        """
        Resolve a dotted path against local and global contexts.
        """
        # 1. Try Local Context First
        if local_context:
            val = self._lookup_path(ref_path, local_context)
            if val is not None and (not isinstance(val, str) or not val.startswith("!ref:")):
                return val

        # 2. Try Global Context
        val = self._lookup_path(ref_path, self.context)
        if val is not None:
            return val

        # Debug, not warning: this load-time pass legitimately misses refs that
        # only resolve at flow time (attribute refs like ``!ref:split.train``,
        # module-path refs) — a warning here is pure noise for valid configs.
        # A ref that never resolves fails LOUDLY at flow() with a typed
        # ReferenceResolutionError, which is the actionable signal.
        logger.debug(f"Reference not resolvable at this stage (deferred to flow): {ref_path}")
        return f"!ref:{ref_path}"

    def _lookup_path(self, path: str, context: Dict[str, Any]) -> Any:
        """Drill into a dict / list via a dotted + bracketed path.

        Supported segment shapes:

        * Dotted dict keys — ``a.b.c``.
        * List indices (numeric segment after dot) — ``items.0``.
        * List indices (bracket form, ``int`` literal) — ``items[0]`` /
          ``items[-1]``.
        * Bracketed name refs — ``items[idx]``: resolves ``idx`` against the
          same context, then uses the resolved value as a list index (when
          ``int``) or dict key (when ``str`` / ``int``). The inner name may
          itself be a bracketed / dotted path (``packs[config.which]``).
        * Free combinations — ``packs[0].name``, ``packs[idx].sub[1]``.

        Returns ``None`` when the path can't be fully walked, which the
        caller treats as "unresolved" — same as before.
        """
        # 1. Direct literal lookup first — preserves the legacy "keys with
        #    dots / brackets in the literal name" behavior.
        if path in context:
            return context[path]

        # 2. Tokenize, then walk.
        segments = _parse_path_segments(path)
        if segments is None:
            return None
        return _walk_path_segments(segments, context, self._lookup_path)

    def _interpolate(self, value: str, local_context: Optional[Dict[str, Any]] = None) -> Any:
        """Substitute ``${...}`` placeholders in a string.

        Two families share the ``${...}`` syntax, dispatched purely on the name:

        * ``${NAME}`` / ``${NAME:default}`` — an **environment variable**
          (``os.getenv``), the historical behaviour. A plain identifier (no
          ``.`` / ``[``) is always an env var.
        * ``${a.b.c}`` / ``${items[0]}`` / ``${a.b:default}`` — a dotted or
          bracketed **config-key path**, resolved against the config tree
          (local context first, then global) with the same ``_lookup_path``
          machinery ``!ref:`` uses. This lets a YAML string embed another
          config value, e.g.
          ``data_dir: "${DATA_ROOT}/${train.dataset}/${train.version}"``.

        A whole-string match returns the resolved value with its native type;
        an embedded match substitutes ``str(value)`` (scalars only — a
        non-scalar target is left as the literal ``${...}``). On a miss the
        ``:default`` is applied (parsed), else the literal ``${...}`` is left
        in place. The referenced config value must already be a resolved
        literal / scalar (interpolation is a single pass, like ``!ref:``).
        """
        if "${" not in value:
            return value

        # Whole-string match — return the resolved value with its real type.
        whole = _INTERP_RE.fullmatch(value)
        if whole:
            resolved, found = self._resolve_placeholder(whole.group(1), whole.group(2), local_context)
            return resolved if found else value

        # Embedded matches — substitute each occurrence as a string.
        def replacer(match: "re.Match[str]") -> str:
            resolved, found = self._resolve_placeholder(match.group(1), match.group(2), local_context)
            if found and _is_scalar(resolved):
                return str(resolved)
            return match.group(0)  # miss / non-scalar → leave the literal ${...}

        return _INTERP_RE.sub(replacer, value)

    def _resolve_placeholder(
        self, name: str, default_val: Optional[str], local_context: Optional[Dict[str, Any]]
    ) -> Tuple[Any, bool]:
        """Resolve one ``${...}`` placeholder to ``(value, found)``.

        A config-key name (dotted / bracketed) is looked up in
        ``local_context`` then ``self.context``; a plain name is an env var.
        On a miss, ``default_val`` (if any) is parsed and returned; otherwise
        ``(None, False)`` signals "leave the literal ``${...}`` in place". A
        looked-up ``None`` is treated as a miss, matching ``_resolve_ref``.
        """
        if _is_config_path(name):
            for ctx in (local_context, self.context):
                if ctx:
                    found = self._lookup_path(name, ctx)
                    if found is not None:
                        return found, True
        else:
            env_val = os.getenv(name)
            if env_val is not None:
                return self._parse_primitive(env_val), True
        if default_val is not None:
            return self._parse_primitive(default_val), True
        return None, False

    def _parse_primitive(self, value: str) -> Any:
        """Convert string to appropriate Python primitive."""
        if value.startswith("!ref:"):
            return value
        return parse_value(value)


def parse_value(value: str) -> Any:
    """Parse a string value into a Python type using YAML for complex types.

    Examples:
        "42" -> 42, "3.14" -> 3.14, "true" -> True, "[1, 2]" -> [1, 2]
    """
    low = value.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    if low in ("null", "none"):
        return None

    try:
        return yaml.safe_load(value)
    except Exception:
        return value
