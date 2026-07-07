import functools
import inspect
from typing import Any, Callable, Optional, Type, TypeVar, Union, overload

from confluid.exceptions import ConfigurableDefinitionError
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
    group: Optional[str] = None,
    task: Optional[str] = None,
    role: Optional[str] = None,
    lazy: bool = False,
    validate: bool = True,
    random: bool = False,
    constant: bool = False,
    strict_typing: bool = False,
    display_name: Optional[str] = None,
) -> Callable[[C], C]: ...


def configurable(
    cls: Optional[C] = None,
    *,
    name: Optional[str] = None,
    category: Optional[str] = None,
    group: Optional[str] = None,
    task: Optional[str] = None,
    role: Optional[str] = None,
    lazy: bool = False,
    validate: bool = True,
    random: bool = False,
    constant: bool = False,
    strict_typing: bool = False,
    display_name: Optional[str] = None,
) -> Union[C, Callable[[C], C]]:
    """Mark a class as confluid-configurable and register it.

    Args:
        cls: The class to decorate.
        name: Optional override for the registration name.
        category: Optional discovery taxonomy bucket (e.g. ``"loss"``,
            ``"model"``, ``"trainer"``). Surfaces via
            :meth:`ConfluidRegistry.list_classes` and navigaitor's
            ``list_configurable_classes(category=...)`` MCP tool.
        group: Optional free-form, path-like sub-grouping WITHIN a category
            (e.g. ``"numpy"``, ``"fft/numpy"``, ``"segmentation"``). Unlike
            ``category`` / ``task`` / ``role`` (which gate *what* is offered),
            ``group`` only organises presentation: FluxStudio nests a node's
            palette folder as ``<Package>/<Category>/<group>``. It is NOT part
            of the discovery contract — an absent group simply means the node
            sits directly under ``<Package>/<Category>``.
        task: Optional ML task this class belongs to (``"classification"`` /
            ``"segmentation"`` / ``"detection"``). With ``role`` it is the
            orthogonal decomposition of ``category``: passing both also derives
            ``category=f"{task}_{role}"`` so existing category-based discovery
            keeps working, while ``list_classes(task=..., role=...)`` enables
            navigaitor's scan-and-generate task surfaces.
        role: Optional slot role this class fills for its task (``"model"`` /
            ``"loss"`` / ``"dataset"`` / ``"metric"`` / ``"trainer"``).
        lazy: When ``True``, stamp ``__confluid_lazy__`` on the class. Marks a
            class whose constructed value should stay **deferred** — a
            runtime-injection slot (e.g. an optimizer needing ``params=`` or a
            DataLoader needing ``dataset=``). Consumers that compose configs read
            it to emit a ``LazyClass`` (deferred) rather than a live instance —
            notably FluxStudio's object nodes, which feed a runnable's deferred
            body slots. Independent of ``category``/``task``/``role``.
        random: When ``True``, stamp ``__confluid_random__`` on the class.
            Marks a class whose output is non-deterministic (e.g. stochastic
            augmentation ops). FluxStudio uses this to inject ``IS_CHANGED``
            on the generated ComfyUI node so downstream nodes (Preview Image,
            etc.) always re-execute rather than serving a cached output.
        constant: When ``True``, stamp ``__confluid_constant__`` on the class.
            Marks a class whose instances (and declared ``@output`` properties)
            are a PURE function of the constructor config — no I/O, no sample
            input, no hidden state. Exporters may fold/hoist such a value
            producer into a static config: FluxStudio's ops-export hoists a
            constant value node as a top-level ``!class:`` entry and rewires
            its consumers via dotted ``!ref:<name>.<output>`` instead of
            dropping the wired values. Mutually exclusive with ``random``.
        strict_typing: When ``True``, stamp ``__confluid_strict_typing__`` on
            the class. FluxStudio uses this to render ``Union[int, str]``
            constructor params as two optional sockets — ``{name}_samples``
            (INT, full range) and ``{name}_duration`` (STRING) — instead of
            the default single STRING widget. Whichever socket is
            connected/filled wins; if neither, the constructor default applies.
        display_name: Optional human-readable label for UI surfaces (e.g.
            FluxStudio palette). Stamped as ``__confluid_display_name__``.
            Falls back to the class name when absent.
        validate: When ``True`` (default), wrap ``cls.__init__`` so it
            validates kwargs against :func:`confluid.to_pydantic` under the
            active :class:`confluid.validation.ValidationPolicy`. Set to
            ``False`` for classes whose ``__init__`` is intentionally untyped
            or where pydantic introspection would be wasteful (e.g. classes
            stored only as type references).
    """
    if constant and random:
        raise ConfigurableDefinitionError(
            "configurable(): 'constant=True' and 'random=True' are contradictory — "
            "a constant's outputs are a pure function of its config, a random class's are not."
        )

    # ``task`` + ``role`` derive ``category`` when an explicit one isn't given,
    # so a single tag feeds both the orthogonal (task/role) and legacy
    # (category) discovery paths.
    effective_category = category or (f"{task}_{role}" if task and role else None)

    def decorator(c: C) -> C:
        # Mark the class with metadata
        setattr(c, "__confluid_configurable__", True)
        if name:
            setattr(c, "__confluid_name__", name)
        if effective_category:
            setattr(c, "__confluid_category__", effective_category)
        if group:
            setattr(c, "__confluid_group__", group)
        if task:
            setattr(c, "__confluid_task__", task)
        if role:
            setattr(c, "__confluid_role__", role)
        if lazy:
            setattr(c, "__confluid_lazy__", True)
        if random:
            setattr(c, "__confluid_random__", True)
        if constant:
            setattr(c, "__confluid_constant__", True)
        if strict_typing:
            setattr(c, "__confluid_strict_typing__", True)
        if display_name:
            setattr(c, "__confluid_display_name__", display_name)

        # Register in global registry
        get_registry().register_class(
            c, name=name, category=effective_category, group=group, task=task, role=role, lazy=lazy
        )

        if validate:
            _wrap_init_with_validation(c)
        return c

    if cls is None:
        return decorator
    return decorator(cls)


