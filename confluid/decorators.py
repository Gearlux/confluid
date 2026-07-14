import functools
import inspect
from typing import Any, Callable, Dict, Optional, Sequence, Type, TypeVar, Union, cast, overload

from confluid.exceptions import ConfigurableDefinitionError
from confluid.registry import get_registry

T = TypeVar("T")
# ``C`` was ``bound=Type[Any]`` (class-only); now ``bound=Callable[..., Any]`` so
# ``@configurable`` / ``register`` accept a class OR a plain builder/factory
# function (both are callable), returning the same type. See the "A Target May
# Be ANY Callable" mandate.
C = TypeVar("C", bound=Callable[..., Any])


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
    eager: bool = False,
    broadcast: bool = True,
    broadcast_attrs: Optional[Sequence[str]] = None,
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
    eager: bool = False,
    broadcast: bool = True,
    broadcast_attrs: Optional[Sequence[str]] = None,
    strict_typing: bool = False,
    display_name: Optional[str] = None,
) -> Union[C, Callable[[C], C]]:
    """Mark a class OR callable as confluid-configurable and register it.

    Works on a class (its ``__init__`` is wrapped for validation) or on a
    plain builder/factory **function** (the function's CALL is wrapped for
    validation — the callable analogue). See the "A Target May Be ANY
    Callable" mandate.

    Args:
        cls: The class or callable to decorate.
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
        eager: When ``True``, stamp ``__confluid_eager__`` on the class.
            Declares that the constructor does REAL WORK from its params
            (a plain/normal Python class, deliberately outside the
            lazy-init/zero-arg convention). Its runtime reader is the
            ``configure()`` staleness warning: setting a constructor-param
            attribute on an eager instance post-construction warns that the
            ``__init__`` work will NOT re-run (derived state may be stale).
            Body attributes stay freely reconfigurable, silently. Orthogonal
            to ``random``/``constant``; does not gate dump round-trip (ctor
            kwargs are captured universally).
        broadcast: When ``False``, stamp ``__confluid_no_broadcast__`` on the
            class: instances never receive BARE-key broadcasts (a top-level
            ``name:``-style key matching by name alone). Addressed
            ``ClassName:`` / instance-name blocks and ``configure()`` still set
            attributes normally. The param-level counterpart is the
            ``NoBroadcast[T]`` annotation (``confluid.no_broadcast``).
        broadcast_attrs: Optional explicit declaration of post-init
            ``__init__``-body attribute names that must stay broadcastable.
            Stamped as ``__confluid_broadcast_attrs__`` (a tuple) and UNIONED
            with the AST-scanned body-slot names by the broadcasting engine —
            declaring can never LOSE scanned attrs. In dev checkouts the scan
            already finds every ``self.x = …`` slot, so the declaration is
            redundant; in compiled/frozen/zip deployments ``inspect.getsource``
            fails and the scan is EMPTY — there the declaration is the ONLY way
            post-init attrs remain broadcast targets (the engine warns once per
            class when it can't scan an undeclared ``@configurable`` class).
            An explicit empty sequence (``broadcast_attrs=[]``) declares "no
            post-init broadcast attrs" and silences that warning.
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
        # A @configurable FUNCTION (not a class) gets its CALL validated by a
        # functools.wraps wrapper — the callable analogue of the class
        # __init__ wrap below. Markers + registration then land on the WRAPPER
        # so it is the object resolve_class/flow build (YAML materialization of
        # the function then validates under policy.yaml too).
        if validate and not isinstance(c, type) and callable(c):
            c = _wrap_callable_with_validation(c)

        # Register + stamp: the registry is the SINGLE stamping authority for
        # every __confluid_*__ mark (see register_class's docstring).
        get_registry().register_class(
            c,
            name=name,
            category=effective_category,
            group=group,
            task=task,
            role=role,
            lazy=lazy,
            random=random,
            constant=constant,
            eager=eager,
            strict_typing=strict_typing,
            display_name=display_name,
            no_broadcast=not broadcast,
            broadcast_attrs=broadcast_attrs,
        )

        if validate and isinstance(c, type):
            _wrap_init_with_validation(c)
        return c

    if cls is None:
        return decorator
    return decorator(cls)


def register(
    cls: C,
    *,
    name: Optional[str] = None,
    category: Optional[str] = None,
    group: Optional[str] = None,
    task: Optional[str] = None,
    role: Optional[str] = None,
    lazy: bool = False,
    eager: bool = False,
) -> C:
    """Register a class OR callable (e.g. a third-party class or builder function) as configurable.

    Discovery-registration only — unlike :func:`configurable`, ``register`` does
    NOT wrap validation (for either a class ``__init__`` or a callable's call);
    it just stamps the discovery markers and indexes the name. Use it for
    off-the-shelf classes / builder functions you don't own.

    Args:
        cls: The class or callable to register.
        name: Optional override for the registration name.
        category: Optional discovery taxonomy bucket.
        group: Optional path-like presentation sub-grouping (see :func:`configurable`).
        task: Optional ML task (see :func:`configurable`).
        role: Optional slot role (see :func:`configurable`).
        lazy: When ``True``, stamp ``__confluid_lazy__`` — the constructed value
            should stay deferred (a runtime-injection slot like a torch optimizer
            needing ``params=`` / a DataLoader needing ``dataset=``). See
            :func:`configurable`.
        eager: When ``True``, stamp ``__confluid_eager__`` — the constructor
            does real work from its params (a plain Python class). Enables the
            ``configure()`` staleness warning. See :func:`configurable`.
    """
    effective_category = category or (f"{task}_{role}" if task and role else None)
    # ``register_class`` stamps the discovery markers (incl. ``__confluid_lazy__``)
    # on the class — it tolerates immutable built-ins via try/except.
    get_registry().register_class(
        cls, name=name, category=effective_category, group=group, task=task, role=role, lazy=lazy, eager=eager
    )
    return cls


def ignore_config(func: T) -> T:
    """Decorator to mark a property or attribute to be ignored by configuration/overview."""
    setattr(func, "__confluid_ignore__", True)
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


def _wrap_callable_with_validation(func: C) -> C:
    """Wrap a ``@configurable`` FUNCTION so each CALL validates its kwargs.

    The callable analogue of :func:`_wrap_init_with_validation` (no ``self``):
    it binds the call's positional + keyword arguments to ``func``'s signature
    and routes the resulting mapping through
    :func:`confluid.validation.validate_kwargs` under the active
    :attr:`ValidationPolicy.init` mode, then invokes ``func`` — STRICT raises
    before the call, WARN logs and proceeds, OFF skips the check.

    Returns a :func:`functools.wraps` wrapper (a NEW object) so the caller /
    module name rebinds to the validated callable; signature, annotations and
    ``__name__`` are preserved (``inspect.signature`` / ``get_type_hints``
    follow ``__wrapped__``), so ``to_pydantic`` / ``resolve_class`` keep
    working. A non-introspectable callable is returned unwrapped. Idempotent
    via the ``__confluid_validated__`` marker.
    """
    if getattr(func, "__confluid_validated__", False):
        return func
    try:
        sig = inspect.signature(func)
    except (TypeError, ValueError):
        # Signature not introspectable — leave the callable alone.
        return func

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        # Lazy import to avoid a hard dependency cycle at decorator-import time.
        from confluid.validation import get_policy, validate_kwargs

        mode = get_policy().init
        if mode != "off":
            try:
                bound = sig.bind(*args, **kwargs)
            except TypeError:
                # ``sig.bind`` rejects unknown kwargs / missing required args
                # before the call — surface that to pydantic so the user sees
                # the structured ``extra="forbid"`` / required-field error.
                validate_kwargs(func, kwargs, mode)
            else:
                cleaned = {
                    param_name: value
                    for param_name, value in bound.arguments.items()
                    if sig.parameters[param_name].kind
                    not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
                }
                validate_kwargs(func, cleaned, mode)
        return func(*args, **kwargs)

    setattr(wrapper, "__confluid_validated__", True)
    return cast(C, wrapper)


def _wrap_init_with_validation(cls: Type[Any]) -> None:
    """Wrap ``cls.__init__`` so each call validates AND captures its kwargs.

    The wrapped ``__init__`` binds the call's positional and keyword
    arguments to the original signature, then routes the resulting mapping
    through :func:`confluid.validation.validate_kwargs` under the active
    :attr:`ValidationPolicy.init` mode. The original ``__init__`` runs
    afterwards regardless of the validation outcome — STRICT raises before
    the call, WARN logs and proceeds, OFF skips the check entirely.

    After the original ``__init__`` returns, the bound named kwargs (the
    explicitly-passed params only — no defaults applied, positionals
    normalized to names, ``*args``/``**kwargs`` bundles dropped) are stamped
    on the instance as ``__confluid_kwargs__``. This is what lets ``dump()``
    round-trip an EAGER class whose constructor transforms its params instead
    of storing them verbatim as same-named attributes (the dumper prefers the
    live attribute and falls back to this capture). Notes: the capture runs
    even when validation mode is ``off``; values are held BY REFERENCE for
    the instance lifetime (the same lifetime a param-storing class gives
    them); ``__slots__``/frozen instances that reject the setattr degrade
    gracefully to the live-attribute dump heuristic; on the YAML path the
    engine re-stamps with the resolved ctor dict afterwards (last write wins).

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
        cleaned: Optional[Dict[str, Any]] = None
        # The bind runs regardless of validation mode — the capture below
        # needs it even when validation is off.
        try:
            bound = sig.bind(self, *args, **kwargs)
        except TypeError:
            # ``sig.bind`` rejects unknown kwargs and missing required
            # positionals before the call reaches the body. Surface that
            # to pydantic so the user sees the structured ``extra="forbid"``
            # / required-field error from the schema — much more legible
            # than Python's native TypeError. (No capture — the call below
            # is about to fail with the same TypeError anyway.)
            if mode != "off":
                validate_kwargs(cls, kwargs, mode)
        else:
            params = {k: v for k, v in bound.arguments.items() if k not in ("self", "cls")}
            # Drop *args / **kwargs bundles — pydantic schema covers
            # named parameters only.
            cleaned = {
                name: value
                for name, value in params.items()
                if sig.parameters[name].kind not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
            }
            if mode != "off":
                validate_kwargs(cls, cleaned, mode)
        original_init(self, *args, **kwargs)
        if cleaned is not None:
            # Capture the explicitly-passed ctor kwargs so dump() can
            # round-trip an eager class (see the function docstring). Stamped
            # AFTER the original __init__ so a same-named assignment in the
            # body can't clobber it, and so in a configurable-subclass chain
            # the most-derived wrapper stamps last and wins.
            try:
                self.__confluid_kwargs__ = cleaned
            except (TypeError, AttributeError):
                pass  # __slots__/frozen instances reject arbitrary attrs

    setattr(wrapper, "__confluid_validated__", True)
    try:
        wrapper.__signature__ = sig  # type: ignore[attr-defined]
    except (AttributeError, TypeError):
        pass
    cls.__init__ = wrapper  # type: ignore[method-assign]
