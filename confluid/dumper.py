import types
from typing import Any, Dict, Optional, Set

import yaml

from confluid.introspect import NO_DEFAULT, slots
from confluid.registry import get_registry

#: The slot kinds the dumper walks — the signature, minus the variadics. A ``*args``
#: name can never be passed by keyword and a ``**kwargs`` name is never a declared
#: slot, so neither belongs in a dumped config: a ``kwargs: {...}`` line never
#: round-tripped anyway (on reload the ctor filter passes it INSIDE the catchall as
#: a literal ``"kwargs"`` key, doubly nested).
#:
#: BODY SLOTS are IN (F2, 2026-08-15). ``dump()`` used to model the constructor
#: alone, on the theory that post-init attributes ride ``__confluid_extra__`` —
#: but that list is populated by ``engine._apply_post_init_attrs``, i.e. only for
#: names a CONFIG KEY landed on during load. A slot the class's own ``__init__``
#: assigned, or that ``configure()`` set afterwards, was in neither place and
#: vanished from the document: `self.epochs = 1` configured to 50 dumped as bare
#: ``_target_: BodyHost`` and reloaded as 1. That contradicts the round-trip rule
#: for a slot every OTHER surface treats as first-class — ``configure()`` sets it,
#: ``to_pydantic`` types it, the accept-list admits it.
#:
#: The two mechanisms are complementary and both stay: this projection covers
#: DECLARED slots, ``__confluid_extra__`` covers names nothing declares (a key
#: setattr'd onto a ``**kwargs`` class).
_DUMP_KINDS = frozenset({"keyword", "positional_only", "body_slot"})


class CompactDumper(yaml.SafeDumper):
    """Custom YAML dumper with !class tag support."""

    pass


def _represent_callable(dumper: yaml.SafeDumper, data: Any) -> Any:
    """Emit a module-level function/builtin as the plain string ``${ref:module.qualname}``.

    The resolver's ``resolve_reference_path`` resolves this back to the live
    object via ``importlib.import_module`` + ``getattr``, so dump/load
    round-trips hold as long as the symbol stays importable at the same
    dotted path.

    It was a ``!ref`` TAG until 2026-08-11, which made a dumped document unreadable
    by ``yaml.safe_load`` — the one property the plain format exists to give — for
    any config carrying a function-valued param (a ``collate_fn`` is the common one).
    The interpolation spelling is what the migrated configs use and what the codemod
    converts a ``!ref:`` to, so this only brings ``dump()`` in line with them.
    """
    module = getattr(data, "__module__", None)
    qualname = getattr(data, "__qualname__", None) or getattr(data, "__name__", None)
    if not module or not qualname or "<" in qualname:
        # Lambdas, closures, and anything anonymous can't be referenced —
        # fall back to the default "cannot represent" error.
        raise yaml.representer.RepresenterError(
            f"cannot represent callable {data!r} — no resolvable dotted import path"
        )
    return dumper.represent_str(f"${{ref:{module}.{qualname}}}")


def _represent_opaque(dumper: yaml.SafeDumper, data: Any) -> Any:
    """Fallback: emit a bare ``{_target_: <module.qualname>}`` marker mapping.

    Used for objects that aren't ``@configurable`` and have no registered
    representer (e.g. a training framework auto-injecting a progress bar into
    its callback list). Not round-trippable — the marker carries no kwargs —
    but lets ``dump()`` complete with informational placeholders instead of
    aborting on the first opaque object.

    A ``!class:<name>`` scalar tag until 2026-08-11, for the reason above it:
    a tag anywhere in the document costs the whole file its plain-YAML
    readability. The reserved-key mapping says the same thing and parses.
    """
    cls = data.__class__
    name = f"{cls.__module__}.{cls.__qualname__}"
    return dumper.represent_dict({"_target_": name})


def _target_name(target: Any) -> str:
    """A marker target's dumpable spelling.

    The REGISTRY answers first, exactly as it does for a live instance a few
    lines below — its public key is the bare name while unique and the dotted key
    once a namesake claims it, so the emitted name re-resolves to THIS target.
    Bypassing it (a raw ``module.qualname``) made two targets undumpable, and both
    failed loudly on reload rather than resolving to something wrong (F3):

    * a class built by a FACTORY carries ``<locals>`` in its qualname, which the
      registry strips for its key and a dotted path keeps —
      ``__main__._make.<locals>.Widget`` resolves to nothing;
    * a registered FUNCTION is not a ``type``, so it fell through to ``str()`` and
      emitted ``<function build at 0x108420fe0>``. A memory address in a dumped
      artifact is not merely unreloadable — it makes two dumps of the SAME object
      differ, so the document cannot be compared or committed.

    A target with no registry key (an unregistered class) keeps the dotted path:
    still importable, and the best spelling available. A target that is already a
    STRING is the spelling the document used and passes through verbatim.
    """
    if isinstance(target, str):
        return target
    key = get_registry().key_for(target)
    if key:
        return key
    module, qualname = getattr(target, "__module__", None), getattr(target, "__qualname__", None)
    if module and qualname:
        return f"{module}.{qualname}"
    return str(target)


