import importlib
import re
import threading
from copy import copy
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Union, cast

import yaml
from loggair import get_logger

from confluid.exceptions import CircularIncludeError, ConfigFileNotFoundError, ReferenceResolutionError
from confluid.merger import deep_merge, expand_dotted_keys
from confluid.resolver import Resolver, parse_value, resolve_reference_path
from confluid.scopes import normalize_active, resolve_scopes

logger = get_logger("confluid.loader")

# Thread-local storage for materialization context
_state = threading.local()


def get_active_context() -> Optional[Dict[str, Any]]:
    return getattr(_state, "context", None)


def _record_loaded_path(path: Path) -> None:
    """Append ``path`` to the active include-accumulator, if any.

    Populated by :func:`load_config_with_paths` for the duration of one
    load so callers can recover the ordered list of every YAML file
    transitively read (entrypoint + recursive ``include:`` targets). The
    accumulator lives on the existing thread-local so re-entrant loads on
    different threads do not collide.
    """
    accum = getattr(_state, "include_accumulator", None)
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
    old = getattr(_state, "include_accumulator", None)
    _state.include_accumulator = accum
    try:
        data = load_config(path)
    finally:
        _state.include_accumulator = old
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


def materialize(data: Any, context: Optional[Dict[str, Any]] = None, solidify: bool = True) -> Any:
    """Resolve config data and instantiate all Class objects recursively.

    Within a single materialize pass, identical raw markers (reached directly
    or via ``!ref:``) produce a single flowed ``Instance`` object, which is
    materialized into a single live instance. ``!clone:`` opts out of this
    sharing with an explicit deepcopy.

    ``solidify=False`` suppresses the post-flow ``solidify()`` hook for every
    object built in this pass (see :func:`confluid.fluid.flow`) — for static
    introspection that needs live objects but must NOT pay for the expensive
    finalize (e.g. building a model backbone). The objects are still fully
    constructed (``__init__`` only stores values per the zero-arg / lazy-init
    convention), just not solidified.
    """
    _acceptable_keys_cache.clear()
    _post_init_attrs_cache.clear()
    _param_kind_cache.clear()
    if context:
        context = expand_dotted_keys(context)
    old_ctx = getattr(_state, "context", None)
    old_flow_memo = getattr(_state, "flow_memo", None)
    old_instance_memo = getattr(_state, "instance_memo", None)
    old_suppress = getattr(_state, "suppress_solidify", None)
    _state.context = context
    _state.flow_memo = {}
    _state.instance_memo = {}
    _state.suppress_solidify = not solidify
    try:
        result = _flow_recursive(data, parent_context=context)
        return _deep_flow(result)
    finally:
        _state.context = old_ctx
        _state.flow_memo = old_flow_memo
        _state.instance_memo = old_instance_memo
        _state.suppress_solidify = old_suppress


def resolve(
    data: Any,
    *,
    context: Optional[Dict[str, Any]] = None,
    scopes: Optional[List[str]] = None,
) -> Any:
    """Broadcast-resolve a config to a Fluid marker graph WITHOUT instantiating.

    Like :func:`materialize`, but stops before ``_deep_flow``: it parses,
    resolves scopes/includes, applies broadcasting and ``!ref:`` resolution
    (sharing referenced markers by identity via ``flow_memo`` — so a fan-out
    ``!ref:`` is one object reached twice), and returns the resulting
    ``Instance`` / ``Lazy`` / ``Class`` markers with their broadcast siblings
    merged into ``.kwargs`` — WITHOUT constructing any live object.

    Use for static structural introspection of a config (e.g. FluxStudio's
    YAML→graph import) when even side-effect-free construction is undesirable.
    ``materialize(data, solidify=False)`` is the instantiate-but-cheap
    counterpart; prefer it unless you specifically need un-built markers.

    Caveat: a *dotted* ``!ref:a.b`` (attribute/method access) still instantiates
    its target subtree to read the attribute — plain whole-object ``!ref:name``
    stays a marker.
    """
    prepared = load(data, flow=False, context=context, scopes=scopes)
    ctx = context if context is not None else (prepared if isinstance(prepared, dict) else None)
    if ctx:
        ctx = expand_dotted_keys(ctx)
    _acceptable_keys_cache.clear()
    _post_init_attrs_cache.clear()
    _param_kind_cache.clear()
    old_ctx = getattr(_state, "context", None)
    old_flow_memo = getattr(_state, "flow_memo", None)
    old_instance_memo = getattr(_state, "instance_memo", None)
    _state.context = ctx
    _state.flow_memo = {}
    _state.instance_memo = {}
    try:
        return _flow_recursive(prepared, parent_context=ctx)
    finally:
        _state.context = old_ctx
        _state.flow_memo = old_flow_memo
        _state.instance_memo = old_instance_memo