def register(
    cls: Type[Any],
    *,
    name: Optional[str] = None,
    category: Optional[str] = None,
    group: Optional[str] = None,
    task: Optional[str] = None,
    role: Optional[str] = None,
    lazy: bool = False,
) -> Type[Any]:
    """Register a class (e.g., from a third-party library) as configurable.

    Args:
        cls: The class to register.
        name: Optional override for the registration name.
        category: Optional discovery taxonomy bucket.
        group: Optional path-like presentation sub-grouping (see :func:`configurable`).
        task: Optional ML task (see :func:`configurable`).
        role: Optional slot role (see :func:`configurable`).
        lazy: When ``True``, stamp ``__confluid_lazy__`` — the constructed value
            should stay deferred (a runtime-injection slot like a torch optimizer
            needing ``params=`` / a DataLoader needing ``dataset=``). See
            :func:`configurable`.
    """
    effective_category = category or (f"{task}_{role}" if task and role else None)
    # ``register_class`` stamps the discovery markers (incl. ``__confluid_lazy__``)
    # on the class — it tolerates immutable built-ins via try/except.
    get_registry().register_class(
        cls, name=name, category=effective_category, group=group, task=task, role=role, lazy=lazy
    )
    return cls


def ignore_config(func: T) -> T:
    """Decorator to mark a property or attribute to be ignored by configuration/overview."""
    setattr(func, "__confluid_ignore__", True)
    return func


def readonly_config(func: T) -> T:
    """Decorator to mark a property or attribute as read-only in configuration/overview."""
    setattr(func, "__confluid_readonly__", True)
    return func


def output(func: T) -> T:
    """Mark a read-only ``@property`` getter as a declared OUTPUT of a Runnable class.

    Apply UNDER ``@property`` so it decorates the underlying getter (``fget``),
    NOT the ``property`` object::

        @property
        @output
        def trained_model(self) -> nn.Module: ...

    Consumers (FluxStudio runnable nodes, navigaitor's form-spec) read
    :func:`confluid.output_specs` to expose these as node OUTPUT sockets. An
    ``@output`` property is read-only / derived, so it is already excluded from
    config introspection (``to_pydantic`` skips setter-less properties) — it never
    becomes a config knob and round-trips cleanly. The complement on the INPUT
    side is :data:`confluid.Mandatory`; together they define the I/O contract.
    """
    setattr(func, "__confluid_output__", True)
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
