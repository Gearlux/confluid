"""YAML parsing and composition — the TOP of the module layering.

Owns everything between a file on disk and the marker tree the engine
materializes: :class:`ConfluidLoader` (the ONLY loader carrying confluid's
constructors — the global ``yaml.SafeLoader`` is never touched),
``_reserved_to_marker`` (the ONE site converting the reserved-key spelling
``_target_`` / ``_partial_`` / ``_ref_`` / ``_scope_`` /
``_notscope_`` into Fluid markers, at parse time and nowhere downstream),
:func:`resolve_config_path` (the ONE path-probe site — CWD → ``./config/`` →
XDG tiers), ``include:`` splicing AT the directive's line (document order IS
precedence, so where an include is written is meaningful), ``import:``
side-effect imports.

Layering: ``fluid → state → broadcast → engine → loader`` — this module may
import the engine (one deliberate late import at the bottom); nothing below
imports it. Docs: ``docs/lifecycle.md`` (the nine passes), ``docs/plain-format.md``,
``docs/search-paths.md``.
"""

import importlib
import os
import re
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, Literal, Optional, Set, Tuple, Union, cast, get_args, overload

import yaml
from loggair import get_logger

from confluid.exceptions import CircularIncludeError, ConfigFileNotFoundError, ConfigurationError
from confluid.merger import deep_merge, expand_dotted_keys
from confluid.resolver import _TARGET_CALL_RE, Resolver, _split_inline_pairs, parse_value
from confluid.scopes import normalize_active, parse_scope_arg, resolve_scopes

logger = get_logger("confluid.loader")

# The ``Target(...)`` call grammar shared by ``!class:`` / ``!lazy:`` / the legacy
# colon-free ``!class`` AND the quoted-string marker parser — ONE constant
# (``resolver._TARGET_CALL_RE``, imported above) so no spelling can drift. The name
# group accepts a dotted path AND a ``@axis=value`` tag selector
# (``!class:FourierOp@group=fft/torch``, ``!class:X@framework=$framework``):
# ``@ = / , $ ~ -`` are all legal YAML tag-suffix characters, so the selector rides
# an unquoted tag. ``_parse_scope_suffix`` keeps its own pattern on purpose: a scope
# key is a plain identifier, not a class target.

# Per-context include accumulator (a YAML-side concern — deliberately NOT on
# the engine's _ENGINE_STATE): populated only inside ``load(..., return_paths=True)``.
_INCLUDE_ACCUMULATOR: ContextVar[Optional[List[Path]]] = ContextVar("confluid_include_accumulator", default=None)


# Process-wide application identity for the XDG search path (see
# resolve_config_path). A plain module global — like validation's policy, the
# app name is the same for every load in the process, unlike the per-context
# include accumulator above.
_APP_NAME: Optional[str] = None


def set_app_name(name: Optional[str]) -> None:
    """Set the application name used for XDG config-file lookup.

    With an app name configured, :func:`resolve_config_path` searches
    ``<xdg-base>/<app_name>/`` then ``<xdg-base>/confluid/`` under each XDG
    base directory; without one it searches the bare base directory. A CLI
    framework typically calls this once at startup with its application
    name. Pass ``None`` to reset.
    """
    global _APP_NAME
    _APP_NAME = name


def get_app_name() -> Optional[str]:
    """Return the application name set via :func:`set_app_name`, or ``None``."""
    return _APP_NAME


def _xdg_base_dirs() -> List[Path]:
    """XDG base directories: ``$XDG_CONFIG_HOME`` then each ``$XDG_CONFIG_DIRS`` entry.

    Defaults per the XDG Base Directory spec (``~/.config`` and ``/etc/xdg``);
    an EMPTY env var is treated as unset, also per the spec.
    """
    dirs = [Path(os.getenv("XDG_CONFIG_HOME") or "~/.config").expanduser()]
    for entry in (os.getenv("XDG_CONFIG_DIRS") or "/etc/xdg").split(":"):
        if entry:
            dirs.append(Path(entry).expanduser())
    return dirs


def _search_candidates(rel: Path, base_dir: Optional[Path]) -> List[Path]:
    """Ordered candidate locations for a RELATIVE config path.

    Local tiers first (a local file always wins over an XDG one), XDG last:
    ``base_dir`` (the including file's directory, include-resolution only) →
    CWD → ``CWD/config`` → the XDG base dirs. Under each XDG base dir the
    app-name subdirectory is tried before the ``confluid`` one; with no app
    name configured the bare base dir is searched instead.
    """
    candidates: List[Path] = []
    if base_dir is not None:
        candidates.append(base_dir / rel)
    candidates.append(Path.cwd() / rel)
    candidates.append(Path.cwd() / "config" / rel)
    for base in _xdg_base_dirs():
        if _APP_NAME:
            candidates.append(base / _APP_NAME / rel)
            candidates.append(base / "confluid" / rel)
        else:
            candidates.append(base / rel)
    return candidates


def resolve_config_path(path: Union[str, Path], *, base_dir: Optional[Path] = None) -> Path:
    """Resolve a config-file path through the search tiers (XDG last).

    An absolute path — or a relative one that exists as given — is returned
    unchanged. Otherwise the first EXISTING candidate from
    :func:`_search_candidates` wins. On a total miss the path is returned
    as given, so the caller's normal not-found handling fires.
    """
    rel = Path(path)
    if rel.is_absolute():
        return rel
    for candidate in _search_candidates(rel, base_dir):
        if candidate.exists():
            if candidate != rel and candidate != Path.cwd() / rel:
                logger.debug(f"resolved config path {str(rel)!r} -> {candidate}")
            return candidate
    return rel


def _record_loaded_path(path: Path) -> None:
    """Append ``path`` to the active include-accumulator, if any.

    Populated by ``load(..., return_paths=True)`` for the duration of one
    load so callers can recover the ordered list of every YAML file
    transitively read (entrypoint + recursive ``include:`` targets, the
    ones an activated scope block splices included). The
    accumulator rides a ContextVar so re-entrant loads on different
    threads/tasks do not collide.
    """
    accum = _INCLUDE_ACCUMULATOR.get()
    if accum is not None:
        accum.append(path)


class ConfluidLoader(yaml.SafeLoader):
    """SafeLoader subclass carrying confluid's tag constructors.

    The constructors are registered on THIS class only (once, at module
    import) — never on the global ``yaml.SafeLoader``. Registering on the
    global class would make every ``yaml.safe_load`` call in the process
    parse confluid tags, silently handing Fluid markers to unrelated
    libraries instead of raising on the unknown tag.
    """