def _represent_object(dumper: yaml.SafeDumper, data: Any) -> Any:
    """Represent @configurable objects and Fluid citizens as YAML tags."""
    from confluid.fluid import Reference, Target
    from confluid.loader import PARTIAL_KEY, REF_KEY, TARGET_KEY

    # A dump emits the PLAIN-YAML spelling, so the artifact a run archives is
    # readable by anything — `yaml.safe_load`, `yq`, a diff viewer — and not only
    # by confluid. Reload fidelity is unchanged: both spellings parse to the same
    # markers (docs/plain-format.md, architecture record 11).

    if isinstance(data, Reference):
        return dumper.represent_mapping("tag:yaml.org,2002:map", {REF_KEY: data.target, **data.kwargs})

    if isinstance(data, Target):
        # ``_partial_`` is emitted only when true — ``false`` is the default and
        # restating it is noise in an archived config.
        body: dict[str, Any] = {TARGET_KEY: _target_name(data.target)}
        if data.partial:
            body[PARTIAL_KEY] = True
        body.update(data.kwargs)
        return dumper.represent_mapping("tag:yaml.org,2002:map", body)

    # Objects materialized via Confluid but not @configurable — use stored origin metadata
    if hasattr(data, "__confluid_class__") and not hasattr(data.__class__, "__confluid_configurable__"):
        target = data.__confluid_class__
        if isinstance(target, type):
            cls_name = f"{target.__module__}.{target.__qualname__}"
        else:
            cls_name = str(target)
        return dumper.represent_mapping(
            "tag:yaml.org,2002:map", {TARGET_KEY: cls_name, **getattr(data, "__confluid_kwargs__", {})}
        )

    # Live @configurable instance → dump with () to indicate instant construction on reload.
    # The registry answers with the PUBLIC key — the bare name while it is unambiguous, the
    # dotted key once a second class claims that name — so the emitted tag re-resolves to
    # THIS class rather than to whichever namesake happens to win a bare lookup. Asking the
    # registry (rather than reading a stamped attribute) is what keeps that correct: a name
    # becomes ambiguous at the second registration, when this class is already stamped.
    cls_name = get_registry().key_for(data.__class__) or getattr(data, "__confluid_name__", data.__class__.__name__)
    # The ONE enumeration, projected to _DUMP_KINDS. ``slots()`` returns signature
    # order by construction, which is what the dump-key round-trip is pinned on;
    # an unreadable signature yields no signature slots (the old except branch).
    dump_slots = [s for s in slots(data.__class__) if s.kind in _DUMP_KINDS]
    params = [s.name for s in dump_slots]
    defaults: Dict[str, Any] = {s.name: s.default for s in dump_slots}

    # Ctor kwargs captured at construction (engine stamp on the YAML path,
    # validation-wrap stamp on direct Python construction) — the fallback for
    # an EAGER class that transforms a param instead of storing it verbatim.
    captured = getattr(data, "__confluid_kwargs__", {})

    def _skip_none(param: str, val: Any) -> bool:
        # Suppress dump noise ONLY when the omission is lossless: a ``None``
        # value on a param whose default is also ``None`` reloads identically.
        # A ``None`` on any other default MUST dump as ``param: null`` —
        # Serialization Symmetry (the old unconditional None-skip reloaded the
        # non-None default instead). ``NO_DEFAULT is None`` is False, so a
        # required param's ``None`` still dumps, as before.
        if val is not None:
            return False
        return defaults.get(param, NO_DEFAULT) is None

    kwargs = {}
    for p in params:
        if hasattr(data, p):
            val = getattr(data, p)
            if not _skip_none(p, val):
                if isinstance(val, type):
                    if hasattr(val, "__confluid_configurable__"):
                        # Same reasoning as the instance branch above: a class-VALUED
                        # kwarg needs the unambiguous key, not the shared short name.
                        key = get_registry().key_for(val) or getattr(val, "__confluid_name__", None)
                        val = {TARGET_KEY: key or val.__name__}
                    else:
                        val = f"{val.__module__}.{val.__qualname__}"
                kwargs[p] = val
        elif p in captured:
            val = captured[p]
            if not _skip_none(p, val):
                kwargs[p] = val

    # Include post-construction attributes set via @configurable
    for name in getattr(data, "__confluid_extra__", []):
        if name in kwargs:
            continue
        val = getattr(data, name, None)
        if val is not None:
            kwargs[name] = val

    return dumper.represent_mapping("tag:yaml.org,2002:map", {TARGET_KEY: cls_name, **kwargs})


