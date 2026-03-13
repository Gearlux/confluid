from typing import Any, Type, Union

from confluid.registry import get_registry


class Fluid:
    """Represents a potential object that can be instantiated on demand."""

    def __init__(self, target: Union[Type[Any], str], **kwargs: Any) -> None:
        self.target = target
        self.kwargs = kwargs

    def __repr__(self) -> str:
        name = self.target if isinstance(self.target, str) else self.target.__name__
        return f"Fluid({name}, {self.kwargs})"


def flow(obj: Any, **runtime_kwargs: Any) -> Any:
    """
    Ensure an object is instantiated and flowing.

    If 'obj' is a Fluid, it instantiates it.
    If 'obj' is a string reference (e.g. "!class:Model"), it resolves and instantiates it.
    If 'obj' is already an instance, it returns it as is.

    Args:
        obj: The object, Fluid, or reference to flow.
        **runtime_kwargs: Optional overrides to use during instantiation.
    """
    from confluid.resolver import Resolver

    # 1. Handle already instantiated objects (Idempotency)
    if hasattr(obj.__class__, "__confluid_configurable__") and not isinstance(obj, (Fluid, str)):
        return obj

    # 2. Handle Fluid objects
    if isinstance(obj, Fluid):
        cls = obj.target
        if isinstance(cls, str):
            resolved_cls = get_registry().get_class(cls)
            if not resolved_cls:
                raise ValueError(f"Class '{cls}' not found in registry.")
            cls = resolved_cls

        merged_kwargs = {**obj.kwargs, **runtime_kwargs}
        return cls(**merged_kwargs)

    # 3. Handle String References
    if isinstance(obj, str) and (obj.startswith("!class:") or obj.startswith("!ref:")):
        resolver = Resolver()
        # The resolver already handles instantiation for tags
        return resolver.resolve(obj)

    # 4. Fallback for primitives or non-configurable objects
    return obj
