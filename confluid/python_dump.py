"""``dump_python`` — a live object graph as a Python script that builds it again.

``dump()`` writes an object graph as a document ``load()`` rebuilds; ``dump_python()`` writes the same graph as
constructor calls a plain Python interpreter runs, for a reader who wants code rather than a config — each class
imported by its module, each argument written as a keyword. The arguments are :func:`~confluid.dumper.dumpable_kwargs`
of every object, the one reconstruction rule ``dump()`` uses, so the script and the document can never disagree about
what an object is; ``dump(<what the script builds>) == dump(<the original>)`` is the contract.

What a script cannot rebuild is refused, naming where it sits in the graph (``obj.signals[0].source``): an object that
is neither ``@configurable``, stamped by confluid nor given a dump spelling, a class whose ``module.qualname`` does not
import back to it (a class defined inside a function, ``__main__``), a set, a deferred marker held as an attribute, and
an object that contains itself. An object reached twice is written once, as a variable, so the rebuilt graph shares it
too.
"""

import enum
import importlib
import keyword
import math
import os
import pathlib
import re
import types
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from confluid.dumper import _DUMP_SPELLINGS, dumpable_kwargs, is_dumpable_object
from confluid.exceptions import PythonDumpError
from confluid.fluid import Fluid, Target

__all__ = ["dump_python"]

#: The line length a call, list or mapping is kept on one line within — above it, one argument per line.
_WIDTH = 100


@dataclass
class _Call:
    """One object of the graph: the class's name in the script and its keyword arguments."""

    name: str
    kwargs: List[Tuple[str, "_Node"]] = field(default_factory=list)
    uses: int = 1
    variable: Optional[str] = None


@dataclass
class _Seq:
    """A list or a tuple."""

    open: str
    close: str
    items: List["_Node"]


@dataclass
class _Map:
    """A dict."""

    items: List[Tuple["_Node", "_Node"]]


_Node = Union[str, _Call, _Seq, _Map]  # a str is source text already


