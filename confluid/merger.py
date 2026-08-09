from copy import deepcopy
from typing import Any, Callable, Dict, cast

from confluid.fluid import Fluid


def deep_merge(base: Dict[str, Any], overlay: Dict[str, Any]) -> Dict[str, Any]:
    """
    Recursively merge overlay into base.
    Returns a new dictionary.

    Live Fluid markers (Class/Instance/Reference/Clone) are preserved by
    identity so that ``!ref:`` resolution stays consistent across contexts.
    Other values are deep-copied for safety.
    """
    result: Dict[str, Any] = cast(
        Dict[str, Any],
        _preserve_identity_copy(base) if isinstance(base, dict) else deepcopy(base),
    )
    for key, value in overlay.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = _preserve_identity_copy(value)
    return result


def _preserve_identity_copy(value: Any) -> Any:
    """Deepcopy ordinary containers but preserve identity of live Fluid objects.

    Fluid markers (Class, Instance, Reference, Clone) represent *one* logical
    configuration citizen. Deep-copying them here would undo the Resolver's
    ``!ref:`` resolution, causing two references to the same Fluid to produce
    two separate live instances downstream. We keep identity intact and let
    ``!clone:`` opt into explicit deepcopy when independence is wanted.
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
    result: Dict[str, Any] = {}
    for k, v in mapping.items():
        if "." not in k:
            result[k] = copy_value(v)
        else:
            head = k.split(".", 1)[0]
            if head not in result and head not in mapping:
                result[head] = {}
    dotted = sorted((k for k in mapping if "." in k), key=lambda k: (k.count("."), k))
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