def _deep_flow(data: Any) -> Any:
    """Flow the top-level Fluid + any Instance objects in the tree.

    ``Lazy`` Fluids are left deferred at every level — they are
    runtime-injection points whose construction happens later (e.g.
    inside ``configure_optimizers`` once ``model.parameters()`` is
    available). Flowing them here would either fail (missing runtime
    args) or produce a partially-initialized object.
    """
    from confluid.fluid import Fluid, Instance, Lazy
    from confluid.fluid import flow as _flow

    def _maybe_flow(v: Any) -> Any:
        if isinstance(v, Lazy):
            return v
        if isinstance(v, Instance):
            return _flow(v)
        return v

    if isinstance(data, Lazy):
        return data
    if isinstance(data, Fluid):
        return _flow(data)
    if isinstance(data, dict):
        return {k: _maybe_flow(v) for k, v in data.items()}
    if isinstance(data, list):
        return [_maybe_flow(item) for item in data]
    return data


_acceptable_keys_cache: Dict[str, Optional[frozenset[str]]] = {}
_post_init_attrs_cache: Dict[str, frozenset[str]] = {}
# Per-class: ``{param_name: "dict" | "list" | None}`` — None means "not annotated
# as a dict/list-shaped type" (default scalar/Fluid-only broadcast rules apply).
_param_kind_cache: Dict[str, Dict[str, Optional[str]]] = {}


def _same_target(fluid_target: Any, cls: Callable[..., Any]) -> bool:
    """True if ``fluid_target`` resolves to the same class object as ``cls``.

    Identity-only comparison: two classes that share a short name across
    different modules are NOT considered "same". This prevents the
    self-broadcast guard from over-skipping fluids whose target happens to
    share a name with the receiving class.

    Handles three cases:
      * ``fluid_target`` IS ``cls`` — fast path.
      * ``fluid_target`` is a string and ``resolve_class`` resolves it to
        ``cls`` — registry-confirmed match.
      * ``fluid_target`` is a string equal to the fully-qualified
        ``cls.__module__.__qualname__`` — last-resort match for classes
        that aren't registered yet but whose dotted path is unambiguous.

    Bare-name strings (``"Trainer"``) that can't be registry-resolved are
    treated as "not same" — better to broadcast and let the receiver's
    accept-list filter than to silently skip across module boundaries.
    """
    if fluid_target is cls:
        return True
    if isinstance(fluid_target, str):
        from confluid.registry import resolve_class

        resolved = resolve_class(fluid_target)
        if resolved is cls:
            return True
        qualified = f"{cls.__module__}.{cls.__qualname__}"
        if fluid_target == qualified:
            return True
    return False


