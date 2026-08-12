import os
import re
from typing import Any, Callable, Dict, FrozenSet, List, Optional, Set, Tuple, Union

import yaml
from loggair import get_logger

from confluid.exceptions import ConfigurationError

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

# Bare ``$IDENTIFIER`` — an ENVIRONMENT-variable read, expanded AFTER the
# ``${...}`` pass. The ``{`` sits outside the character class, so this regex
# can never match a ``${...}`` placeholder — an unresolved braced literal the
# pass above left in place survives the bare pass untouched. Env-only by
# design: NO dotted config-path form and NO ``:default`` — those spellings
# remain ``${...}``-exclusive.
_BARE_ENV_RE = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")

# ``${name:arg}`` placeholders whose NAME is one of these are RESOLVER CALLS, not
# a config key with a ``:default`` — the OmegaConf-idiomatic spelling, and the
# plain-YAML counterpart of the ``!ref:`` / ``!clone:`` tags:
#
#   ${env:DATA_ROOT}    ${env:PORT,8080}    ${oc.env:HOME}   -> environment
#   ${ref:proto}        ${clone:proto}                       -> a marker
#
# Checked BEFORE the config-path test, because ``oc.env`` contains a dot and would
# otherwise route to config-key lookup.
_ENV_RESOLVERS: FrozenSet[str] = frozenset({"env", "oc.env"})
_MARKER_RESOLVERS: FrozenSet[str] = frozenset({"ref", "clone"})

# The ONE ``Target(...)`` call grammar — a target name (dotted paths and the
# ``@axis=value`` / ``$key`` selector characters included; all legal YAML
# tag-suffix characters) followed by an inline-kwargs parenthesis group. Shared
# by the YAML tag constructors (the loader imports it) and the quoted-string
# marker parser below, which used to hand-roll a laxer split that accepted
# spellings no tag can carry (names with spaces or braces) — one grammar, one
# answer, whichever way a target is spelled.
_TARGET_CALL_RE = re.compile(r"^([\w.@=/,~$-]+)\((.*)\)$")


#: Marker prefixes the QUOTED-STRING spelling can be written with. Only the first
#: two are ever honoured, and only outside a marker's own kwargs — the rest, and
#: every one of them in the wrong position, used to reach the config as the literal
#: TEXT with no error, no warning and no diagnostic. See :func:`_refuse_marker_string`.
_STRING_MARKERS_PARSED: FrozenSet[str] = frozenset({"!class:", "!ref:"})
_STRING_MARKERS_ALL: Tuple[str, ...] = ("!class:", "!lazy:", "!ref:", "!clone:", "!notscope:", "!scope:")


def _plain_yaml_for(value: str) -> str:
    """The reserved-key spelling of a quoted marker string, for the error message.

    Best effort by design: the point is to hand the author the line to write, not
    to be a second parser. An unparseable suffix falls back to naming the keys.
    """
    from confluid.loader import CLONE_KEY, PARTIAL_KEY, REF_KEY, SCOPE_KEY, TARGET_KEY

    prefix = next((m for m in _STRING_MARKERS_ALL if value.startswith(m)), "")
    body = value[len(prefix) :].strip()
    if prefix in ("!ref:", "!clone:"):
        key = REF_KEY if prefix == "!ref:" else CLONE_KEY
        return f"{{{key}: {body}}}" if body else f"{{{key}: <path>}}"
    if prefix in ("!scope:", "!notscope:"):
        key = SCOPE_KEY if prefix == "!scope:" else "_notscope_"
        dim, _, val = body.partition("=")
        return f"{{{key}: {{{dim or '<dimension>'}: {val}}}}}"
    call = _TARGET_CALL_RE.match(body)
    name = call.group(1) if call else body
    parts = [f"{TARGET_KEY}: {name or '<Target>'}"]
    if prefix == "!lazy:":
        parts.append(f"{PARTIAL_KEY}: true")
    parts.extend(f"{k}: {v}" for k, v in (_split_inline_pairs(call.group(2)) if call else []))
    return "{" + ", ".join(parts) + "}"


