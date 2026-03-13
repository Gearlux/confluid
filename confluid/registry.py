from typing import Any, Dict, Optional, Set, Type


class ConfluidRegistry:
    """Central registry for configurable classes and objects."""

    def __init__(self) -> None:
        self._classes: Dict[str, Type[Any]] = {}
        self._objects: Dict[str, Any] = {}

    def register_class(self, cls: Type[Any], name: Optional[str] = None) -> Type[Any]:
        cls_name = name or cls.__name__
        self._classes[cls_name] = cls
        # Set markers for discovery
        setattr(cls, "__confluid_configurable__", True)
        setattr(cls, "__confluid_name__", cls_name)
        return cls

    def get_class(self, name: str) -> Optional[Type[Any]]:
        # Handle both name and type-to-name lookup
        if not isinstance(name, str):
            name = getattr(name, "__confluid_name__", getattr(name, "__name__", str(name)))
        return self._classes.get(name)

    def is_configurable(self, obj: Any) -> bool:
        """Check if a class or object is marked as configurable."""
        if hasattr(obj, "__confluid_configurable__"):
            return True
        # Fallback to name lookup
        name = getattr(obj, "__confluid_name__", getattr(obj, "__name__", None))
        return name in self._classes if name else False

    def clear(self) -> None:
        self._classes.clear()
        self._objects.clear()

    def list_classes(self) -> Set[str]:
        """Return a set of all registered class names."""
        return set(self._classes.keys())

    def register_object(self, obj: Any, name: str) -> None:
        """Register an existing object instance."""
        self._objects[name] = obj

    def get_object(self, name: str) -> Optional[Any]:
        """Retrieve a registered object by name."""
        return self._objects.get(name)


# Global Singleton instance
_registry = ConfluidRegistry()


def get_registry() -> ConfluidRegistry:
    """Get the global Confluid registry instance."""
    return _registry