def _ast_scan_init_setattrs(init_func: Any) -> Set[str]:
    """Return non-underscore attribute names assigned via ``self.<name> = …`` or
    ``setattr(self, "<name>", …)`` inside a single ``__init__`` function body.

    Pure AST inspection — no class context required, so the same scan is reused
    by both the MRO walker (:func:`_get_post_init_attrs`) and the
    @configurable-chain walker (:func:`get_post_init_attrs_configurable_chain`).
    """
    import ast
    import inspect
    import textwrap

    names: Set[str] = set()
    try:
        source = inspect.getsource(init_func)
    except (OSError, TypeError):
        return names
    try:
        # ``textwrap.dedent`` preserves relative indentation (strips the
        # common leading whitespace), so the method body still sits
        # under its ``def`` header. ``inspect.cleandoc`` would flatten
        # every line to column 0 and break the parse.
        tree = ast.parse(textwrap.dedent(source))
    except SyntaxError:
        return names

    for node in ast.walk(tree):
        # Pattern 1: ``self.x = ...`` / ``self.x: T = ...`` / ``self.x += ...``
        targets = (
            [node.target]
            if isinstance(node, (ast.AnnAssign, ast.AugAssign))
            else node.targets if isinstance(node, ast.Assign) else []
        )
        for t in targets:
            if (
                isinstance(t, ast.Attribute)
                and isinstance(t.value, ast.Name)
                and t.value.id == "self"
                and not t.attr.startswith("_")
            ):
                names.add(t.attr)

        # Pattern 2: ``setattr(self, "x", ...)`` with a string literal name.
        # Common when classes apply external config in a loop or use a
        # helper to bulk-assign attributes. Non-literal names (variables,
        # f-strings) stay invisible — we don't try to be clever.
        if (
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
            names.add(node.args[1].value)

    return names


def _get_post_init_attrs(target: type) -> frozenset[str]:
    """Extract attribute names assigned in ``__init__`` bodies via AST.

    Walks the class MRO, parses each class's ``__init__`` source, and collects
    every ``self.<name> = ...`` target. Private names (underscore-prefixed) are
    skipped to avoid broadcasting into implementation details. Results cache
    per-class by dotted module name.

    This is what lets broadcasting see post-init attributes (e.g.
    ``self.loss_fn = nn.CrossEntropyLoss()`` in a Trainer's ``__init__``
    body) in addition to the constructor signature — so a top-level YAML
    ``loss_fn: !class:...`` flows into the Trainer without the user
    duplicating the key under the trainer block.
    """
    cache_key = f"{target.__module__}.{target.__qualname__}"
    if cache_key in _post_init_attrs_cache:
        return _post_init_attrs_cache[cache_key]

    names: Set[str] = set()
    try:
        mro = target.__mro__
    except AttributeError:
        _post_init_attrs_cache[cache_key] = frozenset()
        return frozenset()

    for klass in mro:
        if klass is object:
            continue
        init = klass.__dict__.get("__init__")
        if init is None:
            continue
        names.update(_ast_scan_init_setattrs(init))

    result = frozenset(names)
    _post_init_attrs_cache[cache_key] = result
    return result


def _get_parent_attr_blacklist(cls: type) -> frozenset[str]:
    """Non-underscore attribute names contributed by NON-``@configurable`` ancestors.

    Returns the union, across every non-``@configurable`` class in ``cls.__mro__``
    (excluding ``cls`` and ``object``), of:

    * Names from ``__annotations__`` (e.g. ``training: bool`` annotated on
      ``torch.nn.Module``'s class body — gets set instance-side via
      ``super().__setattr__('training', True)`` which the AST scan can't see).
    * Names from ``__dict__`` whose value is a non-callable, non-property
      attribute (e.g. class-level constants like
      ``CHECKPOINT_HYPER_PARAMS_KEY = "hyper_parameters"`` on
      ``pytorch_lightning.LightningModule``).
    * Names assigned via ``self.<name> = …``, ``self.<name>: T = …``, or
      ``setattr(self, "<name>", …)`` in the class's ``__init__`` body
      (e.g. ``self.prepare_data_per_node: bool = True`` in
      ``pytorch_lightning.core.hooks.DataHooks.__init__``).

    Used by parameter-discovery walkers to subtract parent-class contributions
    from ``vars(obj)`` so the configurable surface reflects only what the
    user (and Confluid's own broadcast machinery) put there.
    """
    cache_key = f"{cls.__module__}.{cls.__qualname__}#parent_blacklist"
    if cache_key in _post_init_attrs_cache:
        return _post_init_attrs_cache[cache_key]

    blacklist: Set[str] = set()
    try:
        mro = cls.__mro__
    except AttributeError:
        _post_init_attrs_cache[cache_key] = frozenset()
        return frozenset()

    for klass in mro:
        if klass is object or klass is cls:
            continue
        if getattr(klass, "__confluid_configurable__", False):
            continue
        for name in getattr(klass, "__annotations__", {}).keys():
            if not name.startswith("_"):
                blacklist.add(name)
        for name, val in klass.__dict__.items():
            if name.startswith("_"):
                continue
            if callable(val) or isinstance(val, property):
                continue
            blacklist.add(name)
        init = klass.__dict__.get("__init__")
        if init is not None:
            blacklist.update(_ast_scan_init_setattrs(init))

    result = frozenset(blacklist)
    _post_init_attrs_cache[cache_key] = result
    return result


def get_configurable_attrs(obj: Any) -> frozenset[str]:
    """Return non-underscore instance attributes of ``obj`` that belong to its ``@configurable`` surface.

    Filters ``vars(obj)`` to exclude attributes contributed by non-``@configurable``
    parent classes — their class annotations (``training: bool`` on
    ``torch.nn.Module``), class-level constants
    (``CHECKPOINT_HYPER_PARAMS_KEY`` on ``pytorch_lightning.LightningModule``),
    and ``__init__``-body setattrs (``self.prepare_data_per_node: bool = True``
    on ``pytorch_lightning.core.hooks.DataHooks``). Anything still present
    after that subtraction is either a constructor parameter the user
    declared, a post-construction setattr the user did themselves, or one
    Confluid's broadcast/Enable machinery wrote on the instance.

    See [confluid/confluid/loader.py:_get_parent_attr_blacklist] for the
    blacklist construction.
    """
    cls = obj.__class__
    blacklist = _get_parent_attr_blacklist(cls)
    return frozenset(name for name in vars(obj).keys() if not name.startswith("_") and name not in blacklist)


def _get_acceptable_keys(cls_or_name: Any) -> Optional[frozenset[str]]:
    """Return constructor params (+ configurable properties + post-init attrs) for a class.

    Accepts either a class object or a string name (resolved via registry).
    Returns None if the class cannot be resolved or accepts **kwargs (broadcast everything).

    For ``@configurable`` targets the result also includes attribute names
    assigned in the class's ``__init__`` body (via AST inspection). This
    makes broadcasting see post-init attributes such as
    ``self.loss_fn = nn.CrossEntropyLoss()`` even though they aren't listed
    in the constructor signature — so a top-level YAML key matching one of
    those names flows into the target without having to be duplicated under
    the target's block.

    Resolution order: a string name is resolved to its class FIRST, then
    cached under the resolved class's fully-qualified name. This prevents
    two classes that share a short name across different modules from
    silently inheriting one another's accept-list.
    """
    import inspect

    target: Any
    if isinstance(cls_or_name, type):
        target = cls_or_name
    else:
        # Always resolve the string first so the cache key is module-qualified.
        from confluid.registry import resolve_class

        resolved = resolve_class(cls_or_name)
        if resolved is None:
            # Truly unresolvable — cache the negative result under the raw
            # name so repeated lookups stay O(1). Two modules with the same
            # unresolvable name collide, but the value is None in both cases
            # so the collision is benign.
            if cls_or_name in _acceptable_keys_cache:
                return _acceptable_keys_cache[cls_or_name]
            _acceptable_keys_cache[cls_or_name] = None
            return None
        target = resolved

    cache_key = f"{target.__module__}.{target.__qualname__}"
    if cache_key in _acceptable_keys_cache:
        return _acceptable_keys_cache[cache_key]

    keys: Set[str] = set()
    try:
        init_method = getattr(target, "__init__", None)
        if init_method is None:
            _acceptable_keys_cache[cache_key] = None
            return None
        sig = inspect.signature(init_method)
        if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()):
            _acceptable_keys_cache[cache_key] = None
            return None
        keys.update(p for p in sig.parameters if p not in ("self", "cls"))
    except (ValueError, TypeError):
        _acceptable_keys_cache[cache_key] = None
        return None

    if getattr(target, "__confluid_configurable__", False):
        for name in dir(target):
            if name.startswith("_") or name in keys:
                continue
            member = getattr(target, name, None)
            if member is None or callable(member):
                continue
            if getattr(member, "__confluid_ignore__", False):
                continue
            if isinstance(member, property) and member.fset is None:
                continue
            keys.add(name)

        # Fold in attribute names assigned in __init__'s body (AST scan).
        # These are instance attributes not visible via dir(cls), but the
        # post-init injection loop in confluid.fluid.flow already assigns
        # any matching kwarg via setattr — broadcasting just needs to know
        # the names so a top-level YAML key can flow into them.
        keys.update(_get_post_init_attrs(target))

    result = frozenset(keys)
    _acceptable_keys_cache[cache_key] = result
    return result


