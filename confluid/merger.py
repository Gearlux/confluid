from copy import copy
from typing import Any, Callable, Dict, FrozenSet, List, Optional, cast

from confluid.exceptions import ConfigurationError
from confluid.fluid import Fluid, ScopeBlock, Target


def _same_marker_target(a: Any, b: Any) -> bool:
    """Two marker targets name the SAME class: by registered/last-segment name.

    A string spelling may be dotted (``pkg.mod.Trainer``) while the other side
    holds the class itself; compare the registered name (``__confluid_name__``)
    or ``__name__`` against the string's last segment. A selector or call
    spelling (``@axis=$key`` / ``Cls(...)`` remnants) is never "the same" —
    conservative replace keeps its semantics untouched.
    """

    def name_of(t: Any) -> Optional[str]:
        if isinstance(t, str):
            text: str = t[:-2] if t.endswith("()") else t
            if "@" in text or "(" in text:
                return None
            return text.rsplit(".", 1)[-1]
        registered = t.__dict__.get("__confluid_name__") if hasattr(t, "__dict__") else None
        if registered:
            return str(registered)
        dunder_name = getattr(t, "__name__", None)
        return dunder_name if isinstance(dunder_name, str) else None

    name_a, name_b = name_of(a), name_of(b)
    return name_a is not None and name_a == name_b


