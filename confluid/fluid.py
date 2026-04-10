from copy import copy
from typing import Any, Dict, Optional, Type, Union

from confluid.registry import get_registry, resolve_class


class Fluid:
    """Base class for all deferred configuration objects."""

    __confluid_configurable__ = True

    def __init__(self, target: Any, **kwargs: Any) -> None:
        self.target = target
        self.kwargs = kwargs
        self.context: Optional[Dict[str, Any]] = None

    def __repr__(self) -> str:
        name = self.target if isinstance(self.target, str) else getattr(self.target, "__name__", str(self.target))
        return f"{self.__class__.__name__}({name}, {self.kwargs})"


class Class(Fluid):
    """Deferred class initializer. Stays deferred until explicitly flow()'d."""

    def __init__(self, target: Union[Type[Any], str], **kwargs: Any) -> None:
        super().__init__(target, **kwargs)


class Instance(Fluid):
    """Instant class initializer. Materialized immediately by materialize()/flow()."""

    def __init__(self, target: Union[Type[Any], str], **kwargs: Any) -> None:
        super().__init__(target, **kwargs)


class Reference(Fluid):
    """Late-bound reference to another part of the config."""

    def __init__(self, path: str, **kwargs: Any) -> None:
        super().__init__(path, **kwargs)


def flow(obj: Any, **runtime_kwargs: Any) -> Any:
    """Instantiate a deferred object (Class, Reference, marker dict) into a live instance.

    Idempotent: already-live objects are returned unchanged.
    Accepts runtime kwargs that merge with stored kwargs (runtime wins).
    """
    from confluid.loader import get_active_context, materialize
    from confluid.resolver import Resolver

    # 1. Idempotency — already-live objects pass through
    if not isinstance(obj, (Fluid, str, type, dict)):
        return obj

    # 2. Resolve context from the Fluid object or the global active context
    context = getattr(obj, "context", None) if isinstance(obj, Fluid) else None
    if not context:
        context = get_active_context()

    # 3. Class/Instance — resolve, merge kwargs, instantiate
    if isinstance(obj, (Class, Instance)):
        import inspect

        target = obj.target
        if isinstance(target, str):
            resolved = resolve_class(target)
            if resolved is None:
                raise ValueError(f"Cannot resolve class: {target}")
            target = resolved

        # Build merged kwargs: context < explicit kwargs < runtime kwargs
        obj_context = getattr(obj, "context", None) or {}
        merged: dict[str, Any] = {}
        for k, v in obj_context.items():
            if not isinstance(v, (dict, list, Fluid)):
                merged[k] = v
        # Scoped: ClassName and instance name blocks from context
        cls_name = getattr(target, "__confluid_name__", target.__name__)
        for key in [cls_name, obj.kwargs.get("name")]:
            block = obj_context.get(key) if key else None
            if isinstance(block, dict):
                merged.update(block)
        merged.update(obj.kwargs)
        merged.update(runtime_kwargs)

        # Flow Instance values (instant), propagate context to Class (deferred)
        def _resolve_value(v: Any) -> Any:
            if isinstance(v, Instance):
                if not v.context and obj_context:
                    v = copy(v)
                    v.kwargs = dict(v.kwargs)
                    v.context = obj_context
                return flow(v)
            if isinstance(v, Fluid) and not v.context and obj_context:
                v = copy(v)
                v.kwargs = dict(v.kwargs)
                v.context = obj_context
                return v  # Class/Reference stay deferred
            if isinstance(v, list):
                return [_resolve_value(item) for item in v]
            if isinstance(v, dict):
                return {dk: _resolve_value(dv) for dk, dv in v.items()}
            return v

        merged = {k: _resolve_value(v) for k, v in merged.items()}

        # Instantiate: constructor params go to __init__, rest set as attributes
        try:
            sig = inspect.signature(target.__init__)  # type: ignore[misc]
            params = {p for p in sig.parameters if p not in ("self", "cls")}
        except (ValueError, TypeError):
            params = set()

        ctor = {k: v for k, v in merged.items() if k in params} if params else merged
        instance = target(**ctor)
        for k, v in merged.items():
            if params and k not in params:
                setattr(instance, k, v)
        return instance

    # 4. Bare type passed directly (e.g., flow(MyClass, x=1))
    if isinstance(obj, type):
        if get_registry().is_configurable(obj):
            cls_name = getattr(obj, "__confluid_name__", obj.__name__)
            marker = {"_confluid_class_": cls_name, **runtime_kwargs}
            return materialize(marker, context=context)
        else:
            return obj(**runtime_kwargs)

    # 5. Reference objects — resolve from context
    if isinstance(obj, Reference):
        obj_context = getattr(obj, "context", None) or context
        if obj_context and obj.target in obj_context:
            return flow(obj_context[obj.target], **runtime_kwargs)
        # Fallback: try resolver for nested paths
        resolver = Resolver(context=obj_context or {})
        resolved = resolver._resolve_ref(obj.target)
        if resolved is not None and resolved != f"!ref:{obj.target}":
            return flow(resolved, **runtime_kwargs)
        raise ValueError(f"Cannot resolve Reference: {obj.target}")

    # 6. Generic Fluid fallback — treat as Class if target resolves to a type
    if isinstance(obj, Fluid):
        target = obj.target
        if isinstance(target, str):
            resolved = resolve_class(target)
            if resolved is not None:
                base_kwargs = {**obj.kwargs, **runtime_kwargs}
                return resolved(**base_kwargs)
            raise ValueError(f"Class '{target}' not found in registry.")
        return flow(target, **{**obj.kwargs, **runtime_kwargs})

    # 7. Marker dictionaries (legacy format)
    if isinstance(obj, dict) and ("_confluid_class_" in obj or "_confluid_ref_" in obj):
        if "_confluid_class_" in obj and runtime_kwargs:
            obj = {**obj, **runtime_kwargs}
        return materialize(obj, context=context)

    # 8. String tags ("!class:Name" or "!ref:path")
    if isinstance(obj, str) and (obj.startswith("!class:") or obj.startswith("!ref:")):
        resolver = Resolver(context=context)
        resolved = resolver.resolve(obj)
        if isinstance(resolved, str) and (resolved.startswith("!class:") or resolved.startswith("!ref:")):
            return obj
        return flow(resolved, **runtime_kwargs)

    return obj