def _get_param_kinds(cls_or_name: Any) -> Dict[str, Optional[str]]:
    """Return ``{param_name: "dict" | "list" | None}`` for a target's ctor.

    Used by :func:`_accepts` to decide whether a dict/list value at a
    matching key in the parent context should be broadcast IN (when the
    target's annotation says it expects a dict/list) or left to recurse as
    a config sub-block (the default for un-annotated/scalar-shaped params).

    Resolution is annotation-only (no runtime values); if a class doesn't
    annotate, the value stays None and the legacy "skip dict/list" rule
    applies. ``typing.get_type_hints`` is wrapped in a try/except because
    forward references that can't be resolved would otherwise raise.
    """
    import inspect
    import typing

    target: Any
    if isinstance(cls_or_name, type):
        target = cls_or_name
    else:
        from confluid.registry import resolve_class

        target = resolve_class(cls_or_name)
        if target is None:
            return {}

    cache_key = f"{target.__module__}.{target.__qualname__}"
    if cache_key in _param_kind_cache:
        return _param_kind_cache[cache_key]

    kinds: Dict[str, Optional[str]] = {}
    try:
        init_method = getattr(target, "__init__", None)
        if init_method is None:
            _param_kind_cache[cache_key] = kinds
            return kinds
        sig = inspect.signature(init_method)
    except (ValueError, TypeError):
        _param_kind_cache[cache_key] = kinds
        return kinds

    # Try to resolve forward refs via typing.get_type_hints; fall back to
    # the raw .annotation when that fails (common for self-referential or
    # third-party-imported annotations).
    try:
        hints = typing.get_type_hints(init_method)
    except Exception:
        hints = {}

    for name, param in sig.parameters.items():
        if name in ("self", "cls"):
            continue
        ann = hints.get(name, param.annotation)
        kinds[name] = _classify_annotation(ann)

    _param_kind_cache[cache_key] = kinds
    return kinds


