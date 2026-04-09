from typing import Any, Dict, Optional, Type, Union

from confluid.registry import get_registry, resolve_class


class Fluid:
    """Base class for all deferred/flowing configuration objects."""

    __confluid_configurable__ = True

    def __init__(self, target: Any, automatic: bool = False, **kwargs: Any) -> None:
        self.target = target
        self.kwargs = kwargs
        self.context: Optional[Dict[str, Any]] = None
        # Internal flag: True if created by YAML tag (!class/!ref), False if created in code.
        self.automatic = automatic

    def __repr__(self) -> str:
        name = self.target if isinstance(self.target, str) else getattr(self.target, "__name__", str(self.target))
        return f"{self.__class__.__name__}({name}, automatic={self.automatic}, {self.kwargs})"


class Class(Fluid):
    """Represents a deferred class initializer."""

    def __init__(self, target: Union[Type[Any], str], automatic: bool = False, **kwargs: Any) -> None:
        super().__init__(target, automatic=automatic, **kwargs)


class Reference(Fluid):
    """Represents a late-bound reference to another part of the config."""

    def __init__(self, path: str, automatic: bool = False, **kwargs: Any) -> None:
        super().__init__(path, automatic=automatic, **kwargs)


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

    # 3. Class objects — deferred initializers
    if isinstance(obj, Class):
        target = obj.target
        # Resolve string targets to actual Python types
        if isinstance(target, str):
            resolved = resolve_class(target)
            if resolved is None:
                raise ValueError(f"Cannot resolve class: {target}")
            target = resolved

        base_kwargs = {**obj.kwargs, **runtime_kwargs}

        if get_registry().is_configurable(target):
            # Ensure the class is registered so materialize can find it
            cls_name = getattr(target, "__confluid_name__", target.__name__)
            if not get_registry().get_class(cls_name):
                get_registry().register_class(target, name=cls_name)
            marker = {"_confluid_class_": cls_name, **base_kwargs}
            return materialize(marker, context=context)
        else:
            return target(**base_kwargs)

    # 4. Bare type passed directly (e.g., flow(MyClass, x=1))
    if isinstance(obj, type):
        if get_registry().is_configurable(obj):
            cls_name = getattr(obj, "__confluid_name__", obj.__name__)
            marker = {"_confluid_class_": cls_name, **runtime_kwargs}
            return materialize(marker, context=context)
        else:
            return obj(**runtime_kwargs)

    # 5. Reference objects — late-bound config paths
    if isinstance(obj, Reference):
        resolver = Resolver(context=context)
        resolved = resolver.resolve(f"!ref:{obj.target}")
        if resolved == f"!ref:{obj.target}":
            raise ValueError(f"Failed to resolve Reference: {obj.target}")
        return flow(resolved, **runtime_kwargs)

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