def deep_merge(base: Dict[str, Any], overlay: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively merge ``overlay`` into ``base``; returns a NEW dictionary.

    Live Fluid markers (Target/Partial/Reference) are preserved by
    identity so that reference resolution stays consistent across contexts.
    Other values are deep-copied for safety.

    **A key the overlay re-states takes the OVERLAY's position** (2026-08-11).
    Key order is not cosmetic here: confluid has ONE precedence rule — document
    order, last spec wins — so the merged order IS the arbitration. Python
    preserves the original position when you assign to an existing key, which
    meant an overridden key silently kept the position it had in the BASE, and
    the most common composition in the system inverted its own rule::

        # base.yaml            # main.yaml
        lr: 0.1                include: base.yaml
        Stage:                 lr: 0.3            <- written last, LOST (0.2 won)
          lr: 0.2              s: {_target_: Stage}

    ``lr`` sat at the base's position 0, ahead of the ``Stage:`` block it was
    written after, so the included file's addressed value won — while the same
    document written flat gave 0.3. Re-anchoring makes the merged order "every
    key the overlay did not mention, in base order, then the overlay's keys in
    overlay order", i.e. exactly "the including file is read after the file it
    includes". Sibling rule, same reasoning: ``expand_dotted_mapping`` anchors a
    fresh head at the position the dotted spelling was WRITTEN.

    Pins: ``tests/test_includes.py`` (the ordering group), ``tests/test_merger.py``.
    """
    result: Dict[str, Any] = cast(
        Dict[str, Any],
        _preserve_identity_copy(base),
    )
    for key, value in overlay.items():
        existing = result.get(key) if key in result else None
        merged: Any  # a merged dict, a tuned marker, or a copied leaf
        if key in result and isinstance(existing, dict) and isinstance(value, dict):
            merged = deep_merge(existing, value)
        elif isinstance(existing, Target) and isinstance(value, dict):
            # A mapping merged OVER a marker TUNES it — the same rule the two
            # materialization paths dispatch through ``broadcast.dict_at_slot_kind``,
            # applied to document COMPOSITION, which was a third site nobody counted
            # (BUGS-2026-08-13 P1). A ``Fluid`` is not a ``dict``, so the branch above
            # missed it and the overlay replaced the whole marker: the single most
            # common composition in the system — a base file defining the model, an
            # experiment file overriding one knob — silently produced a plain dict with
            # no target and no error, while the DOTTED spelling of the same override
            # tuned it correctly.
            #
            # The recursion is load-bearing, and is why this is not a call to
            # ``broadcast.tune_marker`` (which is single-level by design, and lives
            # above this module besides — ``broadcast`` imports ``merger``): the dotted
            # and class-block spellings both reach a marker NESTED inside the
            # overridden one, so composition has to as well. Recursing through
            # ``deep_merge`` also keeps the re-anchoring rule below applying at every
            # depth.
            #
            # ``Target`` and not ``Fluid``: this mirrors the classifier's "marker" arm.
            # A ``Reference`` base keeps the previous replace behaviour —
            # the classifier calls those opaque, and widening here would invent a
            # semantic for them that no other path has.
            tuned = copy(existing)
            # COPY, never mutate: markers are preserved by identity through
            # ``_preserve_identity_copy``, so the base document's marker is the same
            # object and tuning it in place would rewrite the included file for every
            # other consumer of it.
            tuned.kwargs = deep_merge(existing.kwargs, value)
            merged = tuned
        elif (
            isinstance(existing, Target)
            and type(value) is type(existing)
            and _same_marker_target(existing.target, value.target)
        ):
            # A marker merged over a marker of the SAME target TUNES it, exactly as
            # the mapping spelling above does (CD5, BUGS-2026-08-19): the class-block
            # and marker spellings of one override must agree, and replacing dropped
            # the base marker's unmentioned children silently. A DIFFERENT target (or
            # a different marker kind — partial over class) still replaces: that is a
            # genuine swap, and the selector spelling (`@axis=$key`) never tunes.
            tuned = copy(existing)
            tuned.kwargs = deep_merge(existing.kwargs, value.kwargs)
            merged = tuned
        elif (
            isinstance(existing, ScopeBlock)
            and isinstance(value, ScopeBlock)
            and existing.dims == value.dims
            and existing.negate == value.negate
        ):
            # The SAME wrapper key, the SAME condition, on both sides of an include
            # (``base.yaml`` declares ``torch: !scope:framework=torch {lr}``, the
            # including file adds ``torch: !scope:framework=torch {epochs}``): written
            # flat in one file the duplicate key is refused, so through an include
            # the author means ONE block — merge the contents (BUGS-2026-08-19 PA4).
            # A ``ScopeBlock`` is neither a dict nor a ``Target``, so it fell to the
            # replace arm below and the base file's keys vanished silently. A
            # DIFFERENT condition is a different block and still replaces. The
            # merged block carries the overlay's location (the later writer).
            both_mappings = isinstance(existing.contents, dict) and isinstance(value.contents, dict)
            merged = ScopeBlock(
                dict(value.dims),
                value.negate,
                (
                    deep_merge(existing.contents, value.contents)
                    if both_mappings
                    else _preserve_identity_copy(value.contents)
                ),
            )
            merged._yaml_loc = value._yaml_loc
        else:
            merged = _preserve_identity_copy(value)
        # Re-anchor: delete before re-inserting so the key lands at the overlay's
        # position instead of keeping the base's (see the docstring).
        result.pop(key, None)
        result[key] = merged
    return result


def document_copy(value: Any, _memo: "Optional[Dict[int, Any]]" = None) -> Any:
    """A load-private copy of an already-parsed document — the passes mutate THE COPY.

    ``load(data)`` runs passes that write in place — scope resolution rewrites a
    marker's kwargs, interpolation burns in, ``import:`` is popped — so a RAW
    document loaded twice answered with the FIRST call's activation and
    ``discover_dimension_values(raw)`` went empty after one load (BUGS-2026-08-19
    SR3; the pop was PA26's caller-dict half, the kwarg write BC20). Containers,
    markers and scope blocks are rebuilt; markers are copied THROUGH the memo so a
    shared marker stays ONE marker in the copy (`${ref:}` identity — and the memo is
    also what lets an explicit `context` share the copy with `data`); every other
    leaf keeps identity, exactly as `_preserve_identity_copy` treats leaves.
    """
    memo: Dict[int, Any] = _memo if _memo is not None else {}
    if id(value) in memo:
        return memo[id(value)]
    if isinstance(value, Fluid):
        duplicate = copy(value)
        memo[id(value)] = duplicate  # the memo entry pins what it keys on (record 16)
        duplicate.kwargs = {k: document_copy(v, memo) for k, v in value.kwargs.items()}
        return duplicate
    if isinstance(value, ScopeBlock):
        block = ScopeBlock(dict(value.dims), value.negate, document_copy(value.contents, memo))
        block._yaml_loc = value._yaml_loc
        memo[id(value)] = block
        return block
    if isinstance(value, dict):
        fresh_map: Dict[Any, Any] = {}
        memo[id(value)] = fresh_map
        for k, v in value.items():
            fresh_map[k] = document_copy(v, memo)
        return fresh_map
    if isinstance(value, list):
        fresh_list: List[Any] = []
        memo[id(value)] = fresh_list
        fresh_list.extend(document_copy(v, memo) for v in value)
        return fresh_list
    return value


def _preserve_identity_copy(value: Any) -> Any:
    """Rebuild the CONTAINERS, keep every LEAF — a structural copy, never a deep one.

    A merge must not mutate its inputs, and that is a property of the containers:
    ``deep_merge`` and ``expand_dotted_mapping`` rebuild dicts, lists, tuples and
    ``ScopeBlock``s so the base document's mappings are never written through.
    A leaf is never mutated by either — it is replaced, or (a marker) tuned into a
    copy — so a leaf is returned AS-IS:

    * a ``Fluid`` marker represents *one* configuration citizen; copying it would
      undo reference resolution (two references to one marker → two instances) and
      break ``_prepare_kwargs``'s identity check for the receiving slot;
    * any OTHER object is a LIVE value the document carries — a dataset, a model, a
      lock — and the caller expects THAT object on the other side. Until 2026-08-19
      this branch was ``deepcopy(value)``: a dataset handed through ``load(<dict>)``
      or ``configure(config=...)`` came out as a silent copy, the named
      ``configure(trainer=t, config={"trainer": {...}})`` spelling copied every
      attribute it did not mention, and an uncopyable value raised a raw
      ``TypeError: cannot pickle`` (BUGS-2026-08-19 PA12 / CD3 / CD4);
    * a ``ScopeBlock`` is a container of its contents — rebuilt, with the markers
      inside kept by identity; ``deepcopy`` duplicated an anchored marker aliased
      inside a block whenever the document also had an ``include:`` (PA5).
    """
    if isinstance(value, Fluid):
        return value
    if isinstance(value, dict):
        return {k: _preserve_identity_copy(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_preserve_identity_copy(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_preserve_identity_copy(item) for item in value)
    if isinstance(value, ScopeBlock):
        block = ScopeBlock(dict(value.dims), value.negate, _preserve_identity_copy(value.contents))
        block._yaml_loc = value._yaml_loc
        return block
    return value


def expand_dotted_mapping(
    mapping: Dict[str, Any],
    *,
    copy_value: Callable[[Any], Any],
    merge_leaf: Callable[[Dict[str, Any], Dict[str, Any]], Dict[str, Any]],
    descend: Callable[[Dict[str, Any], str, Dict[str, Any]], Dict[str, Any]],
    stamp_positions: bool = False,
) -> Dict[str, Any]:
    """The ONE dotted-key expansion grammar; policy hooks carry copy semantics.

    ``a.b.c: v`` nests to ``{a: {b: {c: v}}}``, existing keys are merged (never
    shadowed), a ``Fluid`` value's kwargs are descended into IN PLACE (both
    callers rely on marker identity surviving expansion), and — since 2026-08-09
    — a HEAD that no other key claims is anchored at the position the dotted
    spelling was WRITTEN: creating it by assignment in the walk below means
    APPENDED at the end of the mapping, which made a dotted key unbeatable by
    anything written after it (the scanner orders by position, and document
    order is the one precedence rule).

    The grammar existed twice — here for the document's top level, and in the
    broadcast layer for in-block dotted keys — ~40 near-identical lines whose
    only real differences are the three POLICY hooks:

    * ``copy_value`` — what seeding/assigning a value does (a structural copy —
      containers rebuilt, markers and live leaves kept by identity — for the
      document top level, share-by-reference inside a block);
    * ``merge_leaf`` — how a dict landing on an existing dict combines
      (``deep_merge`` vs a shallow last-write union);
    * ``descend`` — how the walk enters an existing dict (in place over the
      caller's own fresh copies, or copy-on-write over shared references).
    """
    # A non-str key (an int class id, a float threshold) is DATA, never a dotted config path,
    # so it passes through untouched. The guard is what lets the loader preserve YAML's own
    # key types instead of str()-ing the whole document to keep this walk safe.
    result: Dict[Any, Any] = {}
    # ONE pass, in DOCUMENT ORDER. It used to be two — every plain key first, then
    # every dotted key sorted by depth and then ALPHABETICALLY — so a dotted leaf
    # was always applied last and could not lose to anything written after it:
    # `a.b: 1` beat a later `a: {b: 0}`, and the two orderings of one leaf gave the
    # SAME answer, against the one precedence rule (BUGS-2026-08-13 P7). The head
    # was already anchored where written (the fresh-head pins in
    # tests/test_document_order.py); this is the same bug one level down, at the leaf.
    for key, value in mapping.items():
        if not isinstance(key, str) or "." not in key:
            # Symmetric with the LEAF assignment at the bottom of the loop: a dict
            # landing on a dict MERGES (last spec wins per key), anything else
            # replaces. In the old two-pass form the plain keys were all placed
            # before any dotted key, so this branch never met one; in document order
            # it does, and an unconditional assign would drop `a.**.x` when a later
            # `a: {'**': {y}}` block arrived — merging keeps both, which is what
            # `deep_merge` does for the same shape.
            prev = result.get(key)
            if isinstance(prev, dict) and isinstance(value, dict):
                result[key] = merge_leaf(prev, value)
            else:
                result[key] = copy_value(value)
            continue
        parts = key.split(".")
        cur: Dict[str, Any] = result
        owner: Any = None  # the Fluid whose kwargs `cur` is, when it is one
        for part in parts[:-1]:
            nxt = cur.get(part)
            if isinstance(nxt, Fluid):
                owner = nxt
                cur = nxt.kwargs
                continue
            owner = None
            if isinstance(nxt, dict):
                cur = descend(cur, part, nxt)
                continue
            if isinstance(nxt, str) and "${" in nxt:
                # A `${...}` string is a not-yet-resolved placeholder (expansion runs
                # before interpolation) — replacing it with a fresh dict would silently
                # lose it. Value substitution is not sharing; tuning goes through a
                # reference.
                raise ConfigurationError(
                    f"the dotted key {key!r} writes through {part!r}, which holds the unresolved "
                    f"placeholder {nxt!r} — a dotted write cannot land inside a substituted value. "
                    f"Write the keys at the placeholder's referent, or share and tune the one object "
                    f"with ${{ref:...}} / !ref: instead."
                )
            fresh: Dict[str, Any] = {}
            cur[part] = fresh
            cur = fresh
        last = parts[-1]
        prev = cur.get(last)
        if isinstance(prev, dict) and isinstance(value, dict):
            cur[last] = merge_leaf(prev, value)
        else:
            cur[last] = copy_value(value)
        if stamp_positions and owner is not None and cur is owner.kwargs:
            # The value landed DIRECTLY in a marker's kwargs, which moves it to the
            # MARKER's document position — the dotted line's own position would be
            # silently lost (`t.lr: 9.0` as the last line lost to a bare `lr:` between
            # it and the marker — BUGS-2026-08-19 BC4). Stamp the kwarg with the keys
            # written BEFORE the line (`result`'s keys at this point in the one ordered
            # pass): the scanner skips a cascade delivery the line out-positioned, and
            # `dump()` re-emits the line after those keys so an emitted artefact
            # replays identically. A dotted path ending in a PLAIN dict (a fresh head,
            # a dict-valued slot) is untouched — those contests already order by the
            # existing machinery and are pinned.
            stamps: Dict[str, FrozenSet[str]] = getattr(owner, "_dotted_out_positioned", None) or {}
            stamps[last] = frozenset(k for k in result if isinstance(k, str))
            owner._dotted_out_positioned = stamps
    return result


def _descend_in_place(cur: Dict[str, Any], part: str, nxt: Dict[str, Any]) -> Dict[str, Any]:
    """Walk into an existing dict directly — safe over the caller's own copies."""
    return nxt


def expand_dotted_keys(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Expand flat dictionary with dotted keys into a nested dictionary.
    Ensures that existing keys are NOT shadowed but merged.

    Glob segments (``'*'`` / ``'**'``) are ordinary path parts here —
    ``trainer.**.lr: v`` nests to ``{trainer: {'**': {lr: v}}}`` and the
    broadcast layer (``broadcast._prepare_kwargs`` / ``configurator._apply``)
    interprets the literal block names. Only the document's TOP-LEVEL keys
    are expanded; dotted keys inside nested blocks are expanded lazily at
    block-consumption time by ``broadcast._expand_block_keys`` — the SAME
    grammar (:func:`expand_dotted_mapping`) under reference-sharing policy
    hooks. This document-level policy copies values structurally (containers
    rebuilt, markers and live leaves kept by identity) and ``deep_merge``s a
    dict landing on an existing dict.
    """
    return expand_dotted_mapping(
        data,
        copy_value=_preserve_identity_copy,
        merge_leaf=deep_merge,
        descend=_descend_in_place,
        stamp_positions=True,  # document top level — dotted lines keep their position (BC4)
    )