def _classify_annotation(ann: Any) -> Optional[str]:
    """Map a type annotation to ``"dict"`` / ``"list"`` / None.

    Recognizes the obvious built-ins (``dict``, ``list``, ``tuple``,
    ``set``) and their ``typing`` analogues (``Dict``, ``List``, ``Tuple``,
    ``Set``, ``Mapping``, ``Sequence``, ``MutableMapping``, etc.). Unions
    that include any of these on either side count as the corresponding
    kind — e.g. ``Optional[Dict[str, int]]`` classifies as ``"dict"``.

    Returns None for anything else (including bare ``Any`` and unannotated).
    """
    import inspect
    import typing

    if ann is inspect.Parameter.empty:
        return None

    # Direct built-ins.
    if ann in (dict, list, tuple, set, frozenset):
        return "dict" if ann is dict else "list"

    # typing.* origins.
    origin = typing.get_origin(ann)
    if origin is not None:
        if origin in (dict,) or origin is typing.Dict:  # type: ignore[attr-defined]
            return "dict"
        if origin in (list, tuple, set, frozenset):
            return "list"
        # Abstract collections from typing/collections.abc.
        try:
            import collections.abc as cabc
        except ImportError:  # pragma: no cover
            cabc = None  # type: ignore[assignment]
        if cabc is not None:
            if origin in (cabc.Mapping, cabc.MutableMapping):
                return "dict"
            if origin in (
                cabc.Sequence,
                cabc.MutableSequence,
                cabc.Iterable,
                cabc.Collection,
            ):
                return "list"
        if origin is typing.Union:
            for arg in typing.get_args(ann):
                kind = _classify_annotation(arg)
                if kind is not None:
                    return kind
    return None


def _splice_kwargs_at_slot(
    parent_context: Dict[str, Any],
    self_key: Optional[str],
    kwargs: Dict[str, Any],
    receiver_cls: Any = None,
) -> Dict[str, Any]:
    """Build a new ordered dict by replacing ``parent_context[self_key]``
    with ``kwargs``'s items at the same position, preserving document
    order. When ``self_key`` is not in ``parent_context`` (top-level call,
    or identity match failed), kwargs are appended at the end.

    Collisions on a key ``kk`` that appears in BOTH ``parent_context`` and
    ``kwargs`` are resolved by inspecting the receiver class's type:

    * ``kwargs[kk]`` is a :class:`Reference` → keep parent's value
      (avoids infinite recursion when ``foo: !ref:foo`` would resolve
      against itself).
    * ``kk`` is a typed param of the receiver (i.e. in its accept-list)
      → keep parent's value. The receiver's constructor consumes
      ``kwargs[kk]`` directly via ``resolved_kwargs``; the parent's
      entry at ``kk`` is broadcast metadata aimed at descendants and
      must remain visible in ``child_ctx``.
    * Otherwise (``kk`` is NOT a typed param of the receiver) →
      ``kwargs`` wins. The kwarg was placed on the receiver's YAML
      block specifically to pass through to descendants; it sits at a
      later document position than the colliding parent broadcast, so
      last-write-wins gives it the slot.
    * ``receiver_cls`` is unknown or accepts ``**kwargs`` (accept-list
      is ``None``) → keep parent's value. Conservative fallback that
      preserves pre-existing dotted-broadcast behaviour.
    """
    from confluid.fluid import Reference

    acceptable = _get_acceptable_keys(receiver_cls) if receiver_cls is not None else None

    def _parent_wins(kk: str, kv: Any) -> bool:
        if kk not in parent_context:
            return False
        if isinstance(kv, Reference):
            return True
        if acceptable is None:
            return True
        return kk in acceptable

    out: Dict[str, Any] = {}
    if self_key is None or self_key not in parent_context:
        for k, v in parent_context.items():
            out[k] = v
        for k, v in kwargs.items():
            if _parent_wins(k, v):
                continue
            out[k] = v
        return out
    for k, v in parent_context.items():
        if k == self_key:
            for kk, kv in kwargs.items():
                if _parent_wins(kk, kv):
                    continue
                out.pop(kk, None)
                out[kk] = kv
        elif k in kwargs and not _parent_wins(k, kwargs[k]):
            # Wrapper's value at this key will win at the self_key slot;
            # skip parent's value at its original position so the wrapper's
            # value ends up at the slot.
            continue
        else:
            out[k] = v
    return out


