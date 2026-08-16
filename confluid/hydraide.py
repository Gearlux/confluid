"""hydraide — the preprocessor: one resolved, plain-YAML document per config.

``emit(source)`` runs the first seven passes of a load — parse (either
spelling), import, include, scope, interpolate, expand, broadcast — and
serializes the result: every marker carries its FINAL kwargs, every ordering
contest is settled, a shared marker is a YAML anchor, deferral is
``_partial_: true``. The emitted document is ordinary YAML that ``yaml.safe_load``
and ``yq`` read, and it reloads under confluid to the same graph.

"What did my config resolve to?" is therefore a file, not an experiment::

    hydraide experiment.yaml --scope framework=torch -o resolved.yaml
    hydraide resolved.yaml --check        # is this file its own resolution?

Architecture record 19. Phase 1: this module is a WRAPPER — ``resolve()`` is
passes 1–7 and ``dump()`` is the serializer; both existed. Nothing about the
engine changes here. The two invariants the tool rests on are pinned in
``tests/test_hydraide.py``: the two spellings of one document emit byte-identical
output, and ``emit(emit(x)) == emit(x)``.
"""

import argparse
import difflib
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from loggair import get_logger

from confluid.dumper import dump
from confluid.engine import resolve
from confluid.exceptions import ConfluidError
from confluid.fluid import Fluid

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
    document meant: ``resolve()`` shares a marker reached through ``${ref:}``
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
    for object_id, seen_at in paths.items():
        if len(seen_at) < 2:
            continue
        best = min((p for p in seen_at if p), key=lambda p: (len(p), p), default="")
        # An anchor is [0-9a-zA-Z_-]+ in YAML; the paths above are built from
        # keys and indices, so only a key carrying other characters needs the fold.
        names[object_id] = "".join(ch if ch.isalnum() or ch in "_-" else "_" for ch in best) or "shared"
    return names


def emit(source: Union[str, Path], *, scopes: Optional[List[str]] = None) -> str:
    """The resolved document for ``source`` (a path, or YAML text), as plain YAML.

    ``scopes`` are the activation strings a CLI would pass (``["framework=torch"]``).
    Raises the same located ``ConfigurationError`` a ``load()`` would for a
    malformed document — a preprocessor must not degrade what the loader refuses.
    """
    tree = resolve(source, scopes=scopes)
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


def main(argv: Optional[List[str]] = None) -> int:
    """``hydraide CONFIG [--scope k=v ...] [-o OUT | --check]``.

    Exit codes: 0 emitted / already resolved; 1 ``--check`` found a difference
    (the diff is on stdout); 2 the document is malformed (the located error is on
    stderr — no traceback, the location IS the diagnostic).
    """
    parser = argparse.ArgumentParser(
        prog="hydraide",
        description="Resolve a confluid config to ONE plain-YAML document: includes spliced, scopes "
        "applied, dotted keys expanded, broadcasting settled, shared markers anchored.",
    )
    parser.add_argument("config", help="the YAML file to resolve (either spelling)")
    parser.add_argument(
        "--scope",
        action="append",
        default=[],
        metavar="KEY[=VALUE]",
        help="activate a scope dimension; repeatable (e.g. --scope framework=torch --scope debug)",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("-o", "--output", metavar="FILE", help="write the resolved document here (default: stdout)")
    mode.add_argument(
        "--check",
        action="store_true",
        help="exit 1 with a diff if CONFIG is not already its own resolution",
    )
    args = parser.parse_args(argv)

    try:
        if args.check:
            diff = check(args.config, scopes=args.scope)
            if diff is None:
                return 0
            sys.stdout.write(diff)
            return 1
        text = emit(args.config, scopes=args.scope)
    except ConfluidError as exc:
        # A located error is the whole diagnostic; a traceback through the engine
        # would bury the file:line:col that names the offending mapping.
        sys.stderr.write(f"hydraide: {exc}\n")
        return 2

    if args.output:
        Path(args.output).write_text(text)
        logger.debug(f"hydraide: wrote {args.output} ({len(text)} bytes)")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
