from typing import Any, Callable, Dict, FrozenSet, Generic, Optional, Tuple, Union

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
        # Which of ``kwargs`` were ADDRESSED at this node (written on the marker
        # itself, or delivered by a block naming it) rather than cascaded in as
        # bare broadcasts. Stamped by the engine's broadcast pass, which is the
        # only place that knows; ``None`` means "not merged against a document,
        # so every kwarg is the marker's own" — the state a hand-built marker
        # and a direct ``flow(marker)`` are in. Read for exactly one decision:
        # what a ``**kwargs`` constructor receives (see ``engine._flow_target``).
        self._addressed_keys: Optional[frozenset[str]] = None
        # True once this marker's kwargs have been through the engine's ORDERED
        # merge (``broadcast._prepare_kwargs``), which resolves an own-kwarg against
        # a competing bare key by DOCUMENT POSITION — last spec wins. The later
        # broadcast pass reads it to know the contest is already settled and must
        # not be re-run (re-running it would apply the bare key unconditionally,
        # discarding the ordering). ``False`` means no ordered pass has happened:
        # a hand-built marker, or one tuned out-of-band by a mapping addressed at
        # its slot. Never serialized — copy()/dump() ignore it.
        self._order_resolved: bool = False
        # Per dict-valued kwarg, the BARE keys sitting LATER in the document than
        # that kwarg. A mapping addressed at a deferred slot (``optimizer: {lr:
        # 0.5}``) is tuned into the slot's marker after construction — the only
        # moment the slot's default is knowable — by which point the ordered merge
        # is long past and the mapping has no position of its own to be judged by
        # (a plain dict cannot carry one). So the position contest is DECIDED here,
        # where the ordering is still visible, and recorded as its outcome: the
        # bare keys that beat this slot. Empty ⇒ the slot wins outright.
        self._late_bare_keys: Dict[str, FrozenSet[str]] = {}

    def __repr__(self) -> str:
        name = self.target if isinstance(self.target, str) else getattr(self.target, "__name__", str(self.target))
        return f"{self.__class__.__name__}({name}, {self.kwargs})"


# --------------------------------------------------------------------------- #
# Engine bookkeeping — read these, never ``getattr`` the fields
#
# The three ``_addressed_keys`` / ``_order_resolved`` / ``_late_bare_keys`` fields
# above are ENGINE state that happens to live on the marker, because the marker is
# the only thing that survives between the passes that write and read them. Every
# reader is handed a value that may NOT be a Fluid — a bare type, a live instance,
# a plain dict — so each read needs a default, and the defaults were being restated
# at each call site as an inline ``getattr(x, "_field", ...)``. Three loose copies
# of a contract is how they drift; these accessors are the one place each default
# is written down.
#
# Anything reading this state MUST go through these. A new field belongs here too.
# --------------------------------------------------------------------------- #


def addressed_keys_of(obj: Any) -> Optional[FrozenSet[str]]:
    """Which kwargs were ADDRESSED at ``obj``, or ``None`` if it never met a document.

    ``None`` is not "no addressed keys" — it means the marker was never merged
    against a document (hand-built, or a direct ``flow(marker)``), so every kwarg
    it carries is its own. Callers must keep the two apart.
    """
    keys: Optional[FrozenSet[str]] = getattr(obj, "_addressed_keys", None)
    return keys


def is_order_resolved(obj: Any) -> bool:
    """Whether ``obj``'s kwargs have been through the engine's ORDERED merge.

    False for anything that is not a marker, and for a marker that has not yet met
    ``_prepare_kwargs`` — in both cases no position contest has been settled, so a
    caller must not assume one.
    """
    return bool(getattr(obj, "_order_resolved", False))


def late_bare_keys_of(obj: Any) -> Dict[str, FrozenSet[str]]:
    """Per dict-valued kwarg of ``obj``, the bare keys positioned AFTER it.

    Empty for anything that is not a marker and for a marker with no dict-valued
    kwargs — which is the overwhelmingly common case, so this stays allocation-free.
    """
    late: Optional[Dict[str, FrozenSet[str]]] = getattr(obj, "_late_bare_keys", None)
    return late or {}


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
    *annotation* alias (``Annotated[Union[T, Fluid], _LAZY_MARKER]``) at the
    fluid layer.

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
    """Compat: ``flow`` moved to ``confluid.engine`` (2026-07).

    Served lazily so ``from confluid.fluid import flow`` keeps working for
    downstream code without reintroducing a fluid→engine import cycle at
    module-load time (engine imports fluid's markers at its top level).
    The ``cast`` arm was pruned 2026-08-08 — zero users workspace-wide;
    import it from ``confluid`` (public) or ``confluid.engine``.
    """
    if name == "flow":
        from confluid import engine

        return engine.flow
    raise AttributeError(f"module 'confluid.fluid' has no attribute {name!r}")