# --------------------------------------------------------------------------- #
# The reserved-key format — plain YAML, no tags
#
# ``_target_`` / ``_partial_`` mirror Hydra's vocabulary; ``_ref_`` /
# ``_scope_`` / ``_notscope_`` are confluid's additions for the constructs Hydra
# has no equivalent for. A document written this way is ORDINARY
# YAML — ``yaml.safe_load`` and external tooling (yq, editor schemas) read it,
# which no tagged document can be. Both spellings produce the SAME Fluid markers,
# so every downstream pass (includes, scopes, interpolation, broadcasting, flow)
# is untouched by which one an author used.
# --------------------------------------------------------------------------- #

TARGET_KEY = "_target_"
PARTIAL_KEY = "_partial_"
REF_KEY = "_ref_"
#: REMOVED 2026-08-15 (zero users; architecture records 9/10 superseded). Kept in
#: RESERVED_KEYS on purpose: a mapping carrying it must still reach the marker gate
#: so it can be REFUSED with a location, instead of loading silently as plain data
#: with a literal ``_clone_`` key — the degradation this format exists to end.
_REMOVED_CLONE_KEY = "_clone_"
SCOPE_KEY = "_scope_"
NOTSCOPE_KEY = "_notscope_"

#: Every key that turns a plain mapping into a marker. A mapping carrying NONE of
#: these is an ordinary dict and takes PyYAML's untouched construction path.
RESERVED_KEYS: FrozenSet[str] = frozenset(
    {TARGET_KEY, PARTIAL_KEY, REF_KEY, _REMOVED_CLONE_KEY, SCOPE_KEY, NOTSCOPE_KEY}
)

#: The keys that DECIDE which marker is built. ``_partial_`` is a modifier — it
#: qualifies a discriminator rather than standing on its own.
_DISCRIMINATORS: Tuple[str, ...] = (TARGET_KEY, REF_KEY, SCOPE_KEY, NOTSCOPE_KEY)


def _stamp_loc(obj: Any, loader: yaml.SafeLoader, node: yaml.nodes.Node) -> Any:
    """Attach the YAML source location of ``node`` to ``obj`` for diagnostics.

    Stored as ``(filename_or_None, line, column)`` on ``obj._yaml_loc``; line and
    column are 1-based. Surfaces via :func:`confluid.format_yaml_loc` so an error
    can point at the offending YAML mapping. Shared by the tag constructors and
    the reserved-key path so a marker carries its location either way.
    """
    mark = node.start_mark
    filename = getattr(loader, "name", None)
    obj._yaml_loc = (filename, mark.line + 1, mark.column + 1)
    return obj


def _parse_scope_suffix(suffix: str) -> tuple[str, Optional[str]]:
    """Split a scope activation string into ``(key, value)``.

    The ONE grammar behind every spelling — the ``!scope:`` tag suffix, the
    ``_scope_:`` reserved key, and a CLI ``--scope`` argument:

    * ``debug``                  → ``("debug", None)``   (boolean)
    * ``task=classification``    → ``("task", "classification")``
    * ``task(classification)``   → ``("task", "classification")``
    """
    paren = re.match(r"^([\w_.]+)\((.*)\)$", suffix)
    if paren:
        return paren.group(1), paren.group(2).strip()
    # ``KEY=VALUE`` / bare ``KEY`` — the same grammar as a CLI activation string,
    # so the ONE splitter (``scopes.parse_scope_arg``) serves both.
    return parse_scope_arg(suffix)


def _reserved_to_marker(mapping: Dict[str, Any]) -> Any:
    """Convert a reserved-key mapping into its Fluid marker, or return it unchanged.

    ``mapping`` must already carry at least one entry of :data:`RESERVED_KEYS`
    (the caller checks the YAML node's keys first, so an ordinary mapping never
    reaches here). Every non-reserved key becomes the marker's kwargs.

    Raises :class:`ConfigurationError` on a malformed marker rather than degrading
    silently — the tag syntax this replaces had exactly that failure mode (a single
    space in ``!class:Model(a=1, b=2)`` produced a mangled target with both kwargs
    dropped and no error at all).
    """
    from confluid.fluid import Partial, Reference, ScopeBlock, Target

    if _REMOVED_CLONE_KEY in mapping:
        raise ConfigurationError(
            f"{_REMOVED_CLONE_KEY} was removed (2026-08-15) — it had no users, and independence "
            f"has one spelling: write the marker again. To SHARE one instance use {REF_KEY} / ${{ref:...}}"
        )

    present = [k for k in _DISCRIMINATORS if k in mapping]
    if len(present) > 1:
        raise ConfigurationError(f"Conflicting reserved keys in one mapping: {', '.join(sorted(present))}")
    if not present:
        # Only modifiers, no discriminator — a typo that would otherwise be
        # silently carried into the config as an ordinary key.
        extra = sorted(RESERVED_KEYS & set(mapping))
        raise ConfigurationError(
            f"{', '.join(extra)} needs {TARGET_KEY} in the same mapping — it modifies CONSTRUCTION, "
            f"and {TARGET_KEY} is the only key that constructs"
        )

    key = present[0]
    body = {k: v for k, v in mapping.items() if k not in RESERVED_KEYS}

    # ``_partial_`` is a MODIFIER and only the ``_target_`` branch below reads it.
    # Every other discriminator returns before that read, so the key used to be
    # stripped into oblivion: `{_ref_: proto, _partial_: true}` flowed EAGERLY with
    # no error (BUGS-2026-08-13 P10). Refusing here — one site, before the branches
    # — is what the "malformed marker raises, never degrades" rule requires, and it
    # is why the lone-modifier message above no longer advertises the other four.
    if PARTIAL_KEY in mapping and key != TARGET_KEY:
        where = (
            f"put it on the node {key} points at"
            if key == REF_KEY
            # A scope block is a conditional splice, not a construction site, so
            # there is nothing here for the modifier to defer.
            else f"put it on the marker inside the {key} block"
        )
        raise ConfigurationError(
            f"{PARTIAL_KEY} only modifies {TARGET_KEY}, but this mapping carries {key} — {where}. "
            f"Deferral is a property of the node being CONSTRUCTED "
            f"(e.g. proto: {{{TARGET_KEY}: X, {PARTIAL_KEY}: true}} then opt: {{{REF_KEY}: proto}})"
        )

    if key in (SCOPE_KEY, NOTSCOPE_KEY):
        # A MAPPING of dimension -> required value (``None`` for a boolean
        # dimension), rather than the tag form's ``KEY=VAL`` string. The string
        # packs a grammar inside a scalar, which `yq` and an editor schema see as
        # opaque text; as a mapping it is data, and a multi-dimension condition
        # falls out with no extra spelling.
        spec = mapping[key]
        if not isinstance(spec, dict) or not spec:
            raise ConfigurationError(
                f"{key} must be a non-empty mapping of dimension to value "
                f"(e.g. {{framework: keras}}, or {{debug: }} for a boolean) — got {spec!r}"
            )
        dims: Dict[str, Optional[str]] = {}
        for dim, value in spec.items():
            if not isinstance(dim, str) or not dim.strip():
                raise ConfigurationError(f"{key} dimension names must be non-empty strings (got {dim!r})")
            if isinstance(value, bool):
                # YAML 1.1 reads `yes` / `no` / `on` / `off` as booleans, so an
                # unquoted `{extra: yes}` becomes True and stops matching the
                # activation string a CLI passes (`--scope extra=yes`). Refuse it
                # rather than silently never firing.
                raise ConfigurationError(
                    f"{key} value for {dim.strip()!r} is the YAML boolean {value!r} — quote it "
                    f'({{{dim.strip()}: "{str(value).lower()}"}}), or use {{{dim.strip()}: }} '
                    f"for a boolean DIMENSION with no value"
                )
            dims[dim.strip()] = None if value is None else str(value)
        return ScopeBlock(dims=dims, negate=key == NOTSCOPE_KEY, contents=body)

    path = mapping[key]
    if key == REF_KEY:
        if not isinstance(path, str) or not path.strip():
            raise ConfigurationError(f"{key} must be a non-empty string path (got {path!r})")
        marker = Reference(path.strip())
        marker.kwargs.update(body)
        return marker

    if not isinstance(path, str) or not path.strip():
        raise ConfigurationError(f"{TARGET_KEY} must be a non-empty string (got {path!r})")
    partial = mapping.get(PARTIAL_KEY, False)
    if not isinstance(partial, bool):
        raise ConfigurationError(f"{PARTIAL_KEY} must be true or false (got {partial!r})")
    # ``kwargs`` assigned POST-construction: a config kwarg literally named
    # ``target`` collides with the marker ctor's own first parameter when splatted.
    target = Partial(path.strip()) if partial else Target(path.strip())
    target.kwargs.update(body)
    return target


