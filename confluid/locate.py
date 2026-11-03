"""Where each name in a config file points — the class a marker builds, the file an ``include:`` reads, the module an
``import:`` loads — for an editor's go-to-definition.

:func:`locate` reads ONE config text (the editor's buffer, saved or not) and returns its :class:`Mention` s: each
``!class:`` / ``!partial:`` / ``!lazy:`` tag and ``_target_:`` value, each ``include:`` entry, each ``pkg://`` value
and each ``config:`` path, and each ``import:`` module, at the range the editor underlines, with the place it points
to. It is a reading of the text: PyYAML composes nodes, nothing is constructed — and nothing is IMPORTED either. A
dotted class (``pkg.mod.Class``) is found in its source by walking the import system's finders (the editable
installs' included) and reading files with :mod:`ast`; a config file goes through
:func:`confluid.resolve_config_path`, as :func:`confluid.load` reads it.

A registry name (``!class:Trainer``) is known only once the module registering it has run, so its mention comes back
``deferred``: :func:`resolve_target` answers it on request — the first call imports every ``confluid.configurables``
entry point (:func:`confluid.load_configurables`), which can take a minute in a large environment; later calls are
instant. A dotted name the source reading cannot follow (a class made at run time) is deferred the same way.

``python -m confluid.locate --serve`` answers both over stdin/stdout, one JSON request per line — the protocol an
editor extension speaks; ``python -m confluid.locate FILE`` prints a file's mentions as JSON.
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import importlib
import inspect
import json
import os
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import IO, Any, Container, Dict, Iterator, List, Literal, Optional, Sequence, Set, Tuple, Union

import yaml

from confluid.exceptions import ConfluidError
from confluid.keys import TARGET_KEY
from confluid.loader import resolve_config_path
from confluid.packages import is_package_reference, resolve_package_reference
from confluid.registry import load_configurables, resolve_class

__all__ = ["Location", "Mention", "MentionKind", "Resolution", "locate", "main", "resolve_target", "serve"]

#: What a mention names: a class (a marker's target), a config file (an ``include:`` entry, a ``pkg://`` value or a
#: ``config:`` path), a module (``import:``).
MentionKind = Literal["class", "include", "import"]
#: What the text reading finds: a :data:`MentionKind`, or ``"run"`` — the config file another run reads (a ``config:``
#: value), reported as an ``include`` but looked up from the project folder only, as that run finds it.
_FoundKind = Literal["class", "include", "import", "run"]

#: The tag prefixes of a marker that builds (or defers) a class.
_CLASS_TAGS = ("!class:", "!partial:", "!lazy:")
#: The keys whose string value names the config file another run reads (a workflow step's ``config: <file>.yaml``).
_RUN_KEYS = ("config",)
#: How a config file name ends: what ``load()`` takes for a file name, not for YAML text.
_CONFIG_SUFFIXES = (".yaml", ".yml")
#: The tag PyYAML gives a plain or quoted string scalar — a tagged scalar (``!ref:…``) is something else.
_STR_TAG = "tag:yaml.org,2002:str"
#: A target as a tag or ``_target_`` writes it: the dotted name, then an optional ``@axis=value`` selector.
_TARGET = re.compile(r"[A-Za-z_][\w.]*(?:@[^\s(){}\[\],]*)?")
#: The line of a definition, below its decorators.
_DEFINITION = re.compile(r"\s*(?:async\s+def|def|class)\s")
#: One mention as the text reading finds it: ``(kind, name, line, start, stop)``, columns in characters.
_Found = Tuple[_FoundKind, str, int, int, int]
#: How many ``from … import Name`` re-exports are followed to the module defining a class.
_REEXPORT_HOPS = 4


@dataclass(frozen=True)
class Location:
    """A place in a file: its absolute path and a 0-based line (0 for a whole file)."""

    path: str
    line: int


@dataclass(frozen=True)
class Mention:
    """One name in a config text and where it points.

    ``line`` is 0-based; ``start``/``stop`` bound the name on that line, 0-based and counted in UTF-16 code units —
    the editor's columns. ``target`` is None when the name points nowhere this reading can find; ``deferred`` says
    :func:`resolve_target` may still find it (a class only).
    """

    kind: MentionKind
    text: str
    line: int
    start: int
    stop: int
    target: Optional[Location]
    deferred: bool = False


@dataclass(frozen=True)
class Resolution:
    """What :func:`resolve_target` found: the definition, or None and the reason in plain words."""

    target: Optional[Location]
    reason: Optional[str] = None


def locate(text: str, path: Union[str, Path], kinds: Optional[Container[str]] = None) -> List[Mention]:
    """Every class, config file and import ``text`` names, in text order, each with where it points.

    ``path`` is the file the text belongs to: a relative ``include:`` is looked up beside it first, then in its
    project — the nearest folder above it holding a ``pyproject.toml`` — and that project's ``config/``, then the
    XDG folders, the tiers :func:`confluid.load` searches with the project as the working folder. A ``config:``
    value ending ``.yaml`` or ``.yml`` is the config another run reads (``recordstream run <config>`` from the project
    folder): the same tiers, but never beside ``path``. A ``pkg://<distribution>/<path>`` value is a config file
    under any key. A text PyYAML cannot read (half typed) still gives the marker tags written before the error.

    Args:
        text: The config text — the editor's buffer.
        path: The file it is (or will be) saved as.
        kinds: The kinds of mention wanted (``"class"``, ``"include"``, ``"import"``); None: every one. A kind left
            out is not looked up — finding a class's definition reads its source, which a reader that wants only the
            includes (a front end naming each include as the file writes it) need not pay for.

    Returns:
        The mentions, in text order.
    """
    source = Path(path).expanduser().resolve()
    lines = text.splitlines()
    mentions: List[Mention] = []
    for kind, name, line, start, stop in _mentions(text):
        if kinds is not None and ("include" if kind == "run" else kind) not in kinds:
            continue
        target: Optional[Location]
        if kind == "class":
            target = _class_location(name)
        elif kind in ("include", "run"):
            target = _include_location(name, source, beside=kind == "include")
        else:
            target = _module_location(name)
        row = lines[line] if line < len(lines) else ""
        mentions.append(
            Mention(
                kind="include" if kind == "run" else kind,
                text=name,
                line=line,
                start=_utf16(row, start),
                stop=_utf16(row, stop),
                target=target,
                deferred=kind == "class" and target is None,
            )
        )
    return mentions


#: Whether :func:`resolve_target` has imported the ``confluid.configurables`` entry points in this process.
_REGISTRY_LOADED = False


def resolve_target(name: str, imports: Sequence[str] = ()) -> Resolution:
    """The definition of the class ``name`` (a registry name or a dotted path, with or without a selector).

    The slow path behind a ``deferred`` mention: it imports — once per process — every ``confluid.configurables``
    entry point, then the ``imports`` the document lists (its ``import:`` modules), and asks the registry
    (:func:`confluid.resolve_class`, which also imports a dotted path's module). A name two registered classes share
    is ambiguous and answers nothing, with the registry's own message.

    Args:
        name: The target as written (``Trainer``, ``Trainer@task=detection``, ``pkg.mod.Class``).
        imports: Modules to import first — the document's ``import:`` entries.

    Returns:
        The definition, or None with the reason.
    """
    global _REGISTRY_LOADED
    if not _REGISTRY_LOADED:
        load_configurables()
        _REGISTRY_LOADED = True
    for module in imports:
        try:
            importlib.import_module(module)
        except Exception:  # noqa: BLE001 — the loader only warns on a failed import: so does the lookup
            continue
    try:
        found = resolve_class(name, strict=True)
    except ConfluidError as exc:
        return Resolution(None, str(exc))
    if found is None:
        return Resolution(None, f"no class or function named {name!r} is registered or importable")
    target = inspect.unwrap(found)
    try:
        file = inspect.getsourcefile(target)
        lines, first = inspect.getsourcelines(target)
    except (OSError, TypeError):  # a built-in, or a class whose source is not on disk
        file = None
    if file is None:
        return Resolution(None, f"{name!r} is {getattr(target, '__qualname__', target)!r}, which has no source file")
    # The source starts at the first decorator; the definition is the `class` / `def` line, as the source reading finds.
    offset = next((i for i, text in enumerate(lines) if _DEFINITION.match(text)), 0)
    return Resolution(Location(str(Path(file).resolve()), max(first - 1, 0) + offset))


def serve(stdin: IO[str], stdout: IO[str]) -> None:
    """Answer JSON requests, one per line, until ``stdin`` ends — the editor extension's protocol.

    A request is ``{"id": 1, "method": "locate", "params": {"path": ..., "text": ...}}`` (answered with the list of
    mentions) or ``{"id": 2, "method": "resolve", "params": {"name": ..., "imports": [...]}}`` (answered with
    ``{"target": ..., "reason": ...}``). The answer is ``{"id": 1, "result": ...}``, or ``{"id": 1, "error":
    "..."}`` when the request cannot be answered; a line that is not a request gets ``"id": null``.
    """
    for line in stdin:
        if not line.strip():
            continue
        request_id: Any = None
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError("a request is a JSON object")
            request_id = request.get("id")
            reply: Dict[str, Any] = {"id": request_id, "result": _answer(request)}
        except Exception as exc:  # noqa: BLE001 — one bad request never ends the session
            reply = {"id": request_id, "error": f"{type(exc).__name__}: {exc}"}
        stdout.write(json.dumps(reply) + "\n")
        stdout.flush()


def main(argv: Optional[Sequence[str]] = None) -> int:
    """``python -m confluid.locate FILE`` (print its mentions as JSON) or ``--serve`` (the editor protocol)."""
    parser = argparse.ArgumentParser(
        prog="python -m confluid.locate", description="Where each class, include and import of a config points."
    )
    parser.add_argument("file", nargs="?", help="a config file whose mentions to print as JSON")
    parser.add_argument("--serve", action="store_true", help="answer JSON requests on stdin, one per line")
    args = parser.parse_args(argv)
    if args.serve:
        with _protocol_stdout() as out:
            serve(sys.stdin, out)
        return 0
    if args.file is None:
        parser.error("give a FILE, or --serve")
    file = Path(args.file)
    if not file.is_file():
        parser.error(f"no such file: {args.file}")
    json.dump([asdict(m) for m in locate(file.read_text(encoding="utf-8"), file)], sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


def _answer(request: Dict[str, Any]) -> Any:
    """The result of one request; an unknown method or a missing parameter raises."""
    method = request.get("method")
    params = request.get("params") or {}
    if method == "locate":
        return [asdict(m) for m in locate(str(params["text"]), str(params["path"]))]
    if method == "resolve":
        return asdict(resolve_target(str(params["name"]), [str(m) for m in params.get("imports") or ()]))
    raise ValueError(f"unknown method {method!r} — use 'locate' or 'resolve'")


@contextlib.contextmanager
def _protocol_stdout() -> Iterator[IO[str]]:
    """The process's real stdout, for the protocol alone; anything else written to stdout goes to stderr.

    An imported module that prints (or a C library writing to file descriptor 1) would otherwise corrupt the JSON
    stream the editor reads.
    """
    sys.stdout.flush()
    protocol = os.fdopen(os.dup(1), "w", encoding="utf-8")
    saved = sys.stdout
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    try:
        yield protocol
    finally:
        sys.stdout = saved
        protocol.close()


# -- reading the text ---------------------------------------------------------------------------------------------


def _mentions(text: str) -> Iterator[_Found]:
    """``(kind, name, line, start, stop)`` for each mention, columns in characters; tags only on a broken text."""
    try:
        roots = list(yaml.compose_all(text, Loader=yaml.SafeLoader))
    except yaml.YAMLError:
        yield from _tag_mentions(text)
        return
    found: List[_Found] = []
    seen: Set[int] = set()
    for root in roots:
        found.extend(_node_mentions(root, text, seen))
    yield from sorted(found, key=lambda m: (m[2], m[3]))


def _node_mentions(node: Optional[yaml.Node], text: str, seen: Set[int]) -> Iterator[_Found]:
    """The mentions under ``node``, depth first; a node an alias repeats is read once."""
    if node is None or id(node) in seen:
        return
    seen.add(id(node))
    tag = node.tag or ""
    for prefix in _CLASS_TAGS:
        if tag.startswith(prefix):
            found = _tag_name(text, node.start_mark.index, tag, prefix)
            if found is not None:
                yield ("class", *found)
            break
    if isinstance(node, yaml.MappingNode):
        for key, value in node.value:
            name = key.value if isinstance(key, yaml.ScalarNode) else None
            if name == TARGET_KEY and isinstance(value, yaml.ScalarNode):
                target = _scalar_span(value, text)
                if target is not None:
                    match = _TARGET.match(target[0])
                    if match:
                        yield ("class", match.group(0), target[1], target[2], target[2] + match.end())
            elif name in ("include", "import"):
                kind: _FoundKind = "include" if name == "include" else "import"
                items = value.value if isinstance(value, yaml.SequenceNode) else [value]
                for item in items:
                    if isinstance(item, yaml.ScalarNode):
                        span = _scalar_span(item, text)
                        if span is not None and span[0].strip():
                            seen.add(id(item))  # read here: the pkg:// rule below must not report it again
                            yield (kind, *span)
            elif name in _RUN_KEYS and isinstance(value, yaml.ScalarNode) and _is_string(value):
                span = _scalar_span(value, text)
                if span is not None and span[0].lower().endswith(_CONFIG_SUFFIXES):
                    seen.add(id(value))
                    yield ("run", *span)
            yield from _node_mentions(value, text, seen)
    elif isinstance(node, yaml.SequenceNode):
        for item in node.value:
            yield from _node_mentions(item, text, seen)
    elif isinstance(node, yaml.ScalarNode) and _is_string(node) and is_package_reference(node.value):
        span = _scalar_span(node, text)  # a pkg:// reference names a file whatever key holds it
        if span is not None:
            yield ("include", *span)


def _is_string(node: yaml.ScalarNode) -> bool:
    """Is this a plain or quoted string — not a number, a null or a tagged value (``!ref:…``, ``!custom``)?"""
    return node.tag == _STR_TAG


def _tag_mentions(text: str) -> Iterator[_Found]:
    """The marker tags of a text PyYAML cannot compose: every tag token scanned before the error."""
    try:
        for token in yaml.scan(text, Loader=yaml.SafeLoader):
            if isinstance(token, yaml.TagToken):
                handle, suffix = token.value
                tag = (handle or "") + suffix
                for prefix in _CLASS_TAGS:
                    if tag.startswith(prefix):
                        found = _tag_name(text, token.start_mark.index, tag, prefix)
                        if found is not None:
                            yield ("class", *found)
    except yaml.YAMLError:
        return


def _tag_name(text: str, index: int, tag: str, prefix: str) -> Optional[Tuple[str, int, int, int]]:
    """``(name, line, start, stop)`` of the target a tag names, found in the text where the node starts.

    The node starts at its anchor or its tag; the tag as written is searched from there on the same line (PyYAML
    hands the tag back as written, ``%``-escapes aside — one that does not match is skipped).
    """
    line_end = text.find("\n", index)
    at = text.find(tag, index, len(text) if line_end < 0 else line_end)
    if at < 0:
        return None
    match = _TARGET.match(tag, len(prefix))
    if match is None:
        return None
    line_start = text.rfind("\n", 0, at) + 1
    line = text.count("\n", 0, at)
    start = at - line_start + match.start()
    return match.group(0), line, start, start + match.end() - match.start()


def _scalar_span(node: yaml.ScalarNode, text: str) -> Optional[Tuple[str, int, int, int]]:
    """``(value, line, start, stop)`` of a one-line scalar, its quotes outside the range; None for a multi-line one."""
    start, end = node.start_mark, node.end_mark
    if start.line != end.line:
        return None
    if node.style in ("'", '"'):
        return str(node.value), start.line, start.column + 1, end.column - 1
    written = text[start.index : end.index].rstrip()
    return written, start.line, start.column, start.column + len(written)


def _utf16(line: str, column: int) -> int:
    """A character column of ``line`` counted in UTF-16 code units, as an editor counts it."""
    return len(line[:column].encode("utf-16-le")) // 2


# -- where an include points --------------------------------------------------------------------------------------


def _include_location(entry: str, source: Path, *, beside: bool = True) -> Optional[Location]:
    """The file an ``include:`` entry of ``source`` reads, as :func:`confluid.load` finds it, or None.

    ``beside`` is whether the folder of ``source`` is searched first: true for an include, false for the config
    another run reads, which starts from the project folder and never sees the file that names it.
    """
    if "${" in entry:
        return None  # interpolated: known only once the document is resolved
    try:
        if is_package_reference(entry):
            found = resolve_package_reference(entry)
        else:
            with contextlib.chdir(_project_folder(source)):
                found = resolve_config_path(entry, base_dir=source.parent if beside else None)
                found = found if found.is_absolute() else Path.cwd() / found
    except ConfluidError:
        return None
    return Location(str(found.resolve()), 0) if found.is_file() else None


def _project_folder(source: Path) -> Path:
    """The nearest folder above ``source`` holding a ``pyproject.toml`` — where its configs are run — else its own."""
    for folder in source.parents:
        if (folder / "pyproject.toml").is_file():
            return folder
    return source.parent


# -- where a class or a module is defined, read from source --------------------------------------------------------


def _module_location(module: str) -> Optional[Location]:
    file = _module_file(module)
    return Location(str(file.resolve()), 0) if file is not None else None


def _class_location(target: str) -> Optional[Location]:
    """The definition a dotted target names, read from source; None for a registry name or one not found there."""
    name = target.split("@", 1)[0]
    parts = name.split(".")
    for split in range(len(parts) - 1, 0, -1):
        module, chain = ".".join(parts[:split]), parts[split:]
        file = _module_file(module)
        if file is None:
            continue
        line = _definition_line(file, module, chain, _REEXPORT_HOPS)
        if line is not None:
            return Location(str(file.resolve()), line) if isinstance(line, int) else line
    return None


def _module_file(module: str) -> Optional[Path]:
    """The source file of ``module``, found as the import system would find it — without importing anything.

    The top-level name is asked of every finder on :data:`sys.meta_path` (the path finder and each editable
    install's), and their folders are pooled with every ``<sys.path entry>/<name>`` folder — what
    ``pkgutil.extend_path`` adds —, so a package several checkouts extend spans them all. Each further part is
    looked up in those folders as a ``.py`` file or a package folder, the first folder first.
    """
    top, *rest = module.split(".")
    folders: List[Path] = []
    for finder in sys.meta_path:
        find_spec = getattr(finder, "find_spec", None)
        if find_spec is None:
            continue
        try:
            spec = find_spec(top, None)
        except Exception:  # noqa: BLE001 — a finder that cannot answer for this name is skipped
            continue
        if spec is None:
            continue
        if spec.submodule_search_locations is None:  # a plain module
            if not rest and spec.origin and spec.origin.endswith(".py"):
                return Path(spec.origin)
            continue
        folders.extend(Path(f) for f in spec.submodule_search_locations if Path(f) not in folders)
    if folders:
        folders.extend(
            Path(entry) / top for entry in sys.path if (Path(entry) / top).is_dir() and Path(entry) / top not in folders
        )
    if not folders:
        return None
    for index, part in enumerate(rest):
        last = index == len(rest) - 1
        deeper: List[Path] = []
        for folder in folders:
            if last and (folder / f"{part}.py").is_file():
                return folder / f"{part}.py"
            if (folder / part).is_dir():
                deeper.append(folder / part)
        folders = deeper
        if not folders:
            return None
    for folder in folders:
        if (folder / "__init__.py").is_file():
            return folder / "__init__.py"
    return None


def _definition_line(file: Path, module: str, chain: List[str], hops: int) -> Optional[Union[int, Location]]:
    """The 0-based line of ``chain`` (``Class`` or ``Outer.Inner``) in ``file``; a re-export's in another file.

    A name the module only imports (``from .sub import Class``, ``from .sub import *``) is followed, ``hops`` times
    at most, to the module defining it — then the answer is that module's :class:`Location`.
    """
    try:
        tree = ast.parse(file.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return None
    body: List[ast.stmt] = tree.body
    node: Optional[ast.stmt] = None
    for depth, name in enumerate(chain):
        node = _defined(body, name)
        if node is None:
            if depth == 0:
                break
            return None
        body = node.body if isinstance(node, ast.ClassDef) else []
    if node is not None:
        return node.lineno - 1
    if hops <= 0:
        return None
    package = module if file.name == "__init__.py" else module.rpartition(".")[0]
    stars: List[str] = []
    for stmt in tree.body:
        if not isinstance(stmt, ast.ImportFrom):
            continue
        source = _absolute(stmt, package)
        for alias in stmt.names:
            if alias.name == "*":
                stars.append(source)
            elif (alias.asname or alias.name) == chain[0]:
                found = _class_location_in(source, [alias.name, *chain[1:]], hops - 1)
                if found is not None:
                    return found
    for source in stars + _lazy_export_modules(tree, chain[0]):
        found = _class_location_in(source, chain, hops - 1)
        if found is not None:
            return found
    return None


def _lazy_export_modules(tree: ast.Module, name: str) -> List[str]:
    """The modules a lazy-export table names for ``name``, when the module answers names through ``__getattr__``.

    A package that imports its classes on first use (PEP 562) lists them in a dict literal — ``{"Model":
    "pkg.model"}`` or ``{"Model": ("pkg.model", "extra")}``. Each dotted string such an entry holds is a candidate;
    the caller keeps the one whose source defines ``name``.
    """
    if not any(isinstance(stmt, ast.FunctionDef) and stmt.name == "__getattr__" for stmt in tree.body) and not any(
        isinstance(stmt, ast.Assign) and any(_binds_getattr(t) for t in stmt.targets) for stmt in tree.body
    ):
        return []
    modules: List[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values):
            if not (isinstance(key, ast.Constant) and key.value == name):
                continue
            values = value.elts if isinstance(value, (ast.Tuple, ast.List)) else [value]
            modules.extend(
                v.value for v in values if isinstance(v, ast.Constant) and isinstance(v.value, str) and v.value
            )
    return modules


def _binds_getattr(target: ast.expr) -> bool:
    """Does an assignment target bind ``__getattr__`` (``__getattr__ = …`` or ``__getattr__, __dir__ = …``)?"""
    if isinstance(target, ast.Name):
        return target.id == "__getattr__"
    return isinstance(target, ast.Tuple) and any(_binds_getattr(e) for e in target.elts)


def _class_location_in(module: str, chain: List[str], hops: int) -> Optional[Location]:
    file = _module_file(module)
    if file is None:
        return None
    line = _definition_line(file, module, chain, hops)
    if line is None:
        return None
    return Location(str(file.resolve()), line) if isinstance(line, int) else line


def _absolute(stmt: ast.ImportFrom, package: str) -> str:
    """The module a ``from … import`` statement inside ``package`` reads, as an absolute name."""
    if not stmt.level:
        return stmt.module or ""
    parts = package.split(".")
    base = parts[: len(parts) - (stmt.level - 1)]
    return ".".join(base + ([stmt.module] if stmt.module else []))


def _defined(body: List[ast.stmt], name: str) -> Optional[ast.stmt]:
    """The class or function named ``name`` at the top of ``body``; else the assignment binding it (``Name = …``).

    A definition wins over an assignment that rewraps it (``Model = register(Model)``): the class is what a reader
    wants to see.
    """
    assigned: Optional[ast.stmt] = None
    for stmt in body:
        if isinstance(stmt, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and stmt.name == name:
            return stmt
        if isinstance(stmt, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in stmt.targets):
            assigned = assigned or stmt
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name) and stmt.target.id == name:
            assigned = assigned or stmt
    return assigned


if __name__ == "__main__":  # pragma: no cover — exercised by the subprocess tests
    sys.exit(main())
