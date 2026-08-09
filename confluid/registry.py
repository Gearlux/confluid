"""The class registry: registration, discovery indices, and name resolution.

**A registered NAME may map to more than one class.** Two classes legitimately share a
short name when they differ on a discovery tag — the same op implemented for two
frameworks (``group="fft/numpy"`` vs ``"fft/torch"``), or a library that publishes the
same name as both a loss and a metric (``role="loss"`` vs ``"metric"``). The registry
therefore stores a LIST of entries per name and gives each entry a canonical dotted key
(``module.QualName``) that is unique by construction:

* an unambiguous name is its own public key, so ``list_classes()`` → ``get_class(name)``
  reads exactly as it always did;
* an ambiguous name publishes its entries under their DOTTED keys instead, so both are
  enumerable and both are reachable — the old flat dict silently dropped one;
* a bare lookup of an ambiguous name raises :class:`~confluid.AmbiguousClassError`
  naming the candidates, rather than binding whichever module imported last.

Disambiguate by dotted path (``!class:pkg.mod.FourierOp``), by tag filter
(``get_class("FourierOp", group="fft/torch")``), or by the selector spelling the loader
accepts in a tag (``!class:FourierOp@group=fft/torch``). A selector value may name a
document key — ``!class:FourierOp@framework=$framework`` — resolved against the active
configuration so the choice is written once; see :func:`parse_target_spec`.
"""

import importlib
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple, Union, cast

from loggair import get_logger

from confluid.exceptions import AmbiguousClassError, ConfigurationError
from confluid.resolver import Resolver

logger = get_logger("confluid.registry")

#: The discovery axes a lookup may filter on — the same five ``list_classes`` intersects,
#: and the only axis names a ``@axis=value`` selector accepts.
SELECTOR_AXES: Tuple[str, ...] = ("category", "group", "task", "role", "framework")


@dataclass(frozen=True)
class _ClassEntry:
    """One registration: the class, the name it was registered under, and its tags.

    Frozen because the reverse indices and the test-isolation snapshot share entry
    objects by reference — a re-registration REPLACES an entry, never mutates one.
    """

    name: str
    cls: Callable[..., Any]
    key: str
    category: Optional[str] = None
    group: Optional[str] = None
    task: Optional[str] = None
    role: Optional[str] = None
    framework: Optional[str] = None

    @property
    def tags(self) -> Tuple[Optional[str], ...]:
        """The five discovery tags, in :data:`SELECTOR_AXES` order."""
        return (self.category, self.group, self.task, self.role, self.framework)

    def describe(self) -> str:
        """``pkg.mod.Name (role=loss, framework=keras)`` — for error messages."""
        tags = ", ".join(f"{axis}={value}" for axis, value in zip(SELECTOR_AXES, self.tags) if value is not None)
        return f"{self.key} ({tags})" if tags else self.key


def _entry_key(cls: Callable[..., Any]) -> str:
    """The canonical dotted key for ``cls`` — ALWAYS contains a ``.``, never a ``<``.

    The dot invariant is what lets a lookup tell a bare Python name from a key without a
    flag: a bare name never has a dot. ``builtins`` / ``__main__`` are deliberately NOT
    stripped for that reason.

    The no-``<`` invariant is what makes the key usable: ``<`` is not a legal YAML tag
    character, so a key carrying one is a key :meth:`list_classes` can advertise and
    ``!class:`` cannot parse. Such a target was never importable anyway — the key is a
    HANDLE the registry resolves before the import fallback, not a promise of
    importability — so the qualname is made tag-legal rather than kept faithful.

    Two shapes need it, and they need OPPOSITE treatment:

    * ``<locals>`` is DROPPED with its dot (``outer.<locals>.Inner`` → ``outer.Inner``);
      the segment names a scope, and the surrounding names still identify the target.
    * every other bracketed segment is UNWRAPPED (``<lambda>`` → ``lambda``), because it
      IS the name — dropping it would leave a bare trailing dot, identical for every
      lambda in the module. Unwrapping keeps them distinct from each other and lets
      :meth:`_claim_key` separate same-module collisions with its ``~N`` suffix, ``~``
      being a legal tag character.

    ``<lambda>`` reached here unhandled until 2026-08-04: registering two lambdas under
    one name published ``__main__.<lambda>``, which ``list_classes`` returned and
    ``!class:`` rejected with a ``ScannerError``. The same applies to ``<listcomp>`` /
    ``<genexpr>`` / ``<module>``, so the rule is general rather than a lambda special case.
    """
    module = getattr(cls, "__module__", None) or "builtins"
    qualname = getattr(cls, "__qualname__", None) or getattr(cls, "__name__", None) or repr(cls)
    tag_legal = re.sub(r"<(\w+)>", r"\1", qualname.replace("<locals>.", ""))
    return f"{module}.{tag_legal}"