#: PyYAML's tag for a merge key (``<<:``). The TAG is what identifies one — a
#: QUOTED ``"<<"`` is an ordinary string key and resolves to the str tag, so
#: matching on the tag is also the false-positive guard.
_MERGE_TAG = "tag:yaml.org,2002:merge"


def _node_key_names(node: yaml.nodes.MappingNode, seen: Optional[Dict[int, Any]] = None) -> Set[str]:
    """The key names a mapping node carries, SEEING THROUGH merge keys (``<<:``).

    The reserved-key gate answers "does this mapping opt in?" from key NAMES so
    that an ordinary mapping never has its values constructed (the fast path).
    A merge key's own literal key is ``<<``, so a node inheriting ``_target_``
    from an anchor read as "carries no reserved key" and was handed to PyYAML as
    plain data — while ``construct_mapping(deep=True)`` one branch later resolves
    the merge perfectly well. That is why ONE unrelated literal reserved key on
    the node was enough to make the anchor's ``_target_`` work: the conversion
    was never the problem, only this gate (BUGS-2026-08-13 P2).

    ``seen`` is id-keyed and its VALUE is the node — recording IS pinning, per the
    id-keyed-store mandate. The guard is not theoretical: a node whose merge key
    aliases ITSELF composes fine (``a: &x {<<: *x, k: 1}`` loads as ``{'k': 1}``),
    and the walk would otherwise recurse until the stack ran out.
    """
    if seen is None:
        seen = {}
    if id(node) in seen:
        return set()
    seen[id(node)] = node  # the value IS the pin

    names: Set[str] = set()
    for key, value in node.value:
        if not isinstance(key, yaml.nodes.ScalarNode):
            continue
        if key.tag != _MERGE_TAG:
            names.add(key.value)
            continue
        # ``<<: *a`` merges one mapping; ``<<: [*a, *b]`` merges several. A merge
        # of anything else is PyYAML's error to raise, at construction — skipping
        # it here leaves that behaviour exactly as it was.
        merged = value.value if isinstance(value, yaml.nodes.SequenceNode) else [value]
        for item in merged:
            if isinstance(item, yaml.nodes.MappingNode):
                names |= _node_key_names(item, seen)
    return names


def _node_where(node: yaml.nodes.Node, loader: yaml.SafeLoader) -> str:
    """``file:line:col`` for a YAML node — every parse-time refusal names one."""
    mark = node.start_mark
    return f"{getattr(loader, 'name', None) or '<config>'}:{mark.line + 1}:{mark.column + 1}"


def _refuse_malformed_keys(node: yaml.nodes.MappingNode, loader: yaml.SafeLoader) -> None:
    """Every parse-time key-shape refusal, in ONE call.

    Both callers (``map_constructor`` for untagged mappings, ``_str_keyed_mapping``
    for the four tag constructors) go through here, so a new key-shape rule cannot
    be added to one spelling and forgotten in the other.
    """
    _refuse_duplicate_keys(node, loader)
    _refuse_dotted_reserved_keys(node, loader)


def _refuse_dotted_reserved_keys(node: yaml.nodes.MappingNode, loader: yaml.SafeLoader) -> None:
    """Refuse a reserved key written as a segment of a DOTTED key.

    ``model._target_: Widget`` cannot become a marker: the reserved-key conversion
    happens while the document is PARSED, and ``merger.expand_dotted_keys`` runs
    afterwards, so by the time ``_target_`` is a key of its own mapping there is
    nobody left to convert it. The document kept a literal ``_target_`` as ordinary
    data, and ``flow()`` did not rescue it either — it reached the consumer as
    ``{'_target_': 'Widget'}`` (BUGS-2026-08-13 P16).

    Every other key works dotted, INCLUDING a kwarg merging into an already-nested
    marker (``model: {_target_: Widget}`` plus ``model.size: 3`` builds a
    ``Widget(size=3)``), which is what made the failure so easy to miss: the dotted
    spelling breaks for exactly the one key that decides the node is a marker.

    The check covers a reserved segment in ANY position, because every shape
    degrades the same silent way — a nested dotted key is never expanded at all
    (expansion is top-level only) and stays a literal ``inner._target_`` key, and
    inside a marker's kwargs it becomes a constructor kwarg named ``sub._target_``.
    """
    for key, _ in node.value:
        if not isinstance(key, yaml.nodes.ScalarNode) or key.tag == _MERGE_TAG:
            continue
        name = key.value
        if not isinstance(name, str) or "." not in name:
            continue
        offending = next((seg for seg in name.split(".") if seg in RESERVED_KEYS), None)
        if offending is None:
            continue
        raise ConfigurationError(
            f"{name!r} at {_node_where(key, loader)} puts the reserved key {offending!r} inside a "
            f"DOTTED key. Reserved keys are read when the document is parsed and dotted keys are "
            f"expanded afterwards, so this can never become a marker — write it as a nested block "
            f"instead ({name.split('.')[0]}: with {offending}: under it)"
        )


