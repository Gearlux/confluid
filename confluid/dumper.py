import inspect
import types
from typing import Any, Optional, Set

import yaml


class CompactDumper(yaml.SafeDumper):
    """Custom YAML dumper with !class tag support."""

    pass


def _represent_callable(dumper: yaml.SafeDumper, data: Any) -> Any:
    """Emit a module-level function/builtin as ``!ref:module.qualname``.

    The resolver's ``resolve_reference_path`` resolves this back to the live
    object via ``importlib.import_module`` + ``getattr``, so dump/load
    round-trips hold as long as the symbol stays importable at the same
    dotted path.
    """
    module = getattr(data, "__module__", None)
    qualname = getattr(data, "__qualname__", None) or getattr(data, "__name__", None)
    if not module or not qualname or "<" in qualname:
        # Lambdas, closures, and anything anonymous can't be referenced —
        # fall back to the default "cannot represent" error.
        raise yaml.representer.RepresenterError(
            f"cannot represent callable {data!r} — no resolvable dotted import path"
        )
    return dumper.represent_scalar("!ref", f"{module}.{qualname}")


def _represent_opaque(dumper: yaml.SafeDumper, data: Any) -> Any:
    """Fallback: emit a ``!class:<module.qualname>`` scalar marker.

    Used for objects that aren't ``@configurable`` and have no registered
    representer (e.g. Lightning auto-injects ``RichProgressBar`` into
    ``Trainer.callbacks``). Not round-trippable — the marker carries no
    kwargs — but lets ``dump()`` complete with informational placeholders
    instead of aborting on the first opaque object.
    """
    cls = data.__class__
    name = f"{cls.__module__}.{cls.__qualname__}"
    return dumper.represent_scalar(f"!class:{name}", "")


def _represent_object(dumper: yaml.SafeDumper, data: Any) -> Any:
    """Represent @configurable objects and Fluid citizens as YAML tags."""
    from confluid.fluid import Class, Clone, Instance
    from confluid.fluid import Lazy as LazyFluid
    from confluid.fluid import Reference

    if isinstance(data, Clone):
        if data.kwargs:
            return dumper.represent_mapping(f"!clone:{data.target}", data.kwargs)
        return dumper.represent_scalar(f"!clone:{data.target}", "")

    if isinstance(data, Reference):
        return dumper.represent_scalar("!ref", data.target)

    # Lazy comes BEFORE Class/Instance — it's a Class subclass, so the
    # isinstance ladder must match it first to emit ``!lazy:`` instead of
    # ``!class:`` and preserve the deferred-construction contract on reload.
    if isinstance(data, LazyFluid):
        target = data.target
        if isinstance(target, type):
            name = f"{target.__module__}.{target.__qualname__}"
        else:
            name = str(target)
        if data.kwargs:
            return dumper.represent_mapping(f"!lazy:{name}", data.kwargs)
        return dumper.represent_scalar(f"!lazy:{name}", "")

    # Instance comes BEFORE Class in the isinstance ladder (Class is a sibling,
    # but we match the exact type first to pick the right tag: `!class:X()` for
    # Instance, `!class:X` for Class — so a reload reproduces the same
    # eager/deferred semantics.
    if isinstance(data, Instance):
        target = data.target
        if isinstance(target, type):
            name = f"{target.__module__}.{target.__qualname__}"
        else:
            name = str(target)
        return dumper.represent_mapping(f"!class:{name}()", data.kwargs)

    if isinstance(data, Class):
        target = data.target
        if isinstance(target, type):
            name = f"{target.__module__}.{target.__qualname__}"
        else:
            name = str(target)
        return dumper.represent_mapping(f"!class:{name}", data.kwargs)

    # Objects materialized via Confluid but not @configurable — use stored origin metadata
    if hasattr(data, "__confluid_class__") and not hasattr(data.__class__, "__confluid_configurable__"):
        target = data.__confluid_class__
        if isinstance(target, type):
            cls_name = f"{target.__module__}.{target.__qualname__}"
        else:
            cls_name = str(target)
        return dumper.represent_mapping(f"!class:{cls_name}()", getattr(data, "__confluid_kwargs__", {}))

    # Live @configurable instance → dump with () to indicate instant construction on reload
    cls_name = getattr(data, "__confluid_name__", data.__class__.__name__)
    sig: Optional[inspect.Signature]
    try:
        sig = inspect.signature(data.__class__)
        params = [p for p in sig.parameters if p not in ("self", "cls")]
    except (ValueError, TypeError):
        sig = None
        params = []

    # Ctor kwargs captured at construction (engine stamp on the YAML path,
    # validation-wrap stamp on direct Python construction) — the fallback for
    # an EAGER class that transforms a param instead of storing it verbatim.
    captured = getattr(data, "__confluid_kwargs__", {})

    def _skip_none(param: str, val: Any) -> bool:
        # Suppress dump noise ONLY when the omission is lossless: a ``None``
        # value on a param whose default is also ``None`` reloads identically.
        # A ``None`` on any other default MUST dump as ``param: null`` —
        # Serialization Symmetry (the old unconditional None-skip reloaded the
        # non-None default instead).
        if val is not None or sig is None:
            return False
        return sig.parameters[param].default is None

    kwargs = {}
    for p in params:
        if hasattr(data, p):
            val = getattr(data, p)
            if not _skip_none(p, val):
                if isinstance(val, type):
                    if hasattr(val, "__confluid_configurable__"):
                        val = f"!class:{getattr(val, '__confluid_name__', val.__name__)}"
                    else:
                        val = f"{val.__module__}.{val.__name__}"
                kwargs[p] = val
        elif p in captured:
            val = captured[p]
            if not _skip_none(p, val):
                kwargs[p] = val

    # Include post-construction attributes set via @configurable
    for name in getattr(data, "__confluid_extra__", []):
        if name in kwargs:
            continue
        val = getattr(data, name, None)
        if val is not None:
            kwargs[name] = val

    return dumper.represent_mapping(f"!class:{cls_name}()", kwargs)