def _prepare_kwargs(
    cls_name: str,
    own_kwargs: Dict[str, Any],
    parent_context: Dict[str, Any],
    target: Any = None,
    self_obj: Any = None,
) -> Dict[str, Any]:
    """Flat-view, document-order, last-write-wins kwarg assembly.

    Walks ``parent_context`` in document order. The receiving Fluid's own
    ``own_kwargs`` are unrolled at the position WHERE ``self_obj`` sits in
    ``parent_context`` (matched by Python identity); when ``self_obj`` is
    not found, they are applied at the end. Class-name and instance-name
    dict blocks (``Foo: {...}``) are unrolled inline at their position.
    Scalar/Fluid values broadcast when the key matches the receiving
    class's ``acceptable`` set.

    There is no "explicit kwargs > broadcast" priority — every source is
    ordered by its YAML position. Whichever assignment comes last wins.

    ``cls_name`` is the receiver's target name (used for class-name block
    matching and accept-list lookup). ``target`` is an optional class object
    for parameter inspection (avoids name collisions). ``self_obj`` is the
    Fluid being materialized — passed so we can locate its slot in
    ``parent_context``.
    """
    if cls_name.endswith("()"):
        cls_name = cls_name[:-2]
    instance_name = own_kwargs.get("name")

    from confluid.fluid import Fluid
    from confluid.registry import resolve_class

    acceptable = _get_acceptable_keys(target or cls_name)
    target_cls = target if isinstance(target, type) else resolve_class(cls_name) if cls_name else None
    param_kinds = _get_param_kinds(target_cls or cls_name) if (target_cls or cls_name) else {}

    def _accepts(k: str, v: Any) -> bool:
        if isinstance(v, Fluid):
            if acceptable is None or k not in acceptable:
                return False
            # Skip same-target Fluids that are not self — broadcasting them
            # in would loop on infinite re-materialization.
            if target_cls is not None and _same_target(v.target, target_cls):
                return False
            return True
        if isinstance(v, dict):
            # Plain dict — only broadcast IN when the target annotates the
            # param as a dict/mapping. Otherwise keep the legacy behavior
            # (recurse as a config sub-block, do NOT pull the dict in as
            # a value).
            if param_kinds.get(k) == "dict":
                return acceptable is None or k in acceptable
            return False
        if isinstance(v, list):
            if param_kinds.get(k) == "list":
                return acceptable is None or k in acceptable
            return False
        if acceptable is not None and k not in acceptable:
            return False
        return True

    merged: Dict[str, Any] = {}
    self_unrolled = False

    for k, v in parent_context.items():
        # Receiving Fluid's own slot — unroll its kwargs at this position.
        if self_obj is not None and v is self_obj and not self_unrolled:
            merged.update(own_kwargs)
            self_unrolled = True
            continue

        # Same-target Fluid that isn't self — skip (would otherwise loop).
        if isinstance(v, Fluid) and target_cls is not None and _same_target(v.target, target_cls):
            continue

        # Class-name / instance-name dict block — unroll inline.
        if k in (cls_name, instance_name) and isinstance(v, dict):
            for bk, bv in v.items():
                if _accepts(bk, bv):
                    merged[bk] = bv
            continue

        # Plain broadcast.
        if _accepts(k, v):
            merged[k] = v

    if not self_unrolled:
        merged.update(own_kwargs)

    return merged