def _refuse_duplicate_keys(node: yaml.nodes.MappingNode, loader: yaml.SafeLoader) -> None:
    """Refuse a mapping that writes the same key twice.

    The YAML spec restricts a mapping's keys to be unique and lists "mapping keys
    may not be unique" among its loading failure points — but leaves the
    processor's response unspecified, and PyYAML's is to keep the LAST value
    silently. For a config engine that is the worst available answer: two
    ``include:`` directives lost a whole FILE before this loader ever ran, and
    the survivor spliced at the FIRST occurrence's position (a collapsed
    duplicate keeps first-insertion order), inverting the documented "a later
    line overrides an earlier one" rule. An ordinary key loses its first value
    the same way — ``lr: 0.1`` … ``lr: 0.5`` is a differently-trained run
    (BUGS-2026-08-13 P11).

    Identity is ``(tag, value)``, not text: ``1:`` and ``"1":`` are an int key
    and a str key, and PyYAML keeps both (measured: ``{1: 'one', '1': 'two'}``).
    Merge keys are skipped — a ``<<:``-merged key that the node also writes
    literally is ordinary override semantics, which is the whole point of the
    idiom, not a duplicate.
    """
    seen: Dict[Tuple[str, str], int] = {}
    for key, _ in node.value:
        if not isinstance(key, yaml.nodes.ScalarNode) or key.tag == _MERGE_TAG:
            continue
        identity = (key.tag, key.value)
        first_line = seen.get(identity)
        if first_line is not None:
            where = _node_where(key, loader)
            hint = (
                " To pull in several files, write ONE key with a list: include: [a.yaml, b.yaml]."
                if key.value == "include"
                else ""
            )
            raise ConfigurationError(
                f"duplicate key {key.value!r} at {where} (first written at line {first_line}) — YAML "
                f"requires a mapping's keys to be unique. Keeping the last value silently discards "
                f"the first, which is what PyYAML would do.{hint}"
            )
        seen[identity] = key.start_mark.line + 1


def _str_keyed_mapping(loader: yaml.SafeLoader, node: yaml.nodes.MappingNode) -> Dict[str, Any]:
    """Construct a mapping node into a str-keyed dict, refusing duplicate keys.

    The ONE site for the TAG constructors, which have no shared entry point of
    their own — four of them repeated this expression, so a check added to the
    plain path alone would have left the deprecated spelling silently losing a
    duplicated key. A behaviour reachable from only one spelling is a bug in that
    spelling, deprecated or not.

    ``map_constructor`` does NOT come through here: it must refuse BEFORE its
    reserved-key gate, because an ordinary mapping takes the fast path straight
    to PyYAML and never constructs through this function at all.
    """
    _refuse_malformed_keys(node, loader)
    return {str(k): v for k, v in loader.construct_mapping(node, deep=True).items()}


