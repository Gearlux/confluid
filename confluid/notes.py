"""What a config file says about its own settings: each top-level key's comment and the scopes it declares.

A config is read by people as much as by the loader, and the comments above its settings are its documentation.
:func:`document_notes` reads them back for a front end (a command's help, its tab completion): every top-level key of
ONE file — never its includes — with the comment lines directly above it (else the comment at the end of its line),
the class parameters it feeds through ``!ref:``, and every scope block the file writes, with its comment. Nothing is
built, imported or resolved: the file is composed by PyYAML (nodes and their lines), never constructed.

:func:`source_param_docs` answers a class parameter's help from the class's SOURCE — the ``Args:`` block
:func:`confluid.parse_param_docs` reads — without importing the module that defines it, so a tab completion can show
it in a fraction of a second where importing the class may take seconds.
"""

from __future__ import annotations

import ast
import importlib.util
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple, Union

import yaml

from confluid.keys import NOTSCOPE_KEY, PARTIAL_KEY, REF_KEY, SCOPE_KEY, TARGET_KEY
from confluid.loader import _held_text, resolve_config_path
from confluid.schema import _parse_docstring
from confluid.scopes import METADATA_KEYS

#: Top-level keys that are directives of the loader, never settings.
_DIRECTIVES = frozenset({"include", "import", *METADATA_KEYS})
#: The tag prefixes of a marker that builds (or defers) a class.
_CLASS_TAGS = ("!class:", "!partial:", "!lazy:")


@dataclass(frozen=True)
class KeyNote:
    """One top-level setting of a config file."""

    #: The key as written.
    name: str
    #: Its 1-based line in the file.
    line: int
    #: The comment lines directly above it, joined; else the comment at the end of its line; else empty.
    comment: str
    #: ``(class, parameter)`` for every marker parameter written ``!ref:<name>`` in the file, in file order — the class
    #: as spelled after its tag (``pkg.mod.Class``).
    feeds: Tuple[Tuple[str, str], ...] = ()


@dataclass(frozen=True)
class ScopeNote:
    """One scope block a config file writes."""

    #: What ``--scope`` takes to activate it: ``quick``, ``task=classification`` (several dimensions comma-joined).
    activation: str
    #: True for ``!notscope:`` / ``_notscope_``: active unless the scope is named.
    negated: bool
    #: Its 1-based line in the file (the line of the key holding it).
    line: int
    #: The comment lines directly above that line, joined; else the comment at its end; else empty.
    comment: str


@dataclass(frozen=True)
class DocumentNotes:
    """A config file's top-level settings and scope blocks, in file order."""

    keys: Tuple[KeyNote, ...] = ()
    scopes: Tuple[ScopeNote, ...] = ()


def document_notes(path: Union[str, Path]) -> DocumentNotes:
    """The top-level settings of the config file at ``path`` and the scope blocks it writes, each with its comment.

    A key's comment is the run of comment lines directly above it (a blank line ends the run), joined with spaces;
    without one, the comment at the end of its own line. A key whose value is a scope block (``quick: !scope:quick``)
    is a scope, not a setting, and the loader's directives (``include:``, ``import:``, ``default_scopes:`` …) are
    neither. Only this file is read — its includes have notes of their own. A relative ``path`` resolves through the
    config search tiers like :func:`confluid.load`'s; a file the active ``load(..., texts=…)`` holds is read from that
    text. A file whose top is not a plain mapping (a root ``!class:`` document) has no settings.

    Args:
        path: The config file.

    Returns:
        Its settings and scope blocks, in file order.
    """
    resolved = resolve_config_path(path).resolve()
    text = _held_text(resolved)
    if text is None:
        text = resolved.read_text(encoding="utf-8")
    root = yaml.compose(text, Loader=yaml.SafeLoader)
    lines = text.splitlines()
    scopes = tuple(_scope_notes(root, lines))
    if not isinstance(root, yaml.MappingNode) or _class_of(root) is not None:  # a root marker's keys are its kwargs
        return DocumentNotes(scopes=scopes)
    feeds = _ref_feeds(root)
    keys: List[KeyNote] = []
    for key, value in root.value:
        if not isinstance(key, yaml.ScalarNode) or key.tag == "tag:yaml.org,2002:merge":
            continue
        if key.value in _DIRECTIVES or _scope_of(value) is not None:
            continue
        keys.append(
            KeyNote(
                name=key.value,
                line=key.start_mark.line + 1,
                comment=_comment_of(lines, key),
                feeds=tuple(feeds.get(key.value, ())),
            )
        )
    return DocumentNotes(keys=tuple(keys), scopes=scopes)


