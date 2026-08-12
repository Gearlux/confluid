import importlib
import os
import re
import warnings
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, Optional, Set, Tuple, Union, cast

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
# the engine's _ENGINE_STATE): populated only inside load_config_with_paths.
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

    Populated by :func:`load_config_with_paths` for the duration of one
    load so callers can recover the ordered list of every YAML file
    transitively read (entrypoint + recursive ``include:`` targets). The
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
# ``_target_`` / ``_partial_`` mirror Hydra's vocabulary; ``_ref_`` / ``_clone_`` /
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
CLONE_KEY = "_clone_"
SCOPE_KEY = "_scope_"
NOTSCOPE_KEY = "_notscope_"

#: Every key that turns a plain mapping into a marker. A mapping carrying NONE of
#: these is an ordinary dict and takes PyYAML's untouched construction path.
RESERVED_KEYS: FrozenSet[str] = frozenset({TARGET_KEY, PARTIAL_KEY, REF_KEY, CLONE_KEY, SCOPE_KEY, NOTSCOPE_KEY})

#: The keys that DECIDE which marker is built. ``_partial_`` is a modifier — it
#: qualifies a discriminator rather than standing on its own.
_DISCRIMINATORS: Tuple[str, ...] = (TARGET_KEY, REF_KEY, CLONE_KEY, SCOPE_KEY, NOTSCOPE_KEY)


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
    from confluid.fluid import Clone, Partial, Reference, ScopeBlock, Target

    present = [k for k in _DISCRIMINATORS if k in mapping]
    if len(present) > 1:
        raise ConfigurationError(f"Conflicting reserved keys in one mapping: {', '.join(sorted(present))}")
    if not present:
        # Only modifiers, no discriminator — a typo that would otherwise be
        # silently carried into the config as an ordinary key.
        extra = sorted(RESERVED_KEYS & set(mapping))
        raise ConfigurationError(
            f"{', '.join(extra)} needs a discriminator key "
            f"({TARGET_KEY} / {REF_KEY} / {CLONE_KEY} / {SCOPE_KEY} / {NOTSCOPE_KEY}) in the same mapping"
        )

    key = present[0]
    body = {k: v for k, v in mapping.items() if k not in RESERVED_KEYS}

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
    if key in (REF_KEY, CLONE_KEY):
        if not isinstance(path, str) or not path.strip():
            raise ConfigurationError(f"{key} must be a non-empty string path (got {path!r})")
        marker = Reference(path.strip()) if key == REF_KEY else Clone(path.strip())
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


#: Documents already told their tag syntax is deprecated (once per file, not per tag).
#: Module-level so the notice survives across `load()` calls in one process.
_TAG_SPELLING_WARNED: set[str] = set()