def _register_constructors() -> None:
    """Register the !ref: / !class: / !lazy: / !scope: / !notscope: constructors on ConfluidLoader.

    Invoked exactly once at module import (see the call below the definition).
    """
    from confluid.fluid import Partial, Reference, ScopeBlock, Target

    def _parse_inline_kwargs(args_str: str) -> dict[str, Any]:
        """Parse inline ``key=value`` pairs from a ``Name(...)`` tag suffix.

        The split is the shared grammar (``resolver._split_inline_pairs``);
        this tag-form policy coerces each value to its native Python type via
        ``parse_value`` (``"7"`` → ``7``, ``"0.01"`` → ``0.01``, ``"true"`` →
        ``True``) so the unquoted form matches the quoted-string form's
        coercion instead of silently storing raw strings. A nested ``!ref:`` /
        ``${ENV}`` cannot appear in this position — YAML forbids a second tag
        on one node, so the scanner rejects it — use the quoted-string form or
        a mapping body when you need those.
        """
        return {k: parse_value(v) for k, v in _split_inline_pairs(args_str)}

    _stamp = _stamp_loc  # the ONE stamping helper, shared with the reserved-key path

    def _make_fluid(factory: Any, name: str, kwargs: dict[str, Any]) -> Any:
        """Build a Fluid marker with its kwargs assigned POST-construction.

        ``factory(name, **kwargs)`` collides when a YAML kwarg is literally named
        ``target`` (the marker ctor's own first parameter — e.g. a consumer op
        whose own parameter is named ``target``). The marker stores ``self.kwargs = kwargs`` verbatim,
        so the post-construction update is exactly equivalent and collision-proof.
        """
        fluid = factory(name)
        fluid.kwargs.update(kwargs)
        return fluid

    def ref_constructor(loader: yaml.SafeLoader, tag_suffix: str, node: yaml.nodes.Node) -> Any:
        return _stamp(Reference(tag_suffix), loader, node)

    def class_constructor(loader: yaml.SafeLoader, tag_suffix: str, node: yaml.nodes.Node) -> Any:
        instant = _TARGET_CALL_RE.match(tag_suffix)
        factory = Target
        name = instant.group(1) if instant else tag_suffix
        inline = _parse_inline_kwargs(instant.group(2)) if instant else {}

        if isinstance(node, yaml.nodes.MappingNode):
            mapping: dict[str, Any] = _str_keyed_mapping(loader, node)
            # Merge inline ``(k=v)`` kwargs with the mapping body instead of
            # discarding the inline ones. Block-body keys win on conflict —
            # they sit later in document order, matching the flat-view
            # last-write-wins rule.
            return _stamp(_make_fluid(factory, name, {**inline, **mapping}), loader, node)

        if isinstance(node, yaml.nodes.ScalarNode) and instant:
            return _stamp(_make_fluid(factory, name, inline), loader, node)

        return _stamp(Target(tag_suffix), loader, node)

    def lazy_constructor(loader: yaml.SafeLoader, tag_suffix: str, node: yaml.nodes.Node) -> Any:
        # Mirror class_constructor's grammar so users can write either
        # ``!lazy:Adam`` (bare), ``!lazy:Adam(lr=1e-3)`` (inline kwargs),
        # or ``!lazy:Adam`` with a YAML mapping body for the kwargs. Inline
        # values are coerced and merged with the body exactly as for !class:.
        instant = _TARGET_CALL_RE.match(tag_suffix)
        name = instant.group(1) if instant else tag_suffix
        inline = _parse_inline_kwargs(instant.group(2)) if instant else {}

        if isinstance(node, yaml.nodes.MappingNode):
            mapping: dict[str, Any] = _str_keyed_mapping(loader, node)
            return _stamp(_make_fluid(Partial, name, {**inline, **mapping}), loader, node)

        if isinstance(node, yaml.nodes.ScalarNode) and instant:
            return _stamp(_make_fluid(Partial, name, inline), loader, node)

        return _stamp(Partial(tag_suffix), loader, node)

    def _build_scope(loader: yaml.SafeLoader, tag_suffix: str, node: yaml.nodes.Node, *, negate: bool) -> Any:
        """Construct a ``ScopeBlock`` from any of the three YAML body shapes.

        The body shape decides what a splice MEANS, and ``scopes._resolve_dict`` /
        ``_resolve_list`` have always handled all three:

        * **mapping** — keys spliced at the wrapper's slot (a dict, or a marker's kwargs);
          appended whole when the wrapper is a list item.
        * **sequence** — the surrounding list is EXTENDED with these entries. This is the
          only way to write a conditional list ITEM, and a mapping body cannot express it.
        * **scalar** — one conditional VALUE, substituted at the wrapper's position.

        Until 2026-08-01 only the mapping branch was built and everything else became
        ``{}``: a sequence- or scalar-bodied block was a silent no-op with no diagnostic,
        and the matching branches in ``_resolve_list`` were unreachable from YAML. An
        EMPTY body (``if_x: !scope:debug`` with nothing under it) still yields ``{}`` —
        "declares nothing" is a real state, and it is what keeps such a placeholder inert
        rather than splicing an empty string.
        """
        key, value = _parse_scope_suffix(tag_suffix)
        contents: Any
        if isinstance(node, yaml.nodes.MappingNode):
            contents = _str_keyed_mapping(loader, node)
        elif isinstance(node, yaml.nodes.SequenceNode):
            contents = loader.construct_sequence(node, deep=True)
        elif isinstance(node, yaml.nodes.ScalarNode):
            # Coerced through `parse_value` for the same reason inline `!class:Foo(n=7)`
            # kwargs are — a tagged scalar should reach the config as `7`, not `"7"`.
            raw = loader.construct_scalar(node)
            contents = parse_value(raw) if raw else {}
        else:  # pragma: no cover - PyYAML has no fourth node kind
            contents = {}
        return _stamp(
            ScopeBlock(dims={key: value}, negate=negate, contents=contents),
            loader,
            node,
        )

    def scope_constructor(loader: yaml.SafeLoader, tag_suffix: str, node: yaml.nodes.Node) -> Any:
        return _build_scope(loader, tag_suffix, node, negate=False)

    def notscope_constructor(loader: yaml.SafeLoader, tag_suffix: str, node: yaml.nodes.Node) -> Any:
        return _build_scope(loader, tag_suffix, node, negate=True)

    def ref_compat(loader: yaml.SafeLoader, node: Any) -> Any:
        return _stamp(Reference(loader.construct_scalar(node)), loader, node)

    def class_compat(loader: yaml.SafeLoader, node: Any) -> Any:
        val = loader.construct_scalar(node)
        instant = _TARGET_CALL_RE.match(val)
        if instant:
            # Through _make_fluid like every other constructor — a kwarg
            # literally named ``target`` collides with the marker ctor's own
            # first parameter when splatted.
            return _stamp(
                _make_fluid(Target, instant.group(1), _parse_inline_kwargs(instant.group(2))),
                loader,
                node,
            )
        return _stamp(Target(val), loader, node)

    # ---- the tag spelling is the PREFERRED AUTHORING form (ruling 2026-08-15) ------
    #
    # Until 2026-08-15 every tag constructor announced a FutureWarning naming
    # `confluid-migrate` and a 0.4.0 removal. Architecture record 19 reversed that:
    # tags are what a human writes, the reserved-key form is what the `hydraide`
    # preprocessor EMITS, and both are first-class INPUT. So: no warning, no
    # migration tool, and `!partial:` joins `!lazy:` as the tag for a deferred
    # marker — the same name as the key it emits (`_partial_: true`). `!lazy:`
    # stays as an alias; whether it is ever removed is a later ruling.
    for _tag, _ctor in (
        ("!ref:", ref_constructor),
        ("!class:", class_constructor),
        ("!partial:", lazy_constructor),
        ("!lazy:", lazy_constructor),
        ("!scope:", scope_constructor),
        ("!notscope:", notscope_constructor),
    ):
        ConfluidLoader.add_multi_constructor(_tag, _ctor)

    ConfluidLoader.add_constructor("!ref", ref_compat)
    ConfluidLoader.add_constructor("!class", class_compat)

    # ---- the reserved-key format: plain mappings that carry ``_target_`` & co ----
    #
    # Registered on the DEFAULT MAPPING tag, so it sees every untagged mapping in
    # the document. The reserved-key test reads the NODE's key names — no values
    # are constructed to answer it — and a mapping carrying none of them delegates
    # straight to PyYAML's own constructor. That keeps the ordinary path exactly as
    # fast (and as alias/recursion-correct) as it was, and confines the new
    # behaviour to mappings that actually opted in. ``_node_key_names`` sees
    # through merge keys, so an anchored marker (``<<: *base``) opts in like the
    # literal spelling — still without constructing a value.
    default_map_constructor = ConfluidLoader.yaml_constructors[yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG]

    def map_constructor(loader: yaml.SafeLoader, node: yaml.nodes.MappingNode) -> Any:
        # Before anything else, and for EVERY mapping — ordinary or marker. A
        # malformed key shape is a silent data loss whatever the mapping turns
        # out to be, so this runs before the reserved-key gate's fast path.
        _refuse_malformed_keys(node, loader)
        node_keys = _node_key_names(node)
        if not (node_keys & RESERVED_KEYS):
            yield from default_map_constructor(loader, node)
            return
        # ``deep=True`` is required: the marker is built NOW, so its kwargs must be
        # real values rather than PyYAML's not-yet-filled placeholders.
        mapping = {str(k): v for k, v in loader.construct_mapping(node, deep=True).items()}
        try:
            marker = _reserved_to_marker(mapping)
        except ConfigurationError as exc:
            mark = node.start_mark
            where = f"{getattr(loader, 'name', None) or '<config>'}:{mark.line + 1}:{mark.column + 1}"
            raise ConfigurationError(f"{exc} (at {where})") from exc
        yield _stamp_loc(marker, loader, node)

    ConfluidLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, map_constructor)

    # ---- a SEQUENCE whose first item is a scope marker IS a scope block --------
    #
    #     ops:
    #       - always_first
    #       - - _scope_: {extra: yes}     # the marker
    #         - extra_a                   # ...and the body it guards
    #         - extra_b
    #       - always_last
    #
    # A mapping body splices its KEYS at the wrapper's slot; a list body splices
    # its ITEMS into the surrounding list. Both start with `_scope_:`, so the two
    # differ only in their container. This is what a mapping cannot express — a
    # YAML node is a mapping or a sequence, never both — and it is the only way to
    # write a conditional list ITEM. The alternative considered was a `_content_`
    # key holding the list; it needed a reserved key, a second body-shape rule and
    # a scalar special case, all of which this shape removes.
    default_seq_constructor = ConfluidLoader.yaml_constructors[yaml.resolver.BaseResolver.DEFAULT_SEQUENCE_TAG]

    def seq_constructor(loader: yaml.SafeLoader, node: yaml.nodes.SequenceNode) -> Any:
        first = node.value[0] if node.value else None
        marks_a_scope = isinstance(first, yaml.nodes.MappingNode) and any(
            isinstance(k, yaml.nodes.ScalarNode) and k.value in (SCOPE_KEY, NOTSCOPE_KEY) for k, _ in first.value
        )
        if not marks_a_scope:
            yield from default_seq_constructor(loader, node)
            return
        items = loader.construct_sequence(node, deep=True)
        block = items[0]
        if not isinstance(block, ScopeBlock):  # pragma: no cover - the node test already decided
            yield items
            return
        block.contents = items[1:]
        yield _stamp_loc(block, loader, node)

    ConfluidLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_SEQUENCE_TAG, seq_constructor)


