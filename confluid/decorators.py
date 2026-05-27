import functools
import inspect
from typing import Any, Callable, Optional, Type, TypeVar, Union, overload

from confluid.registry import get_registry

T = TypeVar("T")
C = TypeVar("C", bound=Type[Any])


@overload
def configurable(cls: C) -> C: ...


@overload
def configurable(
    *,
    name: Optional[str] = None,
    category: Optional[str] = None,
    validate: bool = True,
) -> Callable[[C], C]: ...


def configurable(
    cls: Optional[C] = None,
    *,
    name: Optional[str] = None,
    category: Optional[str] = None,
    validate: bool = True,
) -> Union[C, Callable[[C], C]]:
    """Mark a class as confluid-configurable and register it.

    Args:
        cls: The class to decorate.
        name: Optional override for the registration name.
        category: Optional discovery taxonomy bucket (e.g. ``"loss"``,
            ``"model"``, ``"trainer"``). Surfaces via
            :meth:`ConfluidRegistry.list_classes` and navigaitor's
            ``list_configurable_classes(category=...)`` MCP tool.
        validate: When ``True`` (default), wrap ``cls.__init__`` so it
            validates kwargs against :func:`confluid.to_pydantic` under the
            active :class:`confluid.validation.ValidationPolicy`. Set to
            ``False`` for classes whose ``__init__`` is intentionally untyped
            or where pydantic introspection would be wasteful (e.g. classes
            stored only as type references).
    """

    def decorator(c: C) -> C:
        # Mark the class with metadata
        setattr(c, "__confluid_configurable__", True)
        if name:
            setattr(c, "__confluid_name__", name)
        if category:
            setattr(c, "__confluid_category__", category)

        # Register in global registry
        get_registry().register_class(c, name=name, category=category)

        if validate:
            _wrap_init_with_validation(c)
        return c

    if cls is None:
        return decorator
    return decorator(cls)


def register(cls: Type[Any], *, name: Optional[str] = None, category: Optional[str] = None) -> Type[Any]:
    """Register a class (e.g., from a third-party library) as configurable.

    Args:
        cls: The class to register.
        name: Optional override for the registration name.
        category: Optional discovery taxonomy bucket.
    """
    # We don't modify third-party classes, just register them
    get_registry().register_class(cls, name=name, category=category)
    return cls


def ignore_config(func: T) -> T:
    """Decorator to mark a property or attribute to be ignored by configuration/overview."""
    setattr(func, "__confluid_ignore__", True)
    return func


def readonly_config(func: T) -> T:
    """Decorator to mark a property or attribute as read-only in configuration/overview."""
    setattr(func, "__confluid_readonly__", True)
    return func


def _wrap_init_with_validation(cls: Type[Any]) -> None:
    """Wrap ``cls.__init__`` so each call validates its kwargs.

    The wrapped ``__init__`` binds the call's positional and keyword
    arguments to the original signature, then routes the resulting mapping
    through :func:`confluid.validation.validate_kwargs` under the active
    :attr:`ValidationPolicy.init` mode. The original ``__init__`` runs
    afterwards regardless of the validation outcome — STRICT raises before
    the call, WARN logs and proceeds, OFF skips the check entirely.

    Idempotent: if ``cls.__init__`` is already wrapped (marker attribute
    set), this is a no-op so re-decorating a class doesn't double-wrap.
    Classes without their own ``__init__`` (i.e. inheriting from ``object``)
    are also skipped — there are no kwargs to validate.
    """
    original_init = cls.__dict__.get("__init__")
    if original_init is None or original_init is object.__init__:
        return
    if getattr(original_init, "__confluid_validated__", False):
        return

    try:
        sig = inspect.signature(original_init)
    except (TypeError, ValueError):
        # Signature not introspectable — leave the original __init__ alone.
        return

    @functools.wraps(original_init)
    def wrapper(self: Any, *args: Any, **kwargs: Any) -> None:
        # Lazy import to avoid a hard dependency cycle at decorator-import time.
        from confluid.validation import get_policy, validate_kwargs

        mode = get_policy().init
        if mode != "off":
            try:
                bound = sig.bind(self, *args, **kwargs)
            except TypeError:
                # ``sig.bind`` rejects unknown kwargs and missing required
                # positionals before the call reaches the body. Surface that
                # to pydantic so the user sees the structured ``extra="forbid"``
                # / required-field error from the schema — much more legible
                # than Python's native TypeError.
                validate_kwargs(cls, kwargs, mode)
            else:
                params = {k: v for k, v in bound.arguments.items() if k not in ("self", "cls")}
                # Drop *args / **kwargs bundles — pydantic schema covers
                # named parameters only.
                cleaned = {
                    name: value
                    for name, value in params.items()
                    if sig.parameters[name].kind
                    not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
                }
                validate_kwargs(cls, cleaned, mode)
        original_init(self, *args, **kwargs)

    setattr(wrapper, "__confluid_validated__", True)
    try:
        wrapper.__signature__ = sig  # type: ignore[attr-defined]
    except (AttributeError, TypeError):
        pass
    cls.__init__ = wrapper  # type: ignore[method-assign]