def dump(obj: Any, *, anchor_names: Optional[Dict[int, str]] = None) -> str:
    """Serialize a (potentially nested) object tree to YAML.

    ``anchor_names`` maps ``id(value)`` → the anchor NAME to emit when that value
    is reached twice (PyYAML's default is ``id001``, ``id002``, …). The
    preprocessor passes names derived from each shared value's shortest path in
    the document (``preprocess_0``), so an emitted artefact reads without a
    lookup table. A value not in the map keeps PyYAML's default name.
    """

    class _LocalDumper(CompactDumper):
        def generate_anchor(self, node: Any) -> Any:  # noqa: ANN401 - PyYAML's own signature
            # PyYAML's Serializer names anchors from a counter. The representer kept
            # ``represented_objects[id(value)] = node`` for every aliasable value, so
            # the node → value direction is one reverse lookup away.
            if anchor_names:
                for object_id, represented in self.represented_objects.items():
                    if represented is node and object_id in anchor_names:
                        return anchor_names[object_id]
            return super().generate_anchor(node)

    # Callable references (module-level functions, builtins) serialize as
    # `!ref:module.qualname` — the loader resolves these via dotted import.
    _LocalDumper.add_representer(types.FunctionType, _represent_callable)
    _LocalDumper.add_representer(types.BuiltinFunctionType, _represent_callable)

    # Register representers for every Fluid subclass upfront — Confluid has a
    # fixed, small set of Fluid shapes, and the traversal below can only register
    # what it has seen. Doing it here closes the gap where a nested marker inside
    # another marker's kwargs would miss out and fall through to
    # `represent_undefined`. PyYAML dispatches on EXACT type, so `Partial` needs
    # its own entry even though it is a `Target` subclass.
    from confluid.fluid import Partial, Reference, Target

    for _fluid_cls in (Target, Partial, Reference):
        _LocalDumper.add_representer(_fluid_cls, _represent_object)

    # Catch-all fallback for opaque non-@configurable objects. PyYAML
    # documents add_representer(None, ...) as the catch-all hook, but
    # typeshed types the first arg as `type[Any]` and rejects None.
    _LocalDumper.add_representer(None, _represent_opaque)  # type: ignore[arg-type]

    def _discover_and_register(target: Any, visited: Optional[Set[int]] = None) -> None:
        if visited is None:
            visited = set()
        if id(target) in visited:
            return
        visited.add(id(target))

        from confluid.fluid import Fluid

        if isinstance(target, Fluid):
            # All four Fluid subclasses already have representers registered
            # above; recurse into kwargs to pick up @configurable types nested
            # inside them (so their post-construction attribute round-trip
            # still works).
            for v in getattr(target, "kwargs", {}).values():
                _discover_and_register(v, visited)
            return

        if hasattr(target.__class__, "__confluid_configurable__"):
            _LocalDumper.add_representer(target.__class__, _represent_object)
            # Traverse constructor params — the same _DUMP_KINDS projection the
            # representer walks, so discovery and emission cannot disagree.
            param_set: set[str] = set()
            for s in slots(target.__class__):
                if s.kind not in _DUMP_KINDS:
                    continue
                param_set.add(s.name)
                if hasattr(target, s.name):
                    _discover_and_register(getattr(target, s.name), visited)
            # Traverse captured ctor kwargs too — a nested configurable an
            # EAGER class stored under a private attr is reachable ONLY here,
            # and _represent_object will emit it via the captured fallback.
            for v in getattr(target, "__confluid_kwargs__", {}).values():
                _discover_and_register(v, visited)
            # Traverse post-construction attributes (set via @configurable)
            for name in getattr(target, "__confluid_extra__", []):
                if name in param_set:
                    continue
                val = getattr(target, name, None)
                if val is not None:
                    _discover_and_register(val, visited)
        elif hasattr(target, "__confluid_class__"):
            _LocalDumper.add_representer(target.__class__, _represent_object)
            if hasattr(target, "__confluid_kwargs__"):
                for v in target.__confluid_kwargs__.values():
                    _discover_and_register(v, visited)
        elif isinstance(target, (list, tuple)):
            for item in target:
                _discover_and_register(item, visited)
        elif isinstance(target, dict):
            for val in target.values():
                _discover_and_register(val, visited)

    _discover_and_register(obj)
    return yaml.dump(obj, Dumper=_LocalDumper, default_flow_style=False, sort_keys=False)