class _Writer:
    """Turns the graph into nodes, collecting the imports and the objects met more than once."""

    def __init__(self) -> None:
        self.imports: Dict[str, Dict[str, str]] = {}  # module -> {imported name: name in the script}
        self.taken: Dict[str, Tuple[str, str]] = {}  # name in the script -> (module, imported name)
        self.calls: Dict[int, _Call] = {}
        self.order: List[_Call] = []  # objects in the order they were finished: dependencies first
        self.active: Set[int] = set()
        self.keep: List[Any] = []  # pins every object whose id() keys `calls`

    def name_of(self, target: Any, where: str) -> str:
        """The name ``target`` (a class, an enum or a function) goes by in the script, imported once."""
        module, qualname = getattr(target, "__module__", None), getattr(target, "__qualname__", None)
        dotted = f"{module}.{qualname}"
        if not module or not qualname or "<" in qualname or module == "__main__" or not _imports_to(target):
            raise PythonDumpError(
                f"{where}: {dotted} cannot be imported by a script — define it at the top level of an importable "
                "module"
            )
        module = _public_module(target, module)
        head, _, rest = qualname.partition(".")
        names = self.imports.setdefault(module, {})
        if head not in names:
            alias = head
            if alias in self.taken and self.taken[alias] != (module, head):
                alias = f"{module.replace('.', '_')}_{head}"
            names[head] = alias
            self.taken[alias] = (module, head)
        return names[head] + (f".{rest}" if rest else "")

    def node(self, value: Any, where: str) -> _Node:
        if isinstance(value, enum.Enum):  # first: an IntEnum or a StrEnum is an int or a str too
            return f"{self.name_of(type(value), where)}.{value.name}"
        if value is None or isinstance(value, bool):
            return repr(value)
        # a subclass (numpy's float64 is a float) is written as its base
        if isinstance(value, int):
            return repr(int(value))
        if isinstance(value, float):
            return _float(float(value))
        if isinstance(value, str):
            return repr(str(value))
        if isinstance(value, bytes):
            return repr(bytes(value))
        if isinstance(value, complex):
            return repr(complex(value))
        if isinstance(value, pathlib.Path):  # a concrete path is written as pathlib's public Path, not its flavour
            return f"{self.name_of(pathlib.Path, where)}({os.fspath(value)!r})"
        if isinstance(value, os.PathLike):
            return f"{self.name_of(type(value), where)}({os.fspath(value)!r})"
        if type(value).__module__ == "numpy" and hasattr(value, "item") and getattr(value, "shape", None) == ():
            return self.node(value.item(), where)
        if isinstance(value, type) or isinstance(value, (types.FunctionType, types.BuiltinFunctionType)):
            return self.name_of(value, where)
        if isinstance(value, list):
            return _Seq("[", "]", [self.node(v, f"{where}[{i}]") for i, v in enumerate(value)])
        if isinstance(value, tuple):
            return _Seq("(", ")", [self.node(v, f"{where}[{i}]") for i, v in enumerate(value)])
        if isinstance(value, dict):
            return _Map([(self.node(k, where), self.node(v, f"{where}[{k!r}]")) for k, v in value.items()])
        if isinstance(value, (set, frozenset)):
            raise PythonDumpError(f"{where}: a set has no order a script could restate — hold a list or a tuple")
        if isinstance(value, Fluid):
            raise PythonDumpError(
                f"{where}: a deferred marker ({type(value).__name__}) — a script builds objects; give the slot the "
                "object it stands for"
            )
        spelled = _spelling(value)
        if spelled is not _NONE:
            if isinstance(spelled, Target) and not spelled.partial:
                return self.call(value, spelled.target, spelled.kwargs, where)
            return self.node(spelled, where)
        if is_dumpable_object(value):
            if hasattr(type(value), "__confluid_configurable__"):
                return self.call(value, type(value), dumpable_kwargs(value), where)
            return self.call(value, value.__confluid_class__, dict(getattr(value, "__confluid_kwargs__", {})), where)
        raise PythonDumpError(
            f"{where}: a {type(value).__module__}.{type(value).__qualname__} is not a configurable object — make it "
            "@configurable, or register its spelling with register_dump_spelling"
        )

    def call(self, value: Any, target: Any, kwargs: Dict[str, Any], where: str) -> _Call:
        key = id(value)
        if key in self.active:
            raise PythonDumpError(
                f"{where}: refers back to itself — a script cannot build an object that contains itself"
            )
        seen = self.calls.get(key)
        if seen is not None:
            seen.uses += 1
            return seen
        call = _Call(self.name_of(target, where))
        self.active.add(key)
        for name, arg in kwargs.items():
            if not isinstance(name, str) or not name.isidentifier() or keyword.iskeyword(name):
                raise PythonDumpError(f"{where}: the argument {name!r} cannot be written as a Python keyword")
            call.kwargs.append((name, self.node(arg, f"{where}.{name}")))
        self.active.discard(key)
        self.calls[key] = call
        self.keep.append(value)
        self.order.append(call)
        return call


_NONE = object()


def _spelling(value: Any) -> Any:
    """The registered dump spelling of ``value``'s type (``_NONE`` when it has none or declines)."""
    for base in type(value).__mro__:
        spell = _DUMP_SPELLINGS.get(base)
        if spell is not None:
            spelled = spell(value)
            return _NONE if spelled is None else spelled
    return _NONE


def _imports_to(target: Any) -> bool:
    """Whether ``module.qualname`` imports back to ``target`` itself."""
    try:
        found: Any = importlib.import_module(target.__module__)
    except ImportError:
        return False
    for part in target.__qualname__.split("."):
        found = getattr(found, part, None)
    return found is target


def _public_module(target: Any, module: str) -> str:
    """The module a reader imports ``target`` from: the package above a private module (``pathlib._local`` →
    ``pathlib``) when that package exports the same object under its name, else ``module`` itself."""
    parts = module.split(".")
    private = next((i for i, part in enumerate(parts) if part.startswith("_")), None)
    if private is None or private == 0 or "." in target.__qualname__:
        return module
    public = ".".join(parts[:private])
    try:
        found = getattr(importlib.import_module(public), target.__qualname__, None)
    except ImportError:  # pragma: no cover - the package of an imported module imports
        return module
    return public if found is target else module


def _float(value: float) -> str:
    if math.isnan(value):
        return "float('nan')"
    if math.isinf(value):
        return "float('inf')" if value > 0 else "float('-inf')"
    return repr(value)


