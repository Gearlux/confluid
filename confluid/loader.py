import importlib
import re
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Union, cast

import yaml
from loggair import get_logger

from confluid.exceptions import CircularIncludeError, ConfigFileNotFoundError
from confluid.merger import deep_merge, expand_dotted_keys
from confluid.resolver import Resolver, parse_value
from confluid.scopes import normalize_active, resolve_scopes

logger = get_logger("confluid.loader")

# Per-context include accumulator (a YAML-side concern — deliberately NOT on
# the engine's _ENGINE_STATE): populated only inside load_config_with_paths.
_INCLUDE_ACCUMULATOR: ContextVar[Optional[List[Path]]] = ContextVar("confluid_include_accumulator", default=None)


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


def _register_constructors() -> None:
    """Register the !ref: / !class: / !clone: / !lazy: / !scope: / !notscope: constructors on ConfluidLoader.

    Invoked exactly once at module import (see the call below the definition).
    """
    from confluid.fluid import Class, Clone, Instance, Lazy, Reference, ScopeBlock

    def _parse_inline_kwargs(args_str: str) -> dict[str, Any]:
        """Parse inline ``key=value`` pairs from a ``Name(...)`` tag suffix.

        Each value is coerced to its native Python type via ``parse_value``
        (``"7"`` → ``7``, ``"0.01"`` → ``0.01``, ``"true"`` → ``True``), so the
        unquoted tag form matches the quoted-string form's coercion instead of
        silently storing raw strings. A nested ``!ref:`` / ``${ENV}`` cannot
        appear in this position — YAML forbids a second tag on one node, so the
        scanner rejects it — use the quoted-string form or a mapping body when
        you need those.
        """
        kwargs: dict[str, Any] = {}
        if args_str and args_str.strip():
            for pair in args_str.split(","):
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    kwargs[k.strip()] = parse_value(v.strip())
        return kwargs

    def _stamp(fl: Any, loader: yaml.SafeLoader, node: yaml.nodes.Node) -> Any:
        """Attach the YAML source location of `node` to `fl` for diagnostics.

        Stored as ``(filename_or_None, line, column)`` on ``fl._yaml_loc``;
        line/column are 1-based. Surfaces in :func:`format_yaml_loc` so error
        messages can point at the offending YAML mapping.
        """
        mark = node.start_mark
        filename = getattr(loader, "name", None)
        fl._yaml_loc = (filename, mark.line + 1, mark.column + 1)
        return fl

    def _make_fluid(factory: Any, name: str, kwargs: dict[str, Any]) -> Any:
        """Build a Fluid marker with its kwargs assigned POST-construction.

        ``factory(name, **kwargs)`` collides when a YAML kwarg is literally named
        ``target`` (the marker ctor's own first parameter — e.g. dataflux
        ``ConfigureOp.target``). The marker stores ``self.kwargs = kwargs`` verbatim,
        so the post-construction update is exactly equivalent and collision-proof.
        """
        fluid = factory(name)
        fluid.kwargs.update(kwargs)
        return fluid

    def ref_constructor(loader: yaml.SafeLoader, tag_suffix: str, node: yaml.nodes.Node) -> Any:
        return _stamp(Reference(tag_suffix), loader, node)

    def class_constructor(loader: yaml.SafeLoader, tag_suffix: str, node: yaml.nodes.Node) -> Any:
        instant = re.match(r"^([\w_.]+)\((.*)\)$", tag_suffix)
        factory = Instance if instant else Class
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

        return _stamp(Class(tag_suffix), loader, node)

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
        instant = re.match(r"^([\w_.]+)\((.*)\)$", tag_suffix)
        name = instant.group(1) if instant else tag_suffix
        inline = _parse_inline_kwargs(instant.group(2)) if instant else {}

        if isinstance(node, yaml.nodes.MappingNode):
            mapping: dict[str, Any] = {str(k): v for k, v in loader.construct_mapping(node, deep=True).items()}
            return _stamp(_make_fluid(Lazy, name, {**inline, **mapping}), loader, node)

        if isinstance(node, yaml.nodes.ScalarNode) and instant:
            return _stamp(_make_fluid(Lazy, name, inline), loader, node)

        return _stamp(Lazy(tag_suffix), loader, node)

    def _parse_scope_suffix(tag_suffix: str) -> tuple[str, Optional[str]]:
        # ``KEY(VALUE)`` — function-call form, mirrors ``!class:Foo(...)`` grammar.
        paren = re.match(r"^([\w_.]+)\((.*)\)$", tag_suffix)
        if paren:
            return paren.group(1), paren.group(2).strip()
        # ``KEY=VALUE`` — assignment form. Split on the first ``=``.
        if "=" in tag_suffix:
            key, value = tag_suffix.split("=", 1)
            return key.strip(), value.strip()
        # Bare ``KEY`` — boolean scope.
        return tag_suffix, None

    def _build_scope(loader: yaml.SafeLoader, tag_suffix: str, node: yaml.nodes.Node, *, negate: bool) -> Any:
        key, value = _parse_scope_suffix(tag_suffix)
        if isinstance(node, yaml.nodes.MappingNode):
            contents: dict[str, Any] = {str(k): v for k, v in loader.construct_mapping(node, deep=True).items()}
        else:
            contents = {}
        return _stamp(
            ScopeBlock(key=key, value=value, negate=negate, contents=contents),
            loader,
            node,
        )

    def scope_constructor(loader: yaml.SafeLoader, tag_suffix: str, node: yaml.nodes.Node) -> Any:
        return _build_scope(loader, tag_suffix, node, negate=False)

    def notscope_constructor(loader: yaml.SafeLoader, tag_suffix: str, node: yaml.nodes.Node) -> Any:
        return _build_scope(loader, tag_suffix, node, negate=True)

    ConfluidLoader.add_multi_constructor("!ref:", ref_constructor)
    ConfluidLoader.add_multi_constructor("!class:", class_constructor)
    ConfluidLoader.add_multi_constructor("!clone:", clone_constructor)
    ConfluidLoader.add_multi_constructor("!lazy:", lazy_constructor)
    ConfluidLoader.add_multi_constructor("!scope:", scope_constructor)
    ConfluidLoader.add_multi_constructor("!notscope:", notscope_constructor)

    def ref_compat(loader: yaml.SafeLoader, node: Any) -> Any:
        return _stamp(Reference(loader.construct_scalar(node)), loader, node)

    def class_compat(loader: yaml.SafeLoader, node: Any) -> Any:
        val = loader.construct_scalar(node)
        instant = re.match(r"^([\w_.]+)\((.*)\)$", val)
        if instant:
            return _stamp(
                Instance(instant.group(1), **_parse_inline_kwargs(instant.group(2))),
                loader,
                node,
            )
        return _stamp(Class(val), loader, node)

    ConfluidLoader.add_constructor("!ref", ref_compat)
    ConfluidLoader.add_constructor("!class", class_compat)