def source_param_docs(target: str) -> Dict[str, str]:
    """``{parameter: help}`` of the class (or function) ``target`` names, read from its source — nothing is imported.

    ``target`` is a dotted path as a ``!class:`` tag spells it (``pkg.mod.Class``; a call suffix ``Class()`` and an
    ``@axis=value`` selector are ignored). The module's file is found with :func:`importlib.util.find_spec`, which
    imports the PACKAGES above the module (``pkg``), never the module itself; the class's ``__init__`` docstring
    entries are read, else the class docstring's — the docstrings :func:`confluid.parse_param_docs` reads. Limits:
    base classes are not followed (an undocumented subclass answers nothing), and a registry name without its module
    (``!class:Class``) answers nothing.

    Args:
        target: The class's dotted path.

    Returns:
        Its parameters' help; empty when the source cannot be found or documents none.
    """
    node = _source_target(target)
    if node is None:
        return {}
    if isinstance(node, ast.ClassDef):
        init = _defined(node.body, "__init__")
        entries = _parse_docstring(ast.get_docstring(init) or "") if init is not None else {}
        return entries or _parse_docstring(ast.get_docstring(node) or "")
    return _parse_docstring(ast.get_docstring(node) or "")


@dataclass(frozen=True)
class SourceParam:
    """One constructor parameter as the source writes it."""

    #: Its name.
    name: str
    #: Its annotation as written (``Optional[int]``), empty without one.
    annotation: str = ""
    #: Its default when the source writes a literal (``500``, ``30kHz``, ``[]``), as text; None without one.
    default: Optional[str] = None
    #: True for a yes/no parameter (annotated ``bool`` / ``Optional[bool]``, or defaulting to ``True``/``False``).
    switch: bool = False


def source_params(target: str) -> Tuple[SourceParam, ...]:
    """The constructor parameters of the class (or function) ``target`` names, in order, read from its source.

    The parameters a front end offers for a ``!class:`` block whether or not the config writes them — found as
    :func:`source_param_docs` finds the source, nothing imported. ``self``, ``*args`` and ``**kwargs`` are not
    parameters. A class without an ``__init__`` of its own answers nothing (base classes are not followed).

    Args:
        target: The class's dotted path, as a ``!class:`` tag spells it.

    Returns:
        Its parameters; empty when the source cannot be found.
    """
    node = _source_target(target)
    if isinstance(node, ast.ClassDef):
        node = _defined(node.body, "__init__")
        skip = 1  # self
    else:
        skip = 0
    if node is None or isinstance(node, ast.ClassDef):
        return ()
    args = node.args
    positional = [*args.posonlyargs, *args.args]
    defaults: List[Optional[ast.expr]] = [None] * (len(positional) - len(args.defaults)) + list(args.defaults)
    pairs = list(zip(positional, defaults))[skip:] + list(zip(args.kwonlyargs, args.kw_defaults))
    return tuple(_source_param(arg, default) for arg, default in pairs)


def _source_param(arg: ast.arg, default: Optional[ast.expr]) -> SourceParam:
    annotation = ast.unparse(arg.annotation) if arg.annotation is not None else ""
    shown: Optional[str] = None
    is_bool = False
    if default is not None:
        try:
            value = ast.literal_eval(default)
        except (ValueError, SyntaxError, TypeError):
            value = None
        else:
            is_bool = isinstance(value, bool)
            shown = None if value is None else str(value)
    switch = is_bool or annotation in _SWITCH_ANNOTATIONS
    return SourceParam(arg.arg, annotation, shown, switch)


