from copy import copy
from typing import Any, Type, Union

from confluid.registry import get_registry, resolve_class


class Fluid:
    """Base class for all deferred configuration objects."""

    __confluid_configurable__ = True

    def __init__(self, target: Any, **kwargs: Any) -> None:
        self.target = target
        self.kwargs = kwargs

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

    # 2. Resolve context from the global active context
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

        # kwargs already contain broadcasting (merged by _flow_recursive)
        merged: dict[str, Any] = dict(obj.kwargs)
        merged.update(runtime_kwargs)

        # Flow Instance values (instant), keep Class/Reference deferred
        # Apply broadcasting from full context to deferred Class objects
        broadcast_ctx = context or merged

        def _resolve_value(v: Any) -> Any:
            if isinstance(v, Instance):
                return flow(v)
            if isinstance(v, Class):
                # Apply broadcasting: pull matching keys from full context
                from confluid.loader import _get_acceptable_keys

                broadcasted = dict(v.kwargs)
                acceptable = _get_acceptable_keys(v.target)
                for bk, bv in broadcast_ctx.items():
                    if bk not in broadcasted and not isinstance(bv, (dict, list, Fluid)):
                        if acceptable is None or bk in acceptable:
                            broadcasted[bk] = bv
                v_copy = copy(v)
                v_copy.kwargs = broadcasted
                return v_copy
            if isinstance(v, Fluid):
                return v  # Reference stays as-is
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

        # Only set non-constructor attributes on configurable classes
        if getattr(target, "__confluid_configurable__", False):
            for k, v in merged.items():
                if params and k not in params:
                    member = getattr(target, k, None)
                    if isinstance(member, property) and member.fset is None:
                        continue
                    if getattr(member, "__confluid_ignore__", False):
                        continue
                    setattr(instance, k, v)

        # Apply broadcasting to constructor defaults not in config (e.g. lightning=Class(L.Trainer))
        for param_name in params:
            if param_name not in ctor:
                attr_val = getattr(instance, param_name, None)
                if attr_val is not None:
                    resolved = _resolve_value(attr_val)
                    if resolved is not attr_val:
                        setattr(instance, param_name, resolved)

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
        obj_context = context
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