def dump(obj: Any) -> str:
    """Serialize a (potentially nested) object tree to YAML."""

    class _LocalDumper(CompactDumper):
        pass

    # Callable references (module-level functions, builtins) serialize as
    # `!ref:module.qualname` — the loader resolves these via dotted import.
    _LocalDumper.add_representer(types.FunctionType, _represent_callable)
    _LocalDumper.add_representer(types.BuiltinFunctionType, _represent_callable)

    # Register representers for all four Fluid subclasses upfront — Confluid
    # has a fixed, small set of Fluid shapes, and the traversal below can
    # only register what it has seen. Doing it here closes the gap where a
    # nested Instance (or Reference / Clone) inside another Fluid's kwargs
    # would miss out and fall through to `represent_undefined`.
    from confluid.fluid import Class, Clone, Instance
    from confluid.fluid import Lazy as LazyFluid
    from confluid.fluid import Reference

    for _fluid_cls in (Class, Instance, Reference, Clone, LazyFluid):
        _LocalDumper.add_representer(_fluid_cls, _represent_object)

    # Catch-all fallback for opaque non-@configurable objects. PyYAML
    # documents add_representer(None, ...) as the catch-all hook, but
    # typeshed types the first arg as `type[Any]` and rejects None.
    _LocalDumper.add_representer(None, _represent_opaque)  # type: ignore[arg-type]

    def _discover_and_register(target: Any, visited: Optional[Set[int]] = None) -> None:
        if visited is None:
            visited = set()
        if id(target) in visited:
            return
        visited.add(id(target))

        from confluid.fluid import Fluid

        if isinstance(target, Fluid):
            # All four Fluid subclasses already have representers registered
            # above; recurse into kwargs to pick up @configurable types nested
            # inside them (so their post-construction attribute round-trip
            # still works).
            for v in getattr(target, "kwargs", {}).values():
                _discover_and_register(v, visited)
            return

        if hasattr(target.__class__, "__confluid_configurable__"):
            _LocalDumper.add_representer(target.__class__, _represent_object)
            # Traverse constructor params
            param_set: set[str] = set()
            try:
                sig = inspect.signature(target.__class__)
                for p in sig.parameters:
                    param_set.add(p)
                    if hasattr(target, p):
                        _discover_and_register(getattr(target, p), visited)
            except (ValueError, TypeError):
                pass
            # Traverse captured ctor kwargs too — a nested configurable an
            # EAGER class stored under a private attr is reachable ONLY here,
            # and _represent_object will emit it via the captured fallback.
            for v in getattr(target, "__confluid_kwargs__", {}).values():
                _discover_and_register(v, visited)
            # Traverse post-construction attributes (set via @configurable)
            for name in getattr(target, "__confluid_extra__", []):
                if name in param_set:
                    continue
                val = getattr(target, name, None)
                if val is not None:
                    _discover_and_register(val, visited)
        elif hasattr(target, "__confluid_class__"):
            _LocalDumper.add_representer(target.__class__, _represent_object)
            if hasattr(target, "__confluid_kwargs__"):
                for v in target.__confluid_kwargs__.values():
                    _discover_and_register(v, visited)
        elif isinstance(target, (list, tuple)):
            for item in target:
                _discover_and_register(item, visited)
        elif isinstance(target, dict):
            for val in target.values():
                _discover_and_register(val, visited)

    _discover_and_register(obj)
    return yaml.dump(obj, Dumper=_LocalDumper, default_flow_style=False, sort_keys=False)
