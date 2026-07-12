from typing import Any, Callable, Generic, Optional, Tuple, Union

from typing_extensions import TypeVar

YamlLoc = Tuple[Optional[str], int, int]
"""``(filename or None, line, column)`` — 1-based YAML source location."""

T = TypeVar("T", default=Any)
"""Phantom type parameter for ``Lazy[T]`` — the eventual flow()'d target type.

Defaults to ``Any`` (PEP 696) so a bare ``LazyClass(Foo)`` infers ``Lazy[Any]``
(not ``Lazy[Never]``) and needs no annotation, while ``LazyClass[Metric]`` stays
available to document intent."""


class Fluid:
    """Base class for all deferred configuration objects."""

    __confluid_configurable__ = True

    def __init__(self, target: Any, **kwargs: Any) -> None:
        self.target = target
        self.kwargs = kwargs
        # Set by the YAML loader (see ``confluid.loader._stamp``) so error
        # messages can point at the offending YAML node. Not part of the
        # serialization contract — copy()/dump() preserve it best-effort.
        self._yaml_loc: Optional[YamlLoc] = None

    def __repr__(self) -> str:
        name = self.target if isinstance(self.target, str) else getattr(self.target, "__name__", str(self.target))
        return f"{self.__class__.__name__}({name}, {self.kwargs})"


def format_yaml_loc(obj: Any) -> str:
    """Render a Fluid's YAML source location as ``"path/to.yaml:line:col"`` or ``""``.

    Returns an empty string if ``obj`` is not a Fluid or carries no location
    (e.g. constructed in code rather than loaded from YAML).
    """
    loc: Optional[YamlLoc] = getattr(obj, "_yaml_loc", None)
    if loc is None:
        return ""
    filename, line, col = loc
    head = filename if filename else "<config>"
    return f"{head}:{line}:{col}"


class Class(Fluid):
    """Deferred class initializer. Stays deferred until explicitly flow()'d."""

    def __init__(self, target: Union[Callable[..., Any], str], **kwargs: Any) -> None:
        super().__init__(target, **kwargs)


class Instance(Fluid):
    """Instant class initializer. Materialized immediately by materialize()/flow()."""

    def __init__(self, target: Union[Callable[..., Any], str], **kwargs: Any) -> None:
        super().__init__(target, **kwargs)


class Reference(Fluid):
    """Late-bound reference to another part of the config."""

    def __init__(self, path: str, **kwargs: Any) -> None:
        super().__init__(path, **kwargs)


class Clone(Fluid):
    """Deep-copy reference. Resolves like !ref: but returns a deepcopy."""

    def __init__(self, path: str, **kwargs: Any) -> None:
        super().__init__(path, **kwargs)


class ScopeBlock:
    """A conditional block carried in the IR until ``resolve_scopes`` rewrites it.

    Produced by the ``!scope:`` / ``!notscope:`` YAML constructors. Three forms
    are accepted at parse time, all normalized to the same fields:

    * ``!scope:debug``                  → ``key="debug"``, ``value=None`` (boolean)
    * ``!scope:task=classification``    → ``key="task"``, ``value="classification"``
    * ``!scope:task(classification)``   → ``key="task"``, ``value="classification"``

    ``negate=True`` denotes the ``!notscope:`` variants, whose activation is
    inverted with an "unset ⇒ active" convention (see ``confluid.scopes``).
    """

    __confluid_configurable__ = False

    def __init__(
        self,
        key: str,
        value: Optional[str],
        negate: bool,
        contents: Any,
    ) -> None:
        self.key = key
        self.value = value
        self.negate = negate
        self.contents = contents
        self._yaml_loc: Optional[YamlLoc] = None

    def __repr__(self) -> str:
        tag = "!notscope" if self.negate else "!scope"
        suffix = self.key if self.value is None else f"{self.key}={self.value}"
        return f"{tag}:{suffix} {self.contents!r}"


class Lazy(Class, Generic[T]):
    """Class fluid that stays deferred through ``materialize()`` / deep-flow.

    Optionally **parameterized** as ``Lazy[T]`` (e.g. ``LazyClass[Metric]``) to
    document the type the deferred template builds once ``flow()``'d. ``T`` is a
    *phantom* parameter — it is never bound from the ``target`` argument (the
    ctor still accepts any ``Type | str``), so ``LazyClass(MulticlassAccuracy)``
    stays ``Lazy[Any]`` and the subscript is purely an intent annotation for
    type-checkers / readers. Mirrors the Python-side ``confluid.Lazy[T]``
    *annotation* alias (``Annotated[T, _LAZY_MARKER]``) at the fluid layer.

    Behaves identically to :class:`Class` for the purposes of broadcasting:
    a ``Lazy`` value receives broadcast kwargs from its surrounding context
    just like a regular ``!class:`` Fluid. The difference is downstream —
    materialization passes (``materialize``, the liquifai ``_deep_flow``
    walker, and any caller that uses ``Instance``-only auto-flow) leave a
    ``Lazy`` deferred. The receiving code is responsible for calling
    ``flow(value, **runtime_kwargs)`` when it has the runtime arguments
    needed to actually construct the target.

    The classic use is an optimizer that needs ``params=model.parameters()``
    — declared in YAML as ``optimizer: !lazy:torch.optim.Adam(lr=0.01)``,
    then instantiated inside ``configure_optimizers`` with the live params.
    Mirrors the Python-side ``confluid.Lazy[T]`` annotation but expressed
    at the YAML layer.
    """

    def __init__(self, target: Union[Callable[..., Any], str], **kwargs: Any) -> None:
        super().__init__(target, **kwargs)


def __getattr__(name: str) -> Any:
    """Compat: ``flow`` / ``cast`` moved to ``confluid.engine`` (2026-07).

    Served lazily so ``from confluid.fluid import flow`` keeps working for
    downstream code without reintroducing a fluid→engine import cycle at
    module-load time (engine imports fluid's markers at its top level).
    """
    if name in ("flow", "cast"):
        from confluid import engine

        return getattr(engine, name)
    raise AttributeError(f"module 'confluid.fluid' has no attribute {name!r}")