# Register once at import — constructors live on ConfluidLoader for the
# lifetime of the process; the global yaml.SafeLoader is never touched.
_register_constructors()


def _load_config_file(path: Union[str, Path], _included: Optional[Set[Path]] = None) -> Dict[str, Any]:
    """Read ONE YAML file: parse (markers), ``import:``, recursive ``include:`` — passes 1–3.

    The file-reading half of ``load(path, until="raw")``, and what every
    ``include:`` directive calls for its target. A relative ``path`` is
    resolved through the config search tiers (CWD → ``CWD/config`` → XDG base
    dirs — see :func:`resolve_config_path`) BEFORE canonicalization, so
    circular-include detection and the include accumulator operate on the
    real file.
    """
    requested = Path(path)
    path = resolve_config_path(path).resolve()
    if _included is None:
        _included = set()
    if path in _included:
        raise CircularIncludeError(f"Circular include: {path}")
    _included.add(path)
    _record_loaded_path(path)

    if not path.exists():
        if requested.is_absolute():
            raise ConfigFileNotFoundError(f"Not found: {path}")
        searched = ", ".join(str(c) for c in _search_candidates(requested, None))
        raise ConfigFileNotFoundError(f"Not found: {requested} (searched: {searched})")

    with open(path, "r") as f:
        data = yaml.load(f, Loader=ConfluidLoader) or {}

    return cast(Dict[str, Any], _import_and_include(data, path, _included))


def _import_and_include(data: Any, base_path: Path, _included: Set[Path]) -> Any:
    """Passes 2–3 on a PARSED root: ``import:`` then ``include:`` splicing.

    Root-level ``!class:`` documents parse to a Fluid. Imports/includes are
    dict-only constructs, so a Fluid root skips the import step and only has
    its kwargs walked for nested includes — the same for a file, a text and
    an in-memory dict, which is what makes ``until="raw"`` one stage
    whatever the input shape.
    """
    if isinstance(data, dict):
        data = _process_imports(data)
    return _process_includes_recursive(data, base_path, _included)


def _process_imports(data: Dict[str, Any]) -> Dict[str, Any]:
    if "import" in data:
        imports = data.pop("import")
        if imports:
            if isinstance(imports, str):
                imports = [imports]
            for m in imports:
                try:
                    importlib.import_module(m)
                except ImportError as exc:
                    # Warn instead of raising: an ``import:`` module may be an
                    # optional dependency of a shared/included config. But a
                    # TYPO'd module previously failed silently here and only
                    # surfaced much later as "Cannot resolve class: X" — the
                    # warning names the real cause at the real moment.
                    logger.warning(f"import: failed to import {m!r}: {exc}")
    return data


def _process_includes_recursive(
    data: Any, current_path: Path, _included: Set[Path], spliced: Optional[List[Path]] = None
) -> Any:
    from confluid.fluid import Fluid, ScopeBlock

    if isinstance(data, list):
        return [_process_includes_recursive(item, current_path, _included, spliced) for item in data]

    # Traverse into Class/Fluid kwargs. The kwargs mapping goes through the DICT
    # branch below rather than being comprehended over value-wise, so a marker's
    # OWN ``include:`` key reaches the splice. Walking only the values left it as
    # a constructor kwarg literally named ``include`` (P15). A marker is not
    # conditional — it is always part of the document — so its include is spliced
    # in this pass, unlike a scope block's (see below).
    if isinstance(data, Fluid):
        data.kwargs = cast(Dict[str, Any], _process_includes_recursive(data.kwargs, current_path, _included, spliced))
        return data

    # Scope blocks: walk their contents so nested includes still process. The body may
    # be a mapping, a sequence or a scalar (see `_build_scope`), so only the mapping
    # shape is walked key-wise — the others delegate to the branches above, which is
    # also what keeps a block's OWN `include:` key unprocessed, as it always was.
    if isinstance(data, ScopeBlock):
        if isinstance(data.contents, dict):
            data.contents = {
                k: _process_includes_recursive(v, current_path, _included, spliced) for k, v in data.contents.items()
            }
        else:
            data.contents = _process_includes_recursive(data.contents, current_path, _included, spliced)
        return data

    if not isinstance(data, dict):
        return data

    # Keys are preserved AS PARSED — never ``str()``-ed. A YAML mapping may legitimately be
    # keyed by int (a class-id -> label table) or float, and stringifying here rewrote every
    # such table in EVERY document, silently: `class_names: {1: DJI MINI3}` reached its
    # consumer as `{'1': ...}`, so a lookup by the int id missed and the label came back
    # empty. The raw parse had the key right the whole time; this walk broke it, and it had
    # done so since 2026-03-13. The dotted-key expansion downstream tolerates non-str keys.
    processed_dict: Dict[Any, Any] = {
        k: _process_includes_recursive(v, current_path, _included, spliced) for k, v in data.items()
    }

    if "include" in processed_dict:
        processed_dict = _splice_includes(processed_dict, current_path, _included, spliced)

    return processed_dict