def _refuse_marker_string(value: str, *, where: str) -> None:
    """Raise when a quoted marker string cannot be honoured where it was written.

    The QUOTED-STRING spelling (``optimizer: "!class:Adam(lr=!ref:base)"``) is a
    third input grammar beside the YAML tags and the reserved keys, and it exists
    only to work around a limitation of the tags: YAML forbids two tags on one
    node, so a nested ``!ref:`` had to be quoted. The reserved-key format has no
    such limitation, and this spelling is deleted with the tags in 0.4.0.

    Until then it must not fail SILENTLY, which is what it did in two whole
    classes of position — ``!lazy:`` / ``!clone:`` / ``!scope:`` anywhere, and
    even ``!class:`` / ``!ref:`` inside a marker's own kwargs, the very position
    ``docs/targets.md`` recommended it for. The value reached the constructor as
    the literal text ``!lazy:Adam(lr=0.01)`` and nothing said so. That is exactly
    the failure mode the plain-YAML format exists to end.

    No ``file:line`` here, and it is not an oversight: only Fluid MARKERS carry a
    location (``loader._stamp_loc``) — PyYAML discards per-key marks for ordinary
    scalars, so a bare string has none to report (tracked in ``TASKS.md``). The
    message therefore quotes the offending text verbatim, which is greppable, and
    names the exact line to write instead.
    """
    raise ConfigurationError(
        f"{value!r} is a marker written as a quoted STRING, which confluid cannot honour "
        f"{where}. Write it as plain YAML instead:\n\n    {_plain_yaml_for(value)}\n\n"
        f"(The quoted-string spelling is deprecated and is removed in confluid 0.4.0; only "
        f'"!class:" and "!ref:" were ever parsed from a string, and only outside a marker\'s '
        f"own kwargs.)"
    )