def parse_target_spec(spec: str) -> Tuple[str, Dict[str, str]]:
    """Split ``Name@axis=value,axis=value`` into its name and its tag selector.

    The selector is how a config disambiguates a shared name without spelling out a
    module path::

        !class:FourierOp@group=fft/torch
        !class:BinaryCrossentropy@role=metric,framework=keras
        !class:FourierOp@framework=$framework      # $key reads the active document

    ``@``, ``=``, ``,``, ``/`` and ``$`` are all legal YAML tag-suffix characters, which
    is why the selector can ride an unquoted tag. ``${...}`` cannot — ``{`` is not a tag
    character and PyYAML's scanner rejects it before confluid sees the document — so the
    document-key form is the unbraced ``$key`` (dotted paths allowed: ``$run.framework``).

    Raises:
        ConfigurationError: on a malformed pair or an axis that is not a discovery axis —
            a typo'd ``@fraemwork=torch`` must fail loudly, not select nothing.
    """
    name, sep, tail = spec.partition("@")
    if not sep:
        return spec, {}
    selectors: Dict[str, str] = {}
    for part in tail.split(","):
        axis, eq, value = part.partition("=")
        axis, value = axis.strip(), value.strip()
        if not eq or not axis or not value:
            raise ConfigurationError(
                f"malformed class selector in {spec!r}: expected 'axis=value' pairs " f"separated by ',', got {part!r}"
            )
        if axis not in SELECTOR_AXES:
            raise ConfigurationError(
                f"unknown selector axis {axis!r} in {spec!r} — must be one of {', '.join(SELECTOR_AXES)}"
            )
        selectors[axis] = value
    return name, selectors