def _flow_recursive(data: Any, parent_context: Optional[Dict[str, Any]] = None) -> Any:
    from confluid.fluid import Class, Clone, Fluid, Instance, Reference

    # Shared-identity memo: ensures the same raw marker (reached directly or via
    # !ref:) always flows to the same Instance/Class marker object, so a single
    # live object is instantiated downstream.
    flow_memo: Optional[Dict[int, Any]] = getattr(_state, "flow_memo", None)

    # 1. Plain dictionaries — pass merged context down
    if isinstance(data, dict):
        local_ctx = {**parent_context, **data} if parent_context else dict(data)
        return {k: _flow_recursive(v, parent_context=local_ctx) for k, v in data.items()}

    # 2. Class/Instance from YAML tags — apply broadcasting to kwargs
    if isinstance(data, (Class, Instance)):
        if flow_memo is not None and id(data) in flow_memo:
            return flow_memo[id(data)]
        raw_id = id(data)
        if parent_context:
            target_name = (
                data.target
                if isinstance(data.target, str)
                else getattr(
                    data.target,
                    "__confluid_name__",
                    getattr(data.target, "__name__", ""),
                )
            )
            actual_target = data.target if isinstance(data.target, type) else None
            merged_kwargs = _prepare_kwargs(
                target_name, data.kwargs, parent_context, target=actual_target, self_obj=data
            )
        else:
            merged_kwargs = dict(data.kwargs)

        # Splice this Fluid's prepared kwargs into its slot in parent_context to
        # preserve document order for downstream broadcasts.
        if parent_context:
            self_key = next((k for k, v in parent_context.items() if v is data), None)
            child_ctx = _splice_kwargs_at_slot(parent_context, self_key, merged_kwargs, receiver_cls=data.target)
        else:
            child_ctx = dict(merged_kwargs)
        resolved_kwargs = {k: _flow_recursive(v, parent_context=child_ctx) for k, v in merged_kwargs.items()}
        res_obj = copy(data)
        res_obj.kwargs = resolved_kwargs
        if flow_memo is not None:
            flow_memo[raw_id] = res_obj
        return res_obj

    # 3. Reference — resolve against parent context
    if isinstance(data, Reference):
        if parent_context and data.target in parent_context:
            resolved = parent_context[data.target]
            # Self-reference guard: a kwarg like ``foo: !ref:foo`` with no
            # outer ``foo`` in scope splices itself into ``parent_context``,
            # so the only ``foo`` it can resolve against is itself —
            # recursing here would stack-overflow. Fail loudly instead.
            if resolved is data:
                from confluid.fluid import format_yaml_loc

                loc = format_yaml_loc(data)
                loc_str = f" at {loc}" if loc else ""
                raise ReferenceResolutionError(
                    f"Self-referential !ref:{data.target}{loc_str}: the only "
                    f"{data.target!r} in scope is this reference itself. "
                    f"Define a top-level {data.target!r} key (e.g. "
                    f"`{data.target}: null`), or remove the kwarg."
                )
            return _flow_recursive(resolved, parent_context=parent_context)
        # Support dotted paths and method calls (e.g., "obj.method()") via
        # the unified rich resolver (attribute access, brackets, module import).
        if parent_context:
            resolved = resolve_reference_path(data.target, parent_context)
            if resolved is not None:
                return resolved
        return data

    # 3b. Clone — resolve reference then deepcopy, merging extra kwargs
    if isinstance(data, Clone):
        if parent_context and data.target in parent_context:
            from copy import deepcopy

            resolved = _flow_recursive(parent_context[data.target], parent_context=parent_context)
            cloned = deepcopy(resolved)
            if data.kwargs and isinstance(cloned, (Class, Instance)):
                resolved_kwargs = {k: _flow_recursive(v, parent_context=parent_context) for k, v in data.kwargs.items()}
                cloned.kwargs.update(resolved_kwargs)
            return cloned
        return data

    # 4. Generic Fluid — pass through
    if isinstance(data, Fluid):
        return data

    # 5. Lists
    if isinstance(data, list):
        return [_flow_recursive(item, parent_context=parent_context) for item in data]

    return data
