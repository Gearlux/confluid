from typing import Any

from confluid.fluid import Fluid
from confluid.registry import get_registry


def solidify(obj: Any, **runtime_kwargs: Any) -> Any:
    """
    Ensure an object is instantiated and solidified.

    1. If 'obj' is a Fluid proxy, it instantiates it.
    2. If 'obj' is a string reference (e.g. "@Model"), it resolves and instantiates it.
    3. If 'obj' is a live instance, it checks _is_fluid(). If True, it calls _solidify().

    Args:
        obj: The object, Fluid, or reference to solidify.
        **runtime_kwargs: Optional overrides to use during instantiation.
    """
    from confluid.resolver import Resolver

    # 1. Handle String References
    if isinstance(obj, str) and obj.startswith("@"):
        resolver = Resolver()
        obj = resolver.resolve(obj)

    # 2. Handle Fluid proxies
    if isinstance(obj, Fluid):
        cls = obj.target
        if isinstance(cls, str):
            resolved_cls = get_registry().get_class(cls)
            if not resolved_cls:
                raise ValueError(f"Class '{cls}' not found in registry.")
            cls = resolved_cls

        merged_kwargs = {**obj.kwargs, **runtime_kwargs}
        obj = cls(**merged_kwargs)

    # 3. Handle live instances (The Fluid-Solid Protocol)
    # If the object has the protocol, ensure it is solid.
    if hasattr(obj, "_is_fluid") and callable(obj._is_fluid):
        if obj._is_fluid():
            if hasattr(obj, "_solidify") and callable(obj._solidify):
                obj._solidify()

    return obj