def _split_inline_pairs(args_str: str) -> List[Tuple[str, str]]:
    """Split an inline-kwargs suffix ``"a=1, b=x"`` into raw ``(key, value)`` pairs.

    The split is the grammar; value COERCION is deliberately the caller's
    policy — the tag form coerces via ``parse_value``, the quoted-string form
    resolves ``${...}`` / ``!ref:`` against its context first. A pair without
    ``=`` is skipped, matching both callers' historical behaviour.
    """
    pairs: List[Tuple[str, str]] = []
    if args_str and args_str.strip():
        for pair in args_str.split(","):
            if "=" in pair:
                k, v = pair.split("=", 1)
                pairs.append((k.strip(), v.strip()))
    return pairs


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
    per-context shared-identity memo ``_flow_recursive`` populates) so a
    dotted ref reuses the SINGLE materialized instance — ``!ref:split.train``
    + ``!ref:split.val`` share one live ``split`` instead of each rebuilding
    the whole subtree. Non-Fluid values pass through untouched.
    """
    from confluid.fluid import Fluid

    if not isinstance(value, Fluid):
        return value
    # The sanctioned lazy seam: resolver is imported by engine at top level,
    # so the reverse dependency (flow + the engine-state memo) is body-local.
    from confluid.engine import _ENGINE_STATE, flow

    flow_memo = _ENGINE_STATE.get().flow_memo
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
    """Resolves references (!ref), environment variables (${ENV} / bare $VAR), and deep keys."""

    def __init__(self, context: Optional[Dict[str, Any]] = None) -> None:
        self.context = context or {}

    def resolve(self, value: Any, local_context: Optional[Dict[str, Any]] = None) -> Any:
        """
        Recursively resolves markers with support for local scoping.
        """
        from confluid.fluid import Fluid, Reference

        # 1. Handle Strings (Interpolation and Tags)
        if isinstance(value, str):
            value = self._interpolate(value, local_context)
            if not isinstance(value, str):
                # A ``${ref:...}`` / ``${clone:...}`` interpolates to a MARKER, which
                # must take the same resolution path as the tag spelling below rather
                # than being returned raw. Any other non-string (an int from
                # ``${a.b}``, say) is already final and falls straight through.
                return self.resolve(value, local_context) if isinstance(value, Fluid) else value

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

            # Every OTHER marker prefix is a marker attempt this path cannot honour.
            # Narrow on purpose: only these exact prefixes, never a bare leading "!",
            # because an ordinary config value may legitimately start with one.
            if any(value.startswith(m) for m in _STRING_MARKERS_ALL if m not in _STRING_MARKERS_PARSED):
                _refuse_marker_string(value, where="here")

            return value

        # 2. Handle Fluid citizens
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

        if isinstance(value, Fluid):
            # A marker passes through WHOLE — its kwargs are the engine's to
            # consume — but its kwarg STRINGS get ``${...}`` substituted first.
            # Before this, a placeholder written in a tag's mapping body stayed
            # the literal string on every path (measured 2026-08-08), while the
            # quoted-string spelling of the same target interpolated via
            # ``_parse_class_string`` — two spellings, two answers.
            self._interpolate_fluid_kwargs(value)
            return value

        # 3. Handle Dictionaries — recurse, passing the current dict as local_context
        if isinstance(value, dict):
            return {k: self.resolve(v, local_context=value) for k, v in value.items()}

        # 4. Handle Lists
        if isinstance(value, list):
            return [self.resolve(item, local_context) for item in value]

        return value

    def _interpolate_fluid_kwargs(self, fluid: Any, _seen: Optional[Set[int]] = None) -> None:
        """Substitute ``${...}`` inside a marker's kwargs, IN PLACE — text only.

        The interpolation-restricted twin of :meth:`resolve` for marker kwargs:
        strings are ``_interpolate``-d (a ``"!ref:"``/``"!class:"`` STRING keeps
        its prefix for flow-time parsing, matching the top-level order —
        interpolate first, parse later), a ``Reference`` fluid stays late-bound
        untouched, nested markers recurse, and containers are rewritten in
        place. Sibling kwargs act as the local scope, exactly as a plain dict's
        keys do in :meth:`resolve`.

        IN PLACE on purpose: marker identity is load-bearing — the engine's
        flow memo and ``!ref:`` sharing key on ``id()`` — and the broadcast
        layer's ``_expand_block_keys`` already extends marker kwargs by
        reference. Substitution burns the value in at load time (the documented
        single-pass contract); a slot that must stay late-bound uses ``!ref:``.
        ``_seen`` guards hand-built marker cycles; a marker reached twice in one
        document is walked once per entry, which is idempotent either way.
        """
        seen = _seen if _seen is not None else set()
        if id(fluid) in seen:
            return
        seen.add(id(fluid))
        kwargs = fluid.kwargs
        for key in list(kwargs):
            kwargs[key] = self._interpolate_only(kwargs[key], kwargs, seen)

    def _interpolate_only(self, value: Any, local_context: Optional[Dict[str, Any]], seen: Set[int]) -> Any:
        """The kwargs-walk value step: interpolate strings, recurse containers/markers.

        Deliberately NOT :meth:`resolve` — that would also parse ``"!ref:"`` /
        ``"!class:"`` strings and eagerly resolve ``Reference`` fluids, changing
        WHEN deferred values bind. Only text substitution happens here.
        """
        from confluid.fluid import Fluid, Reference

        if isinstance(value, str):
            # A marker string inside a marker's own kwargs is honoured by NOTHING —
            # not here (this walk is text substitution only, deliberately, so that
            # deferred values keep binding when they bind), and not downstream. It
            # reached the constructor as literal text, which is the position
            # ``docs/targets.md`` used to recommend the spelling for.
            if any(value.startswith(m) for m in _STRING_MARKERS_ALL):
                _refuse_marker_string(value, where="inside a marker's own kwargs")
            return self._interpolate(value, local_context)
        if isinstance(value, Reference):
            return value  # late-bound by design — flow() resolves it
        if isinstance(value, Fluid):
            self._interpolate_fluid_kwargs(value, seen)
            return value
        if isinstance(value, dict):
            for key in list(value):
                value[key] = self._interpolate_only(value[key], value, seen)
            return value
        if isinstance(value, list):
            for index, item in enumerate(value):
                value[index] = self._interpolate_only(item, local_context, seen)
            return value
        return value

    def _parse_class_string(self, content: str, local_context: Optional[Dict[str, Any]] = None) -> Any:
        """Parse a string ``'ClassName(args)'`` / ``'ClassName'`` into a :class:`Target` marker.

        Both spellings produce the SAME thing — the trailing ``()`` is inert. It
        marked an eager ``Instance`` against a deferred ``Class`` until the two
        collapsed into one ``Target`` carrying ``partial`` (2026-08-11); ``partial``
        is now the only thing that withholds construction, and this string form has
        no way to spell it. Kwargs are assigned post-construction so a kwarg
        literally named ``target`` can't collide with the marker ctor's own
        parameter.

        Note this is the QUOTED-STRING spelling — a third input grammar beside the
        YAML tags and the reserved keys, reached only through
        :meth:`Resolver.resolve`. It resolves each inline value against the context
        EAGERLY (see below), where ``_target_``'s ``${ref:...}`` stays late-bound, so
        the two are not interchangeable for a slot flowed outside the document.
        """
        from confluid.fluid import Target

        instant = _TARGET_CALL_RE.match(content)
        if instant:
            fluid = Target(instant.group(1))
            for k, v in _split_inline_pairs(instant.group(2)):
                # Resolve THEN parse — the quoted-string form's own coercion
                # policy: ``${...}`` / ``!ref:`` values see the context first.
                resolved_v = self.resolve(v, local_context)
                if isinstance(resolved_v, str):
                    resolved_v = self._parse_primitive(resolved_v)
                fluid.kwargs[k] = resolved_v
            return fluid
        return Target(content)

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
        """Substitute ``${...}`` placeholders — then bare ``$VAR`` — in a string.

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

        AFTER the ``${...}`` pass, bare ``$IDENTIFIER`` occurrences expand as
        ENVIRONMENT variables (:meth:`_expand_bare_env`), so
        ``root: $DATA_ROOT/...`` behaves identically on every entry path
        instead of only through front-ends that re-implement
        ``os.path.expandvars``. Marker strings (leading ``!``) are exempt from
        the bare pass — see the guard below.
        """
        # GUARD: a marker STRING ("!class:..." / "!lazy:..." / "!ref:...")
        # keeps its bare-$ text for FLOW-time parsing — the ``@axis=$key``
        # DOCUMENT-selector grammar also spells ``$`` in tag targets, and
        # expanding here could burn an env var in over a document key.
        # ``${...}`` still substitutes (``{`` is not a legal tag character, so
        # the two grammars cannot collide).
        expand_bare = not value.startswith("!")

        if "${" not in value:
            return self._expand_bare_env(value) if expand_bare else value

        # Whole-string match — return the resolved value with its real type.
        whole = _INTERP_RE.fullmatch(value)
        if whole:
            marker = self._marker_resolver(whole.group(1), whole.group(2))
            if marker is not None:
                return marker
            resolved, found = self._resolve_placeholder(whole.group(1), whole.group(2), local_context)
            return resolved if found else value

        # Embedded matches — substitute each occurrence as a string.
        def replacer(match: "re.Match[str]") -> str:
            if match.group(1) in _MARKER_RESOLVERS:
                # A reference resolves to an OBJECT; there is no meaningful way to
                # splice one into the middle of a string, and silently stringifying
                # it would hide the mistake behind a plausible-looking value.
                raise ConfigurationError(
                    f"{match.group(0)} cannot be embedded in a string — "
                    f"a reference must be the whole value (got {value!r})"
                )
            resolved, found = self._resolve_placeholder(match.group(1), match.group(2), local_context)
            if found and _is_scalar(resolved):
                return str(resolved)
            return match.group(0)  # miss / non-scalar → leave the literal ${...}

        result = _INTERP_RE.sub(replacer, value)
        return self._expand_bare_env(result) if expand_bare else result

    def _expand_bare_env(self, value: str) -> str:
        """Expand bare ``$IDENTIFIER`` occurrences as environment variables.

        Runs AFTER the ``${...}`` pass, text-only. An UNSET variable leaves
        the ``$name`` text literal, mirroring ``os.path.expandvars``.
        Deliberately env-only: no dotted config-path form and no ``:default``
        — those spellings remain ``${...}``-exclusive.
        """
        if "$" not in value:
            return value

        def replacer(match: "re.Match[str]") -> str:
            env_val = os.getenv(match.group(1))
            return env_val if env_val is not None else match.group(0)  # unset → literal $name

        return _BARE_ENV_RE.sub(replacer, value)

    def _marker_resolver(self, name: str, arg: Optional[str]) -> Any:
        """Build the marker for a ``${ref:path}`` / ``${clone:path}`` placeholder.

        Returns ``None`` when ``name`` is not a marker resolver, so the caller
        falls through to ordinary placeholder resolution.

        This is the scalar shorthand for the ``_ref_`` / ``_clone_`` reserved keys
        — the same markers the ``!ref:`` / ``!clone:`` tags produce, so identity
        semantics are identical (``${ref:x}`` twice yields ONE shared instance;
        ``${clone:x}`` yields an independent deep copy). The mapping form remains
        the way to override kwargs on a clone, which a scalar cannot express.
        """
        if name not in _MARKER_RESOLVERS:
            return None
        from confluid.fluid import Clone, Reference

        path = (arg or "").strip()
        if not path:
            raise ConfigurationError(f"${{{name}:...}} needs a target path")
        return Reference(path) if name == "ref" else Clone(path)

    def _resolve_placeholder(
        self, name: str, default_val: Optional[str], local_context: Optional[Dict[str, Any]]
    ) -> Tuple[Any, bool]:
        """Resolve one ``${...}`` placeholder to ``(value, found)``.

        A config-key name (dotted / bracketed) is looked up in
        ``local_context`` then ``self.context``; a plain name is an env var.
        On a miss, ``default_val`` (if any) is parsed and returned; otherwise
        ``(None, False)`` signals "leave the literal ``${...}`` in place". A
        looked-up ``None`` is treated as a miss, matching ``_resolve_ref``.

        ``${env:NAME}`` / ``${env:NAME,default}`` (and the ``oc.env`` alias) are
        checked FIRST: they are resolver calls, so the second group is the
        variable name rather than a default. This is the explicit spelling that
        works regardless of what a bare ``${NAME}`` is later taken to mean.
        """
        if name in _ENV_RESOLVERS:
            arg = default_val or ""
            var, _, fallback = arg.partition(",")
            env_val = os.getenv(var.strip())
            if env_val is not None:
                return self._parse_primitive(env_val), True
            return (self._parse_primitive(fallback.strip()), True) if fallback else (None, False)

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
