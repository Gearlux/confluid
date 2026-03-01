import threading
from typing import Any, Dict, Optional, Set, Type


class Registry:
    """A thread-safe registry for configurable classes and objects."""

    def __init__(self) -> None:
        self._classes: Dict[str, Type[Any]] = {}
        self._objects: Dict[str, Any] = {}
        self._lock = threading.Lock()

    def register_class(self, cls: Type[Any], name: Optional[str] = None) -> None:
        """Register a class as configurable."""
        reg_name = name or cls.__name__
        with self._lock:
            self._classes[reg_name] = cls

    def register_object(self, obj: Any, name: str) -> None:
        """Register an existing object instance."""
        with self._lock:
            self._objects[name] = obj

    def get_class(self, name: str) -> Optional[Type[Any]]:
        """Retrieve a registered class by name."""
        return self._classes.get(name)

    def get_object(self, name: str) -> Optional[Any]:
        """Retrieve a registered object by name."""
        return self._objects.get(name)

    def list_classes(self) -> Set[str]:
        """Return a set of all registered class names."""
        return set(self._classes.keys())

    def clear(self) -> None:
        """Clear the registry (mainly for testing)."""
        with self._lock:
            self._classes.clear()
            self._objects.clear()


# Global registry instance
_global_registry = Registry()


def get_registry() -> Registry:
    """Get the global registry instance."""
    return _global_registry