def _resolve_selector_values(selectors: Dict[str, str], context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Replace each ``$key`` selector value with what ``key`` holds in ``context``.

    Lets a config say the axis once — ``framework: torch`` at the top level, then
    ``@framework=$framework`` at every ambiguous target — instead of repeating the value.
    Resolution uses the shared path walker, so ``$run.framework`` works too.

    The context is the document being materialized, so this answers for any target the
    engine builds — including one nested inside another marker's kwargs. It does NOT
    answer for a marker kept deferred and flowed later by domain code, which runs with no
    active document: that raises here naming the key, rather than silently matching
    nothing. Write the value literally in that position, or flow inside
    :func:`confluid.active_context`.
    """
    if not selectors:
        return {}
    out: Dict[str, Any] = {}
    for axis, value in selectors.items():
        if not value.startswith("$"):
            out[axis] = value
            continue
        path = value[1:]
        found = Resolver(context=context)._lookup_path(path, context) if context else None
        if found is None:
            raise ConfigurationError(
                f"selector {axis}=${path} could not be resolved — no key {path!r} in the active "
                f"configuration. Define it (e.g. `{path}: torch` at the top level, or inside the "
                f"matching !scope: block) or write the value literally."
            )
        out[axis] = found
    return out


class ConfluidRegistry:
    """Central registry for configurable classes and objects."""

    def __init__(self) -> None:
        # Registered name -> its entries, in registration order. Values are configurable
        # CALLABLES — classes OR builder/factory functions (see the "A Target May Be ANY
        # Callable" mandate). A list rather than a single value because a name may be
        # claimed by several classes that differ on a discovery tag.
        self._entries: Dict[str, List[_ClassEntry]] = {}
        # Canonical dotted key -> entry. Unique by construction, so this is what an
        # ambiguous name publishes and what an identity lookup goes through.
        self._by_key: Dict[str, _ClassEntry] = {}
        # Reverse indices: <tag value> → set of ENTRY KEYS. Keys, not names, because a
        # name can become ambiguous at a LATER registration — an index keyed by name
        # would have to be rewritten across five dicts at that moment, whereas an entry
        # key never changes. Classes without the corresponding tag aren't stored, so a
        # ``None`` filter falls through to the full set. ``task`` × ``role`` is the
        # orthogonal decomposition of ``category`` — a class tagged
        # ``task="classification"`` + ``role="model"`` also derives
        # ``category="classification_model"``.
        self._by_category: Dict[str, Set[str]] = {}
        self._by_group: Dict[str, Set[str]] = {}
        self._by_task: Dict[str, Set[str]] = {}
        self._by_role: Dict[str, Set[str]] = {}
        # ``framework`` is the third orthogonal axis: which ENGINE's API a class
        # belongs to, and therefore what it can be wired to. `task`/`role` say
        # what a class is FOR; neither says a `torch.nn` loss cannot be handed to
        # a Keras trainer. Deliberately NOT folded into ``category`` — that stays
        # ``f"{task}_{role}"``.
        self._by_framework: Dict[str, Set[str]] = {}

    # ---- indices ------------------------------------------------------------ #
    def _tag_indices(self) -> Tuple[Tuple[Dict[str, Set[str]], int], ...]:
        """The five reverse indices paired with their position in ``_ClassEntry.tags``."""
        return (
            (self._by_category, 0),
            (self._by_group, 1),
            (self._by_task, 2),
            (self._by_role, 3),
            (self._by_framework, 4),
        )

    def _index(self, entry: _ClassEntry) -> None:
        for index, position in self._tag_indices():
            value = entry.tags[position]
            if value is not None:
                index.setdefault(value, set()).add(entry.key)

    def _unindex(self, entry: _ClassEntry) -> None:
        """Drop ``entry``'s key from every bucket it claimed.

        Called before a replacement is indexed, so a re-registration that CHANGES a tag
        cannot leave the class listed under both the old and the new value.
        """
        for index, position in self._tag_indices():
            value = entry.tags[position]
            if value is not None and value in index:
                index[value].discard(entry.key)
                if not index[value]:
                    del index[value]

    def _public_key(self, entry: _ClassEntry) -> str:
        """The name a consumer should use for ``entry`` — bare while unambiguous.

        This is what :meth:`list_classes` returns, and it is always a valid
        :meth:`get_class` argument. Computed on read rather than stored: a name becomes
        ambiguous at the moment a SECOND class claims it, and the first class is already
        registered by then.
        """
        return entry.name if len(self._entries.get(entry.name, ())) == 1 else entry.key

    # ---- registration ------------------------------------------------------- #
    def register_class(
        self,
        cls: Callable[..., Any],
        name: Optional[str] = None,
        category: Optional[str] = None,
        group: Optional[str] = None,
        task: Optional[str] = None,
        role: Optional[str] = None,
        framework: Optional[str] = None,
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
        (e.g. a snapshot restore) may forward as little as ``name``/``category``
        — each mark falls back to the class's EXISTING mark when the argument is
        unset, so a partial re-register never drops tags stamped earlier.
        ``random``/``constant``/``eager``/``strict_typing``/``display_name``/
        ``no_broadcast``/``no_capture``/``broadcast_attrs`` are stamp-only (no
        reverse index).

        A name already taken by ANOTHER class is resolved by the tags:

        * same class object → the entry is replaced, silently (a re-decoration or a
          snapshot restore);
        * different object, IDENTICAL tags → a warning, then last-write-wins as before;
          with no discriminating axis there is nothing a lookup could select on;
        * different object, ANY tag differing → both entries coexist and the name
          publishes them under their dotted keys.
        """
        # ``name`` falls back to the mark this class carries in its OWN ``__dict__`` —
        # never an INHERITED one, which would register a subclass under its parent's
        # custom name. That fallback is what makes a bare ``register_class(cls)`` after
        # a ``@configurable(name="Custom")`` idempotent instead of minting a duplicate.
        own_name = cls.__dict__.get("__confluid_name__") if hasattr(cls, "__dict__") else None
        cls_name = name or own_name or cls.__name__
        # Fall back to any tags already on the class — this keeps a re-register
        # (e.g. a snapshot restore, which only forwards ``category``) from
        # dropping ``group`` / ``task`` / ``role`` set by the original ``@configurable``.
        category = category if category is not None else getattr(cls, "__confluid_category__", None)
        group = group if group is not None else getattr(cls, "__confluid_group__", None)
        task = task if task is not None else getattr(cls, "__confluid_task__", None)
        role = role if role is not None else getattr(cls, "__confluid_role__", None)
        framework = framework if framework is not None else getattr(cls, "__confluid_framework__", None)
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
        self._store(
            _ClassEntry(
                name=cls_name,
                cls=cls,
                key=self._claim_key(cls),
                category=category,
                group=group,
                task=task,
                role=role,
                framework=framework,
            )
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
            if framework is not None:
                setattr(cls, "__confluid_framework__", framework)
            if lazy:
                # A "lazy" class is one whose constructed value should stay
                # deferred (a LazyClass / runtime-injected slot — e.g. an
                # optimizer needing ``params``). Consumers (visual-editor object
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
        return cls

    def _claim_key(self, cls: Callable[..., Any]) -> str:
        """``cls``'s dotted key, suffixed if another object already holds that spelling.

        Two distinct classes can genuinely share ``module.QualName`` — a class rebuilt by
        a decorator, or two defined in the same function scope — and the key must stay a
        unique handle, so the newcomer gets ``…~2``.
        """
        base = _entry_key(cls)
        existing = self._by_key.get(base)
        if existing is None or existing.cls is cls:
            return base
        suffix = 2
        while True:
            candidate = f"{base}~{suffix}"
            taken = self._by_key.get(candidate)
            if taken is None or taken.cls is cls:
                return candidate
            suffix += 1

    def _store(self, entry: _ClassEntry) -> None:
        """Insert ``entry``, replacing an existing registration or coexisting with it."""
        entries = self._entries.setdefault(entry.name, [])
        for position, existing in enumerate(entries):
            if existing.cls is entry.cls:
                # Same object registered again (re-decoration, snapshot restore) —
                # refresh its tags in place and say nothing.
                self._unindex(existing)
                entries[position] = entry
                self._by_key.pop(existing.key, None)
                self._by_key[entry.key] = entry
                self._index(entry)
                return
            if existing.tags == entry.tags:
                # A real clobber: same name, nothing to tell the two apart. Preserve
                # last-write-wins so nothing breaks, but say so — this is silent picker
                # corruption otherwise.
                logger.warning(
                    f"'{entry.name}' is already registered by {existing.describe()} with the same tags; "
                    f"{entry.describe()} REPLACES it. Give one a distinguishing tag "
                    f"(framework= / role= / group=) or an explicit name= to keep both."
                )
                self._unindex(existing)
                entries[position] = entry
                self._by_key.pop(existing.key, None)
                self._by_key[entry.key] = entry
                self._index(entry)
                return
        if entries:
            logger.debug(
                f"'{entry.name}' now maps to {len(entries) + 1} classes; each is published under its "
                f"dotted key and a bare lookup must disambiguate."
            )
        entries.append(entry)
        self._by_key[entry.key] = entry
        self._index(entry)

    # ---- lookup ------------------------------------------------------------- #
    def get_class(
        self,
        name: Union[str, Callable[..., Any]],
        *,
        category: Optional[str] = None,
        group: Optional[str] = None,
        task: Optional[str] = None,
        role: Optional[str] = None,
        framework: Optional[str] = None,
    ) -> Optional[Callable[..., Any]]:
        """Look ``name`` up, optionally narrowing by discovery tag.

        ``name`` may be a registered name, a canonical dotted key (what
        :meth:`list_classes` returns for an ambiguous name), a ``Name@axis=value``
        selector, or a class object.

        The tag filters are the programmatic twin of the selector — they mirror
        :meth:`list_classes`, so a consumer that already narrowed an enumeration can
        carry the same filter into the lookup::

            get_class("BinaryCrossentropy", role="loss", framework="keras")

        Returns:
            The single matching class, or ``None`` when nothing matches.

        Raises:
            AmbiguousClassError: when the name (after filtering) still names more than
                one class — binding one of them arbitrarily would depend on import order.
        """
        if not isinstance(name, str):
            entry = self._entry_for_object(name)
            if entry is not None:
                return entry.cls
            name = getattr(name, "__confluid_name__", getattr(name, "__name__", str(name)))
        base, selectors = parse_target_spec(name)
        explicit = {
            "category": category,
            "group": group,
            "task": task,
            "role": role,
            "framework": framework,
        }
        wanted: Dict[str, Any] = {**selectors, **{k: v for k, v in explicit.items() if v is not None}}
        entry = self._select(base, wanted)
        return entry.cls if entry is not None else None

    def _entry_for_object(self, cls: Any) -> Optional[_ClassEntry]:
        """The entry holding ``cls`` itself, by identity — never by name."""
        for key in (_entry_key(cls), *(k for k in self._by_key if k.startswith(f"{_entry_key(cls)}~"))):
            entry = self._by_key.get(key)
            if entry is not None and entry.cls is cls:
                return entry
        return None

    def _candidates(self, name: str) -> List[_ClassEntry]:
        """Entries reachable under ``name`` — a registered name, else a dotted key."""
        entries = self._entries.get(name)
        if entries:
            return list(entries)
        entry = self._by_key.get(name)
        return [entry] if entry is not None else []

    def _select(self, name: str, wanted: Dict[str, Any]) -> Optional[_ClassEntry]:
        """The one entry under ``name`` matching every filter in ``wanted``."""
        matches = [
            entry
            for entry in self._candidates(name)
            if all(getattr(entry, axis) == value for axis, value in wanted.items())
        ]
        if not matches:
            return None
        if len(matches) == 1:
            return matches[0]
        shown = "\n".join(f"  {entry.describe()}" for entry in matches)
        narrowed = f" (filtered by {', '.join(f'{k}={v}' for k, v in wanted.items())})" if wanted else ""
        # Name an axis the candidates actually DIFFER on — a generic hint would send the
        # reader to a tag every candidate shares, which narrows nothing.
        axis = next(
            (a for i, a in enumerate(SELECTOR_AXES) if len({e.tags[i] for e in matches}) == len(matches)),
            None,
        )
        hint = (
            f"a tag selector (!class:{name}@{axis}={getattr(matches[0], axis)}) or "
            f"get_class({name!r}, {axis}={getattr(matches[0], axis)!r})"
            if axis is not None
            else "an explicit name= on one of them, since no single tag tells them apart"
        )
        raise AmbiguousClassError(
            f"'{name}' is registered by {len(matches)} classes{narrowed}, so a bare lookup cannot "
            f"choose between them:\n{shown}\n"
            f"Disambiguate with the dotted path (!class:{matches[0].key}), {hint}."
        )

    def key_for(self, cls: Any) -> Optional[str]:
        """The public key ``cls`` is reachable under, or ``None`` if it is unregistered.

        Computed live, so it is correct the moment a second class claims the same name —
        which is why the dumper asks for it instead of reading a stamped attribute that
        would still say ``__confluid_name__`` on the class registered first.
        """
        entry = self._entry_for_object(cls)
        return self._public_key(entry) if entry is not None else None

    def is_configurable(self, obj: Any) -> bool:
        """Check if a class or object is marked as configurable."""
        if hasattr(obj, "__confluid_configurable__"):
            return True
        # Fallback to name lookup
        name = getattr(obj, "__confluid_name__", getattr(obj, "__name__", None))
        return bool(name) and name in self._entries

    def clear(self) -> None:
        self._entries.clear()
        self._by_key.clear()
        self._by_category.clear()
        self._by_group.clear()
        self._by_task.clear()
        self._by_role.clear()
        self._by_framework.clear()

    def list_classes(
        self,
        category: Optional[str] = None,
        group: Optional[str] = None,
        task: Optional[str] = None,
        role: Optional[str] = None,
        framework: Optional[str] = None,
    ) -> Set[str]:
        """Return registered class keys, filtered by ``category``/``group``/``task``/``role``/``framework``.

        Each returned key is a valid :meth:`get_class` argument: the bare name while it
        is unambiguous, the canonical dotted key once a second class claims the name. So
        the enumerate-then-look-up idiom keeps working, and BOTH classes of a shared name
        are enumerable — the flat name-keyed dict used to publish only one of them.

        All filters are ``None`` by default (returns every registered key).
        When several are given they INTERSECT (e.g. ``task="classification",
        role="model"`` returns only classification models — equivalent to
        ``category="classification_model"``). A filter no class matches returns
        the empty set rather than raising, so discovery callers can probe freely.

        ``framework`` narrows to one engine's API, which is what makes a picker
        offerable: ``task="classification", role="loss"`` alone would hand a
        ``torch.nn`` loss to a Keras trainer. Classes left UNTAGGED are absent
        from the index, so a ``framework`` filter returns only what explicitly
        claims that engine — probe without the filter to see everything.
        """
        result: Optional[Set[str]] = None
        for index, value in (
            (self._by_category, category),
            (self._by_group, group),
            (self._by_task, task),
            (self._by_role, role),
            (self._by_framework, framework),
        ):
            if value is None:
                continue
            keys = set(index.get(value, set()))
            result = keys if result is None else (result & keys)
        entries = self._by_key.values() if result is None else (self._by_key[k] for k in result if k in self._by_key)
        return {self._public_key(entry) for entry in entries}

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

    def list_frameworks(self) -> Set[str]:
        """Return the set of framework names that have at least one registered class."""
        return set(self._by_framework.keys())


# Global Singleton instance
_registry = ConfluidRegistry()


def get_registry() -> ConfluidRegistry:
    """Get the global Confluid registry instance."""
    return _registry


def resolve_class(
    name: Union[str, type],
    *,
    strict: bool = False,
    context: Optional[Dict[str, Any]] = None,
    category: Optional[str] = None,
    group: Optional[str] = None,
    task: Optional[str] = None,
    role: Optional[str] = None,
    framework: Optional[str] = None,
) -> Optional[Callable[..., Any]]:
    """Resolve a name to a Python **callable** target (a class OR a plain function).

    Resolution order:
    1. If already a type, return as-is.
    2. Registry lookup by name, canonical key, or ``Name@axis=value`` selector.
    3. Module path import (e.g., ``"torch.optim.Adam"`` — a class — or
       ``"torchvision.models.detection.fasterrcnn_resnet50_fpn"`` — a builder
       *function*).

    A ``!class:`` / ``!lazy:`` target may be any callable, not just a class:
    factory/builder functions (torchvision's ``fasterrcnn_resnet50_fpn``,
    ``timm.create_model``, …) are first-class targets. The module-path branch
    therefore accepts any **callable** attribute (class or function), not only
    ``isinstance(_, type)``. (``flow()`` then builds it by introspecting the
    callable's own signature — see :func:`confluid.engine.flow`.)

    Args:
        strict: Raise on an ambiguous name or a malformed selector instead of returning
            ``None``. The construction path passes ``True`` — building the wrong class is
            worse than stopping — while introspection callers stay best-effort, since
            they already treat ``None`` as "not introspectable".
        context: The active configuration, used to resolve a ``$key`` selector value.
            The engine supplies it at construction time; without it a ``$`` selector
            cannot be answered.
        category, group, task, role, framework: Narrow the lookup, exactly as on
            :meth:`ConfluidRegistry.get_class`. They win over a selector on the same axis.

    Note:
        A selector is deliberately ignored on the module-path branch — a dotted path is
        already unique, so verifying the tags there would only reject untagged imports.
    """
    if isinstance(name, type):
        return name

    if not isinstance(name, str):
        return None

    try:
        base, selectors = parse_target_spec(name)
        wanted = _resolve_selector_values(selectors, context)
    except ConfigurationError:
        if strict:
            raise
        return None

    explicit = {"category": category, "group": group, "task": task, "role": role, "framework": framework}
    wanted.update({k: v for k, v in explicit.items() if v is not None})

    # Registry lookup
    try:
        cls = _registry._select(base, wanted)
    except AmbiguousClassError:
        if strict:
            raise
        return None
    if cls is not None:
        return cls.cls

    # Module path import (requires a dot in the name)
    if "." in base:
        module_path, class_attr = base.rsplit(".", 1)
        try:
            module = importlib.import_module(module_path)
            attr = getattr(module, class_attr)
            # Accept any callable target — a class OR a plain builder function.
            if isinstance(attr, type) or callable(attr):
                return cast(Callable[..., Any], attr)
        except (ImportError, AttributeError) as e:
            logger.debug(f"Failed to resolve '{base}' via module path: {e}")

    return None