#: The annotations of a yes/no parameter, as a source writes them.
_SWITCH_ANNOTATIONS = frozenset({"bool", "Optional[bool]", "typing.Optional[bool]", "bool | None"})


def _source_target(target: str) -> Optional[Union[ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef]]:
    """The definition ``target`` (``pkg.mod.Class``, call suffix and selector ignored) names, from its source."""
    name = target.split("@", 1)[0].split("(", 1)[0].strip()
    module, _, qualname = name.rpartition(".")
    if not module:
        return None
    return _source_definition(module, qualname, _REEXPORT_HOPS)


#: How many ``from … import Name`` re-exports :func:`source_param_docs` follows to the module defining a class.
_REEXPORT_HOPS = 3


def _source_definition(
    module: str, name: str, hops: int, origin: Optional[Path] = None
) -> Optional[Union[ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef]]:
    """The definition of ``name`` in ``module``'s source — followed through a package's ``from … import name``.

    ``origin`` is the module's file when already known (a re-export inside the same package, found beside the file
    re-exporting it); else :func:`importlib.util.find_spec` finds it, importing the packages above ``module`` only.
    """
    if origin is None:
        try:
            spec = importlib.util.find_spec(module)
        except Exception:  # a parent package that fails to import: no source to read
            return None
        found = getattr(spec, "origin", None) if spec is not None else None
        origin = Path(found) if found else None
    if origin is None or origin.suffix != ".py" or not origin.is_file():
        return None
    tree = ast.parse(origin.read_text(encoding="utf-8"))
    node = _defined(tree.body, name)
    if node is not None or hops <= 0:
        return node
    is_package = origin.name == "__init__.py"
    package = module if is_package else module.rpartition(".")[0]
    for stmt in tree.body:
        if not isinstance(stmt, ast.ImportFrom):
            continue
        for alias in stmt.names:
            if (alias.asname or alias.name) != name:
                continue
            source = stmt.module or ""
            if stmt.level:
                base = package.split(".")[: len(package.split(".")) - (stmt.level - 1)]
                source = ".".join(base + ([source] if source else []))
            return _source_definition(source, alias.name, hops - 1, _file_inside(source, package, origin.parent))
    return None


def _file_inside(module: str, package: str, folder: Path) -> Optional[Path]:
    """The file of ``module`` when it lies inside ``package`` (whose folder is ``folder``), found without importing."""
    if not module.startswith(package + "."):
        return None
    location = folder.joinpath(*module[len(package) + 1 :].split("."))
    for candidate in (location.with_suffix(".py"), location / "__init__.py"):
        if candidate.is_file():
            return candidate
    return None