#: Backstop for :func:`_settle_scopes_and_includes`. Two files that each include
#: the other from INSIDE a scope block expose one another's directive on every
#: pass, so the alternation has no natural fixpoint — the per-splice
#: ``_included`` set cannot see across passes. Ten is far above any real nesting
#: depth; the point is to fail with a sentence instead of hanging.
_MAX_SETTLE_PASSES = 10


def _settle_scopes_and_includes(data: Any, base_path: Path, active: Dict[str, Optional[str]]) -> Any:
    """Alternate scope resolution and include splicing until neither changes anything.

    Includes are processed BEFORE scopes (see :func:`load`), which is what keeps a
    conditional overlay optional: an unactivated block is dropped without its
    ``include:`` ever being opened, so a framework-specific file need not exist in
    a checkout that never activates that framework. The cost of that ordering is
    that a block's OWN include is still unspliced when the block is activated —
    its contents land in the parent as an ordinary ``include:`` key with nobody
    left to process it, which is how it used to leak into the config as literal
    data (P15).

    Resolving again after splicing is what closes the loop, and it has to REPEAT:
    an activated block can splice a file that itself carries a scope block whose
    activation exposes a further include.
    """
    for _ in range(_MAX_SETTLE_PASSES):
        data = resolve_scopes(data, active)
        spliced: List[Path] = []
        data = _process_includes_recursive(data, base_path, set(), spliced)
        if not spliced:
            return data
    raise ConfigurationError(
        f"include: directives inside scope blocks did not settle after {_MAX_SETTLE_PASSES} passes "
        f"(starting from {base_path}) — two files including each other from inside a scope block "
        f"expose one another's directive on every pass"
    )


def _splice_includes(
    block: Dict[str, Any], current_path: Path, _included: Set[Path], spliced: Optional[List[Path]] = None
) -> Dict[str, Any]:
    """Splice each included document AT THE POSITION its ``include:`` key was written.

    An ``include:`` behaves as if the included document were **pasted into the
    source document at that line** — which is the only reading consistent with
    confluid's ONE precedence rule (document order, last spec wins), and the same
    rule ``!scope:`` blocks already follow: a construct that contributes keys
    splices them at its own slot.

    So the include's position is MEANINGFUL, and both directions are useful::

        include: base.yaml      #  base first  -> everything below overrides it
        lr: 0.3

        lr: 0.3                 #  base last   -> base overrides what is above it
        include: base.yaml

    Until 2026-08-11 the directive was popped and the WHOLE including file was
    merged over the result, so where you wrote it made no difference at all: the
    including file always won, and a config could not express "these are fallbacks,
    let the shared file win" without splitting into another file. Worse, the
    inverse also failed — an override written after the include inherited the
    INCLUDED file's position (``deep_merge`` assigned in place), so it lost to an
    addressed block inside the include while the same document written flat gave
    the opposite answer.

    Implementation note: the document is cut into segments at the include's slot
    and folded left with :func:`confluid.merger.deep_merge`, so a key appearing in
    several segments still deep-merges (nested blocks combine) and lands at its
    LAST position — the ordering rule, applied to composition. The common
    ``include:``-on-line-1 spelling folds to exactly what the old code produced.
    """
    includes = block["include"]
    if isinstance(includes, str):
        includes = [includes]
    if not isinstance(includes, list):
        # Used to consume the key and splice NOTHING, so `include: {path: a.yaml}`
        # lost a whole file in silence (P15).
        raise ConfigurationError(f"include: takes a path or a list of paths, got {includes!r} (in {current_path})")

    included: List[Dict[str, Any]] = []
    for inc_path in includes:
        if not isinstance(inc_path, str):
            # Used to be skipped, so the loss was invisible among its siblings (P15).
            raise ConfigurationError(
                f"include: entries must be paths, got {inc_path!r} in {includes!r} (in {current_path})"
            )
        target_path = resolve_config_path(inc_path, base_dir=current_path.parent)
        included.append(_load_config_file(target_path, _included=set(_included)))
        if spliced is not None:
            spliced.append(target_path)

    segments: List[Dict[str, Any]] = []
    current: Dict[str, Any] = {}
    for key, value in block.items():
        if key == "include":
            if current:
                segments.append(current)
                current = {}
            segments.extend(included)  # pasted here, in the order they were listed
        else:
            current[key] = value
    if current:
        segments.append(current)

    merged: Dict[str, Any] = {}
    for segment in segments:
        merged = deep_merge(merged, segment)
    return merged


#: Where ``load()`` stops. Named after the STATE it hands back, not the pass that
#: produced it (``docs/lifecycle.md`` → "Where you can stop"):
#: ``"raw"`` — passes 1–3 (parsed, imported, includes spliced; scope blocks still
#: in the document, which is what scope DISCOVERY needs to see); ``"document"`` —
#: 1–6 (scopes applied, interpolated, dotted keys expanded: the document a CLI
#: merges its overrides into, BEFORE pass 7 inlines ``!ref:`` values); ``"settled"``
#: — 1–7 (every marker carries its final kwargs, nothing built: what a graph
#: editor imports and what ``hydraide`` emits); ``"objects"`` — 1–9 (live objects,
#: the default). ``_STAGES`` is derived from the Literal, never restated.
Stage = Literal["raw", "document", "settled", "objects"]
_STAGES: Tuple[str, ...] = get_args(Stage)


