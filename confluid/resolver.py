"""Interpolation and the ONE path grammar — ``${...}``, references, value coercion.

Two things live here, and both exist exactly once:

* **The path grammar.** Every dotted/bracketed path in the system —
  reference targets, ``${key.path}`` interpolation, ``configure()``'s dotted
  candidates — is tokenized by ``_parse_path_segments`` and walked by
  ``_walk_path_segments``. The walker is STRUCTURAL — dict keys / list
  indices only, which is what keeps ``${train.split}`` from ever grabbing
  ``str.split``. It used to carry a second, *object* policy (``getattr`` once
  the walk left structured data — the ``!ref:split.train`` attribute reference);
  that was removed 2026-08 (architecture record 19, phase 2): the FIRST segment
  of a reference decides — a document key walks structure only, anything else is
  an import path (:func:`resolve_reference_path`). Extend the shared walker;
  never add another grammar, and never re-add a policy.

* **Interpolation** (:class:`Resolver`). ``${...}`` dispatches on the NAME
  SHAPE — a plain identifier is an environment variable, a dotted/bracketed
  name is a config key; ``${env:...}`` / ``${ref:...}`` are
  resolver calls. A single pass whose substitution BURNS IN, per
  ``docs/interpolation.md``. :func:`parse_value` is the shared scalar-coercion
  policy (deliberately plain ``yaml.safe_load`` — never :class:`ConfluidLoader`).

The quoted-string marker spelling (``"!class:..."``) is parsed here too — the
third input grammar, kept only until 0.4.0; everything it cannot honour raises
via ``_refuse_marker_string`` instead of degrading silently.
"""

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
# plain-YAML counterpart of the ``!ref:`` tag:
#
#   ${env:DATA_ROOT}    ${env:PORT,8080}    ${oc.env:HOME}   -> environment
#   ${ref:proto}                                             -> a marker
#
# Checked BEFORE the config-path test, because ``oc.env`` contains a dot and would
# otherwise route to config-key lookup.
_ENV_RESOLVERS: FrozenSet[str] = frozenset({"env", "oc.env"})
#: ``clone`` stays in the set ONLY so ``${clone:x}`` still routes to the marker resolver,
#: where it is REFUSED with a message — otherwise it would fall through to ordinary
#: placeholder resolution and survive as a literal string (removed 2026-08-15).
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
_STRING_MARKERS_ALL: Tuple[str, ...] = ("!class:", "!lazy:", "!ref:", "!notscope:", "!scope:")


def _plain_yaml_for(value: str) -> str:
    """The reserved-key spelling of a quoted marker string, for the error message.

    Best effort by design: the point is to hand the author the line to write, not
    to be a second parser. An unparseable suffix falls back to naming the keys.
    """
    from confluid.loader import PARTIAL_KEY, REF_KEY, SCOPE_KEY, TARGET_KEY

    prefix = next((m for m in _STRING_MARKERS_ALL if value.startswith(m)), "")
    body = value[len(prefix) :].strip()
    if prefix == "!ref:":
        return f"{{{REF_KEY}: {body}}}" if body else f"{{{REF_KEY}: <path>}}"
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
    classes of position — ``!lazy:`` / ``!scope:`` anywhere, and
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


def _walk_path_segments(
    segments: List[_PathSegment],
    context: Any,
    lookup_fn: Callable[[str, Dict[str, Any]], Any],
) -> Any:
    """Walk pre-tokenized segments through nested dicts and lists — STRUCTURE only.

    For ``"idxref"`` segments, ``lookup_fn(name, context)`` is called with
    the *original* context (not the current cursor) so the inner name
    resolves in the same scope as the outer reference. The resolved value
    decides the step semantics: ``int`` → list index, ``str``/``int``
    → dict key.

    A ``key`` segment steps into a dict; an ``idx`` segment into a list; anything
    else — a marker, a live object, a scalar — ends the walk. There is ONE
    policy: a ``${train.split}`` can never grab a ``str.split`` method, and a
    ``!ref:split.train`` never reads an attribute (record 19, phase 2 — see
    :func:`refuse_attribute_reference` for the located refusal).

    Returns ``None`` when the walk can't proceed (key missing, index
    out of range, type mismatch), preserving the caller's
    "missing → None" contract.
    """
    current: Any = context
    for kind, val in segments:
        if kind == "key":
            if isinstance(current, dict) and val in current:
                current = current[val]
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


_CALL_SUFFIX_RE = re.compile(r"^(?P<base>.+)\.(?P<name>[\w-]+)\(\)$")
"""``obj.method()`` — the removed method-call reference; matched only to REFUSE it with its name."""


def _import_base(obj_path: str) -> Any:
    """Resolve an out-of-context base path: importable module first, else registry/class path."""
    import importlib

    try:
        return importlib.import_module(obj_path)
    except ImportError:
        from confluid.registry import resolve_class

        return resolve_class(obj_path)


def _first_segment(target: str) -> str:
    """The first path segment of a reference target (``split`` for ``split.train`` / ``packs[0].x``)."""
    m = _PATH_TOKEN_RE.match(target)
    return m.group(1) if m and m.group(1) is not None else target