def _register_constructors() -> None:
    """Register the !ref: / !class: / !clone: / !lazy: / !scope: / !notscope: constructors on ConfluidLoader.

    Invoked exactly once at module import (see the call below the definition).
    """
    from confluid.fluid import Clone, Partial, Reference, ScopeBlock, Target

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
        ``target`` (the marker ctor's own first parameter — e.g. recordstream
        ``ConfigureOp.target``). The marker stores ``self.kwargs = kwargs`` verbatim,
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
            mapping: dict[str, Any] = {str(k): v for k, v in loader.construct_mapping(node, deep=True).items()}
            # Merge inline ``(k=v)`` kwargs with the mapping body instead of
            # discarding the inline ones. Block-body keys win on conflict —
            # they sit later in document order, matching the flat-view
            # last-write-wins rule.
            return _stamp(_make_fluid(factory, name, {**inline, **mapping}), loader, node)

        if isinstance(node, yaml.nodes.ScalarNode) and instant:
            return _stamp(_make_fluid(factory, name, inline), loader, node)

        return _stamp(Target(tag_suffix), loader, node)

    def clone_constructor(loader: yaml.SafeLoader, tag_suffix: str, node: yaml.nodes.Node) -> Any:
        if isinstance(node, yaml.nodes.MappingNode):
            mapping: dict[str, Any] = {str(k): v for k, v in loader.construct_mapping(node, deep=True).items()}
            return _stamp(_make_fluid(Clone, tag_suffix, mapping), loader, node)
        return _stamp(Clone(tag_suffix), loader, node)

    def lazy_constructor(loader: yaml.SafeLoader, tag_suffix: str, node: yaml.nodes.Node) -> Any:
        # Mirror class_constructor's grammar so users can write either
        # ``!lazy:Adam`` (bare), ``!lazy:Adam(lr=1e-3)`` (inline kwargs),
        # or ``!lazy:Adam`` with a YAML mapping body for the kwargs. Inline
        # values are coerced and merged with the body exactly as for !class:.
        instant = _TARGET_CALL_RE.match(tag_suffix)
        name = instant.group(1) if instant else tag_suffix
        inline = _parse_inline_kwargs(instant.group(2)) if instant else {}

        if isinstance(node, yaml.nodes.MappingNode):
            mapping: dict[str, Any] = {str(k): v for k, v in loader.construct_mapping(node, deep=True).items()}
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
            contents = {str(k): v for k, v in loader.construct_mapping(node, deep=True).items()}
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

    # ---- the tag spelling is DEPRECATED — tell the user, once per document -------
    #
    # A deprecation nobody sees is not a deprecation: the alias round in this same
    # release found consumers still on names that had been "deprecated" for months,
    # because nothing ever said so at runtime. So every tag constructor announces it.
    #
    # ONCE PER DOCUMENT, naming the file: per-tag would emit hundreds of lines for one
    # config, and once-per-process would tell a user about the first of their configs
    # and stay silent about the rest. `FutureWarning` rather than `DeprecationWarning`
    # because Python SHOWS it by default — this is aimed at the person who wrote the
    # YAML, not at library code, which is exactly the split the two categories encode.
    #
    # The remediation line is only offered when there IS a file to run it against.
    # PyYAML names a string-loaded document `<unicode string>`, so the notice used to
    # end in `confluid-migrate <unicode string>` — a command that cannot be copied,
    # run, or acted on, from a message whose whole job is to say what to do next.
    def _announce(loader: Any, node: Any) -> None:
        name = str(getattr(loader, "name", None) or "")
        from_file = bool(name) and not (name.startswith("<") and name.endswith(">"))
        where = name or "<config>"
        if where in _TAG_SPELLING_WARNED:
            return
        _TAG_SPELLING_WARNED.add(where)
        mark = getattr(node, "start_mark", None)
        line = f":{mark.line + 1}" if mark is not None else ""
        remedy = (
            f"Convert this file with `confluid-migrate {where}` — it rewrites the tags to "
            f"the reserved-key format (_target_ / _partial_ / ${{ref:}}) and verifies the "
            f"marker tree is unchanged before writing."
            if from_file
            else (
                "This document was loaded from a string, so there is no file to convert — "
                "rewrite it in the reserved-key format (_target_ / _partial_ / ${ref:}), or "
                "run `confluid-migrate` on the file it came from."
            )
        )
        warnings.warn(
            f"{where}{line}: the YAML tag syntax (!class: / !lazy: / !ref: / !clone: / "
            f"!scope:) is DEPRECATED and is removed in confluid 0.4.0. {remedy} The new "
            f"format is also plain YAML, so yq, editor schemas and linters can read it.",
            FutureWarning,
            stacklevel=2,
        )

    def _deprecated(constructor: Any) -> Any:
        """Wrap a tag constructor so parsing a tag announces the deprecation."""

        def wrapped(loader: Any, *args: Any) -> Any:
            _announce(loader, args[-1])  # the NODE is last in both constructor arities
            return constructor(loader, *args)

        return wrapped

    for _tag, _ctor in (
        ("!ref:", ref_constructor),
        ("!class:", class_constructor),
        ("!clone:", clone_constructor),
        ("!lazy:", lazy_constructor),
        ("!scope:", scope_constructor),
        ("!notscope:", notscope_constructor),
    ):
        ConfluidLoader.add_multi_constructor(_tag, _deprecated(_ctor))

    ConfluidLoader.add_constructor("!ref", _deprecated(ref_compat))
    ConfluidLoader.add_constructor("!class", _deprecated(class_compat))

    # ---- the reserved-key format: plain mappings that carry ``_target_`` & co ----
    #
    # Registered on the DEFAULT MAPPING tag, so it sees every untagged mapping in
    # the document. The reserved-key test reads the NODE's key names — no values
    # are constructed to answer it — and a mapping carrying none of them delegates
    # straight to PyYAML's own constructor. That keeps the ordinary path exactly as
    # fast (and as alias/recursion-correct) as it was, and confines the new
    # behaviour to mappings that actually opted in.
    default_map_constructor = ConfluidLoader.yaml_constructors[yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG]

    def map_constructor(loader: yaml.SafeLoader, node: yaml.nodes.MappingNode) -> Any:
        node_keys = {k.value for k, _ in node.value if isinstance(k, yaml.nodes.ScalarNode)}
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


