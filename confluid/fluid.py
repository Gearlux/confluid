from typing import Any, Callable, Dict, FrozenSet, Generic, Optional, Tuple, Union

from typing_extensions import TypeVar

YamlLoc = Tuple[Optional[str], int, int]
"""``(filename or None, line, column)`` — 1-based YAML source location."""

T = TypeVar("T", default=Any)
"""Phantom type parameter for ``Partial[T]`` — the eventual flow()'d target type.

Defaults to ``Any`` (PEP 696) so a bare ``PartialClass(Foo)`` infers ``Partial[Any]``
(not ``Partial[Never]``) and needs no annotation, while ``PartialClass[Metric]`` stays
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


def dotted_positions_of(obj: Any) -> Dict[str, FrozenSet[str]]:
    """Per kwarg of ``obj`` delivered by a top-level DOTTED line, the sibling keys that line beat.

    Written by pass 6 (``merger.expand_dotted_mapping``, stamping enabled for the
    document's top level) when a dotted key lands DIRECTLY in a marker's kwargs:
    ``t.lr: 9.0`` folds the value into ``t``'s kwargs — which would silently move
    it to the MARKER's document position — so the fold records the keys written
    BEFORE the dotted line, and the scanner skips a cascade delivery the line
    out-positioned (BUGS-2026-08-19 BC4, user ruling: per-key position, option B).
    ``dump()`` re-emits a stamped kwarg as a dotted line after the keys it beat,
    which is what keeps ``load(emit(x)) == load(x)`` — plain YAML cannot carry the
    stamp, so the position is put back into document ORDER, the one thing it can.

    Empty for anything that is not a marker and for a marker with no dotted
    delivery — the overwhelmingly common case, so this stays allocation-free.
    """
    stamped: Optional[Dict[str, FrozenSet[str]]] = getattr(obj, "_dotted_out_positioned", None)
    return stamped or {}


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


def _at_yaml_loc(node: Any) -> str:
    """`` at <file>:<line>:<col>`` for an error message — ``""`` when the node has no location.

    Every error raised while processing a document names its ``file:line:col``
    (workspace rule 2026-08-11). This renders that suffix so the spelling cannot
    drift between raise sites — it had already drifted into three spellings across
    seven re-inlined copies before it moved HERE (2026-08-13), beside
    :func:`format_yaml_loc` on the dependency leaf, where ``engine``, ``broadcast``
    and ``scopes`` can all import it. The two sites that add a context word
    (``(set at …)``, ``(receiver at …)``) keep their own spelling deliberately.
    """
    loc = format_yaml_loc(node)
    return f" at {loc}" if loc else ""


class Target(Fluid):
    """A callable to build, plus the kwargs to build it with.

    The ``_target_:`` YAML key produces one of these (the ``!class:`` tag spelling
    parses to the same marker). Materialization BUILDS it — always, and
    regardless of what surrounds it.

    There are exactly TWO construction modes, and :attr:`partial` is the whole
    difference: ``False`` here, ``True`` on :class:`PartialClass`. Nothing about the
    parent, the nesting depth or the document position changes whether a marker
    is built.

    Until 2026-08-11 there were three modes: ``Instance`` (built), ``Class``
    (built only when its parent was NOT ``@configurable``) and ``PartialClass`` (never
    built). The middle one was unnecessary — its documented purpose was to let
    broadcasting reach a node before construction, but broadcasting is pass 7
    and construction is pass 8, so a BUILT node already receives every cascading
    key before its constructor runs. What it actually provided was deferral of
    construction COST, which is what ``partial`` is for.
    """

    #: Whether materialization withholds construction. A CLASS attribute, so
    #: every reader can ask ``marker.partial`` without an isinstance ladder.
    partial: bool = False

    def __init__(self, target: Union[Callable[..., Any], str], **kwargs: Any) -> None:
        super().__init__(target, **kwargs)


class Reference(Fluid):
    """Late-bound reference to another part of the config."""

    def __init__(self, path: str, **kwargs: Any) -> None:
        super().__init__(path, **kwargs)


class ScopeBlock:
    """A conditional block carried in the IR until ``resolve_scopes`` rewrites it.

    ``dims`` maps each dimension this block is conditional on to the value it
    requires, or ``None`` for a boolean dimension. Every spelling normalizes to
    it, and the block is active only when ALL of them match:

    * ``!scope:debug``                → ``{"debug": None}``   (boolean)
    * ``!scope:task=classification``  → ``{"task": "classification"}``
    * ``_scope_: {task: classification, framework: keras}``  → both, ANDed

    Holding a MAPPING rather than one key/value pair is what lets the reserved-key
    spelling express a multi-dimension condition directly; the tag spelling can
    carry only one, because a tag suffix is a string.

    ``negate=True`` denotes the ``!notscope:`` / ``_notscope_:`` variants, whose
    activation is inverted with an "unset ⇒ active" convention (see
    ``confluid.scopes``).
    """

    __confluid_configurable__ = False

    def __init__(
        self,
        dims: Dict[str, Optional[str]],
        negate: bool,
        contents: Any,
    ) -> None:
        self.dims = dims
        self.negate = negate
        self.contents = contents
        self._yaml_loc: Optional[YamlLoc] = None

    def __repr__(self) -> str:
        marker = "_notscope_" if self.negate else "_scope_"
        spec = ", ".join(k if v is None else f"{k}: {v}" for k, v in self.dims.items())
        return f"{marker}: {{{spec}}} {self.contents!r}"


class PartialClass(Target, Generic[T]):
    """A :class:`Target` that materialization NEVER builds.

    Written ``_partial_: true`` in YAML (the ``!partial:`` tag spelling parses to the
    same marker), and ``PartialClass(...)`` in code. Nothing auto-flows one — not ``materialize``,
    not an external deep-flow walker. Only an explicit
    ``flow(marker, *args, **kwargs)`` builds it, which is the point: the receiving
    code supplies an argument that does not exist at config time. The textbook
    case is an optimizer needing ``params=model.parameters()``; a model needing
    ``num_classes`` from the dataset is the same shape.

    **Deferral withholds CONSTRUCTION only.** A ``PartialClass`` is broadcast into and
    configured exactly like a built target — merging keys into ``kwargs``
    constructs nothing — so ``lr: 0.001`` still tunes a deferred optimizer.

    Optionally parameterized as ``PartialClass[T]`` (e.g. ``PartialClass[Metric]``) to
    document the type it builds once flowed. ``T`` is a *phantom* parameter, never
    bound from ``target``, so ``PartialClass(Adam)`` stays ``PartialClass[Any]`` and the
    subscript is purely an intent annotation. Mirrors the Python-side
    ``confluid.Partial[T]`` *annotation* alias at the fluid layer: the tag defers a
    VALUE, the annotation defers a SLOT.
    """

    partial: bool = True

    def __init__(self, target: Union[Callable[..., Any], str], **kwargs: Any) -> None:
        super().__init__(target, **kwargs)