# Register once at import — constructors live on ConfluidLoader for the
# lifetime of the process; the global yaml.SafeLoader is never touched.
_register_constructors()


def load_config(path: Union[str, Path], _included: Optional[Set[Path]] = None) -> Dict[str, Any]:
    """Load raw YAML with markers and recursive includes."""
    path = Path(path).resolve()
    if _included is None:
        _included = set()
    if path in _included:
        raise CircularIncludeError(f"Circular include: {path}")
    _included.add(path)
    _record_loaded_path(path)

    if not path.exists():
        raise ConfigFileNotFoundError(f"Not found: {path}")

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

    # Scope blocks: walk their contents so nested includes still process.
    if isinstance(data, ScopeBlock):
        data.contents = {k: _process_includes_recursive(v, current_path, _included) for k, v in data.contents.items()}
        return data

    if not isinstance(data, dict):
        return data

    processed_dict: Dict[str, Any] = {
        str(k): _process_includes_recursive(v, current_path, _included) for k, v in data.items()
    }

    if "include" in processed_dict:
        includes = processed_dict.pop("include")
        if isinstance(includes, str):
            includes = [includes]

        if isinstance(includes, list):
            merged_base: Dict[str, Any] = {}
            for inc_path in includes:
                if not isinstance(inc_path, str):
                    continue
                target_path = current_path.parent / inc_path
                if not target_path.exists():
                    target_path = Path(inc_path)

                inc_data = load_config(target_path, _included=set(_included))
                merged_base = deep_merge(merged_base, inc_data)
            processed_dict = deep_merge(merged_base, processed_dict)

    return processed_dict


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
        if "\n" not in str_data and ":" not in str_data and len(str_data) < 255 and Path(str_data).exists():
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


# --- compat re-exports ---------------------------------------------------
# The materialization engine moved to ``confluid.engine`` (2026-07). These
# names stay importable from ``confluid.loader`` for backward compatibility
# (downstream reach-ins + tests); NEW code should import from the engine.
from confluid.engine import (  # noqa: F401,E402  (re-exports; placed after loader defs)
    _deep_flow,
    _flow_recursive,
    _get_acceptable_keys,
    _get_param_kinds,
    _get_post_init_attrs,
    _prepare_kwargs,
    _same_target,
    get_active_context,
    get_configurable_attrs,
    materialize,
    resolve,
)
