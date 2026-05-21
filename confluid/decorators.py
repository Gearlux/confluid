from typing import Any, Callable, Optional, Type, TypeVar, Union, overload

from confluid.registry import get_registry

T = TypeVar("T")
C = TypeVar("C", bound=Type[Any])


@overload
def configurable(cls: C) -> C: ...


@overload
def configurable(*, name: Optional[str] = None) -> Callable[[C], C]: ...


def configurable(
    cls: Optional[C] = None, *, name: Optional[str] = None
) -> Union[C, Callable[[C], C]]:
    """
    Decorator to mark a class as configurable.

    Args:
        cls: The class to decorate.
        name: Optional override for the registration name.
    """

    def decorator(c: C) -> C:
        # Mark the class with metadata
        setattr(c, "__confluid_configurable__", True)
        if name:
            setattr(c, "__confluid_name__", name)

        # Register in global registry
        get_registry().register_class(c, name=name)
        return c

    if cls is None:
        return decorator
    return decorator(cls)


def register(cls: Type[Any], *, name: Optional[str] = None) -> Type[Any]:
    """
    Register a class (e.g., from a third-party library) as configurable.

    Args:
        cls: The class to register.
        name: Optional override for the registration name.
    """
    # We don't modify third-party classes, just register them
    get_registry().register_class(cls, name=name)
    return cls


def ignore_config(func: T) -> T:
    """Decorator to mark a property or attribute to be ignored by configuration/overview."""
    setattr(func, "__confluid_ignore__", True)
    return func


def readonly_config(func: T) -> T:
    """Decorator to mark a property or attribute as read-only in configuration/overview."""
    setattr(func, "__confluid_readonly__", True)
    return func