def _defined(body: List[ast.stmt], name: str) -> Optional[Union[ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef]]:
    """The class or function named ``name`` defined at the top of ``body``, or None."""
    for stmt in body:
        if isinstance(stmt, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and stmt.name == name:
            return stmt
    return None


def _comment_of(lines: List[str], key: yaml.Node) -> str:
    """The comment lines directly above ``key``'s line, joined; else the comment at the end of that line."""
    index = key.start_mark.line
    above: List[str] = []
    row = index - 1
    while row >= 0 and lines[row].lstrip().startswith("#"):
        above.append(lines[row].lstrip().lstrip("#").strip())
        row -= 1
    joined = " ".join(text for text in reversed(above) if text)
    if joined:
        return joined
    return _trailing_comment(lines[index] if index < len(lines) else "", key.end_mark.column)


def _trailing_comment(line: str, start: int) -> str:
    """The comment ending ``line`` after column ``start``: a ``#`` after whitespace, outside a quoted scalar."""
    quote: Optional[str] = None
    previous = " "
    for column in range(start, len(line)):
        char = line[column]
        if quote is not None:
            if char == quote:
                quote = None
        elif char in "'\"" and previous in " \t[{,:":
            quote = char
        elif char == "#" and previous in " \t":
            return line[column + 1 :].strip()
        previous = char
    return ""


def _scope_of(node: yaml.Node) -> Optional[Tuple[str, bool]]:
    """``(activation, negated)`` when ``node`` is a scope block, in either spelling; else None."""
    tag = node.tag or ""
    for prefix, negated in (("!scope:", False), ("!notscope:", True)):
        if tag.startswith(prefix):
            return tag[len(prefix) :], negated
    if isinstance(node, yaml.MappingNode):
        for key, value in node.value:
            if isinstance(key, yaml.ScalarNode) and key.value in (SCOPE_KEY, NOTSCOPE_KEY):
                return _activation(value), key.value == NOTSCOPE_KEY
    return None


def _activation(value: yaml.Node) -> str:
    """The ``--scope`` spelling of a ``_scope_`` value: a name, or a mapping of dimensions to values."""
    if isinstance(value, yaml.MappingNode):
        parts = []
        for dim, val in value.value:
            text = getattr(val, "value", None)
            parts.append(str(dim.value) if text in (None, "", "~", "null") else f"{dim.value}={text}")
        return ",".join(parts)
    return str(getattr(value, "value", ""))


def _scope_notes(root: Optional[yaml.Node], lines: List[str]) -> Iterator[ScopeNote]:
    """Every scope block under ``root``, in file order, each with the comment of the key (or list item) holding it."""
    for holder, node in _children(root, None):
        found = _scope_of(node)
        if found is not None:
            at = holder if holder is not None else node
            yield ScopeNote(found[0], found[1], at.start_mark.line + 1, _comment_of(lines, at))


def _children(
    node: Optional[yaml.Node], holder: Optional[yaml.Node]
) -> Iterator[Tuple[Optional[yaml.Node], yaml.Node]]:
    """``(the key holding it or None, node)`` for every node under ``node``, depth first, in file order."""
    if node is None:
        return
    yield holder, node
    if isinstance(node, yaml.MappingNode):
        for key, value in node.value:
            yield from _children(value, key)
    elif isinstance(node, yaml.SequenceNode):
        for item in node.value:
            yield from _children(item, None)


def _ref_feeds(root: yaml.Node) -> Dict[str, List[Tuple[str, str]]]:
    """``{name: [(class, parameter), …]}``: each marker parameter whose value is ``!ref:<name>``, in either spelling."""
    feeds: Dict[str, List[Tuple[str, str]]] = {}
    for _, node in _children(root, None):
        target = _class_of(node)
        if target is None or not isinstance(node, yaml.MappingNode):
            continue
        for key, value in node.value:
            name = _ref_name(value)
            if name is not None and isinstance(key, yaml.ScalarNode) and key.value not in (TARGET_KEY, PARTIAL_KEY):
                feeds.setdefault(name.split(".", 1)[0], []).append((target, key.value))
    return feeds


def _class_of(node: yaml.Node) -> Optional[str]:
    """The class a marker node names (``!class:pkg.Cls`` or ``_target_: pkg.Cls``), or None."""
    tag = node.tag or ""
    for prefix in _CLASS_TAGS:
        if tag.startswith(prefix):
            return tag[len(prefix) :]
    if isinstance(node, yaml.MappingNode):
        for key, value in node.value:
            if isinstance(key, yaml.ScalarNode) and key.value == TARGET_KEY and isinstance(value, yaml.ScalarNode):
                return str(value.value)
    return None


def _ref_name(node: yaml.Node) -> Optional[str]:
    """The name a reference node points at (``!ref:name``, ``!ref name``, ``{_ref_: name}``), or None."""
    tag = node.tag or ""
    if tag.startswith("!ref:"):
        return tag[len("!ref:") :]
    if tag == "!ref" and isinstance(node, yaml.ScalarNode):
        return str(node.value)
    if isinstance(node, yaml.MappingNode):
        for key, value in node.value:
            if isinstance(key, yaml.ScalarNode) and key.value == REF_KEY and isinstance(value, yaml.ScalarNode):
                return str(value.value)
    return None
