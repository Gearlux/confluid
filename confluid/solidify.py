from typing import Any

from confluid.fluid import Fluid
from confluid.registry import get_registry


def solidify(obj: Any, **runtime_kwargs: Any) -> Any:
    """
    Ensure an object is instantiated and solidified.
    """
    from confluid.resolver import Resolver

    # 1. Resolve String References or Tags
    if isinstance(obj, str):
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