def load_config(path: Union[str, Path], _included: Optional[Set[Path]] = None) -> Dict[str, Any]:
    """Load raw YAML with markers and recursive includes.

    A relative ``path`` is resolved through the config search tiers (CWD →
    ``CWD/config`` → XDG base dirs — see :func:`resolve_config_path`) BEFORE
    canonicalization, so circular-include detection and the include
    accumulator operate on the real file.
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

    # Root-level !class: documents parse to a Fluid. Imports/includes are
    # dict-only constructs, so skip them and just walk the Fluid's kwargs
    # for nested includes — keeps load_config symmetric with load(text).
    from confluid.fluid import Fluid

    if isinstance(data, Fluid):
        return cast(Dict[str, Any], _process_includes_recursive(data, path, _included))

    data = _process_imports(data)
    data = cast(Dict[str, Any], _process_includes_recursive(data, path, _included))
    return data


def load_config_with_paths(path: Union[str, Path]) -> tuple[Dict[str, Any], List[Path]]:
    """Load a YAML config and return ``(data, ordered_paths)``.

    ``ordered_paths`` is the entrypoint followed by every transitively
    ``include:``-d file in load order, deduplicated. Use this when a caller
    needs to capture the full tree of YAML files that contributed to the
    flowed config (e.g. logging the run's configuration as a reproducible
    artifact). The thin wrapper preserves :func:`load_config`'s existing
    public signature so callers that do not need the tree are unaffected.
    """
    accum: List[Path] = []
    token = _INCLUDE_ACCUMULATOR.set(accum)
    try:
        data = load_config(path)
    finally:
        _INCLUDE_ACCUMULATOR.reset(token)
    seen: Set[Path] = set()
    ordered: List[Path] = []
    for p in accum:
        if p not in seen:
            seen.add(p)
            ordered.append(p)
    return data, ordered


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


def _process_includes_recursive(data: Any, current_path: Path, _included: Set[Path]) -> Any:
    from confluid.fluid import Fluid, ScopeBlock

    if isinstance(data, list):
        return [_process_includes_recursive(item, current_path, _included) for item in data]

    # Traverse into Class/Fluid kwargs
    if isinstance(data, Fluid):
        data.kwargs = {k: _process_includes_recursive(v, current_path, _included) for k, v in data.kwargs.items()}
        return data

    # Scope blocks: walk their contents so nested includes still process. The body may
    # be a mapping, a sequence or a scalar (see `_build_scope`), so only the mapping
    # shape is walked key-wise — the others delegate to the branches above, which is
    # also what keeps a block's OWN `include:` key unprocessed, as it always was.
    if isinstance(data, ScopeBlock):
        if isinstance(data.contents, dict):
            data.contents = {
                k: _process_includes_recursive(v, current_path, _included) for k, v in data.contents.items()
            }
        else:
            data.contents = _process_includes_recursive(data.contents, current_path, _included)
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
        k: _process_includes_recursive(v, current_path, _included) for k, v in data.items()
    }

    if "include" in processed_dict:
        processed_dict = _splice_includes(processed_dict, current_path, _included)

    return processed_dict


def _splice_includes(block: Dict[str, Any], current_path: Path, _included: Set[Path]) -> Dict[str, Any]:
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
        return {k: v for k, v in block.items() if k != "include"}

    included: List[Dict[str, Any]] = []
    for inc_path in includes:
        if not isinstance(inc_path, str):
            continue
        target_path = resolve_config_path(inc_path, base_dir=current_path.parent)
        included.append(load_config(target_path, _included=set(_included)))

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


def load(
    data: Any,
    *,
    flow: bool = True,
    context: Optional[Dict[str, Any]] = None,
    scopes: Optional[List[str]] = None,
    solidify: bool = True,
) -> Any:
    """Load and (optionally) materialize a config.

    ``scopes`` is a list of activation strings forwarded from the CLI layer
    (typically liquifai). Each entry is either a bare boolean name
    (``"debug"``) or a ``"key=value"`` pair (``"task=classification"``). Scope
    blocks tagged with ``!scope:…`` / ``!notscope:…`` in the YAML are resolved
    against this set before flow runs. See :mod:`confluid.scopes`.
    """
    if isinstance(data, (str, Path)):
        str_data = str(data)
        if (
            "\n" not in str_data
            and ":" not in str_data
            and len(str_data) < 255
            and resolve_config_path(str_data).exists()
        ):
            data = load_config(data)
        else:
            data = cast(Dict[str, Any], yaml.load(str_data, Loader=ConfluidLoader) or {})
            data = _process_includes_recursive(data, Path.cwd() / "string.yaml", set())

    # Resolve scope blocks before anything else — they only carry until this
    # point. Aliases live at the top level of the loaded dict; pull them out
    # before normalizing the activation map.
    if isinstance(data, dict):
        aliases = data.get("scope_aliases") if isinstance(data.get("scope_aliases"), dict) else None
        active = normalize_active(scopes or [], aliases)
        data = resolve_scopes(data, active)
    elif scopes:
        # Non-dict roots (e.g. YAML starting with !class:) carry no metadata,
        # but a ScopeBlock could still sit at the top level. Resolve directly.
        data = resolve_scopes(data, normalize_active(scopes, None))

    # Handle root-level Fluid objects (e.g., YAML starting with !class:)
    from confluid.fluid import Fluid

    if isinstance(data, Fluid):
        if flow:
            # Route through materialize() so inner !ref: targets (dotted imports
            # like `posixpath.join`, cross-kwarg references) get resolved
            # against the Fluid's own kwargs. A raw _deep_flow skips that pass.
            return materialize(data, context=context, solidify=solidify)
        return data

    if not isinstance(data, dict):
        return data

    data = cast(Dict[str, Any], _process_imports(data))

    resolver = Resolver(context=context or data)
    data = resolver.resolve(data)
    data = expand_dotted_keys(data)

    if not flow:
        return data

    return materialize(data, context=context or data, solidify=solidify)


# ``materialize`` is a REAL dependency of ``load()`` above, imported after the
# defs because the engine's ``resolve()`` body-imports ``load`` (the sanctioned
# lazy seam) — a top placement would still work, but this keeps the seam's two
# ends visually paired. The old blanket compat re-export block that rode here
# was pruned 2026-08-08: zero users remained workspace-wide (tests were
# retargeted to the real homes).
from confluid.engine import materialize  # noqa: E402
