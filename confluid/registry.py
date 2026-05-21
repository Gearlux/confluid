import importlib
import logging
from typing import Any, Dict, Optional, Set, Type, Union

logger = logging.getLogger(__name__)


class ConfluidRegistry:
    """Central registry for configurable classes and objects."""

    def __init__(self) -> None:
        self._classes: Dict[str, Type[Any]] = {}
        self._objects: Dict[str, Any] = {}

    def register_class(self, cls: Type[Any], name: Optional[str] = None) -> Type[Any]:
        cls_name = name or cls.__name__
        self._classes[cls_name] = cls
        # Set markers for discovery
        try:
            setattr(cls, "__confluid_configurable__", True)
            setattr(cls, "__confluid_name__", cls_name)
        except (TypeError, AttributeError):
            # Built-in or immutable types don't allow attribute setting
            pass
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


def resolve_class(name: Union[str, type]) -> Optional[type]:
    """Resolve a class name to an actual Python type.

    Resolution order:
    1. If already a type, return as-is.
    2. Registry lookup by name.
    3. Module path import (e.g., "torch.optim.Adam").
    """
    if isinstance(name, type):
        return name

    if not isinstance(name, str):
        return None

    # Registry lookup
    cls = _registry.get_class(name)
    if cls is not None:
        return cls

    # Module path import (requires a dot in the name)
    if "." in name:
        module_path, class_attr = name.rsplit(".", 1)
        try:
            module = importlib.import_module(module_path)
            cls = getattr(module, class_attr)
            if isinstance(cls, type):
                return cls
        except (ImportError, AttributeError) as e:
            logger.debug(f"Failed to resolve '{name}' via module path: {e}")

    return None
