"""hydraide — the preprocessor: one resolved, plain-YAML document per config.

``emit(source)`` runs the first seven passes of a load — parse (either
spelling), import, include, scope, interpolate, expand, broadcast — and
serializes the result: every marker carries its FINAL kwargs, every ordering
contest is settled, a shared marker is a YAML anchor, deferral is
``_partial_: true``. The emitted document is ordinary YAML that ``yaml.safe_load``
and ``yq`` read, and it reloads under confluid to the same graph.

"What did my config resolve to?" is therefore a file, not an experiment::

    text = emit("experiment.yaml", scopes=["framework=torch"])   # the resolved document
    diff = check("resolved.yaml")                                 # None, or a unified diff

This module ships the FUNCTIONS only. The ``hydraide`` command line
(``hydraide emit cfg.yaml [--scope k=v] [--output out.yaml]`` / ``hydraide check
cfg.yaml``) is one app of the CLI framework built on confluid, which already
provides config promotion, ``--scope`` / dimension flags, the search tiers and
the failure contract — nothing a second argument parser here would add.

Architecture record 19. Phase 1: this module is a WRAPPER — ``load(until="settled")`` is
passes 1–7 and ``dump()`` is the serializer; both existed. Nothing about the
engine changes here. The two invariants the tool rests on are pinned in
``tests/test_hydraide.py``: the two spellings of one document emit byte-identical
output, and ``emit(emit(x)) == emit(x)``.
"""

import difflib
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from loggair import get_logger

from confluid.dumper import dump
from confluid.fluid import Fluid
from confluid.loader import load

logger = get_logger("confluid.hydraide")


def _anchor_names(tree: Any) -> Dict[int, str]:
    """``id(value)`` → anchor name, for every value reached more than once.

    The name is the SHORTEST path at which the value appears (ties broken
    lexicographically), rendered as an anchor-legal identifier: ``preprocess``
    for a top-level value, ``preprocess_0`` for a list element, ``train_set_ops``
    for a nested key. Only markers and containers can be shared — a scalar is
    never aliased — and only values seen at two or more paths get a name, so the
    common single-occurrence case costs nothing at emit time.

    The walk is over the RESOLVED tree, where identity already means what the
    document meant: ``load(until="settled")`` shares a marker reached through ``${ref:}``
    and copies containers, so a list is copied while its elements are shared.
    """
    paths: Dict[int, List[str]] = {}
    keep: Dict[int, Any] = {}  # id-keyed store: the value IS the pin (the id-pinning rule)

    def walk(node: Any, path: str) -> None:
        if isinstance(node, (Fluid, dict, list)):
            paths.setdefault(id(node), []).append(path)
            keep[id(node)] = node
            if len(paths[id(node)]) > 1:
                return  # already walked once — its children are recorded
        if isinstance(node, Fluid):
            for key, value in node.kwargs.items():
                walk(value, f"{path}_{key}" if path else str(key))
        elif isinstance(node, dict):
            for key, value in node.items():
                walk(value, f"{path}_{key}" if path else str(key))
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{path}_{index}" if path else str(index))

    walk(tree, "")
    names: Dict[int, str] = {}
    used: Dict[str, int] = {}
    for object_id, seen_at in sorted(paths.items(), key=lambda kv: min(kv[1])):
        if len(seen_at) < 2:
            continue
        best = min((p for p in seen_at if p), key=lambda p: (len(p), p), default="")
        # An anchor is [0-9a-zA-Z_-]+ in YAML; the paths above are built from
        # keys and indices, so only a key carrying other characters needs the fold.
        name = "".join(ch if ch.isalnum() or ch in "_-" else "_" for ch in best) or "shared"
        # Two shortest paths can FOLD to one spelling (`m[0]` → `m_0` beside a key
        # literally named `m_0`) — the emitted YAML then carried a duplicate anchor
        # and no parser could read it back (BUGS-2026-08-19 CD15). Deterministic
        # suffixing keeps every anchor unique; iteration order is fixed by path, so
        # the same document always emits the same names.
        used[name] = used.get(name, 0) + 1
        names[object_id] = name if used[name] == 1 else f"{name}-{used[name]}"
    return names


def emit(source: Union[str, Path], *, scopes: Optional[List[str]] = None) -> str:
    """The resolved document for ``source`` (a path, or YAML text), as plain YAML.

    ``scopes`` are the activation strings a CLI would pass (``["framework=torch"]``).
    Raises the same located ``ConfigurationError`` a ``load()`` would for a
    malformed document — a preprocessor must not degrade what the loader refuses.
    """
    from confluid.loader import _IMPORT_ACCUMULATOR

    # Pass 2 CONSUMES `import:` — without re-emitting the directive the artefact
    # could not reload in a fresh process: its classes were registered only as a
    # side effect of this very load (BUGS-2026-08-19 CD13). The accumulator
    # collects every directive, included files' included, deduped in read order.
    imports: List[str] = []
    token = _IMPORT_ACCUMULATOR.set(imports)
    try:
        tree = load(source, until="settled", scopes=scopes)
    finally:
        _IMPORT_ACCUMULATOR.reset(token)
    if imports and isinstance(tree, dict):
        tree = {"import": imports if len(imports) > 1 else imports[0], **tree}
    return dump(tree, anchor_names=_anchor_names(tree))


def check(path: Union[str, Path], *, scopes: Optional[List[str]] = None) -> Optional[str]:
    """``None`` when ``path`` is its own resolution; else a unified diff.

    The property this rests on is idempotence — ``emit(emit(x)) == emit(x)`` —
    so a file hydraide wrote passes, and a file with an unresolved include, scope,
    dotted key or broadcast fails with the lines that would change.
    """
    path = Path(path)
    current = path.read_text()
    emitted = emit(path, scopes=scopes)
    if emitted == current:
        return None
    return "".join(
        difflib.unified_diff(
            current.splitlines(keepends=True),
            emitted.splitlines(keepends=True),
            fromfile=str(path),
            tofile=f"{path} (resolved)",
        )
    )
