from copy import copy, deepcopy
from typing import Any, Callable, Dict, cast

from confluid.fluid import Fluid, Target


def deep_merge(base: Dict[str, Any], overlay: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively merge ``overlay`` into ``base``; returns a NEW dictionary.

    Live Fluid markers (Target/Partial/Reference/Clone) are preserved by
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
        _preserve_identity_copy(base) if isinstance(base, dict) else deepcopy(base),
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
            # A ``Reference`` / ``Clone`` base keeps the previous replace behaviour —
            # the classifier calls those opaque, and widening here would invent a
            # semantic for them that no other path has.
            tuned = copy(existing)
            # COPY, never mutate: markers are preserved by identity through
            # ``_preserve_identity_copy``, so the base document's marker is the same
            # object and tuning it in place would rewrite the included file for every
            # other consumer of it.
            tuned.kwargs = deep_merge(existing.kwargs, value)
            merged = tuned
        else:
            merged = _preserve_identity_copy(value)
        # Re-anchor: delete before re-inserting so the key lands at the overlay's
        # position instead of keeping the base's (see the docstring).
        result.pop(key, None)
        result[key] = merged
    return result


def _preserve_identity_copy(value: Any) -> Any:
    """Deepcopy ordinary containers but preserve identity of live Fluid objects.

    Fluid markers (Target, Partial, Reference, Clone) represent *one* logical
    configuration citizen. Deep-copying them here would undo the Resolver's
    reference resolution, causing two references to the same Fluid to produce
    two separate live instances downstream. We keep identity intact and let
    ``${clone:...}`` opt into explicit deepcopy when independence is wanted.
    (Identity is also what lets ``_prepare_kwargs``'s ``self_obj`` check
    locate the receiving marker's slot in its ambient context.)
    """
    if isinstance(value, Fluid):
        return value
    if isinstance(value, dict):
        return {k: _preserve_identity_copy(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_preserve_identity_copy(item) for item in value]
    return deepcopy(value)


def expand_dotted_mapping(
    mapping: Dict[str, Any],
    *,
    copy_value: Callable[[Any], Any],
    merge_leaf: Callable[[Dict[str, Any], Dict[str, Any]], Dict[str, Any]],
    descend: Callable[[Dict[str, Any], str, Dict[str, Any]], Dict[str, Any]],
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

    * ``copy_value`` — what seeding/assigning a value does (deep-copy with
      Fluid identity preserved for the document top level, share-by-reference
      inside a block);
    * ``merge_leaf`` — how a dict landing on an existing dict combines
      (``deep_merge`` vs a shallow last-write union);
    * ``descend`` — how the walk enters an existing dict (in place over the
      caller's own fresh copies, or copy-on-write over shared references).
    """
    # A non-str key (an int class id, a float threshold) is DATA, never a dotted config path,
    # so it passes through untouched. The guard is what lets the loader preserve YAML's own
    # key types instead of str()-ing the whole document to keep this walk safe.
    result: Dict[Any, Any] = {}
    for k, v in mapping.items():
        if not isinstance(k, str) or "." not in k:
            result[k] = copy_value(v)
        else:
            head = k.split(".", 1)[0]
            if head not in result and head not in mapping:
                result[head] = {}
    dotted = sorted((k for k in mapping if isinstance(k, str) and "." in k), key=lambda k: (k.count("."), k))
    for key in dotted:
        value = mapping[key]
        parts = key.split(".")
        cur: Dict[str, Any] = result
        for part in parts[:-1]:
            nxt = cur.get(part)
            if isinstance(nxt, Fluid):
                cur = nxt.kwargs
                continue
            if isinstance(nxt, dict):
                cur = descend(cur, part, nxt)
                continue
            fresh: Dict[str, Any] = {}
            cur[part] = fresh
            cur = fresh
        last = parts[-1]
        prev = cur.get(last)
        if isinstance(prev, dict) and isinstance(value, dict):
            cur[last] = merge_leaf(prev, value)
        else:
            cur[last] = copy_value(value)
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
    hooks. This document-level policy deep-copies values with Fluid identity
    preserved and ``deep_merge``s a dict landing on an existing dict.
    """
    return expand_dotted_mapping(
        data,
        copy_value=_preserve_identity_copy,
        merge_leaf=deep_merge,
        descend=_descend_in_place,
    )