def _inline(node: _Node) -> str:
    if isinstance(node, str):
        return node
    if isinstance(node, _Call):
        if node.variable is not None:
            return node.variable
        return f"{node.name}(" + ", ".join(f"{k}={_inline(v)}" for k, v in node.kwargs) + ")"
    if isinstance(node, _Seq):
        body = ", ".join(_inline(v) for v in node.items)
        if node.open == "(" and len(node.items) == 1:
            body += ","
        return f"{node.open}{body}{node.close}"
    return "{" + ", ".join(f"{_inline(k)}: {_inline(v)}" for k, v in node.items) + "}"


def _render(node: _Node, indent: int, lead: int, own: bool = False) -> str:
    """``node`` as source starting ``lead`` characters into a line indented ``indent``; ``own`` writes a shared
    object's call itself rather than its variable (its definition)."""
    if isinstance(node, _Call) and node.variable is not None and not own:
        return node.variable
    flat = _call_text(node) if own and isinstance(node, _Call) else _inline(node)
    if isinstance(node, str) or indent + lead + len(flat) + 1 <= _WIDTH:
        return flat
    pad, inner = " " * indent, " " * (indent + 4)
    if isinstance(node, _Call):
        lines = [f"{inner}{k}={_render(v, indent + 4, len(k) + 1)}," for k, v in node.kwargs]
        return f"{node.name}(\n" + "\n".join(lines) + f"\n{pad})"
    if isinstance(node, _Seq):
        lines = [f"{inner}{_render(v, indent + 4, 0)}," for v in node.items]
        return f"{node.open}\n" + "\n".join(lines) + f"\n{pad}{node.close}"
    lines = []
    for k, v in node.items:
        key = _render(k, indent + 4, 0)
        lines.append(f"{inner}{key}: {_render(v, indent + 4, len(key) + 2)},")
    return "{\n" + "\n".join(lines) + f"\n{pad}}}"


def _call_text(node: _Call) -> str:
    return f"{node.name}(" + ", ".join(f"{k}={_inline(v)}" for k, v in node.kwargs) + ")"


def _snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name.split(".")[-1]).lower()


def dump_python(obj: Any, *, name: str = "obj", header: Optional[str] = None) -> str:
    """Python source that builds ``obj`` again: the imports, then ``<name> = <the constructor calls>``.

    Args:
        obj: The object graph — ``@configurable`` objects, objects confluid built, values a registered dump spelling
            writes, and plain data (numbers, strings, lists, tuples, dicts, enums, paths, classes, functions).
        name: The variable the script assigns the object to; a Python name.
        header: Text written first, each line as a ``#`` comment (what made the script, how to run it).

    Raises:
        PythonDumpError: ``name`` is not a Python name, or the graph holds a value a script cannot build (see the
            module docstring); the message names where it sits, from ``name``.
    """
    if not name.isidentifier() or keyword.iskeyword(name):
        raise PythonDumpError(f"dump_python: {name!r} is not a Python name — give the variable a name like 'generator'")
    writer = _Writer()
    root = writer.node(obj, name)
    shared = [call for call in writer.order if call.uses > 1 and call is not root]
    counts: Dict[str, int] = {}
    for call in shared:
        base = _snake(call.name)
        counts[base] = counts.get(base, 0) + 1
        call.variable = f"{base}_{counts[base]}"
        while call.variable in writer.taken or call.variable == name:
            counts[base] += 1
            call.variable = f"{base}_{counts[base]}"

    parts: List[str] = []
    if header is not None:
        parts.append("\n".join(f"# {line}".rstrip() for line in header.splitlines()) + "\n")
    if writer.imports:
        imports = []
        for module in sorted(writer.imports):
            names = sorted(writer.imports[module].items())
            listed = ", ".join(n if alias == n else f"{n} as {alias}" for n, alias in names)
            imports.append(f"from {module} import {listed}")
        parts.append("\n".join(imports) + "\n")
    for call in shared:
        parts.append(f"{call.variable} = {_render(call, 0, len(call.variable or '') + 3, own=True)}\n")
    parts.append(f"{name} = {_render(root, 0, len(name) + 3)}\n")
    return "\n".join(parts)
