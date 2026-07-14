import importlib
from typing import Any, Callable, Dict, Optional, Sequence, Set, Tuple, Union, cast

from loggair import get_logger

logger = get_logger("confluid.registry")


class ConfluidRegistry:
    """Central registry for configurable classes and objects."""

    def __init__(self) -> None:
        # Values are configurable CALLABLES — classes OR builder/factory
        # functions (see the "A Target May Be ANY Callable" mandate).
        self._classes: Dict[str, Callable[..., Any]] = {}
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
        cls: Callable[..., Any],
        name: Optional[str] = None,
        category: Optional[str] = None,
        group: Optional[str] = None,
        task: Optional[str] = None,
        role: Optional[str] = None,
        lazy: bool = False,
        random: bool = False,
        constant: bool = False,
        eager: bool = False,
        strict_typing: bool = False,
        display_name: Optional[str] = None,
        no_broadcast: bool = False,
        no_capture: bool = False,
        broadcast_attrs: Optional[Sequence[str]] = None,
    ) -> Callable[..., Any]:
        """Register ``cls`` and stamp its ``__confluid_*__`` marks — the ONE stamping authority.

        ``@configurable`` delegates every mark here (single source of truth);
        ``register()`` forwards only the discovery subset, and a direct call
        (e.g. navigaitor's snapshot restore) may forward as little as
        ``name``/``category`` — each mark falls back to the class's EXISTING
        mark when the argument is unset, so a partial re-register never drops
        tags stamped earlier. ``random``/``constant``/``eager``/
        ``strict_typing``/``display_name``/``no_broadcast``/``no_capture``/
        ``broadcast_attrs`` are stamp-only (no reverse index).
        """
        cls_name = name or cls.__name__
        self._classes[cls_name] = cls
        # Fall back to any tags already on the class — this keeps a re-register
        # (e.g. navigaitor's snapshot restore, which only forwards ``category``)
        # from dropping ``group`` / ``task`` / ``role`` set by the original ``@configurable``.
        category = category if category is not None else getattr(cls, "__confluid_category__", None)
        group = group if group is not None else getattr(cls, "__confluid_group__", None)
        task = task if task is not None else getattr(cls, "__confluid_task__", None)
        role = role if role is not None else getattr(cls, "__confluid_role__", None)
        lazy = lazy or bool(getattr(cls, "__confluid_lazy__", False))
        random = random or bool(getattr(cls, "__confluid_random__", False))
        constant = constant or bool(getattr(cls, "__confluid_constant__", False))
        eager = eager or bool(getattr(cls, "__confluid_eager__", False))
        strict_typing = strict_typing or bool(getattr(cls, "__confluid_strict_typing__", False))
        display_name = display_name if display_name is not None else getattr(cls, "__confluid_display_name__", None)
        no_broadcast = no_broadcast or bool(getattr(cls, "__confluid_no_broadcast__", False))
        no_capture = no_capture or bool(getattr(cls, "__confluid_no_capture__", False))
        # ``()`` is a DELIBERATE declaration ("no post-init broadcast attrs"),
        # distinct from ``None`` (undeclared) — so the fallback tests ``is not None``.
        effective_broadcast_attrs: Optional[Tuple[str, ...]] = (
            tuple(broadcast_attrs)
            if broadcast_attrs is not None
            else getattr(cls, "__confluid_broadcast_attrs__", None)
        )
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
            if lazy:
                # A "lazy" class is one whose constructed value should stay
                # deferred (a LazyClass / runtime-injected slot — e.g. an
                # optimizer needing ``params``). Consumers (FluxStudio object
                # nodes) read this to emit a deferred ``LazyClass`` instead of a
                # live instance.
                setattr(cls, "__confluid_lazy__", True)
            if random:
                setattr(cls, "__confluid_random__", True)
            if constant:
                setattr(cls, "__confluid_constant__", True)
            if eager:
                # An "eager" class does REAL WORK in its constructor from its
                # params (a plain Python class, outside the lazy-init/zero-arg
                # convention). Read by configure()'s staleness warning — a
                # post-construction setattr of a ctor param can't re-run that
                # work.
                setattr(cls, "__confluid_eager__", True)
            if strict_typing:
                setattr(cls, "__confluid_strict_typing__", True)
            if display_name is not None:
                setattr(cls, "__confluid_display_name__", display_name)
            if no_broadcast:
                setattr(cls, "__confluid_no_broadcast__", True)
            if no_capture:
                # Opt-out of ctor-kwargs capture (``__confluid_kwargs__``): the
                # validation wrap AND the engine's flow re-stamp both consult
                # this — for classes whose ctor args are heavy/disposable and
                # must not be kept alive for the instance lifetime.
                setattr(cls, "__confluid_no_capture__", True)
            if effective_broadcast_attrs is not None:
                setattr(cls, "__confluid_broadcast_attrs__", effective_broadcast_attrs)
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

    def get_class(self, name: str) -> Optional[Callable[..., Any]]:
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


def resolve_class(name: Union[str, type]) -> Optional[Callable[..., Any]]:
    """Resolve a name to a Python **callable** target (a class OR a plain function).

    Resolution order:
    1. If already a type, return as-is.
    2. Registry lookup by name.
    3. Module path import (e.g., ``"torch.optim.Adam"`` — a class — or
       ``"torchvision.models.detection.fasterrcnn_resnet50_fpn"`` — a builder
       *function*).

    A ``!class:`` / ``!lazy:`` target may be any callable, not just a class:
    factory/builder functions (torchvision's ``fasterrcnn_resnet50_fpn``,
    ``timm.create_model``, …) are first-class targets. The module-path branch
    therefore accepts any **callable** attribute (class or function), not only
    ``isinstance(_, type)``. (``flow()`` then builds it by introspecting the
    callable's own signature — see :func:`confluid.fluid.flow`.)
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
            attr = getattr(module, class_attr)
            # Accept any callable target — a class OR a plain builder function.
            if isinstance(attr, type) or callable(attr):
                return cast(Callable[..., Any], attr)
        except (ImportError, AttributeError) as e:
            logger.debug(f"Failed to resolve '{name}' via module path: {e}")

    return None