def refuse_attribute_reference(target: str, context: Optional[Dict[str, Any]], where: str = "") -> None:
    """Raise the located refusal for a reference that would read an ATTRIBUTE or call a METHOD.

    The FIRST segment decides what a dotted reference is (record 19, phase 2): a document key
    walks STRUCTURE only — dict keys and list indices — while anything else is an import path.
    So a reference whose first segment is a document key and whose structural walk misses is
    asking for the object policy that no longer exists — reading ``.train`` off the object
    built at ``split``, or calling ``.build()`` on it — and is refused HERE, on both the
    ``load()`` and the ``resolve()`` path, which is how ``hydraide`` reports it (exit 2).

    ``where`` is the reference node's ``file:line:col`` suffix (``format_yaml_loc``-shaped,
    empty for the ``${ref:...}`` string spelling — a scalar carries no location).
    """
    ctx = context or {}
    call = _CALL_SUFFIX_RE.match(target)
    if call is not None:
        raise ConfigurationError(
            f"!ref:{target}{where} calls the METHOD `{call.group('name')}` of the object built at "
            f"`{call.group('base')}` — method-call references were removed (2026-08, architecture record 19). "
            "Compute the value on the consumer side, or expose it as a constructor parameter of the class "
            f"that needs it and reference the whole object (`!ref:{_first_segment(target)}`)."
        )
    first = _first_segment(target)
    if first not in ctx or "." not in target and "[" not in target:
        return  # not a document key (an import path), or a whole-object ref — not this refusal's case
    segments = _parse_path_segments(target)
    if segments is not None and _walk_path_segments(segments, ctx, Resolver(context=ctx)._lookup_path) is not None:
        return  # a structural walk that succeeds is legitimate
    attr = target.rsplit(".", 1)[-1] if "." in target else target
    raise ConfigurationError(
        f"!ref:{target}{where} reads the ATTRIBUTE `{attr}` of the object built at `{first}` — attribute "
        "references were removed (2026-08, architecture record 19). Read the attribute on the consumer side: "
        f"give `{first}`'s class a selector parameter and reference the whole object (`!ref:{first}`), or "
        "write the marker again with the selector set."
    )


def resolve_reference_path(target: str, context: Optional[Dict[str, Any]]) -> Any:
    """Resolve a dotted / bracketed ``!ref:`` path — STRUCTURE first, then IMPORT.

    The single rich resolver behind ``Reference`` resolution (used by ``flow()`` and
    ``_flow_recursive`` after their exact-key probe). The FIRST segment decides:

    * a DOCUMENT KEY — ``a.b.c`` / ``packs[0].name`` / ``items[idx]`` walk dict keys, list
      indices and bracketed name-refs (:func:`_walk_path_segments`). A walk that leaves
      structure — an attribute of a built object, a method call — is REFUSED by
      :func:`refuse_attribute_reference` (record 19, phase 2), never resolved.
    * anything else — ``package.module.attr`` is imported (``importlib``) or resolved via the
      class registry, e.g. ``${ref:mypkg.detection.detection_collate_fn}`` — the spelling
      ``dump()`` emits for a function-valued param.

    A literal context key containing dots (``"a.b"``) still wins over the segment walk,
    mirroring ``_lookup_path``'s literal-key-first rule. A PURELY structural resolution returns
    ``None`` from this resolver: the deferred-Reference machinery deliberately keeps it late-bound
    so post-load overrides still flow through at final materialize time. Returns ``None`` when
    unresolvable — the caller decides whether that leaves the ``Reference`` deferred or raises.
    """
    ctx = context or {}
    refuse_attribute_reference(target, ctx)
    if _first_segment(target) in ctx or target in ctx:
        return None  # structural — the late-bound machinery's to resolve
    prefix, _, last = target.rpartition(".")
    if prefix:
        base = _import_base(prefix)
        if base is not None:
            return getattr(base, last, None)
    return None


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
                # A ``${ref:...}`` interpolates to a MARKER, which
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

        # 2. Handle Fluid citizens. A ``Reference`` to another MARKER is substituted here
        #    so identity is visible before pass 7 (``load(flow=False)``: ``alias is thing``);
        #    a reference to a plain value is left for pass 7 (``engine._settle_reference``),
        #    which inlines it. The scope probe skips a hit that IS this reference (`exclude`):
        #    ``r: {x: !ref:x}`` with a root ``x`` used to find its own marker and recurse
        #    forever (F6).
        if isinstance(value, Reference):
            res = self._resolve_ref(value.target, local_context, exclude=value)
            if isinstance(res, Fluid) and res is not value:
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

    def _resolve_ref(
        self, ref_path: str, local_context: Optional[Dict[str, Any]] = None, *, exclude: Any = None
    ) -> Any:
        """
        Resolve a dotted path against local and global contexts.

        ``exclude`` is the ``Reference`` being resolved: a scope whose key holds that very
        marker is not an answer (it is the question), so the probe falls through to the
        next scope instead of handing the reference back to itself.
        """
        # 1. Try Local Context First
        if local_context:
            val = self._lookup_path(ref_path, local_context)
            if val is not None and val is not exclude and (not isinstance(val, str) or not val.startswith("!ref:")):
                return val

        # 2. Try Global Context
        val = self._lookup_path(ref_path, self.context)
        if val is not None and val is not exclude:
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
        """Build the marker for a ``${ref:path}`` placeholder.

        Returns ``None`` when ``name`` is not a marker resolver, so the caller
        falls through to ordinary placeholder resolution.

        This is the scalar shorthand for the ``_ref_`` reserved key — the same
        marker the ``!ref:`` tag produces, so identity semantics are identical
        (``${ref:x}`` twice yields ONE shared instance).

        ``${clone:x}`` is REFUSED here (removed 2026-08-15, zero users): it must
        not fall through and survive as a literal string in the config.
        """
        if name not in _MARKER_RESOLVERS:
            return None
        if name == "clone":
            raise ConfigurationError(
                "${clone:...} was removed (2026-08-15) — it had no users, and independence has one "
                "spelling: write the marker again. To SHARE one instance use ${ref:...}"
            )
        from confluid.fluid import Reference

        path = (arg or "").strip()
        if not path:
            raise ConfigurationError(f"${{{name}:...}} needs a target path")
        return Reference(path)

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
