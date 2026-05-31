import importlib
import logging
from typing import Any, Dict, Optional, Set, Type, Union

logger = logging.getLogger(__name__)


class ConfluidRegistry:
    """Central registry for configurable classes and objects."""

    def __init__(self) -> None:
        self._classes: Dict[str, Type[Any]] = {}
        self._objects: Dict[str, Any] = {}
        # Reverse indices: <value> → set of registered class names. Classes
        # without the corresponding tag aren't stored, so a ``None`` filter
        # falls through to the full set. ``task`` × ``role`` is the orthogonal
        # decomposition of ``category`` — a class tagged ``task="classification"``
        # + ``role="model"`` also derives ``category="classification_model"``.
        self._by_category: Dict[str, Set[str]] = {}
        self._by_group: Dict[str, Set[str]] = {}
        self._by_task: Dict[str, Set[str]] = {}
        self._by_role: Dict[str, Set[str]] = {}

    def register_class(
        self,
        cls: Type[Any],
        name: Optional[str] = None,
        category: Optional[str] = None,
        group: Optional[str] = None,
        task: Optional[str] = None,
        role: Optional[str] = None,
    ) -> Type[Any]:
        cls_name = name or cls.__name__
        self._classes[cls_name] = cls
        # Fall back to any tags already on the class — this keeps a re-register
        # (e.g. navigaitor's snapshot restore, which only forwards ``category``)
        # from dropping ``group`` / ``task`` / ``role`` set by the original ``@configurable``.
        category = category if category is not None else getattr(cls, "__confluid_category__", None)
        group = group if group is not None else getattr(cls, "__confluid_group__", None)
        task = task if task is not None else getattr(cls, "__confluid_task__", None)
        role = role if role is not None else getattr(cls, "__confluid_role__", None)
        # Set markers for discovery
        try:
            setattr(cls, "__confluid_configurable__", True)
            setattr(cls, "__confluid_name__", cls_name)
            if category is not None:
                setattr(cls, "__confluid_category__", category)
            if group is not None:
                setattr(cls, "__confluid_group__", group)
            if task is not None:
                setattr(cls, "__confluid_task__", task)
            if role is not None:
                setattr(cls, "__confluid_role__", role)
        except (TypeError, AttributeError):
            # Built-in or immutable types don't allow attribute setting
            pass
        if category is not None:
            self._by_category.setdefault(category, set()).add(cls_name)
        if group is not None:
            self._by_group.setdefault(group, set()).add(cls_name)
        if task is not None:
            self._by_task.setdefault(task, set()).add(cls_name)
        if role is not None:
            self._by_role.setdefault(role, set()).add(cls_name)
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
        self._by_category.clear()
        self._by_group.clear()
        self._by_task.clear()
        self._by_role.clear()

    def list_classes(
        self,
        category: Optional[str] = None,
        group: Optional[str] = None,
        task: Optional[str] = None,
        role: Optional[str] = None,
    ) -> Set[str]:
        """Return registered class names, optionally filtered by ``category`` / ``group`` / ``task`` / ``role``.

        All filters are ``None`` by default (returns every registered name).
        When several are given they INTERSECT (e.g. ``task="classification",
        role="model"`` returns only classification models — equivalent to
        ``category="classification_model"``). A filter no class matches returns
        the empty set rather than raising, so discovery callers can probe freely.
        """
        result: Optional[Set[str]] = None
        for index, value in (
            (self._by_category, category),
            (self._by_group, group),
            (self._by_task, task),
            (self._by_role, role),
        ):
            if value is None:
                continue
            names = set(index.get(value, set()))
            result = names if result is None else (result & names)
        if result is None:
            return set(self._classes.keys())
        return result

    def list_categories(self) -> Set[str]:
        """Return the set of category names that have at least one registered class."""
        return set(self._by_category.keys())

    def list_groups(self) -> Set[str]:
        """Return the set of group names that have at least one registered class."""
        return set(self._by_group.keys())

    def list_tasks(self) -> Set[str]:
        """Return the set of task names that have at least one registered class."""
        return set(self._by_task.keys())

    def list_roles(self) -> Set[str]:
        """Return the set of role names that have at least one registered class."""
        return set(self._by_role.keys())

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