@overload
def load(
    data: Any,
    *,
    until: Stage = ...,
    context: Optional[Dict[str, Any]] = ...,
    scopes: Optional[List[str]] = ...,
    solidify: bool = ...,
    return_paths: Literal[False] = ...,
) -> Any: ...


@overload
def load(
    data: Any,
    *,
    until: Stage = ...,
    context: Optional[Dict[str, Any]] = ...,
    scopes: Optional[List[str]] = ...,
    solidify: bool = ...,
    return_paths: Literal[True],
) -> Tuple[Any, List[Path]]: ...


def load(
    data: Any,
    *,
    until: Stage = "objects",
    context: Optional[Dict[str, Any]] = None,
    scopes: Optional[List[str]] = None,
    solidify: bool = True,
    return_paths: bool = False,
) -> Any:
    """Load a config — from a path, YAML text or already-parsed data — up to a stage.

    ``load`` is the ONE door onto the pipeline (``docs/lifecycle.md``). It accepts
    every input shape (a path, YAML text, a parsed dict / list / marker) and runs the
    passes the input still needs; passes already applied to the data are idempotent,
    so ``load(load(x, until="document"))`` is ``load(x)``.

    ``until`` names the state handed back — see :data:`Stage`. ``scopes`` is a
    list of activation strings forwarded from the CLI layer, each a bare boolean
    name (``"debug"``) or a ``"key=value"`` pair; scope blocks are resolved
    against it in pass 4 (:mod:`confluid.scopes`). ``context`` is the document
    whose keys broadcast into ``data`` when ``data`` is a fragment of it (a
    marker built by a DI framework: ``load(node, context=document)``); it
    defaults to ``data`` itself. ``solidify=False`` builds objects but
    suppresses the post-flow ``solidify()`` finalize.

    ``return_paths=True`` returns ``(result, paths)``: ``paths`` is every file
    read for this call, in read order, deduplicated — the entry file and every
    ``include:`` it pulled in, including one spliced by an activated scope
    block. It is empty when nothing was read from disk.
    """
    if until not in _STAGES:
        raise ConfigurationError(f"load(until={until!r}): must be one of {', '.join(repr(s) for s in _STAGES)}")
    if not return_paths:
        return _load(data, until=until, context=context, scopes=scopes, solidify=solidify)
    accum: List[Path] = []
    token = _INCLUDE_ACCUMULATOR.set(accum)
    try:
        result = _load(data, until=until, context=context, scopes=scopes, solidify=solidify)
    finally:
        _INCLUDE_ACCUMULATOR.reset(token)
    seen: Set[Path] = set()
    ordered: List[Path] = []
    for p in accum:
        if p not in seen:
            seen.add(p)
            ordered.append(p)
    return result, ordered


_CONFIG_SUFFIXES = (".yaml", ".yml")


def _names_a_file(data: Union[str, Path]) -> bool:
    """Is this str/Path a config-file NAME (read it) rather than YAML text (parse it)?

    A ``Path`` always is — there is no other reading of the type. A ``str`` is one
    when it is a single short line with no ``:`` (a mapping would have one) that
    either EXISTS under the search tiers or carries a config-file suffix. The
    suffix rule is what makes a MISSING ``"experiment.yaml"`` raise
    ``ConfigFileNotFoundError`` instead of parsing to the scalar string
    ``"experiment.yaml"`` and loading silently as nothing.
    """
    if isinstance(data, Path):
        return True
    if "\n" in data or ":" in data or len(data) >= 255:
        return False
    return data.endswith(_CONFIG_SUFFIXES) or resolve_config_path(data).exists()


def _load(
    data: Any,
    *,
    until: Stage,
    context: Optional[Dict[str, Any]],
    scopes: Optional[List[str]],
    solidify: bool,
) -> Any:
    # ---- passes 1–3: parse, import, include --------------------------------------
    # The base path for a RELATIVE include, and for the post-scope settle below.
    base_path = Path.cwd() / "string.yaml"
    if isinstance(data, (str, Path)):
        if _names_a_file(data):
            base_path = resolve_config_path(data)
            data = _load_config_file(data)  # a missing file raises ConfigFileNotFoundError
        else:
            data = yaml.load(str(data), Loader=ConfluidLoader) or {}
            data = _import_and_include(data, base_path, set())
    else:
        data = _import_and_include(data, base_path, set())
    if until == "raw":
        return data

    # ---- pass 4: scopes (alternating with the includes they expose) --------------
    # Aliases live at the top level of the loaded dict; pull them out before
    # normalizing the activation map. Scope resolution and include splicing then
    # alternate until they settle: an activated block's own `include:` is still
    # unspliced at this point, because processing it earlier would open a file
    # the block may be about to discard.
    if isinstance(data, dict):
        aliases = data.get("scope_aliases") if isinstance(data.get("scope_aliases"), dict) else None
        data = _settle_scopes_and_includes(data, base_path, normalize_active(scopes or [], aliases))
    elif scopes:
        # Non-dict roots (e.g. a document whose root is a marker) carry no
        # metadata, but a ScopeBlock could still sit at the top level.
        data = _settle_scopes_and_includes(data, base_path, normalize_active(scopes, None))

    # ---- passes 5–6: interpolate, expand -----------------------------------------
    # Run ONCE, here, for ``data`` AND for an explicit ``context`` (a fragment's
    # document, resolved against itself) — the engine takes both as PREPARED and
    # runs neither pass again. Marker KWARGS interpolate in place, text-only
    # (``Resolver._interpolate_fluid_kwargs``); a miss keeps the literal.
    if isinstance(data, dict):
        data = cast(Dict[str, Any], _process_imports(data))  # an `import:` a scope block spliced in
    interp_context = context if context is not None else (data if isinstance(data, dict) else None)
    resolver = Resolver(context=interp_context or {})
    data = resolver.resolve(data)
    if isinstance(data, dict):
        data = expand_dotted_keys(data)
    if context is not None:
        context = expand_dotted_keys(resolver.resolve(context))
    if until == "document":
        return data

    # ---- passes 7–9: settle, build, solidify — the engine ------------------------
    ctx = context if context is not None else (data if isinstance(data, dict) else None)
    if until == "settled":
        return settle(data, context=ctx)
    return materialize(data, context=ctx, solidify=solidify)


# The engine's two entries are REAL dependencies of ``load()`` above (passes 7–9
# and pass 7 alone). Imported after the defs to keep the loader's own parse
# machinery readable first; the layering is one-directional (loader → engine —
# the engine imports nothing from here).
from confluid.engine import materialize, settle  # noqa: E402
